{{ config(materialized='table', tags=['mart', 'metrics'],
          post_hook="{{ create_index(['store_key']) }}") }}

/*
    Store performance by day (Analytics §3).

    Carries `store_cluster` so ranking can be scoped to a peer group — a
    flagship and an outlet do not belong in the same league table, and making
    the cluster a column here means no consumer can forget that.
*/

select
    f.date_key,
    f.business_date,
    f.store_key,
    st.store_id,
    st.store_name,
    st.city,
    st.district,
    st.region,
    st.store_format,
    st.store_cluster,
    st.is_comp_eligible,

    sum(f.net_amount) as net_revenue,
    sum(f.gross_amount) as gross_revenue,
    sum(f.discount_amount) as discount_amount,
    sum(f.margin_amount) as margin_amount,
    sum(f.quantity) as units_sold,
    count(distinct f.order_id) as orders,
    count(distinct f.customer_key) filter (where f.customer_key <> -1) as identified_customers
from {{ ref('fct_sales') }} f
join {{ ref('dim_store') }} st on f.store_key = st.store_key
group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
