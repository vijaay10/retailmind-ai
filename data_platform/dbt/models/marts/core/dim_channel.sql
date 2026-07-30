{{
    config(
        materialized='table',
        tags=['dimension', 'core'],
        post_hook="{{ create_index(['channel_key']) }}",
    )
}}

/*
    dim_channel — Type 0 (DB design §7).

    Tiny and immutable. Kept as its own dimension rather than folded into a
    junk dimension because NLQ compiles user language against dimension names,
    and "channel" is a word merchants actually say.
*/

select
    {{ surrogate_key(['channel_code']) }} as channel_key,
    channel_code,
    channel_name,
    channel_group,
    is_digital
from {{ ref('channel_map') }}

union all

select
    {{ unknown_member_key() }} as channel_key,
    'UNKNOWN' as channel_code,
    'Unknown channel' as channel_name,
    'UNKNOWN' as channel_group,
    false as is_digital
