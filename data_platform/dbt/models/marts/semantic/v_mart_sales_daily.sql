{{ config(materialized='view', tags=['semantic']) }}

/*
    Dashboard and alert-sweep feed. Ratios are computed here rather than stored
    in the mart: they are non-additive, and materializing them would invite a
    consumer to average an average.
*/

select
    *,
    round(net_revenue / nullif(orders, 0), 4) as aov,
    round(net_revenue / nullif(units_sold, 0), 4) as asp,
    round(margin_amount / nullif(net_revenue, 0), 4) as margin_rate,
    round(discount_amount / nullif(gross_revenue, 0), 4) as discount_rate
from {{ ref('mart_sales_daily') }}
