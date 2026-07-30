{{ config(materialized='view', tags=['semantic']) }}

/*
    As-is product view: current versions only (DB design §15).

    The default across the warehouse is as-was — facts keep the attribution
    they had at transaction time. This view is the deliberate opt-in for the
    other question ("what category is this SKU in *now*"), so that choice is
    always explicit at the call site rather than an accident of which join
    somebody wrote.
*/

select * from {{ ref('dim_product') }} where {{ scd2_current_filter() }}
