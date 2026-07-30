/*
    Every fiscal year must contain exactly 52 or 53 weeks, and every non-final
    week exactly 7 days. A calendar with a short week produces comparisons that
    look like performance changes.

    The most recent year is excluded: it is legitimately partial.
*/

with week_sizes as (
    select fiscal_year, fiscal_week, count(*) as days
    from {{ ref('dim_date') }}
    where fiscal_year < (select max(fiscal_year) from {{ ref('dim_date') }})
    group by 1, 2
)

select * from week_sizes where days <> 7
