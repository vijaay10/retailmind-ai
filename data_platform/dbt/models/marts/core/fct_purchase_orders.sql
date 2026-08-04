{{
    config(
        materialized='incremental',
        unique_key='po_line_key',
        incremental_strategy='delete+insert',
        tags=['fact', 'core'],
        post_hook=[
            "{{ create_index(['supplier_key']) }}",
            "{{ create_index(['product_key']) }}",
        ],
    )
}}

/*
    fct_purchase_orders — one row per PO line (DB design §6, SE-1).

    An **accumulating snapshot**: the same line is updated as it moves from
    ordered to confirmed to received, rather than appended. That is why the
    incremental strategy is delete+insert on the line key — an append would
    leave the ordered and received versions of one line both counted, and
    double every open-order figure downstream.
*/

with orders as (

    select * from {{ ref('stg_purchasing__orders') }}

    {% if is_incremental() %}
    where business_date >= (
        select coalesce(max(business_date), date '1900-01-01') - interval 35 day
        from {{ this }}
    )
    {% endif %}

),

keyed as (

    select
        o.*,
        coalesce(sup.supplier_key, {{ unknown_member_key() }}) as supplier_key,
        coalesce(prod.product_key, {{ unknown_member_key() }}) as product_key,
        coalesce(st.store_key, {{ unknown_member_key() }}) as store_key,
        cast(strftime(o.order_date, '%Y%m%d') as integer) as order_date_key
    from orders o
    left join {{ ref('dim_supplier') }} sup on o.supplier_id = sup.supplier_id
    left join {{ ref('dim_product') }} prod
        on o.sku = prod.sku
       and {{ scd2_valid_at('o.order_ts', 'prod.valid_from', 'prod.valid_to') }}
    left join {{ ref('dim_store') }} st
        on o.store_id = st.store_id
       and {{ scd2_valid_at('o.order_ts', 'st.valid_from', 'st.valid_to') }}

)

select
    {{ surrogate_key(['po_number', 'line_no']) }} as po_line_key,
    order_date_key,
    supplier_key,
    product_key,
    store_key,

    po_number,
    line_no,
    sku,
    store_id,
    supplier_id,

    order_date,
    promise_date,
    receipt_date,
    business_date,

    ordered_qty,
    received_qty,
    unit_cost,
    ordered_value,
    po_status,

    actual_lead_time_days,
    promised_lead_time_days,
    -- Signed lateness: negative is early. Sign carries information a
    -- magnitude alone would throw away.
    actual_lead_time_days - promised_lead_time_days as lead_time_variance_days,

    is_open,
    is_on_time,
    is_in_full,
    is_otif,

    current_timestamp as _loaded_at
from keyed
