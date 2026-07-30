/*
    Every sale must resolve to a real dimension member.

    UNKNOWN (-1) is a legitimate destination for a late-arriving dimension, but
    a *rising* count of them means the master feed is drifting away from the
    transaction feed. Above 1% the attribution is no longer trustworthy, and
    silently aggregating into an UNKNOWN bucket is how a category "loses"
    revenue that was never actually lost.
*/

with counts as (
    select
        count(*) as total_rows,
        count(*) filter (where product_key = -1) as unknown_product,
        count(*) filter (where store_key = -1) as unknown_store
    from {{ ref('fct_sales') }}
)

select *
from counts
where total_rows > 0
  and (unknown_product::double / total_rows > 0.01
       or unknown_store::double / total_rows > 0.01)
