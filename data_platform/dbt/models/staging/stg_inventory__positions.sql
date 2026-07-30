{{ config(materialized='view', tags=['staging', 'inventory']) }}

/*
    Silver: conform daily inventory positions.

    The single most important thing this model does is *not* compute cover
    days. Cover requires demand, demand requires sales, and joining the two
    belongs in the fact — doing it here would bury a cross-source dependency
    inside a staging view where nobody looks for it.
*/

select
    sku,
    store_id,
    snapshot_date,
    snapshot_ts,
    business_date,

    on_hand_qty,
    coalesce(on_order_qty, 0) as on_order_qty,
    coalesce(in_transit_qty, 0) as in_transit_qty,
    unit_cost,

    -- Value at cost: the working-capital view a finance user asks for.
    round(on_hand_qty * unit_cost, 4) as inventory_value_cost,

    -- A zero position is the atom of every availability metric.
    (on_hand_qty = 0) as is_stockout,

    source_currency

from {{ source('raw', 'inventory__positions') }}
