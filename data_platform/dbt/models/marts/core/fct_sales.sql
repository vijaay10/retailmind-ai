{{
    config(
        materialized='incremental',
        unique_key='sales_key',
        incremental_strategy='delete+insert',
        tags=['fact', 'core'],
        post_hook=[
            "{{ create_index(['sales_key']) }}",
            "{{ create_index(['order_id']) }}",
            "{{ create_index(['product_key']) }}",
            "{{ create_index(['store_key']) }}",
            "{{ create_index(['customer_key']) }}",
        ],
    )
}}

/*
    fct_sales — one row per order line (DB design §6).

    **Grain is the design.** Everything else follows from it: the surrogate key
    hashes (order_id, line_no), the uniqueness test enforces it, and any join
    that could fan a line into two rows is a bug the grain test will catch.

    **Incremental with delete+insert over a window**, not append. Re-running a
    window replaces it, which is what makes a re-run, a late-file re-land, and
    a backfill all the same safe operation (FR-D03). Append would make each of
    those a double-count.

    **As-was dimension attribution.** Keys resolve to the dimension version
    valid at transaction time (DB §8), so a sale made while a SKU sat in
    Outerwear stays in Outerwear after it is recategorized. This is the join
    most warehouses get wrong by defaulting to `is_current`.
*/

with sales as (

    select * from {{ ref('stg_pos__sales') }}

    {% if is_incremental() %}
    /*
        The 35-day rolling window absorbs late-arriving data (ETL §6) while
        keeping nightly work bounded regardless of how much history exists.
        Anything older is an explicit backfill, which runs this same model
        with a wider window via --vars.
    */
    where business_date >= (
        select coalesce(max(business_date), date '1900-01-01') - interval 35 day
        from {{ this }}
    )
    {% endif %}

),

/*
    Dimension key resolution. Each join is LEFT so an unresolvable member
    produces the reserved UNKNOWN key rather than silently dropping the sale —
    losing revenue to a missing dimension row is the worst possible failure
    mode, and one that inner joins cause quietly.
*/
keyed as (

    select
        s.*,

        coalesce(p.product_key, {{ unknown_member_key() }}) as product_key,
        coalesce(st.store_key, {{ unknown_member_key() }}) as store_key,
        coalesce(c.channel_key, {{ unknown_member_key() }}) as channel_key,
        coalesce(cust.customer_key, {{ unknown_member_key() }}) as customer_key,
        -- No promo on the line is NOT_APPLICABLE (-2), not UNKNOWN (-1):
        -- one means nothing to resolve, the other means we failed to.
        case
            when s.promo_code is null then {{ not_applicable_key() }}
            else coalesce(promo.promo_key, {{ unknown_member_key() }})
        end as promo_key,
        cast(strftime(s.business_date, '%Y%m%d') as integer) as date_key,

        -- Cost is taken from the *as-was* product version, so margin history
        -- is not rewritten when a cost is renegotiated.
        p.unit_cost as unit_cost_at_sale

    from sales s

    left join {{ ref('dim_product') }} p
        on s.sku = p.sku
       and {{ scd2_valid_at('s.transaction_ts', 'p.valid_from', 'p.valid_to') }}

    left join {{ ref('dim_store') }} st
        on s.store_id = st.store_id
       and {{ scd2_valid_at('s.transaction_ts', 'st.valid_from', 'st.valid_to') }}

    left join {{ ref('dim_channel') }} c
        on s.channel_code = c.channel_code

    left join {{ ref('dim_customer') }} cust
        on s.customer_id = cust.customer_id

    left join {{ ref('dim_promotion') }} promo
        on s.promo_code = promo.promo_code

)

select
    -- ── Keys ──
    {{ surrogate_key(['order_id', 'line_no']) }} as sales_key,
    date_key,
    product_key,
    store_key,
    channel_key,
    customer_key,
    promo_key,

    -- ── Degenerate dimensions (DB §6) ──
    order_id,
    line_no,

    -- ── Measures: all fully additive ──
    quantity,
    gross_amount,
    discount_amount,
    net_amount,
    unit_price,

    /*
        COGS and margin computed at the as-was cost. NULL cost (unknown
        product) yields NULL margin rather than a zero that would quietly
        overstate profitability.
    */
    round(quantity * unit_cost_at_sale, 4) as cogs_amount,
    round(net_amount - (quantity * unit_cost_at_sale), 4) as margin_amount,

    -- ── Flags ──
    is_return,

    -- ── Provenance ──
    business_date,
    transaction_ts,
    source_currency,
    gross_amount_source,
    current_timestamp as _loaded_at

from keyed
