{{ config(materialized='view', tags=['semantic', 'inventory']) }}

-- Purchase-order lines for lead-time and supplier analysis.

select * from {{ ref('fct_purchase_orders') }}
