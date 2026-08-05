{{ config(materialized='view', tags=['semantic', 'rca']) }}

-- Semantic entry point for mart_rca_weather_effect.

select * from {{ ref('mart_rca_weather_effect') }}
