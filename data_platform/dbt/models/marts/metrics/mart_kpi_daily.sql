{{
    config(
        materialized='table',
        tags=['mart', 'metrics'],
        post_hook="{{ create_index(['business_date']) }}",
    )
}}

/*
    mart_kpi_daily — one wide row per business date (DB design §14).

    The executive scorecard's source. Small enough to scan entirely, which is
    why the KPI tiles hit their p95 budget without caching anything.

    **The comparison columns are the point.** Week-over-week and year-over-year
    are computed here against fiscal-aligned dates from dim_date, not by
    subtracting 7 or 365 days. Retail compares fiscal week to fiscal week
    (FR-A03); date arithmetic would compare a five-Saturday period against a
    four-Saturday one and report the weekday mix as performance.
*/

with daily as (

    select
        f.date_key,
        f.business_date,
        sum(f.net_amount) as net_revenue,
        sum(f.gross_amount) as gross_revenue,
        sum(f.discount_amount) as discount_amount,
        sum(f.margin_amount) as margin_amount,
        sum(f.quantity) as units_sold,
        count(distinct f.order_id) as orders,
        count(distinct f.store_key) as active_stores
    from {{ ref('fct_sales') }} f
    group by 1, 2

),

with_calendar as (

    select
        d.date_key,
        d.business_date,
        cal.fiscal_year,
        cal.fiscal_quarter,
        cal.fiscal_period,
        cal.fiscal_week,
        cal.week_ending_date,
        cal.same_fiscal_week_last_year,
        d.net_revenue,
        d.gross_revenue,
        d.discount_amount,
        d.margin_amount,
        d.units_sold,
        d.orders,
        d.active_stores
    from daily d
    join {{ ref('dim_date') }} cal on d.date_key = cal.date_key

),

with_comparisons as (

    select
        c.*,

        -- Prior day, for day-over-day movement.
        lag(c.net_revenue) over (order by c.business_date) as net_revenue_prior_day,

        -- Fiscal-aligned prior year: same weekday, same week of the fiscal
        -- year, which is the only honest YoY in retail.
        ly.net_revenue as net_revenue_last_year,
        ly.units_sold as units_sold_last_year,
        ly.margin_amount as margin_amount_last_year

    from with_calendar c
    left join with_calendar ly
        on c.same_fiscal_week_last_year = ly.business_date

)

select
    date_key,
    business_date,
    fiscal_year,
    fiscal_quarter,
    fiscal_period,
    fiscal_week,
    week_ending_date,

    -- ── Headline measures ──
    net_revenue,
    gross_revenue,
    discount_amount,
    margin_amount,
    units_sold,
    orders,
    active_stores,

    -- ── Ratios: recomputed at this grain, never summed ──
    round(net_revenue / nullif(orders, 0), 4) as aov,
    round(net_revenue / nullif(units_sold, 0), 4) as asp,
    round(margin_amount / nullif(net_revenue, 0), 4) as margin_rate,
    round(discount_amount / nullif(gross_revenue, 0), 4) as discount_rate,
    round(units_sold / nullif(orders, 0), 4) as units_per_order,

    -- ── Comparisons ──
    net_revenue_prior_day,
    net_revenue_last_year,
    units_sold_last_year,
    margin_amount_last_year,
    round(
        (net_revenue - net_revenue_last_year) / nullif(abs(net_revenue_last_year), 0),
        4
    ) as net_revenue_yoy_pct,
    round(
        (net_revenue - net_revenue_prior_day) / nullif(abs(net_revenue_prior_day), 0),
        4
    ) as net_revenue_dod_pct

from with_comparisons
