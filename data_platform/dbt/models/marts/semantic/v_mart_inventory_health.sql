{{ config(materialized='view', tags=['semantic', 'inventory']) }}

-- Semantic entry point for mart_inventory_health.

select * from {{ ref('mart_inventory_health') }}
