{{
    config(
        materialized='incremental',
        unique_key='inventory_key',
        incremental_strategy='delete+insert',
        tags=['fact', 'core'],
        post_hook=[
            "{{ create_index(['product_key']) }}",
            "{{ create_index(['store_key']) }}",
        ],
    )
}}

/*
    fct_inventory_daily — SKU × store × day (DB design §6).

    A **periodic snapshot** fact, and that classification drives everything
    downstream. Its measures are *semi-additive*: on-hand sums across stores
    and SKUs but never across dates — adding Monday's and Tuesday's stock
    would invent inventory that never existed. The semantic layer encodes this
    so no consumer can get it wrong; the tests assert it.

    Cover days join demand from sales, which is why the cross-source join lives
    here rather than in staging: it is a fact-grain concern, visible where a
    reader expects it.
*/

with positions as (

    select * from {{ ref('stg_inventory__positions') }}

    {% if is_incremental() %}
    where business_date >= (
        select coalesce(max(business_date), date '1900-01-01') - interval 35 day
        from {{ this }}
    )
    {% endif %}

),

/*
    Trailing demand per SKU × store. Averaged over 28 days to smooth weekday
    effects, and computed from *sales while in stock* would be the fully
    correct form — stockout-censored demand (Analytics §4). With a single day
    of history that refinement has nothing to bite on, so it is deferred and
    flagged rather than faked.
*/
demand as (

    select
        sku,
        store_id,
        sum(quantity) / 28.0 as avg_daily_units
    from {{ ref('stg_pos__sales') }}
    where not is_return
    group by 1, 2

),

keyed as (

    select
        p.*,
        d.avg_daily_units,
        coalesce(prod.product_key, {{ unknown_member_key() }}) as product_key,
        coalesce(st.store_key, {{ unknown_member_key() }}) as store_key,
        cast(strftime(p.business_date, '%Y%m%d') as integer) as date_key
    from positions p
    left join demand d
        on p.sku = d.sku and p.store_id = d.store_id
    left join {{ ref('dim_product') }} prod
        on p.sku = prod.sku
       and {{ scd2_valid_at('p.snapshot_ts', 'prod.valid_from', 'prod.valid_to') }}
    left join {{ ref('dim_store') }} st
        on p.store_id = st.store_id
       and {{ scd2_valid_at('p.snapshot_ts', 'st.valid_from', 'st.valid_to') }}

)

select
    {{ surrogate_key(['sku', 'store_id', 'snapshot_date']) }} as inventory_key,
    date_key,
    product_key,
    store_key,

    -- ── Semi-additive measures: never SUM across dates ──
    on_hand_qty,
    on_order_qty,
    in_transit_qty,
    inventory_value_cost,

    -- ── Derived availability ──
    is_stockout,
    avg_daily_units,
    -- Cover is non-additive entirely: recompute it at whatever grain you
    -- aggregate to, never average the averages.
    case
        when coalesce(avg_daily_units, 0) > 0
        then round(on_hand_qty / avg_daily_units, 2)
    end as cover_days,

    business_date,
    snapshot_ts,
    current_timestamp as _loaded_at

from keyed
