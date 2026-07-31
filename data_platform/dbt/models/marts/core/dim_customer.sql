{{
    config(
        materialized='table',
        tags=['dimension', 'core'],
        post_hook="{{ create_index(['customer_key']) }}",
    )
}}

/*
    dim_customer — behavioural customer intelligence (Analytics §2, DB §7).

    No customer master exists: the POS feed carries a pseudonymous loyalty id
    and nothing else. That is a feature. Every attribute here is *behavioural*,
    computed from what the customer did, which is what segmentation actually
    needs and what avoids holding personal data the product has no use for.

    What this model derives, and the honest limit of each:

    * **RFM** (M2) — recency, frequency, and monetary quintiles collapsed to
      named segments by a fixed rule map. The map is governance, not vibes: it
      lives here so "Champions" means one thing platform-wide.
    * **Historic CLV** — cumulative net revenue. Trustworthy, because it is
      arithmetic over things that actually happened.
    * **Predicted CLV** — an *extrapolation* from observed behaviour, not a
      fitted model. It carries a confidence grade derived from tenure, because
      annualising two weeks of history is a guess wearing a number's clothes.
    * **Churn risk** — a transparent recency-versus-cadence ratio, not a
      classifier. In non-contractual retail nobody announces they have left,
      so this is expressed as *at risk*, never as "churned" (M6).
    * **Lifecycle stage** — where the customer sits on the New → Loyal
      progression (M14).
    * **VIP** — top-decile value *and* repeat behaviour. One big order does not
      make a VIP; it makes a good day.

    Type 2 is deliberately not used. Segments churn constantly by design;
    versioning them would multiply this dimension by every monthly reshuffle
    to answer a question nobody asks. Segment history lives in the cohort and
    lifecycle marts instead.
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
        count(distinct store_id) as distinct_stores,
        sum(case when is_return then 1 else 0 end) as return_lines
    from {{ ref('stg_pos__sales') }}
    where customer_id is not null and customer_id <> 'UNKNOWN'
    group by 1

),

anchored as (

    /*
        Recency is measured against the latest date *in the data*, not today.
        A demo, a backfill, or a stalled pipeline must not make every customer
        look dormant — that would turn an operational problem into a fake
        churn crisis.
    */
    select
        p.*,
        (select max(business_date) from {{ ref('stg_pos__sales') }}) as as_of_date
    from purchases p

),

tenure as (

    select
        *,
        date_diff('day', last_purchase_date, as_of_date) as recency_days,
        date_diff('day', first_purchase_date, as_of_date) as tenure_days,

        /*
            The customer's own purchase cadence — the denominator every churn
            signal needs. Single-order customers have no observable cadence, so
            this is NULL for them rather than an invented number; the
            population median stands in downstream.
        */
        case
            when order_count > 1
            then date_diff('day', first_purchase_date, last_purchase_date)
                 / nullif(order_count - 1, 0)
        end as avg_days_between_orders
    from anchored

),

scored as (

    select
        *,
        -- ntile(5) per axis; 5 is always "best", so a high score means recent,
        -- frequent, or valuable without the reader memorising which way is up.
        ntile(5) over (order by recency_days desc) as recency_score,
        ntile(5) over (order by order_count) as frequency_score,
        ntile(5) over (order by lifetime_value) as monetary_score,

        -- Value percentile drives VIP detection below.
        percent_rank() over (order by lifetime_value) as value_percentile,

        -- Population cadence: the fallback for customers with a single order.
        median(avg_days_between_orders) over () as population_cadence_days
    from tenure

),

derived as (

    select
        *,

        /*
            Churn risk as a transparent ratio: how many expected purchase
            cycles have elapsed since the last order. A customer who buys
            monthly and last bought three months ago scores 3.

            Deliberately not a classifier. A ratio is explainable to a merchant
            in one sentence, and with no labelled churn outcomes to train on, a
            model here would be sophistication without evidence. The calibrated
            classifier lands when engagement produces labels (Analytics M6).
        */
        recency_days / nullif(coalesce(avg_days_between_orders, population_cadence_days), 0)
            as cycles_since_last_order,

        /*
            Annualised order frequency from observed tenure, floored at 30
            days. Dividing by a two-day tenure produces a customer who
            allegedly buys 180 times a year, and that number would poison every
            projection built on it.
        */
        order_count::double / (greatest(tenure_days, 30) / 365.0) as annual_order_frequency,

        lifetime_value / nullif(order_count, 0) as avg_order_value
    from scored

)

