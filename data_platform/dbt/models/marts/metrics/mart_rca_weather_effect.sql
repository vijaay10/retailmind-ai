{{ config(materialized='table', tags=['mart', 'metrics', 'rca']) }}

/*
    Observed revenue gap on severe-weather days, per region.

    Computed in the warehouse rather than in the API because it is a
    *definition*, not a request-time calculation: "what a severe day costs
    this region" should be one number that every caller receives, not
    something each surface derives its own way from two tables.

    The comparison is deliberately within-region. Comparing a Northeast storm
    day against the estate average would measure the difference between the
    Northeast and the estate, which is mostly the difference between six
    stores and forty.

    **This is an association and the model says so.** Severe days are not
    randomly assigned: they cluster in winter, near holidays, and alongside
    whatever else was happening that week. The gap below is what was observed,
    not what the weather caused, and the engine consuming it caps any finding
    built on it accordingly.
*/

with daily as (

    select
        s.slice_value as region,
        s.business_date,
        s.net_revenue,
        coalesce(f.is_severe, false) as is_severe
    from {{ ref('mart_rca_slice_daily') }} s
    left join {{ ref('mart_rca_factor_daily') }} f
        on f.region = s.slice_value and f.business_date = s.business_date
    where s.slice_type = 'region'

)

select
    region,
    count(*) filter (where is_severe) as severe_days,
    count(*) filter (where not is_severe) as ordinary_days,

    round(avg(net_revenue) filter (where is_severe), 2) as severe_day_revenue,
    round(avg(net_revenue) filter (where not is_severe), 2) as ordinary_day_revenue,

    -- Negative means severe days trade below ordinary ones. NULL when the
    -- region has never had a severe day, which is the honest value: no
    -- observation, rather than no effect.
    round(
        avg(net_revenue) filter (where is_severe)
        - avg(net_revenue) filter (where not is_severe), 2
    ) as severe_day_gap,

    round(
        (avg(net_revenue) filter (where is_severe)
         - avg(net_revenue) filter (where not is_severe))
        / nullif(avg(net_revenue) filter (where not is_severe), 0), 4
    ) as severe_day_gap_pct,

    -- Below a handful of severe days the gap is one storm's worth of noise
    -- wearing an average's clothes.
    (count(*) filter (where is_severe) >= 3) as meets_evidence_floor,

    current_timestamp as _loaded_at
from daily
group by 1
