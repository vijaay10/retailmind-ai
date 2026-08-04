{{ config(materialized='table', tags=['mart', 'metrics', 'inventory']) }}

/*
    Warehouse health by region — the operational summary (Analytics §4).

    One row per region with the five signals that describe an inventory
    position: availability, excess, dead stock, aging, and replenishment
    pressure.

    The composite score is deliberately **not** a black box. It is a weighted
    average of five legible components, each reported alongside it, so a
    regional manager can see not just that health is 72 but which of the five
    dragged it there. A score nobody can decompose is a score nobody trusts,
    and one that moves without explanation gets ignored within a month.
*/

with positions as (

    select * from {{ ref('mart_inventory_health') }}

),

by_region as (

    select
        region,
        position_date,

        count(*) as sku_store_positions,
        count(distinct sku) as distinct_skus,
        count(distinct store_id) as stores,

        -- Availability
        count(*) filter (where is_stockout) as stockout_positions,
        count(*) filter (where stockout_before_lead_time) as at_risk_positions,

        -- Capital efficiency
        count(*) filter (where is_overstocked) as overstocked_positions,
        count(*) filter (where is_dead_stock) as dead_stock_positions,
        round(sum(inventory_value_cost), 2) as inventory_value,
        round(sum(coalesce(excess_value, 0)), 2) as excess_value,

        -- Aging
        round(avg(days_since_receipt), 1) as avg_days_since_receipt,
        count(*) filter (where days_since_receipt > 90) as aged_positions,

        -- Replenishment pressure
        sum(open_po_lines) as open_po_lines,
        round(avg(cover_days), 1) as avg_cover_days
    from positions
    group by 1, 2

),

scored as (

    select
        *,
        /*
            Five components, each a 0–100 sub-score where higher is healthier.
            Kept as columns rather than folded away, because the composite is
            only useful if the reader can see what moved it.
        */
        round(100 * (1 - stockout_positions::double / nullif(sku_store_positions, 0)), 1)
            as availability_score,
        round(100 * (1 - at_risk_positions::double / nullif(sku_store_positions, 0)), 1)
            as replenishment_score,
        round(100 * (1 - overstocked_positions::double / nullif(sku_store_positions, 0)), 1)
            as capital_efficiency_score,
        round(100 * (1 - dead_stock_positions::double / nullif(sku_store_positions, 0)), 1)
            as assortment_score,
        round(100 * (1 - aged_positions::double / nullif(sku_store_positions, 0)), 1)
            as freshness_score
    from by_region

)

select
    *,

    /*
        Weights encode what actually costs the business. Availability carries
        the most because a stockout is lost revenue *and* a lost customer;
        aging carries the least because old stock is a slow problem, not an
        acute one. These are business judgements, written down here so they can
        be argued with rather than buried in a spreadsheet.
    */
    round(
        0.35 * availability_score
        + 0.25 * replenishment_score
        + 0.20 * capital_efficiency_score
        + 0.10 * assortment_score
        + 0.10 * freshness_score,
        1
    ) as health_score,

    case
        when 0.35 * availability_score + 0.25 * replenishment_score
             + 0.20 * capital_efficiency_score + 0.10 * assortment_score
             + 0.10 * freshness_score >= 90 then 'healthy'
        when 0.35 * availability_score + 0.25 * replenishment_score
             + 0.20 * capital_efficiency_score + 0.10 * assortment_score
             + 0.10 * freshness_score >= 75 then 'watch'
        else 'action_required'
    end as health_band,

    round(stockout_positions::double / nullif(sku_store_positions, 0), 4) as stockout_rate,
    round(excess_value / nullif(inventory_value, 0), 4) as excess_value_share
from scored
