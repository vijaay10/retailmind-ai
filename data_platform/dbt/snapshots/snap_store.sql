{% snapshot snap_store %}

{{
    config(
        target_schema='staging',
        unique_key='store_id',
        strategy='check',
        check_cols=['store_name', 'city', 'district', 'region',
                    'store_format', 'sqft_band', 'timezone'],
        invalidate_hard_deletes=True,
    )
}}

/*
    SCD2 history for the store master (DB design §8).

    `opened_date` is deliberately *not* watched: it is immutable business fact
    (Type 0), and watching it would turn a source-side correction into a fake
    reformat event in the history.

    Store attributes that are watched — format, region, sqft band — are exactly
    the ones that change the comparison peer group (Analytics §3: stores are
    only ranked within their cluster). Keeping their history is what lets a
    reformatted store's past stay attributed to what it was at the time.
*/

select
    store_id,
    store_name,
    city,
    district,
    region,
    store_format,
    sqft_band,
    timezone,
    opened_date
from {{ ref('store_master') }}

{% endsnapshot %}
