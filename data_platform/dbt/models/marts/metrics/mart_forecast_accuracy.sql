{{ config(materialized='table', tags=['mart', 'metrics']) }}

/*
    Forecast accuracy scoreboard (Analytics M7, PRD G4).

    The platform grades its own forecasts in public. That is the design's
    trust mechanism: a planner who can see how wrong the model has been is a
    planner who knows how much to trust the next number.

    WAPE — total absolute error over total actual — rather than MAPE, because
    MAPE lets a single quiet day with a small denominator dominate the
    headline. Coverage is reported alongside: an interval that claims 95% and
    delivers 60% is miscalibrated, and hiding that would make the bands
    decorative.
*/

select
    model_name,
    model_class,
    count(*) as forecast_days,
    min(business_date) as first_forecast_date,
    max(business_date) as last_forecast_date,

    -- WAPE: weighted by volume, the honest headline.
    round(sum(absolute_error) / nullif(sum(abs(actual_revenue)), 0), 4) as wape,

    -- MAPE alongside it, for readers who expect the familiar number.
    round(avg(absolute_percentage_error), 4) as mape,

    -- Bias: is the model systematically high or low? A model with good WAPE
    -- and strong bias is a model that is wrong in a predictable direction,
    -- which matters enormously for inventory decisions.
    round(
        sum(yhat_revenue - actual_revenue) / nullif(sum(abs(actual_revenue)), 0), 4
    ) as bias,

    round(
        count(*) filter (where within_interval)::double / nullif(count(*), 0), 4
    ) as interval_coverage,

    round(avg(absolute_error), 2) as mean_absolute_error
from {{ ref('fct_forecast') }}
group by 1, 2
