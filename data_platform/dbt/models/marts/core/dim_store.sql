{{
    config(
        materialized='table',
        tags=['dimension', 'core'],
        post_hook=[
            "{{ create_index(['store_key']) }}",
            "{{ create_index(['store_id']) }}",
        ],
    )
}}

/*
    dim_store — SCD2 (DB design §7–8).

    Carries the attributes that decide a store's comparison peer group, because
    ranking a flagship against an outlet is analytical malpractice
    (Analytics §3). `store_cluster` is derived here so every consumer groups
    the same way rather than each inventing its own definition.
*/

with versions as (

    select
        store_id,
        store_name,
        city,
        district,
        region,
        store_format,
        sqft_band,
        timezone,
        opened_date,
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
            when row_number() over (partition by store_id order by dbt_valid_from) = 1
            then timestamp '1900-01-01 00:00:00'
            else dbt_valid_from
        end as valid_from,
        coalesce(dbt_valid_to, timestamp '9999-12-31 23:59:59') as valid_to,
        (dbt_valid_to is null) as is_current,
        row_number() over (partition by store_id order by dbt_valid_from) as version_number
    from {{ ref('snap_store') }}

)

select
    {{ surrogate_key(['store_id', 'valid_from']) }} as store_key,
    store_id,
    store_name,
    city,
    district,
    region,
    store_format,
    sqft_band,
    timezone,
    opened_date,

    -- The peer group for ranking: format × size band. Defined once here so a
    -- cluster can never mean two different things in two dashboards.
    store_format || '/' || sqft_band as store_cluster,

    /*
        Comp-store eligibility (Analytics §1): a store enters like-for-like
        comparisons only after 53 weeks of trading, so its first anniversary
        compares against a full prior year rather than a partial ramp.
    */
    (date_diff('day', opened_date, current_date) >= 371) as is_comp_eligible,
    date_diff('day', opened_date, current_date) as days_since_opening,

    valid_from,
    valid_to,
    is_current,
    version_number
from versions

union all

select
    {{ unknown_member_key() }} as store_key,
    'UNKNOWN' as store_id,
    'Unknown store' as store_name,
    'UNKNOWN' as city,
    'UNKNOWN' as district,
    'UNKNOWN' as region,
    'UNKNOWN' as store_format,
    'UNKNOWN' as sqft_band,
    'UTC' as timezone,
    cast(null as date) as opened_date,
    'UNKNOWN' as store_cluster,
    false as is_comp_eligible,
    cast(null as bigint) as days_since_opening,
    timestamp '1900-01-01 00:00:00' as valid_from,
    timestamp '9999-12-31 23:59:59' as valid_to,
    true as is_current,
    1 as version_number
