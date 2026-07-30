{{
    config(
        materialized='table',
        tags=['dimension', 'core'],
        post_hook="{{ create_index(['promo_key']) }}",
    )
}}

/*
    dim_promotion — Type 1 (DB design §7).

    Promotions are corrected, not versioned: a fixed typo in a promo name
    should not create a second promotion in every historical report. Where a
    promotion's *terms* genuinely change mid-flight, the business issues a new
    code — so the natural key already carries the history.
*/

select
    {{ surrogate_key(['promo_code']) }} as promo_key,
    promo_code,
    promo_name,
    mechanic,
    depth_pct,
    start_date,
    end_date,
    funding,
    date_diff('day', start_date, end_date) + 1 as planned_duration_days,
    (current_date between start_date and end_date) as is_active
from {{ ref('promotion_master') }}

union all

-- Reserved: the sale carried no promotion. Distinct from UNKNOWN, which would
-- mean we failed to resolve one (DB §7).
select
    {{ not_applicable_key() }} as promo_key,
    'NONE' as promo_code,
    'No promotion' as promo_name,
    'none' as mechanic,
    cast(0 as bigint) as depth_pct,
    cast(null as date) as start_date,
    cast(null as date) as end_date,
    'none' as funding,
    cast(null as bigint) as planned_duration_days,
    false as is_active

union all

select
    {{ unknown_member_key() }} as promo_key,
    'UNKNOWN' as promo_code,
    'Unknown promotion' as promo_name,
    'unknown' as mechanic,
    cast(null as bigint) as depth_pct,
    cast(null as date) as start_date,
    cast(null as date) as end_date,
    'unknown' as funding,
    cast(null as bigint) as planned_duration_days,
    false as is_active
