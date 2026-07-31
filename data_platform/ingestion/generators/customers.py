"""A synthetic customer base with realistic heterogeneity.

Sampling buyers uniformly at random produces a population where everybody buys
constantly — every customer looks Loyal, retention sits at 100%, and every
customer-intelligence surface is degenerate. Segmentation only means something
when the population genuinely differs.

So this module builds a *stable* population once, deterministically, and each
day's generator samples from it. Three properties make the resulting analytics
real:

* **Skewed frequency.** Most customers buy rarely; a small tail buys often.
  This is what produces a VIP decile worth detecting and an RFM grid with
  spread instead of a single hot cell.
* **Staggered acquisition.** Customers appear over time rather than all
  existing on day one, which is what gives retention cohorts something to
  compare.
* **Lapsing.** A share of customers stop buying partway through, which is what
  churn risk is supposed to find. Without them, "at risk" is untestable.

The population is derived from one seed and never from the day being
generated, so a customer behaves consistently across the whole history — the
property that makes cohort and cadence analysis coherent.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

#: Behavioural tiers and their share of the base. Roughly a power law: the
#: long tail of one-time buyers is the majority of any real retail file.
TIERS: tuple[tuple[str, float, float, float], ...] = (
    # (name, share of base, min daily purchase probability, max)
    ("one_time", 0.42, 0.000, 0.004),
    ("occasional", 0.33, 0.010, 0.035),
    ("regular", 0.18, 0.045, 0.110),
    ("loyal", 0.07, 0.130, 0.280),
)


@dataclass(frozen=True, slots=True)
class Customer:
    """One synthetic customer's stable behavioural profile."""

    customer_id: str
    tier: str
    daily_purchase_probability: float
    acquired_on: date
    lapsed_on: date | None
    """When they stopped buying, or None if still active."""

    def is_active_on(self, day: date) -> bool:
        if day < self.acquired_on:
            return False
        return self.lapsed_on is None or day < self.lapsed_on


@lru_cache(maxsize=8)
def build_population(
    size: int,
    first_day: date,
    last_day: date,
    seed: int = 4242,
) -> tuple[Customer, ...]:
    """Construct the customer base for a history window.

    Cached on its arguments so every day of a multi-day generation run sees the
    identical population — that consistency is the whole point, and rebuilding
    per day would silently randomise each customer's tier.
    """
    rng = random.Random(seed)  # noqa: S311 — determinism is the requirement
    span_days = max((last_day - first_day).days, 1)

    population: list[Customer] = []
    index = 0

    for tier_name, share, low, high in TIERS:
        count = int(size * share)
        for _ in range(count):
            index += 1

            # Acquisition is skewed toward the start so early cohorts have
            # enough observable weeks to produce a retention curve.
            acquisition_offset = int(rng.triangular(0, span_days, span_days * 0.2))
            acquired = first_day + timedelta(days=acquisition_offset)

            # A minority lapse partway through — the population churn risk is
            # meant to detect. One-time buyers lapse almost immediately by
            # definition of their tier.
            lapsed: date | None = None
            lapse_chance = {"one_time": 0.75, "occasional": 0.30, "regular": 0.12}.get(
                tier_name, 0.04
            )
            if rng.random() < lapse_chance:
                remaining = (last_day - acquired).days
                if remaining > 3:
                    lapsed = acquired + timedelta(days=rng.randint(2, remaining))

            population.append(
                Customer(
                    customer_id=f"CU-{index:05d}",
                    tier=tier_name,
                    daily_purchase_probability=rng.uniform(low, high),
                    acquired_on=acquired,
                    lapsed_on=lapsed,
                )
            )

    return tuple(population)


def buyers_for_day(
    population: tuple[Customer, ...],
    day: date,
    *,
    seed: int,
    max_buyers: int,
) -> list[str]:
    """Which customers shop on ``day``.

    Each active customer is sampled against their own purchase probability, so
    a loyal customer appears often and a one-time buyer essentially once. The
    result is truncated to ``max_buyers`` because the caller has a fixed number
    of order lines to distribute.
    """
    rng = random.Random(seed)  # noqa: S311
    shopping = [
        customer.customer_id
        for customer in population
        if customer.is_active_on(day) and rng.random() < customer.daily_purchase_probability
    ]
    rng.shuffle(shopping)
    return shopping[:max_buyers]
