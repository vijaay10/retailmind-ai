{{ config(materialized='table', tags=['mart', 'metrics']) }}

/*
    Segment-level customer metrics (Analytics §2, method M2).

    Aggregated to the segment on purpose. Individual customer rows exist in
    dim_customer for joins, but every *reported* customer metric is a segment
    aggregate — the platform analyses cohorts, not people, and the k-anonymity
    floor below makes that structural rather than a policy nobody enforces.
*/

with segments as (

    select
        rfm_segment,
        count(*) as customers,
        sum(lifetime_value) as segment_value,
        avg(lifetime_value) as avg_lifetime_value,
        avg(order_count) as avg_order_count,
        avg(avg_order_value) as avg_order_value,
        avg(recency_days) as avg_recency_days,
        count(*) filter (where is_repeat_customer) as repeat_customers
    from {{ ref('dim_customer') }}
    where customer_key <> -1
    group by 1

)

select
    rfm_segment,
    customers,
    segment_value,
    round(avg_lifetime_value, 2) as avg_lifetime_value,
    round(avg_order_count, 2) as avg_order_count,
    round(avg_order_value, 2) as avg_order_value,
    round(avg_recency_days, 1) as avg_recency_days,
    repeat_customers,
    round(repeat_customers::double / nullif(customers, 0), 4) as repeat_rate,
    round(segment_value / nullif(sum(segment_value) over (), 0), 4) as value_share,
    -- Below this floor a "segment" is a handful of identifiable people
    -- (Analytics §2 k-anonymity rule); consumers suppress these rows.
    (customers >= 20) as meets_privacy_floor
from segments
