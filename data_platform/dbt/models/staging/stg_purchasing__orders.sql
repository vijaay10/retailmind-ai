{{ config(materialized='view', tags=['staging', 'purchasing']) }}

/*
    Silver: conform purchase-order lines.

    The one derivation worth doing here is the OTIF split. "On time" and "in
    full" are separate failures with separate causes — a supplier who ships
    everything a week late needs a different conversation from one who ships
    on time but short — and collapsing them into a single pass/fail hides
    which conversation to have.
*/

select
    po_number,
    line_no,
    supplier_id,
    sku,
    store_id,

    order_ts,
    cast(order_ts as date) as order_date,
    promise_date,
    receipt_date,
    business_date,

    ordered_qty,
    received_qty,
    unit_cost,
    round(ordered_qty * unit_cost, 4) as ordered_value,
    po_status,

    -- Lead time is only defined once something arrived. NULL for open lines,
    -- because an order still in transit has no lead time *yet* — and averaging
    -- a zero into supplier performance would flatter every slow vendor.
    case
        when receipt_date is not null
        then date_diff('day', order_date, receipt_date)
    end as actual_lead_time_days,

    date_diff('day', order_date, promise_date) as promised_lead_time_days,

    (receipt_date is null and po_status <> 'cancelled') as is_open,
    (receipt_date is not null and receipt_date <= promise_date) as is_on_time,
    (receipt_date is not null and coalesce(received_qty, 0) >= ordered_qty) as is_in_full,
    (
        receipt_date is not null
        and receipt_date <= promise_date
        and coalesce(received_qty, 0) >= ordered_qty
    ) as is_otif,

    source_currency
from {{ source('raw', 'purchasing__orders') }}
