# API Reference

RetailMind AI REST API - 13 endpoint modules, authentication, request/response schemas, and usage examples.

**Last Updated**: 2026-08-15
**Version**: v1
**Base URL**: `https://api.retailmind.example.com/api/v1`

---

## Table of Contents

- [Authentication](#authentication)
- [Endpoint Modules](#endpoint-modules)
- [Common Patterns](#common-patterns)
- [Error Handling](#error-handling)
- [Rate Limiting](#rate-limiting)
- [Idempotency](#idempotency)

---

## Authentication

### JWT Bearer Token

All endpoints require authentication via JWT bearer token in the `Authorization` header.

**Login**:

```bash
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password123"
}
```

**Response**:

```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "expires_in": 900
}
```

**Using Token**:

```bash
GET /api/v1/analytics/revenue/summary
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGc...
```

### Token Refresh

```bash
POST /api/v1/auth/refresh
Content-Type: application/json

{
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

---

## Endpoint Modules

### 1. Authentication (`/auth`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/login` | POST | Authenticate user |
| `/auth/refresh` | POST | Refresh access token |
| `/auth/logout` | POST | Invalidate refresh token |

### 2. Analytics (`/analytics`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/analytics/{domain}/summary` | POST | Aggregate metrics (no dimensions) |
| `/analytics/{domain}/breakdown` | POST | Metrics by 1-2 dimensions |
| `/analytics/{domain}/trend` | POST | Metrics over time |

**Domains**: revenue, store, customer, inventory, marketing, profitability, etc. (23 total)

**Example**:

```bash
POST /api/v1/analytics/revenue/summary
{
  "metrics": ["net_revenue", "aov"],
  "start_date": "2026-08-01",
  "end_date": "2026-08-15"
}
```

### 3. Root Cause Analysis (`/rca`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rca/investigate` | POST | Run RCA for metric variance |
| `/rca/compare` | POST | Period-over-period comparison |

**Example**:

```bash
POST /api/v1/rca/investigate
{
  "metric": "revenue",
  "current_period": {"start": "2026-08-01", "end": "2026-08-15"},
  "baseline_period": {"start": "2026-07-01", "end": "2026-07-15"}
}
```

### 4. AI Analyst (`/analyst`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/analyst/` | POST | Ask natural language question |

**Example**:

```bash
POST /api/v1/analyst/
{
  "question": "Why did revenue drop 15% last week?"
}
```

**Response**:

```json
{
  "capability": "investigate",
  "headline": "Revenue fell 15% ($1.25M → $1.06M), driven primarily by Store #42...",
  "facts": ["Revenue decreased 15%", ...],
  "inferences": [...],
  "caveats": [...],
  "follow_ups": [...]
}
```

### 5. Recommendations (`/recommendations`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/recommendations/` | GET | List active recommendations |
| `/recommendations/{id}` | GET | Get recommendation details |
| `/recommendations/{id}/decide` | POST | Accept/reject recommendation |
| `/recommendations/{id}/outcome` | POST | Record outcome measurement |

**Example**:

```bash
POST /api/v1/recommendations/{id}/decide
{
  "decision": "accept",
  "rationale": "Aligns with Q3 inventory strategy"
}
```

### 6. Forecasting (`/forecasts`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/forecasts/` | GET | List forecasts |
| `/forecasts/{id}` | GET | Get forecast details |
| `/forecasts/train` | POST | Train new forecast model |
| `/forecasts/backtest` | POST | Run backtest validation |

### 7. Natural Language Queries (`/nlq`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/nlq/` | POST | Execute NL query |

**Example**:

```bash
POST /api/v1/nlq/
{
  "question": "What were sales by category last month?"
}
```

### 8. Customers (`/customers`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/customers/segments` | GET | Customer segments |
| `/customers/cohorts` | GET | Cohort analysis |
| `/customers/rfm` | GET | RFM analysis |

### 9. Inventory (`/inventory`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/inventory/positions` | GET | Current stock positions |
| `/inventory/health` | GET | Inventory health metrics |
| `/inventory/reorder` | GET | Reorder suggestions |

### 10. Dashboards (`/dashboard`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dashboard/` | GET | List dashboards |
| `/dashboard/{id}` | GET | Get dashboard configuration |
| `/dashboard/` | POST | Create dashboard |
| `/dashboard/{id}` | PUT | Update dashboard |

### 11. Reports (`/reports`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/reports/` | GET | List reports |
| `/reports/generate` | POST | Generate report |
| `/reports/{id}/schedule` | POST | Schedule recurring report |

### 12. Notifications (`/notifications`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/notifications/` | GET | List notifications |
| `/notifications/{id}/read` | POST | Mark as read |

### 13. Admin (`/admin`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/users` | GET, POST | Manage users |
| `/admin/users/{id}/roles` | PUT | Assign roles |
| `/admin/permissions` | GET | List permissions |

---

## Common Patterns

### Pagination

```bash
GET /api/v1/recommendations/?offset=20&limit=10
```

**Response**:

```json
{
  "items": [...],
  "total": 150,
  "offset": 20,
  "limit": 10
}
```

### Filtering

```bash
GET /api/v1/notifications/?status=unread&start_date=2026-08-01
```

### Sorting

```bash
GET /api/v1/recommendations/?sort_by=score&descending=true
```

---

## Error Handling

### HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | OK | Successful request |
| 201 | Created | Resource created |
| 400 | Bad Request | Invalid request body |
| 401 | Unauthorized | Missing/invalid token |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource not found |
| 422 | Unprocessable Entity | Validation error |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server error |

### Error Response Format

```json
{
  "detail": "User does not have permission to access revenue analytics",
  "error_code": "PERMISSION_DENIED",
  "timestamp": "2026-08-15T10:30:00Z"
}
```

---

## Rate Limiting

**Limits**:
- 100 requests/minute per IP address
- 200 requests/minute per authenticated user
- 10 requests/minute for `/auth/login` (brute force protection)

**Headers**:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1692097860
```

**429 Response**:

```json
{
  "detail": "Rate limit exceeded: 100 requests per minute",
  "retry_after": 42
}
```

---

## Idempotency

**Mutation endpoints** (POST, PUT, PATCH, DELETE) support idempotency keys.

**Header**:

```
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000
```

**Behavior**:
- First request: Executes, caches response (24h TTL)
- Retry: Returns cached response without re-executing

**Response Headers**:

```
X-Idempotency-Cached: true
X-Idempotency-Age: 42
```

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
