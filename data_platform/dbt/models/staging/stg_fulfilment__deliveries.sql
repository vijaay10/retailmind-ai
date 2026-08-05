{{ config(materialized='view', tags=['staging', 'fulfilment']) }}

/*
    Silver: conform outbound shipments.

    Two derivations, and together they remove a bias that is easy to miss and
    reverses the answer.

    The first is the purchase-order rule: an undelivered shipment raised
    yesterday against a three-day promise has *not* failed. Counting it as a
    miss makes a carrier's score deteriorate precisely when volume grows.

    The second is its mirror, and it is the one that matters near the
    observation edge. A shipment still open whose promise date has *passed* is
    not unknown — it is definitively late, and it will only get later. Scoring
    only delivered shipments looks even-handed and is not: in the last few
    days of any dataset the quick deliveries have closed and the slow ones are
    still open, so the on-time rate is computed almost entirely from the
    successes. Measured that way, this estate's worst carrier week appeared as
    its *best* — on-time rose from 87% to 96% while a planted incident was
    running, purely because the failures had not landed yet.

    So the denominator is every shipment whose promise date has come due,
    delivered or not, and only genuinely-in-play shipments are excluded.
*/

with observation as (

    -- The edge of what the warehouse knows. Anything promised after this is
    -- still legitimately in play.
    select max(business_date) as as_of from {{ source('raw', 'fulfilment__deliveries') }}

)

select
    shipment_id,
    order_id,
    store_id,
    carrier,
    business_date,
    promised_date,
    delivered_date,
    delivery_status,

    (delivered_date is null) as is_open,

    -- Overdue and unlanded: late beyond argument.
    (delivered_date is null and promised_date < (select as_of from observation)) as is_overdue,

    -- Judgeable when the shipment has landed *or* its promise has expired.
    -- Anything else is still in play and stays out of every rate.
    (
        delivered_date is not null
        or promised_date < (select as_of from observation)
    ) as is_judgeable,

    case
        when delivered_date is not null then delivered_date <= promised_date
        when promised_date < (select as_of from observation) then false
    end as is_on_time,

    case
        when delivered_date is not null
        then date_diff('day', promised_date, delivered_date)
    end as days_late,

    case
        when delivered_date is not null
        then date_diff('day', business_date, delivered_date)
    end as transit_days,

    current_timestamp as _loaded_at
from {{ source('raw', 'fulfilment__deliveries') }}
