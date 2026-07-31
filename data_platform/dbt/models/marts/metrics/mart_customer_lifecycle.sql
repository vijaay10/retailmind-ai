{{ config(materialized='table', tags=['mart', 'metrics', 'customer']) }}

/*
    Lifecycle funnel: the New → Repeat → Established → Loyal progression
    (Analytics method M14).

    The conversion rates between stages are what marketing plans against —
    they show where the relationship breaks down. The single most-watched
    number in retail is the New → Repeat step, because a customer who never
    makes a second purchase never earns back their acquisition cost.

    Churn risk is reported *within* each stage rather than as a stage of its
    own. A lapsing Loyal customer is still Loyal — losing them is a different
    (and more expensive) problem than never converting a New one, and merging
    the two hides that.
*/

with stages as (

    select
        lifecycle_stage,
        count(*) as customers,
        round(sum(lifetime_value), 2) as stage_value,
        round(avg(lifetime_value), 2) as avg_lifetime_value,
        round(avg(order_count), 2) as avg_orders,
        round(avg(avg_order_value), 2) as avg_order_value,
        round(avg(tenure_days), 1) as avg_tenure_days,
        round(avg(avg_days_between_orders), 1) as avg_days_between_orders,
        count(*) filter (where is_at_risk) as at_risk_customers,
        count(*) filter (where is_vip) as vip_customers
    from {{ ref('dim_customer') }}
    where customer_key <> -1
    group by 1

),

ordered as (

    select
        *,
        case lifecycle_stage
            when 'New' then 1
            when 'Repeat' then 2
            when 'Established' then 3
            when 'Loyal' then 4
        end as stage_order
    from stages

),

with_funnel as (

    select
        *,
        -- Everyone at this stage or beyond: a Loyal customer passed through
        -- New, so funnel width must be cumulative from the far end.
        sum(customers) over (order by stage_order desc) as reached_stage
    from ordered

)

select
    lifecycle_stage,
    stage_order,
    customers,
    reached_stage,
    stage_value,
    avg_lifetime_value,
    avg_orders,
    avg_order_value,
    avg_tenure_days,
    avg_days_between_orders,
    at_risk_customers,
    round(at_risk_customers::double / nullif(customers, 0), 4) as at_risk_rate,
    vip_customers,

    -- Conversion into this stage from the previous one.
    round(
        reached_stage::double
        / nullif(lag(reached_stage) over (order by stage_order), 0),
        4
    ) as conversion_from_previous,

    round(
        reached_stage::double
        / nullif(max(reached_stage) over (), 0),
        4
    ) as share_of_all_customers,

    (customers >= 20) as meets_privacy_floor
from with_funnel
order by stage_order
