{{ config(materialized='table', tags=['mart', 'metrics', 'inventory'],
          post_hook="{{ create_index(['sku']) }}") }}

/*
    SKU × store inventory health — the operational grain (Analytics §4).

    One row per stocked position on the latest snapshot date, carrying
    everything a planner needs to triage: how long the stock lasts, when it
    runs out, how long it has been sitting, and whether it is worth reordering.

    **Read for a single day.** Positions are semi-additive; a multi-day window
    would sum stock that existed only once. The date is pinned to the latest
    snapshot so the answer is always about the current position rather than a
    smear across history.

    Demand comes from trailing sales *while in stock*. A SKU that sold nothing
    because there was nothing to sell has zero observed demand, and treating
    that as genuine disinterest is how a stockout becomes self-justifying.
*/

with latest_positions as (

    select *
    from {{ ref('fct_inventory_daily') }}
    where business_date = (select max(business_date) from {{ ref('fct_inventory_daily') }})

),

demand as (

    /*
        Trailing 28-day demand, measured only over days the SKU was actually
        in stock at that store. The stocked-day denominator is the whole point:
        dividing by 28 when the shelf was empty for 20 of them understates
        demand precisely for the items that need reordering most.
    */
    select
        sku,
        store_id,
        sum(units) as units_28d,
        count(*) as selling_days,
        stddev_samp(units) as daily_demand_stddev
    from (
        -- Collapse to one row per SKU × store × day *first*. Aggregating the
        -- line-grain table directly against a daily subquery needs a join on
        -- the date as well as the keys; omitting it fans every line against
        -- every day and multiplies demand by the length of the window.
        select sku, store_id, business_date, sum(quantity) as units
        from {{ ref('stg_pos__sales') }}
        where not is_return
          and business_date >= (
              select max(business_date) - interval 28 day from {{ ref('stg_pos__sales') }}
          )
        group by 1, 2, 3
    ) daily
    group by 1, 2

),

receipts as (

    -- Most recent receipt per SKU-store: the anchor for inventory aging.
    select
        sku,
        store_id,
        max(receipt_date) as last_receipt_date,
        count(*) filter (where is_open) as open_po_lines,
        sum(ordered_qty) filter (where is_open) as on_order_from_po
    from {{ ref('fct_purchase_orders') }}
    group by 1, 2

),

joined as (

    select
        pos.business_date as position_date,
        p.sku,
        p.product_name,
        p.category,
        p.department,
        st.store_id,
        st.store_name,
        st.region,
        st.store_cluster,
        sup.supplier_id,
        sup.supplier_name,
        sup.contract_lead_time_days,

        pos.on_hand_qty,
        pos.on_order_qty,
        pos.inventory_value_cost,
        pos.is_stockout,

        coalesce(d.units_28d / 28.0, 0) as avg_daily_demand,
        coalesce(d.daily_demand_stddev, 0) as daily_demand_stddev,
        d.selling_days,

        r.last_receipt_date,
        coalesce(r.open_po_lines, 0) as open_po_lines,
        coalesce(r.on_order_from_po, 0) as on_order_from_po,

        abc.abc_class,
        abc.target_service_level
    from latest_positions pos
    join {{ ref('dim_product') }} p on pos.product_key = p.product_key
    join {{ ref('dim_store') }} st on pos.store_key = st.store_key
    left join demand d on p.sku = d.sku and st.store_id = d.store_id
    left join receipts r on p.sku = r.sku and st.store_id = r.store_id
    left join {{ ref('mart_product_abc') }} abc on p.sku = abc.sku
    left join {{ ref('product_supplier') }} ps on p.sku = ps.sku
    left join {{ ref('dim_supplier') }} sup on ps.supplier_id = sup.supplier_id
    where p.product_key <> -1 and st.store_key <> -1

)

select
    *,

    -- ── Cover and run-out ──
    case
        when avg_daily_demand > 0 then round(on_hand_qty / avg_daily_demand, 1)
    end as cover_days,

    /*
        Projected run-out date. NULL when there is no observed demand — a SKU
        nobody buys does not "run out", it just sits, and inventing a date for
        it would fill the reorder queue with dead stock.
    */
    case
        when avg_daily_demand > 0
        then position_date + cast(floor(on_hand_qty / avg_daily_demand) as integer)
    end as projected_stockout_date,

    case
        when avg_daily_demand > 0
        then greatest(0, cast(floor(on_hand_qty / avg_daily_demand) as integer))
    end as days_until_stockout,

    -- Will it run out before a replenishment could physically arrive?
    case
        when avg_daily_demand > 0 and contract_lead_time_days is not null
        then (on_hand_qty / avg_daily_demand) < contract_lead_time_days
        else false
    end as stockout_before_lead_time,

    /*
        The two on-order figures are two *views of the same physical stock*,
        not two pools of it: the position feed carries the store's own record
        of what is inbound, and the purchasing feed carries the open PO lines
        that put it there. Adding them double-counts stock in transit, which
        under-orders — the same failure as double-ordering, wearing the
        opposite sign and far harder to notice, because the symptom is a
        stockout weeks later rather than a suspicious invoice today.

        The purchasing system is the record of truth wherever it has coverage;
        the position feed is the fallback for SKU-stores it does not reach.
        Both inputs stay in the model so a buyer can see them disagree, and
        the source is labelled rather than inferred.
    */
    case
        when open_po_lines > 0 then on_order_from_po
        else on_order_qty
    end as on_order_total,

    case
        when open_po_lines > 0 then 'purchase_orders'
        else 'position_feed'
    end as on_order_source,

    -- ── Aging ──
    case
        when last_receipt_date is not null
        then date_diff('day', last_receipt_date, position_date)
    end as days_since_receipt,

    /*
        Aging buckets widen as they age. The difference between 5 and 20 days
        on hand is a replenishment signal; the difference between 200 and 215
        is not, and equal-width buckets would spend most of their resolution
        on the range where nothing is decided.

        Bucketing here rather than in the API keeps one definition: a position
        cannot be "31-60 days" on a dashboard and "1-2 months" in an export.
    */
    case
        when last_receipt_date is null then 'unknown'
        when date_diff('day', last_receipt_date, position_date) <= 30 then '0-30'
        when date_diff('day', last_receipt_date, position_date) <= 60 then '31-60'
        when date_diff('day', last_receipt_date, position_date) <= 90 then '61-90'
        when date_diff('day', last_receipt_date, position_date) <= 180 then '91-180'
        else '180+'
    end as aging_bucket,

    -- ── Overstock ──
    /*
        Excess is measured against a *policy* horizon, not a fixed unit count:
        eighty-four days of cover is twelve weeks of capital tied up regardless
        of whether that is 5 units or 500.
    */
    case
        when avg_daily_demand > 0 and (on_hand_qty / avg_daily_demand) > 84
        then round(on_hand_qty - (avg_daily_demand * 84), 1)
    end as excess_units,

    case
        when avg_daily_demand > 0 and (on_hand_qty / avg_daily_demand) > 84
        then round((on_hand_qty - (avg_daily_demand * 84))
                   * (inventory_value_cost / nullif(on_hand_qty, 0)), 2)
    end as excess_value,

    (avg_daily_demand > 0 and (on_hand_qty / avg_daily_demand) > 84) as is_overstocked,

    -- Zero demand and stock on hand is the worst kind: not overstocked
    -- against a horizon, simply not moving at all.
    (coalesce(avg_daily_demand, 0) = 0 and on_hand_qty > 0) as is_dead_stock
from joined
