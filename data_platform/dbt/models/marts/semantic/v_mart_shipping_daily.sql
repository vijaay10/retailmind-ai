{{ config(materialized='view', tags=['semantic', 'shipping']) }}

-- Semantic entry point for mart_shipping_daily.

select * from {{ ref('mart_shipping_daily') }}
