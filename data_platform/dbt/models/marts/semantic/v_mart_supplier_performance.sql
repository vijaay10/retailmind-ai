{{ config(materialized='view', tags=['semantic', 'inventory']) }}

-- Semantic entry point for mart_supplier_performance.

select * from {{ ref('mart_supplier_performance') }}
