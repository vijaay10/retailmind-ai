{{ config(materialized='view', tags=['semantic']) }}

/*
    Executive scorecard feed (Analytics §10). A 1:1 projection: the mart is
    already at the right grain, and the view exists so the application binds to
    a stable semantic name rather than to a mart it should be free to reshape.
*/

select * from {{ ref('mart_kpi_daily') }}
