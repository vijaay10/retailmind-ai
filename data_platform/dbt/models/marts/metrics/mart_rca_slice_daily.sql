{{ config(materialized='table', tags=['mart', 'metrics', 'rca']) }}

/*
    Daily sales unpivoted into (slice_type, slice_value) — the RCA backbone.

    **Why long and not wide.** Root-cause analysis asks the same question of
    every dimension: which slices of this cut account for the change, and did
    any of them move differently from the rest? Modelling each dimension as
    its own column would mean a separate query, a separate decomposition, and
    a separate opportunity for region and category to disagree about what
    "net revenue" means. One long table means one decomposition routine, and
    a new dimension is a new `union all` rather than new code.

    The `network` slice is emitted alongside the rest and carries the estate
    total. It is what every contribution is measured against, and computing it
    here rather than by summing the slices means the denominator cannot drift
    when a slice has NULL membership — a sale with no identified customer
    belongs to no segment, and the segment slices therefore do not sum to the
    network. That is correct, and it is exactly the kind of gap that silently
    breaks a share calculation built from the parts.

    Returns are carried as their own measures rather than netted away. "Sales
    fell" and "returns rose" are different diagnoses with different owners,
    and a mart that only publishes the net figure cannot tell them apart.
*/

with sales as (

    select
        f.business_date,
        p.category,
        p.department,
        st.region,
        st.store_id,
        st.store_name,
        c.channel_group,
        -- Unidentified is a slice, not an absence. Guest checkout is a real
        -- and often large part of the business, and dropping it would make
        -- the segment cut quietly describe a different population from every
        -- other cut in this table.
        coalesce(cu.rfm_segment, 'Unidentified') as segment,
        f.quantity,
        f.net_amount,
        f.gross_amount,
        f.discount_amount,
        f.margin_amount,
        f.order_id,
        f.is_return
    from {{ ref('fct_sales') }} f
    join {{ ref('dim_product') }} p on f.product_key = p.product_key
    join {{ ref('dim_store') }} st on f.store_key = st.store_key
    join {{ ref('dim_channel') }} c on f.channel_key = c.channel_key
    left join {{ ref('dim_customer') }} cu on f.customer_key = cu.customer_key

),

sliced as (

    select 'network' as slice_type, 'ALL' as slice_value, * from sales
    union all
    select 'region', region, * from sales
    union all
    select 'store', store_id, * from sales
    union all
    select 'category', category, * from sales
    union all
    select 'department', department, * from sales
    union all
    select 'channel', channel_group, * from sales
    union all
    select 'segment', segment, * from sales

)

select
    slice_type,
    slice_value,
    business_date,
    cast(strftime(business_date, '%Y%m%d') as integer) as date_key,

    round(sum(net_amount), 2) as net_revenue,
    round(sum(gross_amount), 2) as gross_revenue,
    round(sum(discount_amount), 2) as discount_amount,
    round(sum(margin_amount), 2) as margin_amount,
    sum(quantity) as units_sold,

    /*
        Distinct orders, not summed. A basket spanning two categories belongs
        to both, so these count correctly within a slice and must never be
        added across slices — which is why the decomposition works on revenue
        and treats order counts as a diagnostic rather than a component.
    */
    count(distinct order_id) as orders,
    count(*) as line_count,

    round(sum(case when is_return then abs(net_amount) else 0 end), 2) as return_amount,
    sum(case when is_return then abs(quantity) else 0 end) as return_units,

    -- Rate measures, recomputed here at the mart's own grain. The
    -- decomposition needs them per slice-day and recomputing from the sums
    -- above at read time would be an average of averages.
    round(sum(net_amount) / nullif(count(distinct order_id), 0), 4) as aov,
    round(sum(net_amount) / nullif(sum(quantity), 0), 4) as asp,
    round(sum(quantity) / nullif(count(distinct order_id), 0), 4) as units_per_order,
    round(
        sum(case when is_return then abs(net_amount) else 0 end)
        / nullif(sum(case when not is_return then net_amount else 0 end), 0), 6
    ) as return_rate,

    current_timestamp as _loaded_at
from sliced
where slice_value is not null
group by 1, 2, 3, 4
