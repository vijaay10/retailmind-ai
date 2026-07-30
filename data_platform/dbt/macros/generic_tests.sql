{#
    Generic tests, defined locally rather than pulled from dbt_utils.

    Two of that package's tests are load-bearing here (grain uniqueness and
    numeric ranges), and taking a package dependency for them would mean every
    CI run and every fresh clone needs `dbt deps` and network access. Fifty
    lines of SQL is the cheaper trade.
#}

{% test unique_combination(model, combination_of_columns) %}
    {#- Grain enforcement: the combination must identify exactly one row.

        This is the test that catches join fanout — the failure mode where a
        fact silently doubles because a dimension join matched two versions.
        Every fact and SCD2 dimension declares its grain through this. -#}
    {%- set column_list = combination_of_columns | join(', ') -%}

    select {{ column_list }}, count(*) as occurrences
    from {{ model }}
    group by {{ column_list }}
    having count(*) > 1

{% endtest %}


{% test accepted_range(model, column_name, min_value=none, max_value=none) %}
    {#- Numeric bounds. Nulls pass: absence is a nullability question, tested
        separately by not_null, and conflating the two produces failures that
        do not say what is actually wrong. -#}

    select {{ column_name }}
    from {{ model }}
    where {{ column_name }} is not null
      and (
        false
        {% if min_value is not none %} or {{ column_name }} < {{ min_value }} {% endif %}
        {% if max_value is not none %} or {{ column_name }} > {{ max_value }} {% endif %}
      )

{% endtest %}


{% test scd2_no_overlapping_versions(model, entity_column) %}
    {#- SCD2 integrity (DB design §8): an entity must never have two versions
        valid at the same instant.

        Overlapping windows are the SCD2 failure that corrupts facts silently —
        an as-was join matches both versions and the fact fans out, inflating
        every measure downstream. Worth a dedicated test. -#}

    with versions as (
        select
            {{ entity_column }} as entity,
            valid_from,
            valid_to,
            lead(valid_from) over (
                partition by {{ entity_column }} order by valid_from
            ) as next_valid_from
        from {{ model }}
    )

    select entity, valid_from, valid_to, next_valid_from
    from versions
    where next_valid_from is not null
      and valid_to > next_valid_from

{% endtest %}


{% test scd2_exactly_one_current(model, entity_column) %}
    {#- Every entity has precisely one open version. Zero means history was
        closed without a successor; more than one means the same fanout risk
        as overlapping windows. -#}

    select {{ entity_column }} as entity, count(*) as current_versions
    from {{ model }}
    where is_current
    group by {{ entity_column }}
    having count(*) <> 1

{% endtest %}
