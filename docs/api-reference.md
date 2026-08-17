# RetailMind API Reference

**Version:** 0.9.0
**Base URL:** `https://{domain}/api/v1`
**Last Updated:** 2026-08-15

---

## Table of Contents

- [Authentication](#authentication)
- [Health & Operations](#health--operations)
- [Business Analyst](#business-analyst)
- [Analytics](#analytics)
- [Root Cause Analysis](#root-cause-analysis)
- [Forecasting](#forecasting)
- [Recommendations](#recommendations)
- [Reports](#reports)
- [Natural Language Query](#natural-language-query)
- [Customers](#customers)
- [Inventory](#inventory)
- [Dashboards](#dashboards)
- [Notifications](#notifications)
- [Admin](#admin)

---

## Authentication

All v1 endpoints require authentication except `/auth/login` and `/auth/refresh`.

**Authentication Method:** JWT Bearer Token (RS256)

**Headers:**
```http
Authorization: Bearer {access_token}
```

**Token Lifecycle:**
- Access Token: 15 minutes
- Refresh Token: 30 days (HTTP-only cookie)

### Endpoints

#### `POST /api/v1/auth/login`

**Summary:** Authenticate user and issue tokens

**Request:**
```json
{
  "email": "user@example.com",
  "password": "string"
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 900
}
```

**Errors:**
- `401 Unauthorized` - Invalid credentials
- `429 Too Many Requests` - Rate limit exceeded

---

#### `POST /api/v1/auth/refresh`

**Summary:** Refresh access token using refresh cookie

**Response:** `200 OK`
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 900
}
```

---

#### `POST /api/v1/auth/logout`

**Summary:** Invalidate refresh token

**Response:** `204 No Content`

---

## Health & Operations

#### `GET /health`

**Summary:** Liveness probe

**Response:** `200 OK`
```json
{
  "status": "healthy",
  "version": "0.9.0",
  "timestamp": "2026-08-15T12:00:00Z"
}
```

---

#### `GET /ready`

**Summary:** Readiness probe (checks database connection)

**Response:** `200 OK`
```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "redis": "ok"
  }
}
```

---

#### `GET /metrics`

**Summary:** Prometheus metrics exposition

**Response:** `200 OK` (text/plain)
```
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="GET",endpoint="/health",status="200"} 1234
...
```

---

## Business Analyst

**Prefix:** `/api/v1/analyst`

**Permission Required:** `insights.read` (+ capability-specific permissions)

### POST /analyst/ask

**Summary:** Ask the business analyst a question

**Capabilities:**
- `EXPLAIN_KPI` - Explain what a metric means
- `ANSWER` - Answer factual questions from data
- `INVESTIGATE` - Investigate metric movements (RCA)
- `RECOMMEND` - Get action recommendations
- `SUMMARISE` - Generate executive summaries
- `COMPARE` - Compare two time periods
- `EXPLAIN_FORECAST` - Explain forecast confidence
- `IMPROVE` - Identify measurement gaps

**Request:**
```json
{
  "question": "Why did revenue drop last week?",
  "conversation_id": "uuid-optional",
  "context": {
    "date_range": {
      "start": "2026-08-01",
      "end": "2026-08-14"
    }
  }
}
```

**Response:** `200 OK`
```json
{
  "question": "Why did revenue drop last week?",
  "capability": "INVESTIGATE",
  "headline": "Revenue decreased 15% from $1.25M to $1.06M due to store closures.",
  "facts": [
    "Revenue decreased from $1,250,000 to $1,060,000 (-15%)",
    "5 stores were closed for maintenance Aug 8-10"
  ],
  "inferences": [
    "Store closures likely account for majority of decline"
  ],
  "checked": [
    "Store-level revenue",
    "Product mix",
    "Pricing changes"
  ],
  "not_checked": [
    "Customer acquisition cost",
    "Marketing spend"
  ],
  "caveats": [
    "Store closure data is incomplete for Aug 10"
  ],
  "follow_ups": [
    {
      "question": "Which products were most affected?",
      "reason": "Product-level breakdown would show impact distribution"
    }
  ],
  "data": {
    "plan": "investigate_revenue_movement",
    "rows": [...],
    "domain": "revenue"
  },
  "meta": {
    "duration_ms": 2340,
    "evidence_sources": ["warehouse", "semantic_layer"]
  }
}
```

**Response Fields:**
- `headline`: LLM-enhanced summary (or deterministic if LLM unavailable)
- `facts`: Verified, measured statements from data
- `inferences`: Statistical/analytical conclusions
- `checked`: Dimensions/factors that were investigated
- `not_checked`: Relevant dimensions NOT investigated (transparency)
- `caveats`: Known limitations of the analysis
- `follow_ups`: Suggested next questions
- `data`: Structured data backing the answer
- `meta`: Request metadata

**Errors:**
- `403 Forbidden` - Missing required permission
- `422 Unprocessable Entity` - Cannot answer question
  ```json
  {
    "type": "https://retailmind.ai/errors/cannot-answer",
    "title": "Cannot answer",
    "detail": "Question requires customer-level data, which is not available."
  }
  ```

---

## Analytics

**Prefix:** `/api/v1/analytics`

**Permission Required:** Domain-specific (e.g., `analytics.revenue.read`)

### Available Domains

1. `revenue` - Revenue, AOV, transactions
2. `inventory` - Stock levels, turnover, days on hand
3. `customers` - Acquisition, retention, LTV
4. `products` - Sales, margin, ABC classification
5. `stores` - Store performance, comparisons
6. `profitability` - Margin, contribution, breakeven
7. `forecasting` - Predictions, accuracy metrics
8. ... (23 domains total)

### GET /analytics/{domain}/summary

**Summary:** Get summary metrics for a domain

**Parameters:**
- `start_date`: ISO date
- `end_date`: ISO date
- `group_by`: Optional (day, week, month, quarter, year)
- `filters`: Optional dimension filters

**Example:** `GET /analytics/revenue/summary?start_date=2026-08-01&end_date=2026-08-14`

**Response:** `200 OK`
```json
{
  "domain": "revenue",
  "period": {
    "start": "2026-08-01",
    "end": "2026-08-14"
  },
  "metrics": {
    "net_revenue": 1060000.00,
    "gross_revenue": 1120000.00,
    "discount_amount": 60000.00,
    "aov": 52.50,
    "transactions": 20190
  },
  "trends": {
    "net_revenue": {
      "change": -15.2,
      "change_pct": -0.152,
      "direction": "down"
    }
  },
  "dimensions": {
    "top_stores": [...],
    "top_products": [...]
  }
}
```

---

### POST /analytics/{domain}/query

**Summary:** Execute custom analytics query

**Request:**
```json
{
  "metrics": ["net_revenue", "aov", "transactions"],
  "dimensions": ["store_name", "channel"],
  "filters": {
    "channel": ["online", "retail"],
    "date_range": {
      "start": "2026-08-01",
      "end": "2026-08-14"
    }
  },
  "limit": 100,
  "offset": 0
}
```

**Response:** `200 OK`
```json
{
  "domain": "revenue",
  "plan": {
    "view": "v_revenue_daily",
    "aggregations": ["SUM(net_revenue)", "AVG(aov)"],
    "group_by": ["store_name", "channel"]
  },
  "rows": [
    {
      "store_name": "Downtown",
      "channel": "retail",
      "net_revenue": 125000.00,
      "aov": 65.00,
      "transactions": 1923
    },
    ...
  ],
  "total_rows": 48,
  "pagination": {
    "limit": 100,
    "offset": 0,
    "has_more": false
  }
}
```

---

## Root Cause Analysis

**Prefix:** `/api/v1/rca`

**Permission Required:** `analytics.investigate.read`

### POST /rca/investigate

**Summary:** Investigate why a metric moved

**Request:**
```json
{
  "metric": "net_revenue",
  "current_period": {
    "start": "2026-08-08",
    "end": "2026-08-14"
  },
  "baseline_period": {
    "start": "2026-08-01",
    "end": "2026-08-07"
  },
  "dimensions": ["store", "product", "channel"]
}
```

**Response:** `200 OK`
```json
{
  "metric": "net_revenue",
  "movement": {
    "baseline": 1250000.00,
    "current": 1060000.00,
    "change": -190000.00,
    "change_pct": -0.152
  },
  "factors": [
    {
      "dimension": "store",
      "contributor": "Store closures (Aug 8-10)",
      "impact": -120000.00,
      "impact_pct": -0.096,
      "confidence": "high",
      "tier": "MECHANICAL"
    },
    {
      "dimension": "product",
      "contributor": "Electronics sales down",
      "impact": -50000.00,
      "impact_pct": -0.04,
      "confidence": "medium",
      "tier": "STATISTICAL"
    }
  ],
  "checked_dimensions": ["store", "product", "channel", "day_of_week"],
  "not_checked_dimensions": ["customer_segment", "promo_type"],
  "caveats": [
    "Store closure data incomplete for Aug 10"
  ]
}
```

**Evidence Tiers:**
- `MECHANICAL`: Direct arithmetic contribution (store closures → lost revenue)
- `STATISTICAL`: Correlation-based (product mix changed)
- `ASSOCIATIVE`: Moves together, causation unclear

---

## Forecasting

**Prefix:** `/api/v1/forecasts`

**Permission Required:** `forecasts.read`

### GET /forecasts/{metric}

**Summary:** Get forecast for a metric

**Parameters:**
- `horizon_days`: Forecast horizon (default: 30, max: 90)
- `confidence_level`: Optional (0.80, 0.90, 0.95)

**Example:** `GET /forecasts/net_revenue?horizon_days=14`

**Response:** `200 OK`
```json
{
  "metric": "net_revenue",
  "forecast_date": "2026-08-15",
  "horizon_days": 14,
  "model": "ridge_regression_v2",
  "predictions": [
    {
      "date": "2026-08-16",
      "predicted_value": 152000.00,
      "lower_bound": 140000.00,
      "upper_bound": 164000.00,
      "confidence": 0.90
    },
    ...
  ],
  "accuracy": {
    "wape": 0.042,
    "mase": 0.87,
    "beats_naive": true
  },
  "explanation": "Forecast based on 180 days historical data with seasonal adjustment"
}
```

---

## Recommendations

**Prefix:** `/api/v1/recommendations`

**Permission Required:** `recommendations.read`

### GET /recommendations

**Summary:** Get active recommendations

**Parameters:**
- `type`: Optional filter (reorder, markdown, promo, assortment)
- `confidence`: Optional minimum confidence
- `limit`, `offset`: Pagination

**Response:** `200 OK`
```json
{
  "recommendations": [
    {
      "id": "uuid",
      "type": "reorder",
      "subject": {
        "sku": "SKU-12345",
        "store": "Downtown"
      },
      "expected_impact": {
        "profit": 2500.00,
        "risk": "low",
        "confidence": 0.85
      },
      "rationale": "Stock will hit reorder point in 3 days based on sales velocity",
      "created_at": "2026-08-15T08:00:00Z",
      "expires_at": "2026-08-18T08:00:00Z"
    }
  ],
  "total": 42,
  "pagination": {
    "limit": 20,
    "offset": 0,
    "has_more": true
  }
}
```

---

### POST /recommendations/{id}/accept

**Summary:** Accept a recommendation

**Response:** `200 OK`
```json
{
  "id": "uuid",
  "status": "accepted",
  "accepted_at": "2026-08-15T12:30:00Z"
}
```

---

### POST /recommendations/{id}/dismiss

**Summary:** Dismiss a recommendation

**Request:**
```json
{
  "reason": "Already addressed manually"
}
```

**Response:** `200 OK`

---

## Reports

**Prefix:** `/api/v1/reports`

**Permission Required:** `reports.read`

### POST /reports/generate

**Summary:** Generate executive report

**Request:**
```json
{
  "report_type": "executive_summary",
  "period": {
    "start": "2026-08-01",
    "end": "2026-08-14"
  },
  "sections": [
    "revenue_overview",
    "key_movements",
    "recommendations",
    "forecasts"
  ]
}
```

**Response:** `200 OK`
```json
{
  "report_id": "uuid",
  "report_type": "executive_summary",
  "period": {...},
  "sections": [
    {
      "section_type": "revenue_overview",
      "title": "Revenue Overview",
      "summary": "Revenue decreased 15% week-over-week...",
      "data": {...}
    }
  ],
  "generated_at": "2026-08-15T12:00:00Z"
}
```

---

## Natural Language Query

**Prefix:** `/api/v1/nlq`

**Permission Required:** `analytics.query.read`

### POST /nlq/query

**Summary:** Query analytics using natural language

**Request:**
```json
{
  "query": "Show me top 10 products by revenue last week",
  "session_id": "optional-uuid"
}
```

**Response:** `200 OK`
```json
{
  "query": "Show me top 10 products by revenue last week",
  "interpretation": {
    "domain": "products",
    "metrics": ["net_revenue"],
    "dimensions": ["product_name"],
    "filters": {
      "date_range": {
        "start": "2026-08-08",
        "end": "2026-08-14"
      }
    },
    "sort": {"net_revenue": "desc"},
    "limit": 10
  },
  "results": {
    "rows": [...],
    "total_rows": 10
  },
  "session_id": "uuid"
}
```

---

## Customers

**Prefix:** `/api/v1/customers`

**Permission Required:** `customers.read`

### GET /customers

**Summary:** List customers

**Parameters:**
- `limit`, `offset`: Pagination
- `search`: Search by email/name
- `segment`: Filter by segment (vip, at_risk, new, etc.)

---

### GET /customers/{id}

**Summary:** Get customer details

**Response:** `200 OK`
```json
{
  "id": "uuid",
  "email": "customer@example.com",
  "segment": "vip",
  "lifetime_value": 15000.00,
  "total_orders": 42,
  "avg_order_value": 357.14,
  "last_order_date": "2026-08-10",
  "rfm": {
    "recency_score": 5,
    "frequency_score": 5,
    "monetary_score": 4
  }
}
```

---

## Inventory

**Prefix:** `/api/v1/inventory`

**Permission Required:** `inventory.read`

### GET /inventory/positions

**Summary:** Get current inventory positions

**Parameters:**
- `store_id`: Optional store filter
- `sku`: Optional SKU filter
- `low_stock`: Boolean, show only low stock items

---

### GET /inventory/health

**Summary:** Get inventory health metrics

**Response:** `200 OK`
```json
{
  "total_sku_count": 1200,
  "in_stock_count": 1150,
  "out_of_stock_count": 50,
  "low_stock_count": 120,
  "excess_stock_count": 80,
  "inventory_turnover": 4.2,
  "days_on_hand_avg": 45
}
```

---

## Dashboards

**Prefix:** `/api/v1/dashboards`

**Permission Required:** `dashboards.read`

### GET /dashboards

**Summary:** List user dashboards

---

### POST /dashboards

**Summary:** Create custom dashboard

**Request:**
```json
{
  "name": "My Dashboard",
  "layout": {
    "tiles": [
      {
        "type": "metric",
        "position": {"x": 0, "y": 0, "w": 4, "h": 2},
        "config": {
          "domain": "revenue",
          "metric": "net_revenue"
        }
      }
    ]
  }
}
```

---

## Notifications

**Prefix:** `/api/v1/notifications`

**Permission Required:** `notifications.read`

### GET /notifications

**Summary:** Get user notifications

**Parameters:**
- `unread_only`: Boolean
- `limit`, `offset`: Pagination

---

### POST /notifications/{id}/mark_read

**Summary:** Mark notification as read

---

## Admin

**Prefix:** `/api/v1/admin`

**Permission Required:** `admin` role

### GET /admin/users

**Summary:** List all users (admin only)

---

### POST /admin/users

**Summary:** Create user (admin only)

---

### PATCH /admin/users/{id}/roles

**Summary:** Update user roles (admin only)

---

## Rate Limiting

**Default Limits:**
- Authenticated: 1000 requests/hour
- Unauthenticated: 100 requests/hour

**Headers:**
```http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 987
X-RateLimit-Reset: 1692097200
```

**Error:** `429 Too Many Requests`
```json
{
  "type": "https://retailmind.ai/errors/rate-limit",
  "title": "Rate limit exceeded",
  "detail": "Too many requests. Retry after 42 seconds.",
  "retry_after": 42
}
```

---

## Idempotency

**Support:** All POST, PUT, PATCH requests support idempotency

**Header:**
```http
Idempotency-Key: {uuid-v4}
```

**Behavior:**
- First request: Processes normally, stores result for 24 hours
- Duplicate request (same key): Returns stored result (200 OK, not 201)
- Different payload, same key: Returns 409 Conflict

---

## Error Responses

All errors follow RFC 7807 Problem Details format:

```json
{
  "type": "https://retailmind.ai/errors/{error-type}",
  "title": "Human-readable title",
  "status": 400,
  "detail": "Detailed explanation",
  "instance": "/api/v1/analytics/revenue/summary?start_date=invalid",
  "hint": "Optional hint for resolution"
}
```

**Common Error Types:**
- `validation-error` - Invalid request data (400)
- `unauthorized` - Missing/invalid token (401)
- `forbidden` - Permission denied (403)
- `not-found` - Resource not found (404)
- `conflict` - Idempotency conflict (409)
- `cannot-answer` - Question cannot be answered (422)
- `rate-limit` - Rate limit exceeded (429)
- `internal-error` - Server error (500)

---

## Versioning

**Current Version:** v1

**Deprecation Policy:**
- Endpoints marked deprecated minimum 6 months before removal
- Breaking changes require new version (v2, v3, etc.)
- Backward-compatible changes do not require versioning

**Checking Version:**
```http
GET /health
```

Response includes `version` field.

---

## OpenAPI Specification

**Interactive Documentation:** `https://{domain}/docs`

**OpenAPI JSON:** `https://{domain}/openapi.json`

---

**Maintained by:** RetailMind AI Backend Team
**Support:** See docs/contributing.md
**Last Updated:** 2026-08-15
