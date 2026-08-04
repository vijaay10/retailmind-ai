{{ config(materialized='view', tags=['semantic', 'inventory']) }}

-- Semantic entry point for mart_product_abc.

select * from {{ ref('mart_product_abc') }}
