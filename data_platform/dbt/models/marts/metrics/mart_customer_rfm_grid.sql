{{ config(materialized='table', tags=['mart', 'metrics', 'customer']) }}

/*
    The RFM grid: recency × frequency cells with monetary value (Analytics M2).

    This is the bubble chart merchandisers actually read — where customers sit
    on the two axes that predict behaviour, sized by what they are worth. The
    named segments in dim_customer are a *summary* of this grid; the grid is
    where a marketer sees that "Loyal" contains two populations behaving
    differently.
*/

select
    recency_score,
    frequency_score,
    count(*) as customers,
    round(sum(lifetime_value), 2) as segment_value,
    round(avg(lifetime_value), 2) as avg_lifetime_value,
    round(avg(monetary_score), 2) as avg_monetary_score,
    round(avg(recency_days), 1) as avg_recency_days,
    round(avg(order_count), 2) as avg_order_count,
    count(*) filter (where is_at_risk) as at_risk_customers,
    count(*) filter (where is_vip) as vip_customers,
    (count(*) >= 20) as meets_privacy_floor
from {{ ref('dim_customer') }}
where customer_key <> -1
group by 1, 2
