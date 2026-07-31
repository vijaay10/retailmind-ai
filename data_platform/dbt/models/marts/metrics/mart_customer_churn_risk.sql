{{ config(materialized='table', tags=['mart', 'metrics', 'customer']) }}

/*
    Churn risk by band and segment (Analytics method M6).

    Aggregated deliberately. Individual churn scores exist in dim_customer for
    joins, but every *reported* risk figure is a population — the product
    analyses cohorts, not people, and the privacy floor below makes that
    structural rather than a policy nobody enforces.

    `value_at_risk` is the number that earns a meeting: not how many customers
    might lapse, but how much revenue walks out with them. Sorting retention
    effort by headcount rather than value is how teams spend a quarter saving
    customers worth less than the campaign.
*/

select
    churn_risk_band,
    rfm_segment,
    lifecycle_stage,

    count(*) as customers,
    round(sum(lifetime_value), 2) as value_at_risk,
    round(avg(lifetime_value), 2) as avg_lifetime_value,
    round(avg(recency_days), 1) as avg_recency_days,
    round(avg(avg_days_between_orders), 1) as avg_cadence_days,
    round(avg(cycles_since_last_order), 2) as avg_cycles_overdue,
    count(*) filter (where is_vip) as vip_customers,

    -- VIPs drifting into risk are the highest-priority retention target:
    -- expensive to replace, and still reachable.
    round(sum(lifetime_value) filter (where is_vip), 2) as vip_value_at_risk,

    (count(*) >= 20) as meets_privacy_floor
from {{ ref('dim_customer') }}
where customer_key <> -1
group by 1, 2, 3
