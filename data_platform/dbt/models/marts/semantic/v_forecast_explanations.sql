{{ config(materialized='view', tags=['semantic', 'forecast']) }}

-- Why a forecast came out where it did. `baseline + sum(effect)` reconstructs
-- the point forecast exactly, because these are the model's own arithmetic
-- rather than a post-hoc attribution of it.

select
    target,
    series_key,
    business_date,
    horizon,
    feature,
    feature_value,
    effect,
    baseline,
    abs(effect) as effect_magnitude,
    case when effect >= 0 then 'increases' else 'decreases' end as direction
from {{ source('ml', 'forecast_explanations') }}