select
    {{ surrogate_key(['customer_id']) }} as customer_key,
    customer_id,

    -- ── Observed behaviour ──
    order_count,
    lifetime_value,
    lifetime_units,
    distinct_skus,
    distinct_stores,
    return_lines,
    first_purchase_date,
    last_purchase_date,
    recency_days,
    tenure_days,
    round(avg_order_value, 4) as avg_order_value,
    round(avg_days_between_orders, 1) as avg_days_between_orders,
    (order_count > 1) as is_repeat_customer,

    -- ── RFM (M2) ──
    recency_score,
    frequency_score,
    monetary_score,
    (recency_score * 100) + (frequency_score * 10) + monetary_score as rfm_cell,

    /*
        The named-segment rule map. Order matters: the most valuable and most
        urgent segments are tested first, so a high-value lapsing customer is
        called "At Risk" rather than falling through to a milder label.
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

    -- ── Lifecycle stage (M14) ──
    /*
        Stage is frequency-based, because that is what "journey" means: how far
        along the repeat-purchase progression a customer has travelled. Recency
        is tracked separately as churn risk, so a lapsing Loyal customer stays
        Loyal *and* at risk — collapsing both into one label would hide exactly
        the customer worth saving.
    */
    case
        when order_count = 1 then 'New'
        when order_count between 2 and 3 then 'Repeat'
        when order_count between 4 and 7 then 'Established'
        else 'Loyal'
    end as lifecycle_stage,

    -- ── Churn risk (M6) ──
    round(cycles_since_last_order, 2) as cycles_since_last_order,

    /*
        Risk bands, not a probability. Calling a heuristic ratio "68% likely to
        churn" would imply a calibration nobody has measured. Bands say only
        what is known: this customer is overdue by a factor of N.
    */
    case
        when cycles_since_last_order is null then 'unknown'
        when cycles_since_last_order >= 3 then 'high'
        when cycles_since_last_order >= 2 then 'medium'
        when cycles_since_last_order >= 1 then 'low'
        else 'none'
    end as churn_risk_band,

    (coalesce(cycles_since_last_order, 0) >= 2) as is_at_risk,

    -- ── Predicted CLV ──
    round(annual_order_frequency, 2) as annual_order_frequency,

    /*
        Twelve-month value projection: observed AOV × annualised frequency. An
        extrapolation, and labelled as one — no survival model, no discounting,
        no assumed churn curve.
    */
    round(avg_order_value * annual_order_frequency, 2) as predicted_clv_12m,

    /*
        How much to trust that projection, graded by how much history it rests
        on. Publishing the number without this grade is how a two-week customer
        ends up in a five-year revenue plan.
    */
    case
        when tenure_days >= 365 then 'high'
        when tenure_days >= 90 then 'medium'
        else 'low'
    end as clv_confidence,

    -- ── VIP detection ──
    /*
        Top decile by lifetime value *and* a repeat buyer. The repeat condition
        matters: a single large order is a good day, not a relationship, and
        treating it as VIP spends retention budget on someone passing through.
    */
    (value_percentile >= 0.9 and order_count > 1) as is_vip,
    round(value_percentile, 4) as value_percentile,

    as_of_date
from derived

union all

/*
    Guest checkout aggregates here. Reported, never interpolated: the share of
    unidentified sales is itself a KPI that guards every customer inference
    drawn from this dimension.
*/
select
    {{ unknown_member_key() }} as customer_key,
    'UNIDENTIFIED' as customer_id,
    cast(null as bigint) as order_count,
    cast(null as decimal(18, 4)) as lifetime_value,
    cast(null as decimal(18, 4)) as lifetime_units,
    cast(null as bigint) as distinct_skus,
    cast(null as bigint) as distinct_stores,
    cast(null as bigint) as return_lines,
    cast(null as date) as first_purchase_date,
    cast(null as date) as last_purchase_date,
    cast(null as bigint) as recency_days,
    cast(null as bigint) as tenure_days,
    cast(null as double) as avg_order_value,
    cast(null as double) as avg_days_between_orders,
    false as is_repeat_customer,
    cast(null as integer) as recency_score,
    cast(null as integer) as frequency_score,
    cast(null as integer) as monetary_score,
    cast(null as integer) as rfm_cell,
    'Unidentified' as rfm_segment,
    'Unidentified' as lifecycle_stage,
    cast(null as double) as cycles_since_last_order,
    'unknown' as churn_risk_band,
    false as is_at_risk,
    cast(null as double) as annual_order_frequency,
    cast(null as double) as predicted_clv_12m,
    'none' as clv_confidence,
    false as is_vip,
    cast(null as double) as value_percentile,
    cast(null as date) as as_of_date
