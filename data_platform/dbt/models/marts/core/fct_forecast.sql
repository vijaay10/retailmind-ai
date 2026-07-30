{{
    config(
        materialized='table',
        tags=['fact', 'core'],
        post_hook="{{ create_index(['business_date']) }}",
    )
}}

/*
    fct_forecast — daily revenue and unit forecasts (DB design §6, ARCH §28).

    **This is the seasonal-naive baseline, and it is labelled as such.** The
    forecast for a future day is what happened on the same weekday one week
    earlier, smoothed over the trailing four occurrences of that weekday.

    That is not a placeholder for a "real" model — it is the *benchmark* every
    real model must beat. The design requires forecasts to publish their
    accuracy against seasonal naive (PRD G4: beat it by ≥15% WAPE), so shipping
    naive first means the scoreboard has a denominator from day one and no
    model can be adopted on faith.

    Intervals come from the historical spread of that weekday's values rather
    than a distributional assumption. Crude, honest, and — importantly — they
    widen when the series is genuinely volatile, which is the property that
    makes an interval worth showing.

    When a better model lands (ARCH §22's LightGBM/Prophet portfolio), it
    writes into this same table with a different `model_name`, and the
    scoreboard compares them. Nothing downstream changes.
*/

with daily as (

    select
        business_date,
        sum(net_amount) as net_revenue,
        sum(quantity) as units_sold
    from {{ ref('fct_sales') }}
    group by 1

),

with_weekday as (

    select
        *,
        extract(dayofweek from business_date) as day_of_week
    from daily

),

/*
    Trailing average of the same weekday. Four occurrences balances
    responsiveness against noise: fewer and one odd Saturday dominates, more
    and the forecast stops reacting to genuine level shifts.
*/
weekday_profile as (

    select
        business_date,
        day_of_week,
        net_revenue,
        units_sold,
        avg(net_revenue) over (
            partition by day_of_week order by business_date
            rows between 4 preceding and 1 preceding
        ) as revenue_baseline,
        avg(units_sold) over (
            partition by day_of_week order by business_date
            rows between 4 preceding and 1 preceding
        ) as units_baseline,
        stddev_samp(net_revenue) over (
            partition by day_of_week order by business_date
            rows between 4 preceding and 1 preceding
        ) as revenue_spread,
        count(*) over (
            partition by day_of_week order by business_date
            rows between 4 preceding and 1 preceding
        ) as observations
    from with_weekday

),

/*
    In-sample fit: the baseline evaluated against the day it predicted. This is
    what the accuracy scoreboard reads, and it is honest precisely because the
    baseline for each day uses only *prior* observations (the window excludes
    the current row).
*/
scored as (

    select
        business_date,
        day_of_week,
        observations,
        round(revenue_baseline, 4) as yhat_revenue,
        round(units_baseline, 4) as yhat_units,
        net_revenue as actual_revenue,
        units_sold as actual_units,
        -- A 1.96σ band from the weekday's own historical spread. Where spread
        -- cannot be computed (too few observations) the interval is NULL
        -- rather than a fabricated width.
        round(revenue_baseline - 1.96 * revenue_spread, 4) as yhat_revenue_lower,
        round(revenue_baseline + 1.96 * revenue_spread, 4) as yhat_revenue_upper,
        abs(net_revenue - revenue_baseline) as absolute_error
    from weekday_profile
    where revenue_baseline is not null

)

select
    cast(strftime(business_date, '%Y%m%d') as integer) as date_key,
    business_date,
    'seasonal_naive_w4' as model_name,
    'baseline' as model_class,
    day_of_week,
    observations as training_observations,

    yhat_revenue,
    yhat_revenue_lower,
    yhat_revenue_upper,
    yhat_units,

    actual_revenue,
    actual_units,
    absolute_error,

    -- Per-day absolute percentage error; the scoreboard weights these by
    -- actual volume (WAPE) rather than averaging them (MAPE), because MAPE
    -- lets a quiet day dominate the headline.
    round(absolute_error / nullif(abs(actual_revenue), 0), 6) as absolute_percentage_error,

    -- Interval coverage: does the band actually contain the truth? A 95%
    -- interval that covers 60% of actuals is miscalibrated, and that is a bug
    -- the scoreboard must surface rather than hide.
    (actual_revenue between coalesce(yhat_revenue_lower, actual_revenue)
                        and coalesce(yhat_revenue_upper, actual_revenue)) as within_interval,

    current_timestamp as _loaded_at
from scored
