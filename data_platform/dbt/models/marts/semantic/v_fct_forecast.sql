{{ config(materialized='view', tags=['semantic']) }}

-- Forecast series for the dashboard and the planning workflow. Every consumer
-- reads intervals alongside points: a bare-point forecast is a forbidden
-- state in this product (UX spec §S7).

select * from {{ ref('fct_forecast') }}
