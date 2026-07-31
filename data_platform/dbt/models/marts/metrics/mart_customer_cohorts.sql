{{ config(materialized='table', tags=['mart', 'metrics', 'customer']) }}

/*
    Retention cohorts (Analytics §2, method M2/M14).

    One row per acquisition cohort × weeks-since-acquisition. This is the
    triangle every retention chart draws, and the only honest way to compare
    customer quality across time: comparing raw repeat rates between a cohort
    acquired last month and one acquired last year measures how long each has
    had to come back, not how good either is.

    **Weekly cohorts, not monthly.** Monthly buckets need a year of history to
    say anything; weekly ones start being useful in a quarter. The grain is a
    property of the data available, and this warehouse currently holds weeks.

    Cohorts are truncated at the observation edge: a cohort acquired two weeks
    ago has no week-8 retention *yet*, and emitting a zero there would draw a
    cliff that does not exist. Absence is the honest value.
*/

with first_purchase as (

    select
        customer_id,
        min(business_date) as acquisition_date
    from {{ ref('stg_pos__sales') }}
    where customer_id is not null and customer_id <> 'UNKNOWN'
    group by 1

),

cohorts as (

    select
        customer_id,
        acquisition_date,
        -- Week starting Monday; date_trunc gives a stable cohort label.
        date_trunc('week', acquisition_date)::date as cohort_week
    from first_purchase

),

activity as (

    select
        c.cohort_week,
        c.customer_id,
        date_diff(
            'week', c.cohort_week, date_trunc('week', s.business_date)::date
        ) as weeks_since_acquisition,
        s.net_amount,
        s.order_id
    from {{ ref('stg_pos__sales') }} s
    join cohorts c on s.customer_id = c.customer_id
    where s.customer_id is not null and s.customer_id <> 'UNKNOWN'

),

cohort_size as (

    select cohort_week, count(*) as cohort_customers
    from cohorts
    group by 1

),

observation_edge as (

    -- How many weeks each cohort has actually had the chance to be observed.
    -- Beyond this, silence is not churn — it is the future.
    select
        c.cohort_week,
        date_diff(
            'week',
            c.cohort_week,
            (select date_trunc('week', max(business_date))::date
             from {{ ref('stg_pos__sales') }})
        ) as observable_weeks
    from cohort_size c

),

retained as (

    select
        a.cohort_week,
        a.weeks_since_acquisition,
        count(distinct a.customer_id) as active_customers,
        count(distinct a.order_id) as orders,
        sum(a.net_amount) as revenue
    from activity a
    group by 1, 2

)

select
    r.cohort_week,
    r.weeks_since_acquisition,
    s.cohort_customers,
    r.active_customers,
    r.orders,
    round(r.revenue, 2) as revenue,

    round(r.active_customers::double / nullif(s.cohort_customers, 0), 4) as retention_rate,
    round(r.revenue / nullif(s.cohort_customers, 0), 2) as revenue_per_cohort_customer,

    -- Cumulative value per acquired customer: the curve that answers "what is
    -- a customer from this cohort worth so far".
    round(
        sum(r.revenue) over (
            partition by r.cohort_week order by r.weeks_since_acquisition
        ) / nullif(s.cohort_customers, 0),
        2
    ) as cumulative_value_per_customer,

    -- Below the privacy floor a "cohort" is a handful of identifiable people
    -- (Analytics §2); consumers suppress these rows.
    (s.cohort_customers >= 20) as meets_privacy_floor

from retained r
join cohort_size s on r.cohort_week = s.cohort_week
join observation_edge e on r.cohort_week = e.cohort_week
where r.weeks_since_acquisition <= e.observable_weeks
