{{
    config(
        materialized='table',
        tags=['dimension', 'core'],
        post_hook="{{ create_index(['customer_key']) }}",
    )
}}

/*
    dim_customer — derived from transaction history (Analytics §2, DB §7).

    No customer master exists: the POS feed carries a pseudonymous loyalty id
    and nothing else. That is a feature, not a gap — every attribute here is
    *behavioural*, computed from what the customer did, which is exactly what
    segmentation needs and what avoids holding personal data we have no use for.

    RFM scoring follows Analytics M2: recency, frequency, and monetary value
    each scored into quintiles, then collapsed to named segments by a fixed
    rule map. The map is governance, not vibes — it lives here so "Champions"
    means one thing platform-wide.

    Type 2 is deliberately *not* used. Segments change constantly by design;
    versioning them would multiply this dimension by every monthly reshuffle
    while answering a question nobody asks. Segment history lives in the
    monthly RFM mart instead.
*/

with purchases as (

    select
        customer_id,
        count(distinct order_id) as order_count,
        sum(net_amount) as lifetime_value,
        sum(quantity) as lifetime_units,
        min(business_date) as first_purchase_date,
        max(business_date) as last_purchase_date,
        count(distinct sku) as distinct_skus,
        sum(case when is_return then 1 else 0 end) as return_lines
    from {{ ref('stg_pos__sales') }}
    where customer_id is not null and customer_id <> 'UNKNOWN'
    group by 1

),

anchored as (

    -- Recency is measured against the latest date in the data, not today:
    -- a demo or a backfill must not make every customer look dormant.
    select
        p.*,
        (select max(business_date) from {{ ref('stg_pos__sales') }}) as as_of_date
    from purchases p

),

scored as (

    select
        *,
        date_diff('day', last_purchase_date, as_of_date) as recency_days,
        -- ntile(5) over each axis; 5 is always "best", so a high score means
        -- recent, frequent, or valuable without the reader memorising which.
        ntile(5) over (order by date_diff('day', last_purchase_date, as_of_date) desc)
            as recency_score,
        ntile(5) over (order by order_count) as frequency_score,
        ntile(5) over (order by lifetime_value) as monetary_score
    from anchored

)

select
    {{ surrogate_key(['customer_id']) }} as customer_key,
    customer_id,

    -- ── Behaviour ──
    order_count,
    lifetime_value,
    lifetime_units,
    distinct_skus,
    return_lines,
    first_purchase_date,
    last_purchase_date,
    recency_days,
    round(lifetime_value / nullif(order_count, 0), 4) as avg_order_value,
    (order_count > 1) as is_repeat_customer,

    -- ── RFM ──
    recency_score,
    frequency_score,
    monetary_score,
    (recency_score * 100) + (frequency_score * 10) + monetary_score as rfm_cell,

    /*
        The named-segment rule map (Analytics M2). Order matters: the first
        matching branch wins, so the most valuable and most urgent segments
        are tested before the general ones.
    */
    case
        when recency_score >= 4 and frequency_score >= 4 and monetary_score >= 4
            then 'Champions'
        when recency_score >= 3 and frequency_score >= 3 then 'Loyal'
        when recency_score >= 4 and frequency_score <= 2 then 'New'
        when recency_score = 3 and frequency_score <= 2 then 'Promising'
        when recency_score <= 2 and monetary_score >= 4 then 'At Risk'
        when recency_score <= 2 and frequency_score >= 3 then 'Needs Attention'
        when recency_score = 1 then 'Hibernating'
        else 'Potential'
    end as rfm_segment,

    as_of_date
from scored

union all

-- Guest checkout aggregates here. Reported, never interpolated: the share of
-- unidentified sales is itself a KPI that guards every customer inference.
select
    {{ unknown_member_key() }} as customer_key,
    'UNIDENTIFIED' as customer_id,
    cast(null as bigint) as order_count,
    cast(null as decimal(18, 4)) as lifetime_value,
    cast(null as decimal(18, 4)) as lifetime_units,
    cast(null as bigint) as distinct_skus,
    cast(null as bigint) as return_lines,
    cast(null as date) as first_purchase_date,
    cast(null as date) as last_purchase_date,
    cast(null as bigint) as recency_days,
    cast(null as double) as avg_order_value,
    false as is_repeat_customer,
    cast(null as integer) as recency_score,
    cast(null as integer) as frequency_score,
    cast(null as integer) as monetary_score,
    cast(null as integer) as rfm_cell,
    'Unidentified' as rfm_segment,
    cast(null as date) as as_of_date
