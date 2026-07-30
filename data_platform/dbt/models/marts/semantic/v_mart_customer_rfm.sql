{{ config(materialized='view', tags=['semantic']) }}

-- Semantic entry point for mart_customer_rfm. The application binds to this name; the
-- mart beneath stays free to be reshaped (DB design §15).

select * from {{ ref('mart_customer_rfm') }}
