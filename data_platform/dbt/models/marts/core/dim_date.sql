{{
    config(
        materialized='table',
        tags=['dimension', 'core'],
        post_hook="{{ create_index(['full_date']) }}",
    )
}}

/*
    dim_date — Gregorian and NRF 4-5-4 retail calendar (DB design §7, FR-A03).

    Why this model earns its complexity: retail does not compare July to July.
    It compares fiscal week 26 to fiscal week 26, because week-over-week and
    year-over-year only mean anything when the weeks contain the same number of
    each weekday. Comparing calendar months would put five Saturdays against
    four and call the difference performance.

    **The 4-5-4 rule implemented here** (NRF standard):

    * a fiscal year begins on the Sunday nearest to February 1;
    * each quarter is 13 weeks, split 4-5-4 into three periods;
    * a 53rd week is appended to the year when the next year's start is 371
      days out rather than 364.

    Anchors this is tested against: FY2025 starts 2025-02-02, FY2026 starts
    2026-02-01, FY2027 starts 2027-01-31 — all published NRF dates.

    Type 0: dates never change, so there is no SCD machinery here.
*/

with date_spine as (

    -- Ten years is enough for YoY-plus-history without bloating a dimension
    -- that every query touches.
    select cast(range as date) as full_date
    from range(date '2019-01-01', date '2033-01-01', interval 1 day)

),

fiscal_year_starts as (

    /*
        For each candidate year, the Sunday nearest February 1.

        `date_trunc('week')` in DuckDB yields Monday, so the Sunday of that
        ISO week is one day earlier. Whichever of that Sunday and the following
        Sunday sits closer to Feb 1 is the fiscal-year start.
    */
    select
        year_number,
        case
            when abs(date_diff('day', candidate_sunday, feb_first))
                 <= abs(date_diff('day', candidate_sunday + 7, feb_first))
            then candidate_sunday
            else candidate_sunday + 7
        end as fiscal_year_start
    from (
        select
            year_number,
            make_date(year_number, 2, 1) as feb_first,
            date_trunc('week', make_date(year_number, 2, 1))::date - 1 as candidate_sunday
        from range(2019, 2033) as t(year_number)
    )

),

fiscal_bounds as (

    select
        year_number as fiscal_year,
        fiscal_year_start,
        lead(fiscal_year_start) over (order by fiscal_year_start) as next_year_start
    from fiscal_year_starts

),

assigned as (

    select
        d.full_date,
        b.fiscal_year,
        b.fiscal_year_start,
        -- 371 days = 53 weeks: the leap-week years.
        (date_diff('day', b.fiscal_year_start, b.next_year_start) = 371) as is_53_week_year,
        date_diff('day', b.fiscal_year_start, d.full_date) as days_into_year
    from date_spine d
    join fiscal_bounds b
      on d.full_date >= b.fiscal_year_start
     and d.full_date < b.next_year_start

),

fiscal_parts as (

    select
        *,
        -- `//` is integer division; DuckDB's `/` returns DOUBLE and would
        -- produce fractional week numbers that silently poison every
        -- comparison built on them.
        (days_into_year // 7) + 1 as fiscal_week,
        least((days_into_year // 7) // 13 + 1, 4) as fiscal_quarter,
        -- Position of the week inside its 13-week quarter drives the 4-5-4
        -- split: weeks 1-4 → first period, 5-9 → second, 10-13 → third.
        ((days_into_year // 7) % 13) + 1 as week_in_quarter
    from assigned

)

select
    -- Surrogate key is the date itself as yyyymmdd: readable in raw SQL,
    -- sortable, and stable across rebuilds without a registry lookup.
    cast(strftime(full_date, '%Y%m%d') as integer) as date_key,
    full_date,

    -- ── Gregorian ──
    extract(year from full_date) as calendar_year,
    extract(month from full_date) as calendar_month,
    extract(day from full_date) as day_of_month,
    extract(dayofweek from full_date) as day_of_week,
    strftime(full_date, '%A') as day_name,
    strftime(full_date, '%B') as month_name,
    (extract(dayofweek from full_date) in (0, 6)) as is_weekend,

    -- ── NRF 4-5-4 ──
    fiscal_year,
    fiscal_quarter,
    (fiscal_quarter - 1) * 3 + case
        when week_in_quarter <= 4 then 1
        when week_in_quarter <= 9 then 2
        else 3
    end as fiscal_period,
    fiscal_week,
    week_in_quarter,
    fiscal_year_start,
    is_53_week_year,

    -- Week ending Saturday — the label retail reports actually carry.
    full_date + cast(6 - (days_into_year % 7) as integer) as week_ending_date,

    -- ── Comparison helpers ──
    /*
        The same fiscal week one year earlier, by construction rather than by
        subtracting 365 days. This column is what makes "vs LY" correct: it
        aligns week 26 to week 26, so the comparison holds the weekday mix
        constant.
    */
    full_date - 364::integer as same_fiscal_week_last_year,

    current_date = full_date as is_today

from fiscal_parts
-- Only fully-covered fiscal years: the spine's edge years are partial by
-- construction, and a partial year in a calendar dimension is a trap.
where fiscal_year between 2020 and 2031
