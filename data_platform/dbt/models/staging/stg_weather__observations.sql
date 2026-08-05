{{ config(materialized='view', tags=['staging', 'weather']) }}

/*
    Silver: conform daily regional weather.

    The derivations here turn measurements into comparisons, which is the only
    form weather is usable in. "12mm of rain" means nothing on its own — it is
    a downpour in the Southwest and a normal Tuesday in the Southeast — so
    every signal below is expressed against that region's own distribution.

    Region is left in the source's upper-case form. Reconstructing the store
    dimension's spelling with string surgery would work for single-word names
    and quietly fail the first time a region is called "South West" — so the
    join downstream normalises both sides instead, and the dimension keeps
    ownership of how a region is spelled. A case mismatch here is the kind of
    defect that surfaces as "weather has no effect on sales".
*/

with observations as (

    select
        region,
        observation_date as business_date,
        temp_c_mean,
        precipitation_mm,
        wind_kph_max,
        severe_flag
    from {{ source('raw', 'weather__observations') }}

),

regional_norms as (

    -- Each region's own baseline. A single estate-wide norm would flag every
    -- Southeast day as wet and never flag a Southwest drought.
    select
        region,
        avg(temp_c_mean) as norm_temp_c,
        avg(precipitation_mm) as norm_precipitation_mm,
        coalesce(stddev_samp(precipitation_mm), 0) as precipitation_stddev,
        coalesce(stddev_samp(temp_c_mean), 0) as temp_stddev
    from observations
    group by 1

)

select
    o.region as region_key,
    o.business_date,
    o.temp_c_mean,
    o.precipitation_mm,
    o.wind_kph_max,
    o.severe_flag,

    (o.severe_flag <> 'none') as is_severe,

    round(o.temp_c_mean - n.norm_temp_c, 2) as temp_vs_norm_c,
    round(o.precipitation_mm - n.norm_precipitation_mm, 2) as precipitation_vs_norm_mm,

    -- Standardised against the region's own spread, so a Northeast storm and
    -- a Southwest one are comparable numbers rather than comparable words.
    round(
        (o.precipitation_mm - n.norm_precipitation_mm) / nullif(n.precipitation_stddev, 0), 3
    ) as precipitation_z,
    round(
        (o.temp_c_mean - n.norm_temp_c) / nullif(n.temp_stddev, 0), 3
    ) as temp_z,

    current_timestamp as _loaded_at
from observations o
join regional_norms n using (region)
