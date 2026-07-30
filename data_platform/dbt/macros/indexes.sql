{#
    Index management (DB design §12–13).

    An honest note on what indexes do here. DuckDB is columnar: range scans on
    the date key are served by min/max zone maps over row groups, and adding a
    b-tree for them would be pure overhead. What indexes *do* help is
    equality lookups — resolving one SKU, one store, one order — which is
    exactly the shape of drill-down and evidence-link queries.

    So: index the point-lookup columns, leave the scan columns to the storage
    layout. On the Snowflake profile these become clustering keys instead
    (DB §16), which is why the choice lives in a macro rather than inline SQL.

    Indexes here are never UNIQUE. Analytical stores declare constraints
    informationally and enforce them in the test layer (DB §11) — a real
    unique index would also fight `delete+insert` on incremental models,
    where the insert can land before the delete. Grain is guaranteed by the
    `unique` and `unique_combination` tests, which fail the build rather
    than failing the load at 2 a.m.
#}

{% macro create_index(columns, unique=False) %}
    {%- set index_name = 'ix_' ~ this.identifier ~ '_' ~ columns | join('_') -%}
    create {% if unique %}unique{% endif %} index if not exists {{ index_name }}
        on {{ this }} ({{ columns | join(', ') }})
{% endmacro %}


{% macro index_point_lookups(columns) %}
    {#- Post-hook helper: one index per equality-lookup column. -#}
    {%- for column in columns %}
        {{ create_index([column]) }};
    {%- endfor %}
{% endmacro %}
