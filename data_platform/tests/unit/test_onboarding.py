"""Onboarding — detect what an upload is, map its columns, validate its rows.

Three fictional retailers, each naming the same POS sales concepts
differently, stand in for the real product problem: a tenant's file never
matches `ingestion/schemas/pos/sales.yml` column-for-column, and onboarding
has to bridge that gap without a human hand-mapping every file.
"""

from onboarding.detection import detect_dataset_type
from onboarding.mapping import suggest_column_mapping
from onboarding.validate import validate_mapped_dataset

# ── Three retailers, three naming conventions, the same sales concept ──

COMPANY_A_COLUMNS = ["transaction_id", "date", "sku", "store", "qty", "sales"]
COMPANY_B_COLUMNS = ["order_no", "business_date", "product_code", "location_id", "units", "revenue"]
COMPANY_C_COLUMNS = [
    "invoice_id",
    "sale_timestamp",
    "item_code",
    "branch_id",
    "quantity_sold",
    "net_value",
]

# What each company's columns should resolve to on the real, shipped
# `pos.sales` schema (`ingestion/schemas/pos/sales.yml`) — not a hypothetical
# canonical model. That schema's business keys are `order_id`/`line_no`/`sku`/
# `store_id`, its event-time column is `transaction_ts`, and its sales measure
# is `gross_amount`; there is no separate `product_id`/`business_date`/
# `net_amount` field in the declared contract, so mapping targets those real
# names instead of inventing new ones.
COMPANY_A_EXPECTED_MAPPING = {
    "transaction_id": "order_id",
    "date": "transaction_ts",
    "sku": "sku",
    "store": "store_id",
    "qty": "quantity",
    "sales": "gross_amount",
}
COMPANY_B_EXPECTED_MAPPING = {
    "order_no": "order_id",
    "business_date": "transaction_ts",
    "product_code": "sku",
    "location_id": "store_id",
    "units": "quantity",
    "revenue": "gross_amount",
}
COMPANY_C_EXPECTED_MAPPING = {
    "invoice_id": "order_id",
    "sale_timestamp": "transaction_ts",
    "item_code": "sku",
    "branch_id": "store_id",
    "quantity_sold": "quantity",
    "net_value": "gross_amount",
}

HR_COLUMNS = ["employee_id", "hire_date", "department", "salary", "manager_name"]


# ── Detection ────────────────────────────────────────────────────────


def _top(columns: list[str]):
    results = detect_dataset_type(columns)
    assert results, "detect_dataset_type must return at least one candidate"
    return results[0]


def test_company_a_is_detected_as_pos_sales() -> None:
    top = _top(COMPANY_A_COLUMNS)
    assert (top.source, top.table) == ("pos", "sales")
    assert top.confidence > 0.5


def test_company_b_is_detected_as_pos_sales() -> None:
    top = _top(COMPANY_B_COLUMNS)
    assert (top.source, top.table) == ("pos", "sales")
    assert top.confidence > 0.5


def test_company_c_is_detected_as_pos_sales() -> None:
    top = _top(COMPANY_C_COLUMNS)
    assert (top.source, top.table) == ("pos", "sales")
    assert top.confidence > 0.5


def test_detection_results_are_sorted_by_confidence_descending() -> None:
    results = detect_dataset_type(COMPANY_A_COLUMNS)
    confidences = [r.confidence for r in results]
    assert confidences == sorted(confidences, reverse=True)


def test_detection_beats_every_other_candidate_by_a_clear_margin() -> None:
    """The winning schema should not just edge out the runner-up — a sales
    file sharing `sku`/`store_id` with inventory and purchasing schemas means
    some competing confidence is expected, but pos.sales should win clearly."""
    results = detect_dataset_type(COMPANY_A_COLUMNS)
    assert results[0].confidence > results[1].confidence * 1.3


def test_unrelated_columns_score_low_for_every_dataset_type() -> None:
    """HR data must not be mistaken for any declared retail schema."""
    results = detect_dataset_type(HR_COLUMNS)
    assert results, "the registry must still return candidates, just low-confidence ones"
    for result in results:
        assert result.confidence < 0.2, (
            f"{result.source}.{result.table} scored {result.confidence} "
            "against unrelated HR columns"
        )


def test_missing_required_columns_are_reported() -> None:
    """None of the three companies supply `line_no`, `currency`, or `channel` —
    all required on the real schema — so detection should say so rather than
    silently claiming a perfect match."""
    top = _top(COMPANY_A_COLUMNS)
    assert "line_no" in top.missing_required
    assert "currency" in top.missing_required


# ── Mapping ──────────────────────────────────────────────────────────


def _mapping_dict(columns: list[str], schema) -> dict[str, str | None]:
    return {s.source_column: s.canonical_field for s in suggest_column_mapping(columns, schema)}


def test_company_a_columns_map_to_the_real_sales_schema(pos_schema) -> None:
    assert _mapping_dict(COMPANY_A_COLUMNS, pos_schema) == COMPANY_A_EXPECTED_MAPPING


def test_company_b_columns_map_to_the_real_sales_schema(pos_schema) -> None:
    assert _mapping_dict(COMPANY_B_COLUMNS, pos_schema) == COMPANY_B_EXPECTED_MAPPING


def test_company_c_columns_map_to_the_real_sales_schema(pos_schema) -> None:
    assert _mapping_dict(COMPANY_C_COLUMNS, pos_schema) == COMPANY_C_EXPECTED_MAPPING


