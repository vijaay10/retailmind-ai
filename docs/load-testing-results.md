# Load Testing Results

**Test Date:** 2026-08-15
**Test Tool:** k6
**Test Configuration:** 50 VU sustained load
**Test Duration:** 10 minutes (1m warmup + 3m ramp + 5m sustain + 1m cooldown)

---

## Test Status

**❌ NOT EXECUTED - k6 not installed**

### Prerequisites Not Met

```bash
# Check k6 installation
which k6
# Result: k6 not found

# Installation required (macOS):
brew install k6

# Or (Linux):
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
  | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update
sudo apt-get install k6
```

---

## How to Run Load Tests

### 1. Prepare Environment

```bash
# Start the full stack (if not already running)
cd /Users/vijaays/retailmind-ai
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml up -d

# Wait for services to be healthy
docker ps --filter "health=healthy"

# Create test user if needed
# (Use UI at http://localhost:18080 or API to create admin user)
```

### 2. Configure Test Parameters

```bash
# Set environment variables for test — these match the real seeded demo
# tenant (backend/app/infrastructure/db/seeds/sample.py); substitute your
# own tenant's credentials for anything other than the demo
export BASE_URL='https://localhost:18443'
export TEST_USER='priya@northwind.example'
export TEST_PASSWORD='ChangeMe-Demo1!'

# Or modify the test file directly:
# Edit tests/load/api-load-test.js lines 55-57
```

### 3. Run Tests

```bash
# Run with default settings (50 VUs, 10 min)
k6 run tests/load/api-load-test.js

# Run with custom VUs
k6 run --vus 100 --duration 5m tests/load/api-load-test.js

# Run with HTML report
k6 run --out html=load-test-results.html tests/load/api-load-test.js
```

---

## Expected Results

Based on SLO targets defined in `docs/slos.md`:

### Availability SLO
- **Target:** 99.5% success rate (non-5xx)
- **Error Budget:** 0.5% (216 min/month)
- **Threshold:** `http_req_failed < 0.01`

**Expected:**
```
✓ http_req_failed..................: <1.00%
✓ checks..........................: >99.00%
```

### Latency SLO
- **Target:** P95 < 2s for most endpoints
- **Exclusions:** `/api/v1/rca/investigate` (multi-engine sweep, expected slow)

**Expected:**
```
Dashboard endpoint:
✓ http_req_duration{endpoint:dashboard}..........: p(95)<2000ms

Recommendations endpoint:
✓ http_req_duration{endpoint:recommendations}....: p(95)<2000ms

Investigation endpoint (excluded from SLO):
  http_req_duration{endpoint:investigation}......: p(95)<5000ms (informational)
```

### Throughput
- **Expected:** ~500-800 requests/second sustained (50 VUs)
- **Request mix:**
  - 100% Dashboard requests
  - 30% Recommendations requests
  - 10% Investigation requests
  - 20% Forecast requests
  - 15% Analytics requests

### Resource Utilization

**Expected Metrics (from Prometheus):**
```promql
# API response time
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{job="api"}[5m]))
# Expected: <2.0 seconds

# Error rate
rate(http_requests_total{job="api",status=~"5.."}[5m]) / rate(http_requests_total{job="api"}[5m])
# Expected: <0.01 (1%)

# Request rate
rate(http_requests_total{job="api"}[5m])
# Expected: 8-13 req/sec/VU * 50 VUs = 400-650 req/sec
```

**Container Resource Usage:**
```bash
# During load test, monitor:
docker stats rmprod-api-1 rmprod-postgres-1 rmprod-worker-1

# Expected API container:
# CPU: 150-180% (1.5-1.8 cores out of 2.0 limit)
# Memory: 800MB-1.2GB (out of 1.5GB limit)

# Expected Postgres container:
# CPU: 80-120% (0.8-1.2 cores out of 2.0 limit)
# Memory: 600MB-900MB (out of 2.0GB limit)
```

---

## Test Scenarios

The load test script (`tests/load/api-load-test.js`) simulates realistic user behavior:

### Scenario 1: Dashboard (100% of users)
```javascript
GET /api/v1/dashboard/executive
Expected: 200 OK, <500ms p95
```

### Scenario 2: Recommendations (30% of users)
```javascript
GET /api/v1/recommendations?limit=20
Expected: 200 OK, <1s p95
```

### Scenario 3: Investigation (10% of users, heavy)
```javascript
GET /api/v1/rca/investigate?metric=net_revenue&current_start=2026-08-07&current_end=2026-08-13
Expected: 200 OK, <5s p95 (multi-dimensional sweep)
```

### Scenario 4: Forecasts (20% of users)
```javascript
GET /api/v1/forecasts?target=net_revenue&horizon=28
Expected: 200 or 404, <2s p95
```

### Scenario 5: Analytics (15% of users)
```javascript
POST /api/v1/analytics/query
Body: {"question": "What was revenue last week?"}
Expected: 200 OK, <3s p95
```

---

## Thresholds Defined

From `tests/load/api-load-test.js`:

```javascript
thresholds: {
  // SLO: 95% of requests < 2s
  'http_req_duration{endpoint:dashboard}': ['p(95)<2000'],
  'http_req_duration{endpoint:investigation}': ['p(95)<5000'],
  'http_req_duration{endpoint:recommendations}': ['p(95)<2000'],

  // SLO: Error rate < 1%
  'http_req_failed': ['rate<0.01'],
  'errors': ['rate<0.01'],

  // Success rate > 99%
  'checks': ['rate>0.99'],
}
```

**Test PASSES if:**
- All thresholds met
- No crashes or OOM kills
- Prometheus shows error rate < 1%

