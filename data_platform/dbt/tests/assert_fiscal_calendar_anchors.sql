/*
    The 4-5-4 calendar against published NRF anchors.

    Fiscal-year starts are external facts, not something to derive and hope
    about: if this drifts, every YoY comparison in the platform silently
    compares the wrong weeks. Anchors below are the NRF-published dates.
*/

with expected as (
    select * from (values
        (2025, date '2025-02-02'),
        (2026, date '2026-02-01'),
        (2027, date '2027-01-31')
    ) as t(fiscal_year, expected_start)
),

actual as (
    select fiscal_year, min(full_date) as actual_start
    from {{ ref('dim_date') }}
    group by fiscal_year
)

select e.fiscal_year, e.expected_start, a.actual_start
from expected e
join actual a on e.fiscal_year = a.fiscal_year
where e.expected_start <> a.actual_start
