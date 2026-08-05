{{ config(materialized='view', tags=['semantic', 'rca']) }}

-- Semantic entry point for mart_rca_factor_daily.

select * from {{ ref('mart_rca_factor_daily') }}
