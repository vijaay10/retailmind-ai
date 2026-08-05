{{ config(materialized='table', tags=['mart', 'metrics']) }}

/*
    Carrier performance by day and region.

    Split out from the RCA factor table because a carrier scorecard is a
    surface in its own right — the factor table aggregates across carriers to
    keep one row per region-day, which is exactly the wrong grain for the
    question "which carrier is the problem".

    Open shipments are excluded from every rate for the same reason they are
    in the purchase-order feed: a shipment still moving has not yet had the
    chance to be late.
*/

select
    st.region,
    d.carrier,
    d.business_date,
    cast(strftime(d.business_date, '%Y%m%d') as integer) as date_key,

    count(*) as shipments,
    count(*) filter (where d.is_open) as shipments_in_transit,
    count(*) filter (where d.is_judgeable) as shipments_closed,
    count(*) filter (where d.is_on_time) as shipments_on_time,

    round(
        count(*) filter (where d.is_on_time)::double
        / nullif(count(*) filter (where d.is_judgeable), 0), 4
    ) as on_time_rate,

    round(avg(d.transit_days) filter (where not d.is_open), 2) as avg_transit_days,
    round(avg(d.days_late) filter (where d.days_late > 0), 2) as avg_days_late,
    max(d.days_late) as worst_days_late
from {{ ref('stg_fulfilment__deliveries') }} d
join {{ ref('dim_store') }} st on d.store_id = st.store_id and st.is_current
group by 1, 2, 3, 4
