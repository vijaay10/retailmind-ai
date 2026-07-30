{#
    SCD2 helpers (DB design §8).

    Facts join the dimension version that was valid **at transaction time**,
    not the current one. That is the "as-was" contract: a sale made while a
    SKU sat in Outerwear stays attributed to Outerwear forever, even after the
    SKU is recategorized. Reporting as-is is a deliberate opt-in, never the
    accident of a naive join.

    Every fact stitches keys through this one macro so the edge cases — an
    event exactly on a version boundary, an event before the first known
    version — are decided once and tested once, rather than re-derived (and
    re-broken) in each model.
#}

{% macro scd2_current_filter() %}
    is_current = true
{% endmacro %}


{% macro scd2_valid_at(event_time_column, valid_from='valid_from', valid_to='valid_to') %}
    {#- Validity predicate for an as-was join.

        Half-open on purpose: [valid_from, valid_to). An event landing exactly
        on a boundary belongs to the *new* version, so consecutive versions can
        never both match and fan the fact out. -#}
    {{ event_time_column }} >= {{ valid_from }}
    and {{ event_time_column }} < {{ valid_to }}
{% endmacro %}


{% macro scd2_columns() %}
    {#- The standard SCD2 column set, so every dimension looks the same to
        every consumer and to the integrity tests. -#}
    valid_from,
    valid_to,
    is_current,
    version_number
{% endmacro %}