**Test FAILS if:**
- Any threshold violated
- Container restarts during test
- Database connection pool exhausted

---

## What Was NOT Tested

### Cannot Test Without k6
1. **Sustained load performance** - Need k6 installed
2. **Actual latency percentiles** - Need load generator
3. **Error rates under load** - Need concurrent requests
4. **Resource utilization curves** - Need sustained traffic
5. **Rate limit effectiveness** - Need high request volume

### Can Test Manually
```bash
# Basic API health (verified ✅)
curl -s -k https://localhost:18443/healthz
# Result: 200 OK

# Single request latency (sample only, not load test)
time curl -s -k -H "Authorization: Bearer $TOKEN" https://localhost:18443/api/v1/dashboard/executive
# Result: Not tested (would need valid token)
```

---

## Baseline Performance (Estimated)

Based on container resource limits and similar FastAPI applications:

### Expected Performance
- **Dashboard endpoint:** ~50-100ms p50, ~200-500ms p95
- **Recommendations:** ~100-300ms p50, ~500-1000ms p95
- **Investigation:** ~2-4s p50, ~4-8s p95 (multi-engine)
- **Throughput:** ~500-800 req/sec (50 VUs, mixed scenarios)
- **Database connections:** 20-40 concurrent (out of 200 max)

### Bottlenecks (Predicted)
1. **Investigation endpoint** - Sweeps 9 dimensions, expected slow
2. **Database queries** - Complex joins for analytics
3. **LLM calls** - If enabled, would add 1-2s per request

**Note:** These are estimates. Actual results require running k6 load tests.

---

## Monitoring During Load Tests

### Prometheus Queries

```promql
# Request rate
rate(http_requests_total{job="api"}[1m])

# Error rate
rate(http_requests_total{job="api",status=~"5.."}[1m])

# P95 latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{job="api"}[1m]))

# Database connections
pg_stat_activity_count

# Rate limit hits
rate(http_requests_total{job="api",status="429"}[1m])
```

### Grafana Dashboards

Use the API Dashboard (`infra/monitoring/grafana/provisioning/dashboards/api-dashboard.json`):
- Request rate panel
- Error rate panel
- Latency (p50/p95/p99) panel
- Top endpoints panel

---

## Load Test Execution Checklist

- [ ] k6 installed (`brew install k6` or apt)
- [ ] Full stack running (`docker compose up -d`)
- [ ] Test user created with known credentials
- [ ] Environment variables set (BASE_URL, TEST_USER, TEST_PASSWORD)
- [ ] Grafana dashboard open for monitoring
- [ ] Prometheus accessible for queries
- [ ] Run test: `k6 run tests/load/api-load-test.js`
- [ ] Capture results: `k6 run --out html=report.html tests/load/api-load-test.js`
- [ ] Verify SLO thresholds met
- [ ] Check for container restarts (`docker ps -a`)
- [ ] Review Prometheus for error budget burn rate
- [ ] Document actual results in this file

---

## Results (When Available)

### Run 1: [Date]

**Configuration:**
- VUs: 50
- Duration: 10 minutes
- Target: https://localhost:18443

**Results:**
```
[Paste k6 output here]

     ✓ dashboard: status 200
     ✓ dashboard: has revenue
     ...

     checks.........................: XX.XX% ✓ XXXX      ✗ XX
     data_received..................: XX MB  XX kB/s
     data_sent......................: XX MB  XX kB/s
     http_req_blocked...............: avg=XXms  p(95)=XXms
     http_req_duration..............: avg=XXms  p(95)=XXms
     http_req_failed................: XX.XX%
     http_reqs......................: XXXX   XX/s
     iteration_duration.............: avg=XXs
```

**SLO Compliance:**
- [ ] Availability: XX.X% (target: 99.5%)
- [ ] Latency P95: XXXms (target: <2000ms)
- [ ] Error rate: X.XX% (target: <1%)

**Observations:**
[Add observations about bottlenecks, errors, resource usage]

**Action Items:**
[List any performance improvements needed]

---

## Recommendations

### Before Production
1. **Install k6 and run load tests** - Critical for validating SLOs
2. **Test with production-like data** - Current test uses admin user
3. **Run multi-hour soak test** - Verify no memory leaks
4. **Test rate limiting** - Verify 100/200 req/min limits work

### Performance Tuning
1. Cache dashboard queries (15-second TTL)
2. Add database query indexes for frequent analytics queries
3. Pre-compute investigation results for common metrics
4. Consider read replicas if database becomes bottleneck

### Monitoring
1. Set up alerting based on load test thresholds
2. Monitor error budget burn rate
3. Track P95 latency trends over time
4. Alert on sustained >80% resource utilization

---

## Conclusion

**Status:** ⚠️ Load testing NOT performed (k6 not installed)

**What Was Done:**
- ✅ Load test script created (`tests/load/api-load-test.js`)
- ✅ SLO thresholds defined
- ✅ Test scenarios documented
- ✅ Expected results documented
- ✅ Monitoring queries prepared

**What Is Needed:**
- ❌ Install k6 load testing tool
- ❌ Execute load tests and capture actual results
- ❌ Verify SLO compliance under load
- ❌ Tune performance based on actual bottlenecks

**Deployment Risk:**
**MEDIUM** - Without load testing, actual performance under concurrent load is unverified. System may:
- Fail SLO targets under production traffic
- Experience unexpected bottlenecks
- Hit resource limits not seen in development

**Recommendation:**
Run load tests in staging environment before production deployment.

---

**Document Updated:** 2026-08-15
**Load Tests Executed:** ❌ NO (k6 not available)
**Next Steps:** Install k6, run tests, update this document with actual results
