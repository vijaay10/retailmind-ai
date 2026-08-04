{{ config(materialized='table', tags=['mart', 'metrics', 'inventory']) }}

/*
    ABC classification (Analytics method M3).

    Products ranked by revenue contribution within their category, then cut at
    the cumulative 80/95% marks. The point is not the labels — it is that A, B,
    and C items deserve *different operating policies*: A items get tight
    service targets and frequent review, C items get left alone.

    **Classified within category, not globally.** A top-selling accessory and a
    mid-tier jacket have different absolute revenues but the same operational
    importance to their buyers. Ranking them in one pool would put every
    accessory in class C and make the classification useless to the person who
    buys accessories.
*/

with product_sales as (

    select
        p.sku,
        p.product_name,
        p.category,
        p.department,
        sum(f.net_amount) as revenue,
        sum(f.quantity) as units,
        sum(f.margin_amount) as margin,
        count(distinct f.order_id) as orders,
        count(distinct f.business_date) as selling_days
    from {{ ref('fct_sales') }} f
    join {{ ref('dim_product') }} p on f.product_key = p.product_key
    where p.product_key <> -1
    group by 1, 2, 3, 4

),

ranked as (

    select
        *,
        sum(revenue) over (partition by category) as category_revenue,
        sum(revenue) over (
            partition by category order by revenue desc
            rows between unbounded preceding and current row
        ) as running_revenue,
        row_number() over (partition by category order by revenue desc) as rank_in_category,
        count(*) over (partition by category) as skus_in_category
    from product_sales

),

classified as (

    select
        *,
        running_revenue / nullif(category_revenue, 0) as cumulative_share,
        case
            when running_revenue / nullif(category_revenue, 0) <= 0.80 then 'A'
            when running_revenue / nullif(category_revenue, 0) <= 0.95 then 'B'
            else 'C'
        end as abc_class
    from ranked

)

select
    sku,
    product_name,
    category,
    department,
    abc_class,
    rank_in_category,
    skus_in_category,
    round(revenue, 2) as revenue,
    units,
    round(margin, 2) as margin,
    orders,
    selling_days,
    round(cumulative_share, 4) as cumulative_share,
    round(revenue / nullif(category_revenue, 0), 4) as share_of_category,

    /*
        The service level each class earns. A-items carry the tightest target
        because a stockout there costs the most; C-items get a looser one
        because carrying safety stock for the long tail is how working capital
        disappears. These feed the reorder calculation directly.
    */
    case abc_class when 'A' then 0.98 when 'B' then 0.95 else 0.90 end as target_service_level,

    -- Velocity, normalised by how many days the SKU was actually selling.
    round(units / nullif(selling_days, 0), 3) as units_per_selling_day
from classified
