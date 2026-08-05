{{ config(materialized='table', tags=['mart', 'metrics', 'rca']) }}

/*
    Daily operational factors by region — the "why", to the slice mart's "where".

    A decomposition over sales can only ever say *which* slices moved. It can
    never say why, because the answer is not in the sales data: a region that
    lost 30% of its revenue looks identical whether the stores were empty, the
    shelves were, or the deliveries never arrived. This table carries the
    candidate explanations, aligned to region and day so they can be lined up
    against a movement and tested for coincidence.

    **Region-day is the join grain, and it is a compromise made explicit.**
    Weather is only sold at regional resolution, so aligning anything finer
    would attribute a regional signal to individual stores and manufacture
    precision the feed does not have. Inventory and shipping *are* available
    per store; they are aggregated up so that every factor sits at the same
    grain and a comparison between them is honest.

    Nothing here is evidence of causation on its own. A factor moving with a
    KPI is a coincidence until a mechanism is stated, and the engine that
    reads this table grades mechanical factors (a stockout prevents a sale)
    differently from exogenous ones (weather correlates with footfall).
*/

with calendar as (

    select distinct business_date from {{ ref('fct_sales') }}

),

weather as (

    -- Normalised on both sides of the join: the provider upper-cases region
    -- names and the store dimension title-cases them, and matching on the raw
    -- strings would silently produce a table where weather is always NULL.
    select
        upper(region_key) as region_key,
        business_date,
        temp_c_mean,
        precipitation_mm,
        wind_kph_max,
        severe_flag,
        is_severe,
        precipitation_z,
        temp_z
    from {{ ref('stg_weather__observations') }}

),

shipping as (

    select
        st.region,
        d.business_date,
        count(*) as shipments,
        count(*) filter (where d.is_open) as shipments_in_transit,
        count(*) filter (where d.is_judgeable) as shipments_closed,
        count(*) filter (where d.is_on_time) as shipments_on_time,
        -- On-time rate over *closed* shipments only. Including in-transit
        -- ones would make the rate fall whenever volume rises, which is the
        -- opposite of what it should measure.
        round(
            count(*) filter (where d.is_on_time)::double
            / nullif(count(*) filter (where d.is_judgeable), 0), 4
        ) as on_time_rate,
        round(avg(d.days_late) filter (where d.days_late > 0), 2) as avg_days_late,
        count(distinct d.carrier) filter (where d.is_judgeable and not d.is_on_time)
            as carriers_missing_promise
    from {{ ref('stg_fulfilment__deliveries') }} d
    join {{ ref('dim_store') }} st on d.store_id = st.store_id and st.is_current
    group by 1, 2

),

availability as (

    select
        region,
        business_date,
        sum(sku_store_positions) as sku_store_positions,
        sum(stockout_positions) as stockout_positions,
        round(
            sum(stockout_positions)::double / nullif(sum(sku_store_positions), 0), 4
        ) as stockout_rate,
        round(
            sum(on_hand_units) / nullif(sum(sku_store_positions), 0), 2
        ) as units_per_position
    from {{ ref('mart_inventory_daily') }}
    group by 1, 2

),

promotions as (

    -- Promotions are not regional in this estate, so the same figures attach
    -- to every region. Stated rather than hidden: a promo ending explains a
    -- national drop and can never explain why one region fell further.
    select
        business_date,
        count(distinct promo_key) as active_promotions,
        round(sum(promo_revenue), 2) as promo_revenue,
        sum(promo_units) as promo_units,
        round(avg(effective_depth), 4) as avg_promo_depth
    from {{ ref('mart_promo_daily') }}
    group by 1

),

regions as (

    -- The unknown-member sentinel is excluded: cross-joining it against the
    -- calendar manufactures a region-day grid with no data behind it, and
    -- every factor in it reads as NULL rather than as absent.
    select distinct region
    from {{ ref('dim_store') }}
    where is_current and region <> 'UNKNOWN'

)

select
    r.region,
    c.business_date,
    cast(strftime(c.business_date, '%Y%m%d') as integer) as date_key,

    -- ── Weather (exogenous) ──
    w.temp_c_mean,
    w.precipitation_mm,
    w.wind_kph_max,
    coalesce(w.severe_flag, 'unknown') as severe_flag,
    coalesce(w.is_severe, false) as is_severe,
    w.precipitation_z,
    w.temp_z,

    -- ── Shipping (operational) ──
    s.shipments,
    s.shipments_closed,
    -- The numerator ships with the rate. Rolling several days together needs
    -- on-time shipments over closed shipments, not the mean of daily rates —
    -- a quiet Sunday would otherwise weigh the same as a peak Friday.
    s.shipments_on_time,
    s.on_time_rate,
    s.avg_days_late,
    s.carriers_missing_promise,

    -- ── Availability (mechanical: a stockout prevents a sale outright) ──
    a.sku_store_positions,
    a.stockout_positions,
    a.stockout_rate,
    a.units_per_position,

    -- ── Promotions (national) ──
    p.active_promotions,
    p.promo_revenue,
    p.promo_units,
    p.avg_promo_depth,

    current_timestamp as _loaded_at
from calendar c
cross join regions r
left join weather w on w.region_key = upper(r.region) and w.business_date = c.business_date
left join shipping s on s.region = r.region and s.business_date = c.business_date
left join availability a on a.region = r.region and a.business_date = c.business_date
left join promotions p on p.business_date = c.business_date
