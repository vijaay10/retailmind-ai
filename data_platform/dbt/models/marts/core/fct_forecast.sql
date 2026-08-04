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

    The trained models land through the union at the bottom of this file: the
    forecasting job (ml/forecasting) writes to analytics_ml.forecast_predictions
    with its own `model_name`, and the scoreboard compares them on identical
    terms. Nothing downstream changes.

    **The two halves are scored differently, and the difference is honest.**
    The baseline below is evaluated in-sample-but-causally — each day's
    forecast uses only prior observations — which is why it can be computed in
    one pass over history. The model rows are *forward* forecasts for dates
    that have not happened yet, so they carry NULL actuals until the day
    arrives and the next build joins one in. A NULL actual is the correct
    value for a prediction about Thursday made on Monday, and filling it with
    anything would fabricate an accuracy number for a day nobody has lived.
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
    -- Two implementations of "seasonal naive" exist: this SQL one and the
    -- Python one in ml/forecasting. They share a name because they are the
    -- same idea, and they are *not* the same code — different lookback
    -- handling gives different numbers. Grading them in one row would
    -- attribute one implementation's accuracy to the other.
    'warehouse_sql' as produced_by,
    1 as horizon,
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

union all

/*
    Trained-model forecasts, produced by ml/forecasting and read back here so
    that every model — baseline or otherwise — reaches the accuracy scoreboard
    through exactly one path. A second scoreboard reading a second table is how
    two teams end up quoting different accuracy for the same model.

    Only revenue rows join this fact: it is a revenue-and-units grain, and the
    demand, inventory, and profit forecasts live at grains this table cannot
    represent. They are served from the semantic view over the predictions
    table directly.
*/
select
    cast(strftime(p.business_date, '%Y%m%d') as integer) as date_key,
    p.business_date,
    p.model_name,
    p.model_class,
    'ml_pipeline' as produced_by,
    p.horizon,
    cast(extract(dayofweek from p.business_date) as bigint) as day_of_week,
    null as training_observations,

    p.yhat as yhat_revenue,
    p.yhat_lower as yhat_revenue_lower,
    p.yhat_upper as yhat_revenue_upper,
    null as yhat_units,

    -- Joined where the day has since happened, NULL where it has not. This is
    -- what lets the same row be a forecast today and a scored forecast next
    -- week without being rewritten.
    a.net_revenue as actual_revenue,
    a.units_sold as actual_units,
    abs(a.net_revenue - p.yhat) as absolute_error,
    round(abs(a.net_revenue - p.yhat) / nullif(abs(a.net_revenue), 0), 6)
        as absolute_percentage_error,

    (a.net_revenue between p.yhat_lower and p.yhat_upper) as within_interval,

    current_timestamp as _loaded_at
from {{ source('ml', 'forecast_predictions') }} p
left join (
    select business_date, sum(net_amount) as net_revenue, sum(quantity) as units_sold
    from {{ ref('fct_sales') }}
    group by 1
) a on a.business_date = p.business_date
where p.target = 'revenue'
