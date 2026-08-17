# Service Level Objectives (SLOs)

## Overview

RetailMind defines SLOs for availability, latency, and correctness across three
tiers: API, Data Pipeline, and Database. Each SLO has a target, measurement
window, and error budget.

**Error Budget:** The allowed failure rate. If we promise 99.5% availability,
our error budget is 0.5% (the failures we can afford).

**Burn Rate:** How fast we're consuming the error budget. A burn rate of 1.0
means we're using the budget at exactly the sustainable rate. >1.0 means we're
burning too fast.

## API SLOs

### Availability SLO

**Target:** 99.5% of requests succeed (non-5xx status)

**Measurement Window:** 30 days rolling

**Error Budget:** 0.5% (216 minutes/month downtime)

**Current Performance:** Measured via `api:requests:success_rate:24h`

**Burn Rate Alert:** `api:error_budget:burn_rate:1h > 5.0` for 15 minutes
- Burn rate >5.0 means consuming 5× budget, will exhaust in 6 days

**Recording Rules:**
```promql
api:requests:success_rate:5m
api:requests:success_rate:1h
api:requests:success_rate:24h
api:error_budget:burn_rate:5m
api:error_budget:burn_rate:1h
```

**Why 99.5% not 99.9%:**
- Realistic for early-stage product
- Allows 3.6 hours/month planned maintenance
- Focuses effort on features over ultra-high availability

### Latency SLO

**Target:** 95% of requests complete in <2 seconds

**Measurement Window:** 24 hours rolling

**Rationale:** Investigation sweeps 9 dimensions (legitimately slow), but most
queries should be fast

**Current Performance:** Measured via `api:latency:p95:1h`

**Recording Rules:**
```promql
api:latency:p95:5m
api:latency:p95:1h
api:latency:sli:5m  # Percentage meeting <2s target
```

**Excluded from SLO:**
- `/api/v1/rca/investigate` (expected slow, multi-engine sweep)
- `/api/v1/reporting/*` (document generation)

## Data Pipeline SLOs

### Task Success SLO

**Target:** 99% of background tasks succeed

**Measurement Window:** 7 days rolling

**Error Budget:** 1% (10,080 minutes/week)

**Current Performance:** Measured via `pipeline:tasks:success_rate:24h`

**Recording Rules:**
```promql
pipeline:tasks:success_rate:5m
pipeline:tasks:success_rate:1h
pipeline:tasks:success_rate:24h
pipeline:error_budget:burn_rate:1h
```

**Why 99% not 99.9%:**
- Tasks are retriable (idempotent sweeps)
- Some failures are data-dependent (empty windows, missing dims)
- 1% budget allows for ~100 failures per 10,000 tasks

### Freshness SLO

**Target:** Detection sweep completes every 2 hours

**Measurement:** `time() - retailmind_last_sweep_timestamp_seconds`

**Alert:** `pipeline:sweep:age_seconds > 7200` for 10 minutes

**Rationale:** Alerts that don't fire are invisible; staleness is same as
downtime for detection

**Recording Rule:**
```promql
pipeline:sweep:age_seconds
```

## Database SLOs

### Availability SLO

**Target:** Database reachable 99.9% of the time

**Measurement Window:** 7 days rolling

**Error Budget:** 0.1% (10 minutes/week)

**Current Performance:** Measured via `database:availability:5m`

**Recording Rule:**
```promql
database:availability:5m
```

**Why 99.9% (higher than API):**
- Database is single point of failure
- Postgres is more stable than application code
- Database downtime = total outage (API cannot serve)

### Connection Pool SLO

**Target:** Connection pool utilization <80%

**Measurement:** Active connections / max_connections

**Alert:** `database:connections:utilization > 0.80` for 5 minutes

**Rationale:** High utilization indicates contention; leaves no headroom for
spikes

**Recording Rule:**
```promql
database:connections:utilization
```

## System-Wide Health

### Overall Availability

**Target:** All critical services (api, postgres, worker, beat) up

**Measurement:** `system:services:all_up`

**Recording Rule:**
```promql
system:services:all_up  # 1 if all up, 0 if any down
```

### Critical Alerts

**Target:** Zero critical alerts firing

