{{ config(materialized='view', tags=['semantic']) }}

/*
    Customer dimension without the join key.

    `customer_id` is pseudonymous, but exposing it invites building a profile
    per person. Analytics reads segments and behaviour; nothing in the product
    needs to point at an individual, so the identifier does not cross this
    boundary (DB design §33).
*/

select
    customer_key,
    order_count,
    lifetime_value,
    lifetime_units,
    distinct_skus,
    first_purchase_date,
    last_purchase_date,
    recency_days,
    avg_order_value,
    is_repeat_customer,
    recency_score,
    frequency_score,
    monetary_score,
    rfm_segment,
    as_of_date
from {{ ref('dim_customer') }}
