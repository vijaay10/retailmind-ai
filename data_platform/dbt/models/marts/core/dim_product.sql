{{
    config(
        materialized='table',
        tags=['dimension', 'core'],
        post_hook=[
            "{{ create_index(['product_key']) }}",
            "{{ create_index(['sku']) }}",
        ],
    )
}}

/*
    dim_product — SCD2 (DB design §7–8).

    One row per SKU *version*. Facts point at the version that was current when
    the sale happened, so historical attribution never rewrites itself when a
    product is recategorized or repriced.

    The surrogate key hashes (sku, valid_from): deterministic, so a full
    rebuild produces identical keys and every fact keeps pointing where it
    pointed (DB §9).
*/

with versions as (

    select
        sku,
        product_name,
        subcategory,
        category,
        department,
        brand,
        unit_cost,
        list_price,
        status,
        /*
            The first version's validity is backdated to a far-past sentinel.

            `dbt_valid_from` is the moment the snapshot first *observed* the
            row, not the moment the attribute became true. Without backdating,
            every fact older than the first snapshot run fails the as-was
            predicate and lands on the UNKNOWN member — which is exactly what
            happens when a warehouse is built on top of history it did not
            watch accumulate.

            Presuming version 1 was valid for all prior time is the honest
            default: we have no evidence of a different earlier value, and the
            alternative silently discards the attribution of every historical
            fact.
        */
        case
            when row_number() over (partition by sku order by dbt_valid_from) = 1
            then timestamp '1900-01-01 00:00:00'
            else dbt_valid_from
        end as valid_from,
        -- dbt leaves the open window as NULL; a sentinel far-future date makes
        -- the as-was BETWEEN predicate work without a NULL branch in every
        -- fact model.
        coalesce(dbt_valid_to, timestamp '9999-12-31 23:59:59') as valid_to,
        (dbt_valid_to is null) as is_current,
        row_number() over (partition by sku order by dbt_valid_from) as version_number
    from {{ ref('snap_product') }}

),

keyed as (

    select
        {{ surrogate_key(['sku', 'valid_from']) }} as product_key,
        *
    from versions

)

select
    product_key,
    sku,
    product_name,
    subcategory,
    category,
    department,
    brand,
    unit_cost,
    list_price,
    status,
    -- Margin potential at list — a buyer's first screen, computed once here
    -- rather than in every consuming query.
    round((list_price - unit_cost) / nullif(list_price, 0), 4) as list_margin_rate,
    valid_from,
    valid_to,
    is_current,
    version_number
from keyed

union all

/*
    The reserved members (DB §7). Facts referencing an unknown or inapplicable
    product still join, which is what keeps every fact FK non-null and every
    aggregate join-safe.
*/
select
    {{ unknown_member_key() }} as product_key,
    'UNKNOWN' as sku,
    'Unknown product' as product_name,
    'UNKNOWN' as subcategory,
    'UNKNOWN' as category,
    'UNKNOWN' as department,
    'UNKNOWN' as brand,
    cast(null as decimal(10, 2)) as unit_cost,
    cast(null as decimal(10, 2)) as list_price,
    'unknown' as status,
    cast(null as double) as list_margin_rate,
    timestamp '1900-01-01 00:00:00' as valid_from,
    timestamp '9999-12-31 23:59:59' as valid_to,
    true as is_current,
    1 as version_number
