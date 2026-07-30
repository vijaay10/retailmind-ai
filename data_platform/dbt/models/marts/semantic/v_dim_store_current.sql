{{ config(materialized='view', tags=['semantic']) }}

/*
    As-is store view — the current-version counterpart to the as-was default.
    See v_dim_product_current for why this is opt-in.
*/

select * from {{ ref('dim_store') }} where {{ scd2_current_filter() }}
