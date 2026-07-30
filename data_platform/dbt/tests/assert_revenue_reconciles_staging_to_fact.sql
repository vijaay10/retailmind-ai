/*
    QR-BAL-030: conservation of money, staging → fact (DB design §17).

    The highest-value test class in a financial pipeline. Row-level tests
    cannot see join fanout, dedup overreach, or a window slip — but all three
    change the total, and this catches every one of them.

    Tolerance is one cent for rounding, not a fudge factor: any real discrepancy
    is orders of magnitude larger.
*/

with staging_total as (
    select coalesce(sum(net_amount), 0) as amount, count(*) as rows
    from {{ ref('stg_pos__sales') }}
),

fact_total as (
    select coalesce(sum(net_amount), 0) as amount, count(*) as rows
    from {{ ref('fct_sales') }}
)

select
    s.amount as staging_amount,
    f.amount as fact_amount,
    s.rows as staging_rows,
    f.rows as fact_rows
from staging_total s
cross join fact_total f
where abs(s.amount - f.amount) > 0.01
   or s.rows <> f.rows
