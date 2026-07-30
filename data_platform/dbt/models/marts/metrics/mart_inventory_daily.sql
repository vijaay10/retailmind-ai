{{ config(materialized='table', tags=['mart', 'metrics'],
          post_hook="{{ create_index(['business_date']) }}") }}

/*
    Inventory position by category × region × day (Analytics §4).

    Every ratio here is recomputed from the summed components rather than
    averaged from the detail. Averaging a stockout rate across stores weights
    a two-SKU store the same as a two-thousand-SKU store, which is how
    availability metrics end up quietly wrong.
*/

select
    i.date_key,
    i.business_date,
    p.category,
    p.department,
    st.region,
    st.store_cluster,

    -- Semi-additive: valid to sum across SKUs and stores within a single day,
    -- never across days.
    sum(i.on_hand_qty) as on_hand_units,
    sum(i.on_order_qty) as on_order_units,
    sum(i.inventory_value_cost) as inventory_value_cost,

    count(*) as sku_store_positions,
    count(*) filter (where i.is_stockout) as stockout_positions,
    round(count(*) filter (where i.is_stockout)::double / nullif(count(*), 0), 4)
        as stockout_rate,

    -- Cover recomputed at this grain from summed stock and summed demand.
    round(
        sum(i.on_hand_qty) / nullif(sum(i.avg_daily_units), 0), 2
    ) as cover_days,
    count(*) filter (where i.cover_days > 84) as overstocked_positions

from {{ ref('fct_inventory_daily') }} i
join {{ ref('dim_product') }} p on i.product_key = p.product_key
join {{ ref('dim_store') }} st on i.store_key = st.store_key
group by 1, 2, 3, 4, 5, 6
