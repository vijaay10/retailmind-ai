{{ config(materialized='table', tags=['dimension', 'core'],
          post_hook="{{ create_index(['supplier_key']) }}") }}

/*
    dim_supplier — Type 1 (DB design §7, schema SE-1).

    Contract terms live here; *performance* against them is measured in the
    supplier mart. Keeping the two apart matters: a contract is what was
    agreed, performance is what happened, and a dimension that mixed them
    would rewrite history every time a vendor had a bad month.
*/

select
    {{ surrogate_key(['supplier_id']) }} as supplier_key,
    supplier_id,
    supplier_name,
    category_focus,
    country,
    payment_terms,
    contract_lead_time_days,
    contract_otif_target
from {{ ref('supplier_master') }}

union all

select
    {{ unknown_member_key() }} as supplier_key,
    'UNKNOWN' as supplier_id,
    'Unknown supplier' as supplier_name,
    'UNKNOWN' as category_focus,
    'UNKNOWN' as country,
    'UNKNOWN' as payment_terms,
    cast(null as bigint) as contract_lead_time_days,
    cast(null as double) as contract_otif_target
