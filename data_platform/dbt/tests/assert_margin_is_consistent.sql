/*
    Margin must equal net revenue minus COGS, row by row.

    A cheap invariant that catches a whole class of expensive mistakes: a
    changed cost source, a mis-stitched as-was key, or a units/amount mix-up
    all break this identity long before anyone notices the margin rate drifting
    on a dashboard.
*/

select
    sales_key,
    net_amount,
    cogs_amount,
    margin_amount,
    net_amount - cogs_amount as expected_margin
from {{ ref('fct_sales') }}
where cogs_amount is not null
  and abs(margin_amount - (net_amount - cogs_amount)) > 0.01
