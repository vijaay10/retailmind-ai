{{
    config(
        materialized='table',
        tags=['mart', 'metrics'],
        post_hook="{{ create_index(['date_key']) }}",
    )
}}

/*
    mart_sales_daily — date × category × region × channel (DB design §14).

    **On "materialized view".** DuckDB and Snowflake both lack a maintained MV
    that would serve this shape, and a plain view would re-scan the fact on
    every dashboard load. A dbt table *is* the materialized view here: derived,
    disposable, and rebuilt by the same DAG that publishes the fact — with the
    advantage that it is testable and version-controlled, which no engine MV is.

    This is the grain most dashboard tiles and the alert sweep actually query,
    so serving them from ~thousands of pre-aggregated rows instead of millions
    of fact rows is the single largest performance lever in the warehouse
    (DB §31: the best optimization is not running the big query at all).
*/

with sales as (

    select
        f.date_key,
        f.business_date,
        p.category,
        p.department,
        st.region,
        st.store_cluster,
        c.channel_code,
        c.channel_group,
        f.quantity,
        f.net_amount,
        f.gross_amount,
        f.discount_amount,
        f.margin_amount,
        f.cogs_amount,
        f.order_id,
        f.is_return
    from {{ ref('fct_sales') }} f
    join {{ ref('dim_product') }} p on f.product_key = p.product_key
    join {{ ref('dim_store') }} st on f.store_key = st.store_key
    join {{ ref('dim_channel') }} c on f.channel_key = c.channel_key

)

select
    date_key,
    business_date,
    category,
    department,
    region,
    store_cluster,
    channel_code,
    channel_group,

    -- ── Additive measures ──
    sum(net_amount) as net_revenue,
    sum(gross_amount) as gross_revenue,
    sum(discount_amount) as discount_amount,
    sum(margin_amount) as margin_amount,
    sum(cogs_amount) as cogs_amount,
    sum(quantity) as units_sold,

    /*
        Distinct order count is *not* additive across this grain — summing it
        over categories would count a mixed-category order once per category.
        It is kept because AOV must be computed on order grain, and the
        semantic layer knows to recompute rather than sum it.
    */
    count(distinct order_id) as orders,
    count(*) as line_count,

    -- Returns tracked by value and by unit: they diverge tellingly
    -- (Analytics §1).
    sum(case when is_return then abs(net_amount) else 0 end) as return_amount,
    sum(case when is_return then abs(quantity) else 0 end) as return_units

from sales
group by 1, 2, 3, 4, 5, 6, 7, 8