def test_mapping_confidence_and_reason_are_populated(pos_schema) -> None:
    suggestions = suggest_column_mapping(COMPANY_A_COLUMNS, pos_schema)
    by_column = {s.source_column: s for s in suggestions}

    assert by_column["sku"].confidence == 1.0
    assert by_column["sku"].reason == "exact match"

    assert by_column["qty"].canonical_field == "quantity"
    assert by_column["qty"].confidence == 0.9
    assert by_column["qty"].reason == "synonym match"


def test_unmapped_column_gets_no_confident_match(pos_schema) -> None:
    suggestions = suggest_column_mapping([*COMPANY_A_COLUMNS, "loyalty_signup_source"], pos_schema)
    extra = next(s for s in suggestions if s.source_column == "loyalty_signup_source")
    assert extra.canonical_field is None
    assert extra.confidence == 0.0
    assert extra.reason == "no confident match"


def test_two_columns_do_not_collide_on_the_same_canonical_field(pos_schema) -> None:
    """`store` and `store_id` would both plausibly target `store_id` in
    isolation; the greedy assignment must not map both onto it."""
    suggestions = suggest_column_mapping(["store", "store_id"], pos_schema)
    targets = [s.canonical_field for s in suggestions if s.canonical_field is not None]
    assert len(targets) == len(set(targets)), "two source columns were mapped to the same field"
    # The exact match claims the field; the synonym match is left unmapped
    # rather than silently overwriting it.
    by_column = {s.source_column: s for s in suggestions}
    assert by_column["store_id"].canonical_field == "store_id"
    assert by_column["store_id"].reason == "exact match"


# ── Validation ───────────────────────────────────────────────────────


def _mapped_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        order_id="O1",
        line_no=1,
        sku="SKU-001",
        store_id="STORE-1",
        transaction_ts="2026-08-01 10:00:00",
        quantity=2,
        gross_amount=20.0,
        unit_price=10.0,
        currency="USD",
        channel="store",
    )
    base.update(overrides)
    return base


def test_clean_mapped_dataset_is_fully_valid(pos_schema) -> None:
    rows = [_mapped_row(order_id=f"O{i}") for i in range(5)]
    report = validate_mapped_dataset(rows, pos_schema)
    assert report.total_records == 5
    assert report.valid_records == 5
    assert report.valid_pct == 100.0
    assert report.issues == []


def test_validation_catches_missing_product_id_unparseable_date_and_bad_quantity(
    pos_schema,
) -> None:
    """Three real, injected defects in an otherwise-clean five-row file:

    * row 2 is missing its product identifier (`sku`)
    * row 3 has an unparseable `transaction_ts`
    * row 4 has a quantity far outside the schema's declared bounds
      (real `pos.sales.quantity` allows -10000..10000 for returns, so the
      injected defect is a value beyond even that — a genuine data fault,
      not a normal return)
    """
    rows = [
        _mapped_row(order_id="O1"),
        _mapped_row(order_id="O2", sku=None),
        _mapped_row(order_id="O3", transaction_ts="not-a-real-date"),
        _mapped_row(order_id="O4", quantity=-99999),
        _mapped_row(order_id="O5"),
    ]
    report = validate_mapped_dataset(rows, pos_schema)

    assert report.total_records == 5
    assert report.valid_records == 2  # O1 and O5 only
    assert report.valid_pct == 40.0

    by_code = {issue.code: issue for issue in report.issues}

    assert by_code["MISSING_REQUIRED_SKU"].severity == "error"
    assert by_code["MISSING_REQUIRED_SKU"].count == 1
    assert "O2" in by_code["MISSING_REQUIRED_SKU"].sample_ids[0]

    assert by_code["INVALID_DATE_TRANSACTION_TS"].severity == "error"
    assert by_code["INVALID_DATE_TRANSACTION_TS"].count == 1
    assert "O3" in by_code["INVALID_DATE_TRANSACTION_TS"].sample_ids[0]

    assert by_code["OUT_OF_RANGE_QUANTITY"].severity == "error"
    assert by_code["OUT_OF_RANGE_QUANTITY"].count == 1
    assert "O4" in by_code["OUT_OF_RANGE_QUANTITY"].sample_ids[0]


def test_validation_reports_business_language_messages(pos_schema) -> None:
    rows = [_mapped_row(order_id="O1", sku=None)]
    report = validate_mapped_dataset(rows, pos_schema)
    issue = next(i for i in report.issues if i.code == "MISSING_REQUIRED_SKU")
    assert issue.message == "1 records have a missing sku"


def test_duplicate_business_key_is_a_warning_not_an_error(pos_schema) -> None:
    """Same (order_id, line_no) twice — a warning, matching the severity
    `quality.rules.DUPLICATE_RATE` assigns the same situation post-load."""
    rows = [
        _mapped_row(order_id="O1", line_no=1),
        _mapped_row(order_id="O1", line_no=1),
    ]
    report = validate_mapped_dataset(rows, pos_schema)
    issue = next(i for i in report.issues if i.code == "DUPLICATE_BUSINESS_KEY")
    assert issue.severity == "warning"
    assert issue.count == 1
    # A warning alone does not invalidate the record.
    assert report.valid_records == 2


def test_non_numeric_measure_is_caught(pos_schema) -> None:
    rows = [_mapped_row(order_id="O1", gross_amount="not-a-number")]
    report = validate_mapped_dataset(rows, pos_schema)
    issue = next(i for i in report.issues if i.code == "INVALID_NUMBER_GROSS_AMOUNT")
    assert issue.count == 1
    assert report.valid_records == 0


def test_empty_dataset_reports_zero_percent_not_a_division_error(pos_schema) -> None:
    report = validate_mapped_dataset([], pos_schema)
    assert report.total_records == 0
    assert report.valid_records == 0
    assert report.valid_pct == 0.0
    assert report.issues == []