**Measurement:** `system:alerts:critical_firing`

**Recording Rule:**
```promql
system:alerts:critical_firing
```

## SLO Dashboard Panels

### Availability Panel

Shows error budget consumption and burn rate:

```promql
# Current availability (30-day)
api:requests:success_rate:24h

# Error budget remaining
1 - ((1 - api:requests:success_rate:24h) / 0.005)

# Burn rate (5× = critical)
api:error_budget:burn_rate:1h
```

### Latency Panel

Shows P95 latency and SLI compliance:

```promql
# P95 latency
api:latency:p95:1h

# Percentage meeting <2s target
api:latency:sli:5m * 100
```

### Pipeline Health Panel

Shows task success and sweep freshness:

```promql
# Task success rate
pipeline:tasks:success_rate:1h

# Detection sweep age (should be <2h)
pipeline:sweep:age_seconds / 3600  # Convert to hours
```

## Error Budget Alerts

### Fast Burn (Critical)

Consuming error budget at 5× rate — will exhaust in 6 days:

```yaml
- alert: ErrorBudgetFastBurn
  expr: api:error_budget:burn_rate:1h > 5.0
  for: 15m
  labels:
    severity: critical
  annotations:
    summary: "Error budget burning 5× too fast"
    action: "Investigate recent deployments and error logs"
```

### Slow Burn (Warning)

Consuming error budget at 2× rate — will exhaust in 15 days:

```yaml
- alert: ErrorBudgetSlowBurn
  expr: api:error_budget:burn_rate:1h > 2.0
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "Error budget burning 2× too fast"
    action: "Monitor for trend, investigate if sustained"
```

### Budget Exhausted (Critical)

Error budget completely consumed:

```yaml
- alert: ErrorBudgetExhausted
  expr: api:requests:success_rate:24h < 0.995
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "SLO violated: availability below 99.5%"
    action: "Declare incident, halt non-critical deployments"
```

## SLO Review Schedule

**Weekly:** Review burn rate trends, adjust if needed
**Monthly:** Review SLO targets vs actual performance
**Quarterly:** Adjust targets based on product maturity

## Non-SLO Metrics

Not every metric is an SLO. These are monitored but don't have error budgets:

- LLM call volume (informational)
- Recommendation count (informational)
- Forecast accuracy (WAPE/MASE tracked, but no hard SLO yet)
- Cache hit rate (performance, not reliability)

## SLO Violations: What to Do

1. **Check burn rate:** Is this a spike or sustained?
2. **Identify cause:** Recent deployment? External dependency? Database?
3. **Decide:** Fix forward or rollback?
4. **Communicate:** Update status page, notify affected users
5. **Postmortem:** Write blameless postmortem, improve alerts

## SLO Calculation Examples

### Example 1: API Availability

**Goal:** 99.5% over 30 days
**Actual:** 99.3% over last 24 hours

**Error Budget:**
- Allowed failures: 0.5%
- Actual failures: 0.7%
- Budget consumed: 0.7 / 0.5 = 140% (exhausted)

**Burn Rate:**
- Normal burn: 0.5% / 30 days = 0.0167%/day
- Actual burn: 0.7%/day
- Burn rate: 0.7 / 0.0167 = 42× (critical!)

**Action:** Immediate incident, halt deployments, investigate cause

### Example 2: Task Success

**Goal:** 99% over 7 days
**Actual:** 99.5% over last 24 hours

**Error Budget:**
- Allowed failures: 1%
- Actual failures: 0.5%
- Budget consumed: 0.5 / 1 = 50% (healthy)

**Burn Rate:**
- Normal burn: 1% / 7 days = 0.14%/day
- Actual burn: 0.5%/day
- Burn rate: 0.5 / 0.14 = 3.6× (warning)

**Action:** Monitor for trend, investigate if sustained for >6 hours

## References

- **Google SRE Book:** Error budgets, burn rates, SLO methodology
- **Grafana SLO Tracking:** Dashboard examples and PromQL patterns
- **Prometheus Recording Rules:** Pre-computation for fast dashboards

---

**Last Updated:** 2026-08-14
**Owner:** Infrastructure Team
**Review Schedule:** Monthly
