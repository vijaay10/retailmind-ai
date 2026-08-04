{{ config(materialized='view', tags=['semantic', 'inventory']) }}

-- Semantic entry point for mart_warehouse_health.

select * from {{ ref('mart_warehouse_health') }}
