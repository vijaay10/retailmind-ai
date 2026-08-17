"""PII scrubbing layer — prevent sensitive data from reaching external LLMs."""

import pytest

from app.infrastructure.llm.scrubbing import PIIScrubber


@pytest.fixture(scope="module")
def scrubber() -> PIIScrubber:
    return PIIScrubber()


# ── Email detection and scrubbing ────────────────────────────────────


def test_scrub_email_address(scrubber: PIIScrubber) -> None:
    text = "Contact john.doe@example.com for details"
    scrubbed = scrubber.scrub(text)
    assert "[EMAIL_REDACTED]" in scrubbed
    assert "john.doe@example.com" not in scrubbed


def test_contains_pii_detects_email(scrubber: PIIScrubber) -> None:
    assert scrubber.contains_pii("Email me at user@domain.com")
    assert not scrubber.contains_pii("No sensitive data here")


def test_scrub_multiple_emails(scrubber: PIIScrubber) -> None:
    text = "Send to alice@example.com and bob@company.org"
    scrubbed = scrubber.scrub(text)
    assert scrubbed.count("[EMAIL_REDACTED]") == 2
    assert "alice@example.com" not in scrubbed
    assert "bob@company.org" not in scrubbed


# ── Phone number detection and scrubbing ─────────────────────────────


def test_scrub_phone_number_formats(scrubber: PIIScrubber) -> None:
    cases = [
        ("Call 555-123-4567", "555-123-4567"),
        ("Phone: (555) 123-4567", "(555) 123-4567"),
        ("Contact 555.123.4567", "555.123.4567"),
        ("Mobile +1-555-123-4567", "+1-555-123-4567"),
    ]

    for text, phone in cases:
        scrubbed = scrubber.scrub(text)
        assert "[PHONE_REDACTED]" in scrubbed, f"Failed to scrub {phone}"
        assert phone not in scrubbed


def test_contains_pii_detects_phone(scrubber: PIIScrubber) -> None:
    assert scrubber.contains_pii("Call 555-123-4567")
    assert not scrubber.contains_pii("The year 2024 has no PII")


# ── Credit card detection and scrubbing ──────────────────────────────


def test_scrub_credit_card_number(scrubber: PIIScrubber) -> None:
    text = "Card 4532-1234-5678-9010 on file"
    scrubbed = scrubber.scrub(text)
    assert "[CARD_REDACTED]" in scrubbed
    assert "4532-1234-5678-9010" not in scrubbed


def test_scrub_credit_card_no_separators(scrubber: PIIScrubber) -> None:
    text = "Card number: 4532123456789010"
    scrubbed = scrubber.scrub(text)
    assert "[CARD_REDACTED]" in scrubbed
    assert "4532123456789010" not in scrubbed


def test_contains_pii_detects_credit_card(scrubber: PIIScrubber) -> None:
    assert scrubber.contains_pii("Card 4532-1234-5678-9010")


# ── SSN detection and scrubbing ──────────────────────────────────────


def test_scrub_ssn(scrubber: PIIScrubber) -> None:
    text = "SSN: 123-45-6789"
    scrubbed = scrubber.scrub(text)
    assert "[SSN_REDACTED]" in scrubbed
    assert "123-45-6789" not in scrubbed


def test_contains_pii_detects_ssn(scrubber: PIIScrubber) -> None:
    assert scrubber.contains_pii("SSN 123-45-6789")


# ── UK-specific PII ──────────────────────────────────────────────────


def test_scrub_uk_postcode(scrubber: PIIScrubber) -> None:
    postcodes = ["SW1A 1AA", "EC1A 1BB", "W1A 0AX"]
    for postcode in postcodes:
        text = f"Address: {postcode}"
        scrubbed = scrubber.scrub(text)
        assert "[POSTCODE]" in scrubbed, f"Failed to scrub {postcode}"
        assert postcode not in scrubbed


def test_scrub_uk_ni_number(scrubber: PIIScrubber) -> None:
    text = "NI Number: AB 12 34 56 C"
    scrubbed = scrubber.scrub(text)
    assert "[NI_NUMBER_REDACTED]" in scrubbed
    assert "AB 12 34 56 C" not in scrubbed


def test_contains_pii_detects_uk_patterns(scrubber: PIIScrubber) -> None:
    assert scrubber.contains_pii("Postcode SW1A 1AA")
    assert scrubber.contains_pii("NI: AB123456C")


# ── Mixed PII scenarios ──────────────────────────────────────────────


def test_scrub_multiple_pii_types(scrubber: PIIScrubber) -> None:
    """Multiple PII types in one text should all be scrubbed."""
    text = "Customer john@example.com called 555-123-4567 regarding card 4532-1234-5678-9010"
    scrubbed = scrubber.scrub(text)

    assert "[EMAIL_REDACTED]" in scrubbed
    assert "[PHONE_REDACTED]" in scrubbed
    assert "[CARD_REDACTED]" in scrubbed
    assert "john@example.com" not in scrubbed
    assert "555-123-4567" not in scrubbed
    assert "4532-1234-5678-9010" not in scrubbed


def test_scrub_preserves_non_pii_content(scrubber: PIIScrubber) -> None:
    """Business content should remain intact."""
    text = "Revenue for Q4 2024 was $1,234,567 across 42 stores"
    scrubbed = scrubber.scrub(text)
    assert scrubbed == text  # No PII, no changes


def test_scrub_empty_string(scrubber: PIIScrubber) -> None:
    assert scrubber.scrub("") == ""
    assert not scrubber.contains_pii("")


def test_scrub_business_context_with_email(scrubber: PIIScrubber) -> None:
    """Real-world example: business context with embedded PII."""
    text = (
        "Store manager alice@store.com reported 15% increase in sales. "
        "Q4 revenue: $250,000. Customer satisfaction: 4.2/5."
    )
    scrubbed = scrubber.scrub(text)

    # PII removed
    assert "[EMAIL_REDACTED]" in scrubbed
    assert "alice@store.com" not in scrubbed

    # Business data preserved
    assert "15% increase" in scrubbed
    assert "$250,000" in scrubbed
    assert "4.2/5" in scrubbed
