{{ config(materialized='view', tags=['semantic']) }}

/*
    The application's *only* entry point to sales facts (DB design §15, §20).

    Views in this layer carry no business math — they join, filter, and
    project. Metric definitions live in the semantic layer's registry so that
    "net revenue" cannot mean one thing in a view and another in a dashboard.

    On the cloud profile this becomes a Snowflake SECURE VIEW carrying the
    row-access policy that scopes rows to the caller's tenant. DuckDB has no
    equivalent, so local runs are single-tenant by construction — the boundary
    is the same, the enforcement moves.
*/

select
    f.sales_key,
    f.business_date,
    f.order_id,
    f.line_no,

    -- Dimension attributes flattened for query convenience; the star is still
    -- the source of truth, this is a projection over it.
    p.sku,
    p.product_name,
    p.subcategory,
    p.category,
    p.department,
    p.brand,
    st.store_id,
    st.store_name,
    st.city,
    st.district,
    st.region,
    st.store_format,
    st.store_cluster,
    st.is_comp_eligible,
    c.channel_code,
    c.channel_group,
    c.is_digital,
    d.fiscal_year,
    d.fiscal_quarter,
    d.fiscal_period,
    d.fiscal_week,
    d.week_ending_date,

    -- Measures
    f.quantity,
    f.gross_amount,
    f.discount_amount,
    f.net_amount,
    f.cogs_amount,
    f.margin_amount,
    f.is_return,
    f.source_currency

from {{ ref('fct_sales') }} f
join {{ ref('dim_product') }} p on f.product_key = p.product_key
join {{ ref('dim_store') }} st on f.store_key = st.store_key
join {{ ref('dim_channel') }} c on f.channel_key = c.channel_key
join {{ ref('dim_date') }} d on f.date_key = d.date_key
