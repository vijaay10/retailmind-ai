{% snapshot snap_product %}

{{
    config(
        target_schema='staging',
        unique_key='sku',
        strategy='check',
        check_cols=['product_name', 'subcategory', 'category', 'department',
                    'brand', 'unit_cost', 'list_price', 'status'],
        invalidate_hard_deletes=True,
    )
}}

/*
    SCD2 history for the product master (DB design §8).

    `check` strategy rather than `timestamp`: the master feed carries no
    reliable updated_at, and inventing one from load time would create a new
    version on every run. Comparing the watched columns means a version appears
    when something actually changed — which is the only signal worth keeping
    history for.

    `invalidate_hard_deletes` closes the validity window when a SKU disappears
    from the feed, so a delisted product stops being "current" without its
    history being rewritten.

    Every column here is watched. That is deliberate for a master this small;
    on a wide dimension you would watch only the attributes whose history the
    business actually asks about, because each watched column is a reason to
    mint a version.
*/

select
    sku,
    product_name,
    subcategory,
    category,
    department,
    brand,
    unit_cost,
    list_price,
    status
from {{ ref('product_master') }}

{% endsnapshot %}
