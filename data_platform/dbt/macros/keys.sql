{#
    Surrogate-key generation (DB design §9).

    Keys are **deterministic hashes**, not sequences. That is the whole reason
    a backfill can reproduce byte-identical marts: a sequence would assign
    different integers on every rebuild, and every downstream fact would
    silently re-point. Hashing the natural key means the same input always
    yields the same key, on any machine, in any order.

    Collision risk is the usual trade: 64 bits over millions of members is
    negligible, and the uniqueness test on every dimension would catch it.
#}

{% macro surrogate_key(columns) %}
    {#- Stable integer key from one or more natural-key columns.

        NULLs are coalesced to a sentinel so a missing component yields a
        deterministic key rather than a NULL that would silently drop rows.
        Components are joined with a separator that cannot occur in the data,
        so ('AB','C') and ('A','BC') cannot collide.

        The right shift matters: DuckDB's hash() is UINT64, which overflows a
        signed BIGINT. Shifting by one bit keeps 63 bits — still ~9.2 × 10^18
        of key space — and guarantees a non-negative result, so a generated key
        can never collide with the reserved -1/-2 members below. -#}
    (hash(
        {%- for column in columns %}
        coalesce(cast({{ column }} as varchar), '∅')
        {%- if not loop.last %} || '‖' || {% endif %}
        {%- endfor %}
    ) >> 1)::bigint
{% endmacro %}


{% macro unknown_member_key() %}
    {#- Reserved key for late-arriving or unresolvable dimension members
        (DB §7). Facts are never NULL on a foreign key, which is what makes
        every join inner-join-safe and every SUM trustworthy. -#}
    (-1)::bigint
{% endmacro %}


{% macro not_applicable_key() %}
    {#- Reserved key for "this dimension does not apply" — e.g. promo_key on a
        non-promotional sale. Distinct from UNKNOWN on purpose: one means we
        could not resolve it, the other means there is nothing to resolve. -#}
    (-2)::bigint
{% endmacro %}
