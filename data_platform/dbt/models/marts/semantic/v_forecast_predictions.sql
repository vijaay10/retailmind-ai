{{ config(materialized='view', tags=['semantic', 'forecast']) }}

/*
    Semantic entry point for every forecast, at every grain.

    fct_forecast carries only the revenue series, because that is its grain.
    Demand sits at SKU × store, inventory at position, profit at network — all
    of which this view exposes without pretending they share a fact table.
*/

select
    p.target,
    p.series_key,
    p.model_name,
    p.model_class,
    p.origin_date,
    p.business_date,
    p.horizon,
    p.yhat,
    p.yhat_lower,
    p.yhat_upper,

    -- Interval width relative to the point forecast: the single most useful
    -- number for deciding how much to trust one. A forecast of 100 ± 8 and a
    -- forecast of 100 ± 90 are not the same claim.
    round((p.yhat_upper - p.yhat_lower) / nullif(abs(p.yhat), 0), 4) as relative_interval_width,

    r.wape as model_wape,
    r.mase as model_mase,
    r.interval_coverage as model_interval_coverage,
    r.promoted as challenger_accepted,
    r.promotion_reason,
    r.evaluation_points
from {{ source('ml', 'forecast_predictions') }} p
left join {{ source('ml', 'forecast_runs') }} r
    on r.run_id = p.run_id and r.target = p.target
