{{ config(materialized='view', tags=['semantic']) }}

-- The scoreboard. Reachable from every forecast display, by design: the
-- trust loop only closes if accuracy is one click from the number.

select * from {{ ref('mart_forecast_accuracy') }}
