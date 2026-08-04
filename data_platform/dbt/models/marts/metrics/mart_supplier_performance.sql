{{ config(materialized='table', tags=['mart', 'metrics', 'inventory']) }}

/*
    Supplier scorecard (Analytics §7).

    OTIF is the headline, but the number that actually changes planning is
    **lead-time variability**. A supplier reliably taking 25 days is easy to
    plan around; one averaging 20 with a ±10 spread forces safety stock on
    every SKU they touch, and that carrying cost never appears on their
    invoice.

    Suppliers below the minimum-receipt floor are reported with their counts
    but flagged: scoring a vendor on three deliveries is noise dressed as
    judgement.
*/

with receipts as (

    select
        po.supplier_id,
        s.supplier_name,
        s.country,
        s.contract_lead_time_days,
        s.contract_otif_target,
        po.ordered_qty,
        po.received_qty,
        po.ordered_value,
        po.actual_lead_time_days,
        po.lead_time_variance_days,
        po.is_open,
        po.is_on_time,
        po.is_in_full,
        po.is_otif
    from {{ ref('fct_purchase_orders') }} po
    join {{ ref('dim_supplier') }} s on po.supplier_id = s.supplier_id

),

scored as (

    select
        supplier_id,
        max(supplier_name) as supplier_name,
        max(country) as country,
        max(contract_lead_time_days) as contract_lead_time_days,
        max(contract_otif_target) as contract_otif_target,

        count(*) as po_lines,
        count(*) filter (where is_open) as open_lines,
        count(*) filter (where not is_open) as closed_lines,
        round(sum(ordered_value), 2) as ordered_value,

        -- OTIF and its two halves, kept separate: on-time and in-full fail for
        -- different reasons and need different conversations.
        --
        -- The numerators ship alongside the rates. A caller grouping suppliers
        -- by country or risk band has to recompute the ratio from counts;
        -- averaging five suppliers' OTIF rates would weigh a vendor with 20
        -- lines the same as one with 2,000.
        count(*) filter (where is_otif) as otif_lines,
        count(*) filter (where is_on_time) as on_time_lines,
        count(*) filter (where is_in_full) as in_full_lines,
        sum(received_qty) as received_qty,
        sum(ordered_qty) filter (where not is_open) as closed_ordered_qty,

        round(count(*) filter (where is_otif)::double
              / nullif(count(*) filter (where not is_open), 0), 4) as otif_rate,
        round(count(*) filter (where is_on_time)::double
              / nullif(count(*) filter (where not is_open), 0), 4) as on_time_rate,
        round(count(*) filter (where is_in_full)::double
              / nullif(count(*) filter (where not is_open), 0), 4) as in_full_rate,
        round(sum(received_qty) / nullif(sum(ordered_qty) filter (where not is_open), 0), 4)
            as fill_rate,

        round(avg(actual_lead_time_days), 1) as avg_lead_time_days,
        round(coalesce(stddev_samp(actual_lead_time_days), 0), 2) as lead_time_stddev,
        round(quantile_cont(actual_lead_time_days, 0.9), 1) as p90_lead_time_days,
        round(avg(lead_time_variance_days), 1) as avg_days_late
    from receipts
    group by 1

)

select
    *,

    /*
        Coefficient of variation: spread relative to the mean. This is the
        comparable number across suppliers with different lead times — a ±3 day
        spread is trivial on a 30-day lead time and severe on a 5-day one.
    */
    round(lead_time_stddev / nullif(avg_lead_time_days, 0), 3) as lead_time_cov,

    round(otif_rate - contract_otif_target, 4) as otif_vs_contract,
    round(avg_lead_time_days - contract_lead_time_days, 1) as lead_time_vs_contract,

    /*
        A composite risk band. Deliberately rule-based and legible: a buyer has
        to defend this in a supplier review, and "the model said so" is not a
        defence.
    */
    case
        when closed_lines < 20 then 'insufficient_data'
        when otif_rate < 0.85 or lead_time_stddev > 6 then 'high'
        when otif_rate < 0.93 or lead_time_stddev > 3 then 'medium'
        else 'low'
    end as risk_band,

    (closed_lines >= 20) as meets_evidence_floor
from scored
