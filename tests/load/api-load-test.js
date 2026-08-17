/**
 * RetailMind API Load Test
 *
 * Tests top API endpoints with realistic traffic patterns.
 * Run with: k6 run tests/load/api-load-test.js
 *
 * Metrics tracked:
 * - Request rate (requests/second)
 * - Error rate (percentage 5xx/4xx)
 * - Latency (p50, p95, p99)
 * - Database connections
 * - Rate limit hits
 *
 * Thresholds:
 * - p95 latency < 2s (per SLO)
 * - p99 latency < 5s
 * - Error rate < 1%
 * - Success rate > 99%
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const authLatency = new Trend('auth_latency');
const dashboardLatency = new Trend('dashboard_latency');
const investigationLatency = new Trend('investigation_latency');

// Test configuration
export const options = {
  stages: [
    { duration: '1m', target: 10 },  // Warm up: ramp to 10 users
    { duration: '3m', target: 50 },  // Load: ramp to 50 users
    { duration: '5m', target: 50 },  // Sustain: hold 50 users
    { duration: '1m', target: 0 },   // Cool down: ramp to 0
  ],
  thresholds: {
    // SLO: 95% of requests < 2s
    'http_req_duration{endpoint:dashboard}': ['p(95)<2000'],
    'http_req_duration{endpoint:investigation}': ['p(95)<5000'],  // Expected slow
    'http_req_duration{endpoint:recommendations}': ['p(95)<2000'],

    // SLO: Error rate < 1%
    'http_req_failed': ['rate<0.01'],
    'errors': ['rate<0.01'],

    // Success rate > 99%
    'checks': ['rate>0.99'],
  },
};

// Configuration
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8090';
// Defaults match the real seeded demo tenant (backend/app/infrastructure/db/seeds/sample.py) —
// the previous defaults here didn't correspond to any real user, so an
// unconfigured run tested nothing but login failures.
const TEST_USER_EMAIL = __ENV.TEST_USER || 'priya@northwind.example';
const TEST_USER_PASSWORD = __ENV.TEST_PASSWORD || 'ChangeMe-Demo1!';

let accessToken = null;

/**
 * Setup: Authenticate and get access token.
 * Runs once per VU at start.
 */
export function setup() {
  const loginRes = http.post(`${BASE_URL}/api/v1/auth/login`, JSON.stringify({
    email: TEST_USER_EMAIL,
    password: TEST_USER_PASSWORD,
  }), {
    headers: { 'Content-Type': 'application/json' },
  });

  if (loginRes.status !== 200) {
    console.error('Login failed:', loginRes.status, loginRes.body);
    throw new Error('Cannot authenticate test user');
  }

  const body = JSON.parse(loginRes.body);
  return {
    accessToken: body.access_token,
    tenantId: body.tenant_id,
  };
}

/**
 * Main test scenario.
 * Simulates realistic user behavior: dashboard → investigation → recommendations.
 */
export default function (data) {
  const headers = {
    'Authorization': `Bearer ${data.accessToken}`,
    'Content-Type': 'application/json',
  };

  // Scenario 1: Load dashboard (most common request)
  const dashboardRes = http.get(`${BASE_URL}/api/v1/dashboard/executive`, { headers, tags: { endpoint: 'dashboard' } });

  check(dashboardRes, {
    'dashboard: status 200': (r) => r.status === 200,
    'dashboard: has revenue': (r) => JSON.parse(r.body).revenue !== undefined,
  }) || errorRate.add(1);

  dashboardLatency.add(dashboardRes.timings.duration);

  sleep(Math.random() * 2 + 1);  // 1-3 seconds between requests

  // Scenario 2: View recommendations (30% of users)
  if (Math.random() < 0.3) {
    const recsRes = http.get(
      `${BASE_URL}/api/v1/recommendations?limit=20`,
      { headers, tags: { endpoint: 'recommendations' } }
    );

    check(recsRes, {
      'recommendations: status 200': (r) => r.status === 200,
      'recommendations: is array': (r) => Array.isArray(JSON.parse(r.body).recommendations),
    }) || errorRate.add(1);

    sleep(Math.random() * 3 + 2);  // 2-5 seconds reading recommendations
  }

  // Scenario 3: Run investigation (10% of users, heavy query)
  if (Math.random() < 0.1) {
    const investigationRes = http.get(
      `${BASE_URL}/api/v1/rca/investigate?metric=net_revenue&current_start=2026-08-07&current_end=2026-08-13`,
      { headers, tags: { endpoint: 'investigation' }, timeout: '30s' }
    );

    check(investigationRes, {
      'investigation: status 200': (r) => r.status === 200,
      'investigation: has findings': (r) => {
        const body = JSON.parse(r.body);
        return body.where !== undefined || body.why !== undefined;
      },
    }) || errorRate.add(1);

    investigationLatency.add(investigationRes.timings.duration);

    sleep(Math.random() * 5 + 5);  // 5-10 seconds reading investigation
  }

  // Scenario 4: Check forecasts (20% of users)
  if (Math.random() < 0.2) {
    const forecastRes = http.get(
      `${BASE_URL}/api/v1/forecasts?target=net_revenue&horizon=28`,
      { headers, tags: { endpoint: 'forecasts' } }
    );

    check(forecastRes, {
      'forecasts: status 200 or 404': (r) => r.status === 200 || r.status === 404,  // 404 if no forecasts yet
    }) || errorRate.add(1);

    sleep(Math.random() * 2 + 1);
  }

  // Scenario 5: Query analytics (15% of users)
  if (Math.random() < 0.15) {
    const analyticsRes = http.post(
      `${BASE_URL}/api/v1/analytics/query`,
      JSON.stringify({
        question: "What was revenue last week?",
      }),
      { headers, tags: { endpoint: 'analytics' }, timeout: '10s' }
    );

    check(analyticsRes, {
      'analytics: status 200': (r) => r.status === 200,
    }) || errorRate.add(1);

    sleep(Math.random() * 3 + 2);
  }

  sleep(Math.random() * 5 + 3);  // 3-8 seconds between scenarios
}

/**
 * Teardown: Report final summary.
 */
export function teardown(data) {
  console.log('Load test complete. Check k6 summary for results.');
}
