{{ config(materialized='table', tags=['mart', 'metrics', 'inventory']) }}

/*
    Reorder suggestions — order-up-to quantities (Analytics §4, ARCH §26).

    The classic newsvendor form: order enough to cover demand over the lead
    time, plus safety stock sized to the *variability* of that demand and the
    service level the item's ABC class earns.

        reorder point  = demand during lead time + safety stock
        safety stock   = z × σ_demand × √lead_time
        suggested qty  = order-up-to level − (on hand + on order)

    On-order is reconciled to a single source before it is subtracted —
    see the note at ``on_order_total``. Counting the same in-transit units
    twice is the quietest way to starve a store.

    Two things this deliberately does **not** do.

    It does not suggest anything for items with no observed demand. A SKU
    nobody buys does not need replenishing, and a queue full of dead stock is
    how planners learn to ignore the queue.

    It does not treat lead time as a constant. Variability is what safety stock
    exists to absorb — a supplier averaging 20 days with a ±10 day spread needs
    materially more cover than one reliably taking 25.
*/

with health as (

    select * from {{ ref('mart_inventory_health') }}
    where avg_daily_demand > 0

),

supplier_reliability as (

    -- Observed lead time, not the contracted one: what a supplier promises and
    -- what they deliver are different numbers, and safety stock must be sized
    -- against the second.
    select
        supplier_id,
        avg(actual_lead_time_days) as observed_lead_time_days,
        coalesce(stddev_samp(actual_lead_time_days), 0) as lead_time_stddev,
        count(*) as observed_receipts
    from {{ ref('fct_purchase_orders') }}
    where actual_lead_time_days is not null
    group by 1

),

sized as (

    select
        h.*,
        r.observed_lead_time_days,
        r.lead_time_stddev,
        r.observed_receipts,

        -- Fall back to the contract only when a supplier has no receipt
        -- history yet; a promise is better than nothing but worse than fact.
        coalesce(r.observed_lead_time_days, h.contract_lead_time_days, 14)
            as effective_lead_time_days,

        -- z for the class's service level. Kept as an explicit map rather than
        -- an inverse-normal call so the number in the response can be traced
        -- to a decision somebody made.
        case h.abc_class
            when 'A' then 2.05   -- 98%
            when 'B' then 1.65   -- 95%
            else 1.28            -- 90%
        end as service_z
    from health h
    left join supplier_reliability r on h.supplier_id = r.supplier_id

),

calculated as (

    select
        *,
        avg_daily_demand * effective_lead_time_days as lead_time_demand,

        /*
            Safety stock absorbs two independent uncertainties: demand varying
            over the lead time, and the lead time itself varying. Combining
            them in quadrature is standard practice and matters here — ignoring
            lead-time variance is what leaves a planner short exactly when a
            slow supplier is also late.
        */
        service_z * sqrt(
            (effective_lead_time_days * pow(coalesce(daily_demand_stddev, 0), 2))
            + (pow(avg_daily_demand, 2) * pow(coalesce(lead_time_stddev, 0), 2))
        ) as safety_stock
    from sized

)

select
    sku,
    product_name,
    category,
    store_id,
    store_name,
    region,
    supplier_id,
    supplier_name,
    abc_class,
    target_service_level,

    on_hand_qty,
    on_order_qty,
    on_order_from_po,

    -- Reconciled upstream in mart_inventory_health: the position feed and the
    -- purchasing feed are two views of the same in-transit stock, and only one
    -- of them may be subtracted.
    on_order_total,
    on_order_source,

    round(avg_daily_demand, 3) as avg_daily_demand,
    round(daily_demand_stddev, 3) as daily_demand_stddev,

    round(effective_lead_time_days, 1) as effective_lead_time_days,
    round(lead_time_stddev, 2) as lead_time_stddev,
    observed_receipts,

    round(lead_time_demand, 1) as lead_time_demand,
    round(safety_stock, 1) as safety_stock,
    round(lead_time_demand + safety_stock, 1) as reorder_point,

    -- Order-up-to covers the lead time plus a review cycle, so the next order
    -- is not needed tomorrow.
    round(avg_daily_demand * (effective_lead_time_days + 14) + safety_stock, 1)
        as order_up_to_level,

    greatest(
        0,
        round(
            avg_daily_demand * (effective_lead_time_days + 14) + safety_stock
            - on_hand_qty - on_order_total,
            0
        )
    ) as suggested_order_qty,

    -- Inventory *position*, not on-hand: stock already inbound counts against
    -- the reorder point, or every order placed triggers another one.
    (on_hand_qty + on_order_total)
        < (lead_time_demand + safety_stock) as below_reorder_point,

    days_until_stockout,
    projected_stockout_date,

    -- Revenue exposed if the shelf empties before replenishment lands. This
    -- is what ranks the queue: a planner works down expected loss, not
    -- alphabetically.
    round(
        greatest(0, effective_lead_time_days - coalesce(days_until_stockout, 999))
        * avg_daily_demand
        * (inventory_value_cost / nullif(on_hand_qty, 0)),
        2
    ) as revenue_at_risk,

    position_date
from calculated
