{{ config(materialized='view', tags=['semantic', 'customer']) }}

-- Semantic entry point for mart_customer_cohorts. Every customer surface is
-- read through these views, which carry the privacy floor as a column so
-- suppression is a decision the consumer must make explicitly.

select * from {{ ref('mart_customer_cohorts') }}
