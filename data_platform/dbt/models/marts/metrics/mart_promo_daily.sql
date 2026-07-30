{{ config(materialized='table', tags=['mart', 'metrics']) }}

/*
    Promotion performance by promo × day (Analytics §6).

    Deliberately stops short of *lift*. Honest lift requires a counterfactual —
    what the series would have done without the promotion — which comes from
    the forecast engine, not from comparing promoted to non-promoted rows.
    Publishing a naive "lift" column here would be a number people trust and
    shouldn't. What this mart provides is the measured side: subsidy,
    participation, and realised margin.
*/

select
    f.date_key,
    f.business_date,
    f.promo_key,
    pr.promo_code,
    pr.promo_name,
    pr.mechanic,
    pr.depth_pct,
    pr.funding,

    sum(f.net_amount) as promo_revenue,
    sum(f.discount_amount) as subsidy_amount,
    sum(f.margin_amount) as promo_margin,
    sum(f.quantity) as promo_units,
    count(distinct f.order_id) as promo_orders,
    count(distinct f.customer_key) filter (where f.customer_key <> -1) as promo_customers,

    round(sum(f.discount_amount) / nullif(sum(f.gross_amount), 0), 4) as effective_depth,
    round(sum(f.margin_amount) / nullif(sum(f.net_amount), 0), 4) as promo_margin_rate

from {{ ref('fct_sales') }} f
join {{ ref('dim_promotion') }} pr on f.promo_key = pr.promo_key
where f.promo_key not in (-1, -2)
group by 1, 2, 3, 4, 5, 6, 7, 8
