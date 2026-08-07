"""Rules the test suite itself has to keep.

The integration suites now share two warehouse builds instead of building ten.
That is a ~20-minute saving and it rests on assumptions no individual test can
see, so they are asserted here — in a unit test, where a violation is caught in
seconds rather than in a thirty-minute job.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
INTEGRATION = ROOT / "backend" / "tests" / "integration"
SUITES = sorted(path for path in INTEGRATION.glob("test_*.py"))


def test_there_are_integration_suites_to_check() -> None:
    """A guard whose subject has moved is a guard that passes forever."""
    assert len(SUITES) >= 10


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.name)
def test_no_suite_builds_its_own_warehouse(path: Path) -> None:
    """Ten private builds of the same star schema cost half an hour, and a
    thirty-minute integration job is one that never runs on a pull request.

    The migration and dbt-model suites are exempt: the first tests migrating a
    database from empty, and the second owns the warehouse build itself.
    """
    if path.name in {"test_migrations.py"}:
        return

    source = path.read_text()
    assert "dbt" not in source or "warehouse" in source, path.name
    assert "IngestionPipeline" not in source, (
        f"{path.name} builds its own warehouse; ask for the `api` or `deep_api` "
        "fixture instead — see tests/integration/warehouse.py"
    )


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.name)
def test_no_suite_keeps_a_private_copy_of_the_user_map(path: Path) -> None:
    """Every module used to carry its own USERS dict, and three of them
    disagreed about which demo user held which role — so a test asserting "a
    regional manager cannot see this" was quietly signing in as marketing."""
    source = path.read_text()
    assert not re.search(r"^USERS\s*=\s*\{", source, re.M), (
        f"{path.name} defines its own user map; import it from warehouse.py"
    )


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.name)
def test_every_warehouse_connection_is_read_only(path: Path) -> None:
    """Sharing a warehouse across suites is only safe because they read.

    Checked as "the connection is opened read-only" rather than "the file
    contains no write statements": the NLQ suite deliberately *attempts* a
    write to prove the connection refuses it, and a grep-for-INSERT rule would
    flag the test that proves the property it is trying to protect.
    """
    source = path.read_text()
    # DuckDB connections only: the Postgres engine's `.connect()` is a
    # different thing, and those suites *do* write — to their own database.
    # Read past the call rather than to the first bracket: `connect(str(x))`
    # closes a nested call first, and a non-greedy match would stop there and
    # miss the keyword it is looking for.
    for match in re.finditer(r"duckdb\.connect\(", source):
        window = source[match.end() : match.end() + 140]
        assert "read_only=True" in window, (
            f"{path.name} opens the shared warehouse for writing: "
            f"duckdb.connect({window.splitlines()[0][:60]}"
        )


def test_the_two_shapes_stay_small_enough_to_be_worth_sharing() -> None:
    """The point of a shared build is that it is cheap. A shape that grows
    without anyone noticing turns one slow build into one slower build."""
    from tests.integration import warehouse

    assert warehouse.ESTATE.days <= 70
    assert warehouse.ESTATE.stores <= 12
    assert warehouse.DEEP_HISTORY.days <= 160
    # Deep history buys folds, not breadth — a wide estate over 140 days is
    # four times the rows and buys nothing the backtest uses.
    assert warehouse.DEEP_HISTORY.stores <= 4


def test_the_user_map_matches_the_seeded_tenant() -> None:
    """If the seed adds or renames a user, the shared map must follow — every
    integration suite signs in through it."""
    from app.infrastructure.db.seeds.sample import USERS as SEEDED
    from tests.integration import warehouse

    seeded = {row["email"] for row in SEEDED}
    assert set(warehouse.USERS.values()) <= seeded, "the map names a user the seed does not create"

    by_role = {row["role_key"]: row["email"] for row in SEEDED}
    for role, email in warehouse.USERS.items():
        if role in by_role:
            assert by_role[role] == email, f"'{role}' points at the wrong user"


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.name)
def test_no_suite_signs_in_as_an_ambiguous_role(path: Path) -> None:
    """Every sign-in must name a role the seed actually grants.

    "manager" reads well and says nothing about which permissions the test
    expects. One suite used it to mean marketing and another to mean regional
    manager, so an assertion that "this role cannot see inventory" passed while
    signing in as somebody who could.
    """
    from tests.integration import warehouse

    for role in re.findall(r"auth_headers\([a-z_]+,\s*\"([a-z_]+)\"\)", path.read_text()):
        assert role in warehouse.USERS, (
            f"{path.name} signs in as '{role}', which is not a seeded role"
        )


def test_a_suite_that_locks_an_account_unlocks_it() -> None:
    """Lockout is derived from the `auth_event` ledger, so rows left behind
    leave the account locked for every later suite.

    This dependency was invisible while each suite spent three minutes building
    its own warehouse — the lockout window expired during the build. Sharing
    the warehouse removed the delay and the tests started failing three suites
    away from the cause. A test that only passes because the suite is slow is a
    test waiting to fail.
    """
    source = (INTEGRATION / "test_auth_lockout.py").read_text()
    assert "DELETE FROM auth_event" in source, "the lockout suite must unlock what it locks"
    assert "autouse=True" in source, "cleanup must run even when a test fails part-way"
