{{
    config(
        materialized='view',
        tags=['staging', 'pos'],
    )
}}

/*
    Silver: conform POS sales lines for the star schema.

    The ingestion pipeline already did the hard, source-specific work — typing,
    reject routing, dedup, currency conversion, business dating. This model's
    job is narrower and purely dimensional: derive the measures the warehouse
    guarantees (net revenue, margin) and expose natural keys ready for
    surrogate resolution.

    A view, not a table: it is a thin projection over raw, and materializing it
    would double storage to save nothing. Facts materialize; staging clarifies.
*/

with source as (

    select * from {{ source('raw', 'pos__sales') }}

),

conformed as (

    select
        -- ── Natural keys (already normalized upstream) ──
        order_id,
        line_no,
        sku,
        store_id,
        lower(channel) as channel_code,
        nullif(promo_code, '') as promo_code,
        -- Guest checkout leaves this blank; it stays NULL rather than
        -- becoming a fake identity, because the identification rate is
        -- itself a reported KPI (Analytics §2).
        nullif(customer_id, '') as customer_id,

        -- ── Grain and time ──
        business_date,
        transaction_ts,

        -- ── Measures ──
        quantity,
        gross_amount,
        coalesce(discount_amount, 0) as discount_amount,
        unit_price,

        /*
            Net revenue is defined once, here, and every consumer reads it.
            Returns arrive as negative quantities with negative amounts
            (ETL §11), so net revenue nets them naturally: no CASE, no filter,
            no chance of a downstream model forgetting to exclude them.
        */
        gross_amount - coalesce(discount_amount, 0) as net_amount,

        -- Returns are identifiable without changing how they aggregate.
        (quantity < 0) as is_return,

        -- ── Currency provenance (ETL §15) ──
        source_currency,
        gross_amount_source_amount as gross_amount_source,

        -- ── Lineage ──
        cashier_id

    from source

)

select * from conformed
