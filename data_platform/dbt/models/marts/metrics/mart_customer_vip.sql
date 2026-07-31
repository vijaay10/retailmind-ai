{{ config(materialized='table', tags=['mart', 'metrics', 'customer']) }}

/*
    VIP cohort profile (Analytics §2).

    Aggregated by segment and risk band, never listed individually. What a
    merchant needs from "VIP" is not a name list — it is the shape of the
    group: how much of the business they represent, how they behave
    differently, and how many are drifting away.

    The concentration figure here is the one that changes decisions: when a
    tenth of customers carry half the revenue, retention stops being a
    marketing line item.
*/

with vips as (

    select * from {{ ref('dim_customer') }}
    where customer_key <> -1 and is_vip

),

population as (

    select
        count(*) as total_customers,
        sum(lifetime_value) as total_value
    from {{ ref('dim_customer') }}
    where customer_key <> -1

)

select
    v.rfm_segment,
    v.churn_risk_band,

    count(*) as vip_customers,
    round(sum(v.lifetime_value), 2) as vip_value,
    round(avg(v.lifetime_value), 2) as avg_lifetime_value,
    round(avg(v.order_count), 2) as avg_orders,
    round(avg(v.avg_order_value), 2) as avg_order_value,
    round(avg(v.distinct_skus), 1) as avg_distinct_skus,
    round(avg(v.predicted_clv_12m), 2) as avg_predicted_clv_12m,

    -- Share of the whole customer base's value held by this VIP slice.
    round(sum(v.lifetime_value) / nullif(max(p.total_value), 0), 4) as share_of_total_value,
    round(count(*)::double / nullif(max(p.total_customers), 0), 4) as share_of_customers,

    (count(*) >= 20) as meets_privacy_floor
from vips v
cross join population p
group by 1, 2
