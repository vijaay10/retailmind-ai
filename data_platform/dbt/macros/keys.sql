{#
    Surrogate-key generation (DB design §9).

    Keys are **deterministic hashes**, not sequences. That is the whole reason
    a backfill can reproduce byte-identical marts: a sequence would assign
    different integers on every rebuild, and every downstream fact would
    silently re-point. Hashing the natural key means the same input always
    yields the same key, on any machine, in any order.

    Collision risk is the usual trade — but only for a hash that actually
    mixes. See the note on md5_number below: this used DuckDB's built-in
    hash() and that assumption did not hold.
#}

{% macro surrogate_key(columns) %}
    {#- Stable integer key from one or more natural-key columns.

        NULLs are coalesced to a sentinel so a missing component yields a
        deterministic key rather than a NULL that would silently drop rows.
        Components are joined with a separator that cannot occur in the data,
        so ('AB','C') and ('A','BC') cannot collide.

        **Why md5_number and not hash().** This macro used `hash(...) >> 1`,
        on the usual reasoning that 63 bits over millions of members makes
        collisions negligible. That reasoning assumes the hash distributes
        structured input well, and DuckDB's does not. On 75,200 real sales
        lines it produced 64 colliding keys — roughly ten orders of magnitude
        above the birthday estimate — and every collision paired a store
        difference against a compensating date difference:

            POS-S2006-20260621-0019  ⟶  same hash as
            POS-S2116-20260721-0012

        Positional changes cancel, so keys that differ in exactly the fields a
        retail natural key differs in are the ones most likely to collide.
        That is the worst possible failure shape here: fct_sales is
        incremental on `unique_key='sales_key'`, so two colliding lines do not
        raise an error — the merge silently overwrites one real sale with
        another, and revenue quietly goes missing.

        md5_number is a 128-bit cryptographic digest returned as UHUGEINT.
        Taking it modulo 2^63−1 therefore lands in [0, 2^63−2]: uniformly
        distributed, and *provably* non-negative, so a generated key can never
        collide with the reserved -1/-2 members below. Same determinism, same
        reproducible backfills, no dependence on the hash that failed. -#}
    (md5_number(
        {%- for column in columns %}
        coalesce(cast({{ column }} as varchar), '∅')
        {%- if not loop.last %} || '‖' || {% endif %}
        {%- endfor %}
    ) % 9223372036854775807)::bigint
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
