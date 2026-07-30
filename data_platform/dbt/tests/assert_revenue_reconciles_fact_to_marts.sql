/*
    QR-BAL-030 continued: fact → aggregation marts.

    Aggregates are where a wrong GROUP BY or a dropped join silently loses
    revenue. Both marts must sum to the fact they derive from; if either
    drifts, no dashboard built on it can be trusted.
*/

with fact_total as (
    select coalesce(sum(net_amount), 0) as amount from {{ ref('fct_sales') }}
),

sales_mart_total as (
    select coalesce(sum(net_revenue), 0) as amount from {{ ref('mart_sales_daily') }}
),

kpi_mart_total as (
    select coalesce(sum(net_revenue), 0) as amount from {{ ref('mart_kpi_daily') }}
)

select
    f.amount as fact_amount,
    s.amount as sales_mart_amount,
    k.amount as kpi_mart_amount
from fact_total f
cross join sales_mart_total s
cross join kpi_mart_total k
where abs(f.amount - s.amount) > 0.01
   or abs(f.amount - k.amount) > 0.01
