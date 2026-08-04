{{ config(materialized='view', tags=['semantic', 'forecast']) }}

-- The model scoreboard: what was trained, how it scored, and whether the
-- challenger cleared the gate. Exposed so the platform can be asked to
-- justify a forecast without anyone reading the training logs.

select
    target,
    model_name,
    model_class,
    version,
    promoted as challenger_accepted,
    promotion_reason,
    horizon,
    data_snapshot_id,
    wape,
    mase,
    bias,
    interval_coverage,
    evaluation_points,

    -- The adoption bar, restated as data rather than left in a docstring.
    (mase is not null and mase < 1.0) as beats_seasonal_naive,
    created_at
from {{ source('ml', 'forecast_runs') }}
