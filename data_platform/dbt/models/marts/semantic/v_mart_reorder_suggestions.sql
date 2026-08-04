{{ config(materialized='view', tags=['semantic', 'inventory']) }}

-- Semantic entry point for mart_reorder_suggestions.

select * from {{ ref('mart_reorder_suggestions') }}
