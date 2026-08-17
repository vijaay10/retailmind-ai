# Product Guide

RetailMind AI product overview - capabilities, user roles, workspaces, use cases, and workflows.

**Last Updated**: 2026-08-15
**Version**: 0.9.0
**Product Type**: Business Intelligence & Decision Support Platform

---

## Table of Contents

- [Overview](#overview)
- [User Roles](#user-roles)
- [Workspaces](#workspaces)
- [Core Capabilities](#core-capabilities)
- [Use Cases](#use-cases)
- [User Workflows](#user-workflows)
- [Key Differentiators](#key-differentiators)

---

## Overview

### What is RetailMind AI?

RetailMind AI is a **business intelligence and decision support platform** for retail operations, combining:

- **23 analytics domains** with ~300 governed metrics
- **AI-powered investigation** that explains metric variances via dimensional analysis
- **Evidence-based recommendations** with impact estimation and outcome tracking
- **Forecasting engine** with quality gates (models must beat naive baseline)
- **Natural language interface** that routes questions to specialized engines

**Not a chatbot.** Questions are parsed into plans over a closed vocabulary, compiled to SQL, and executed deterministically. "Why" questions route to the root-cause engine, not generative AI.

### Target Users

- **C-suite executives** (CEO, CFO, CMO) - Strategic oversight
- **Functional leaders** (Store Managers, Inventory Planners) - Operational decisions
- **Analysts** - Deep-dive investigations and reporting

### Deployment Model

- **Self-hosted** (Docker Compose on a single server)
- **Data residency**: All data stays on-premises (no external data transmission except optional LLM calls)
- **Scalability**: <100GB data, <1000 concurrent users

---

## User Roles

### Role-Based Access Control (RBAC)

**6 built-in roles** with granular permissions:

| Role | Focus | Key Permissions | Typical User |
|------|-------|-----------------|--------------|
| **CEO** | Enterprise overview | All analytics domains, all workspaces | Chief Executive Officer |
| **CFO** | Financial performance | Revenue, profitability, forecasts | Chief Financial Officer |
| **CMO** | Marketing effectiveness | Customer, marketing, campaigns | Chief Marketing Officer |
| **Store Manager** | Store operations | Store analytics, inventory, sales by location | Regional/Store Manager |
| **Inventory Planner** | Supply chain | Inventory analytics, forecasts, reorder recommendations | Supply Chain Manager |
| **Analyst** | Deep-dive analysis | All analytics (read-only), investigation tools | Business Analyst, Data Analyst |

### Permission Model

**Granular permissions** control access to:
- Analytics domains (e.g., `analytics.revenue.read`, `analytics.inventory.read`)
- Capabilities (e.g., `insights.read`, `recommendations.read`, `forecasts.read`)
- Actions (e.g., `recommendations.decide`, `admin.users.write`)

**Example**: Store Manager can:
- ✅ View store analytics
- ✅ View inventory analytics
- ✅ View recommendations
- ❌ View company-wide profitability (CFO-only)
- ❌ Accept/reject recommendations (needs `recommendations.decide`)

### Default Test Users (Demo Mode)

When `RM_APP_ENV=dev`, 7 test users are seeded:

| Email | Role | Password |
|-------|------|----------|
| priya@northwind.example | CEO | ChangeMe-Demo1! |
| arjun@northwind.example | CFO | ChangeMe-Demo1! |
| meera@northwind.example | CMO | ChangeMe-Demo1! |
| raj@northwind.example | Store Manager | ChangeMe-Demo1! |
| anita@northwind.example | Inventory Planner | ChangeMe-Demo1! |
| vijay@northwind.example | Analyst | ChangeMe-Demo1! |
| dev@northwind.example | Admin (all permissions) | ChangeMe-Demo1! |

---

## Workspaces

### 12 Streamlit Workspaces

Each workspace is role-specific and task-focused:

#### 1. Command Center

**Icon**: ◈
**For**: Executives (CEO, CFO)
**Purpose**: The day, read in thirty seconds

**Content**:
- **Headline**: Net revenue, latest business day (absolute value + direction)
- **Growth horizons**: Week, month, quarter comparisons
- **Top action**: Highest-impact recommendation
- **Risk alerts**: Critical alerts requiring attention
- **Movement chart**: Revenue trend (7-day rolling)

**Philosophy**:
> "This screen does not open with charts. The first thing on it is a sentence about what happened, then the money, then the single action most worth taking, then what is on fire."

**Use Case**: Daily morning check-in (30 seconds)

---

#### 2. AI Investigation

**Icon**: ◆
**For**: Analysts, Executives
**Purpose**: Root cause analysis for metric variances

**How It Works**:
1. User selects metric (e.g., "revenue")
2. Selects current period and baseline period
3. Platform sweeps **9 dimensions**:
   - Store, Product, Customer, Channel, Day of Week, Payment Method, Promotion, Region, Segment
4. Returns **facts** (mechanical/statistical), **inferences** (hypotheses), **caveats** (limitations)

**Example Output**:
```
Headline: Revenue fell 15% ($1.25M → $1.06M)

Facts:
✓ Store #42 contributed -$120K (-63% of total variance)
✓ Weekend revenue fell 22% while weekday revenue grew 3%
✓ Electronics category declined 28% ($85K impact)

Inferences:
⊕ Store #42 weekend decline suggests staffing or inventory issue
⊕ Electronics decline correlates with end of promotional period

Caveats:
• Did not check: supplier delays, local events, weather
• Correlation does not imply causation
```

**Evidence Tiers**:
- **Mechanical**: Arithmetic decomposition (e.g., "Store #42 contributed -$120K")
- **Statistical**: Statistically significant patterns (e.g., "Weekend revenue fell 22%")
- **Associative**: Correlations without causal proof

---

#### 3. Decision Center

**Icon**: ◈
**For**: Executives, Functional Leaders
**Purpose**: Review and act on AI-generated recommendations

**Recommendation Types**:
1. **Reorder** - Inventory replenishment (SKU below reorder point)
2. **Markdown** - Price reduction (excess inventory, slow turnover)
3. **Price Adjust** - Competitive pricing optimization
4. **Promotion** - Promotional campaign suggestions
5. **Retention** - Customer retention actions
6. **Supplier** - Supplier switch recommendations
7. **Allocation** - Inventory allocation across stores

**Recommendation Card**:
```
Reorder SKU-12345 at Store #042
─────────────────────────────────
Current stock: 50 units (58% below reorder point)
Suggested qty: 500 units
Order by: 2026-08-20

Expected impact: +$45K revenue (forecast-driven, high confidence)

Rationale: Stock at 50 units. 7-day forecast shows 480 units demand.

[Accept] [Reject] [Defer]
```

**Decision Tracking**:
- Accept/Reject/Defer with rationale
- Outcome measurement (actual vs. projected impact)
- Calibration loop (adjust generator weights based on accuracy)

---

#### 4. AI Analyst

**Icon**: ◇
**For**: All users
**Purpose**: Natural language analytics interface

**Not a chatbot**:
> "A chatbot returns prose and asks you to trust it. This returns the whole chain: the question, how it was understood, the plan it compiled, the SQL that plan produced, the rows, the chart, and what the answer did not cover."

**Example Interaction**:
```
User: "What were sales by category last month?"

Understood as: ANSWER
Domain: revenue
Metrics: net_revenue, orders
Grouped by: product_category
Window: 2026-07-01 → 2026-07-31

Compiled SQL:
SELECT
  product_category,
  SUM(line_total) AS net_revenue,
  COUNT(DISTINCT order_id) AS orders
FROM fct_sales
WHERE order_date BETWEEN ? AND ?
GROUP BY product_category
ORDER BY net_revenue DESC

Results:
┌──────────────┬─────────────┬────────┐
│ Category     │ Revenue     │ Orders │
├──────────────┼─────────────┼────────┤
│ Electronics  │ $1,250,000  │ 4,200  │
│ Apparel      │ $980,000    │ 6,800  │
│ Home         │ $750,000    │ 3,100  │
└──────────────┴─────────────┴────────┘

[Chart: Bar chart showing revenue by category]

Caveats:
• Does not include returns (net_revenue is gross)
• Product categorization as of end-of-period
```

**Routing**:
- **"What" questions** → Analytics (SQL query)
- **"Why" questions** → Root Cause Analysis (dimensional sweep)
- **"When will" questions** → Forecasting
- **"What should I do" questions** → Recommendations

---

#### 5. Sales Intelligence

**Icon**: ▣
**For**: Analysts, Store Managers
**Purpose**: Deep-dive into sales performance

**Views**:
- **Summary**: KPIs (revenue, orders, AOV, conversion rate)
- **Breakdown**: By store, product, channel, time
- **Trend**: Daily/weekly/monthly trends
- **Leaderboard**: Top stores, products, sales reps

**Metrics Available** (~40 sales metrics):
- `net_revenue`, `gross_revenue`, `discounts`, `returns`
- `orders`, `units_sold`, `aov` (average order value)
- `conversion_rate`, `basket_size`, `upt` (units per transaction)

---

#### 6. Customer Intelligence

**Icon**: ▤
**For**: CMO, Analysts
**Purpose**: Customer analytics and segmentation

**Views**:
- **Segments**: RFM (Recency, Frequency, Monetary) analysis
- **Cohorts**: Retention by acquisition cohort
- **Lifetime Value**: CLV by segment
- **Churn Risk**: At-risk customers

**Metrics Available** (~35 customer metrics):
- `active_customers`, `new_customers`, `churned_customers`
- `ltv` (lifetime value), `avg_frequency`, `avg_recency`
- `churn_rate`, `retention_rate`

---

#### 7. Inventory Intelligence

**Icon**: ▥
**For**: Inventory Planners, Store Managers
**Purpose**: Inventory health and optimization

**Views**:
- **Positions**: Current stock by SKU/store
- **Health**: Stock-outs, excess inventory, turnover rate
- **Reorder**: Automated reorder suggestions
- **Forecast**: Demand forecasts by SKU

**Metrics Available** (~30 inventory metrics):
- `stock_on_hand`, `stock_value`, `days_of_supply`
- `turnover_rate`, `stockout_rate`, `excess_stock_pct`
- `reorder_point`, `safety_stock`, `lead_time_days`

---

#### 8. Store Intelligence

**Icon**: ▦
**For**: Store Managers, Executives
**Purpose**: Store-level performance

**Views**:
- **Comparison**: Store rankings by metric
- **Geography**: Sales heatmap by region
- **Performance**: Store KPIs vs. targets
- **Anomalies**: Unusual store performance

**Metrics Available** (~25 store metrics):
- `sales_per_sqft`, `traffic`, `conversion_rate`
- `labor_cost_pct`, `shrinkage_pct`

---

#### 9. Forecast Intelligence

**Icon**: ▧
**For**: Inventory Planners, Analysts
**Purpose**: Demand forecasting

**Models**:
- **Ridge Regression** (hand-written, ~200 LOC)
- **Naive Baseline** (tomorrow = today)

**Features** (~15):
- Calendar: day_of_week, week_of_month, month, is_weekend, is_holiday
- Level: rolling_mean_7d, rolling_mean_14d, rolling_mean_28d
- Trend: pct_change_7d, pct_change_28d

**Quality Gates**:
- **MASE < 1.0** (must beat naive baseline)
- **WAPE** (weighted absolute percentage error) reported

**Example Output**:
```
7-Day Forecast: SKU-12345
─────────────────────────
Aug 16: 1,250 units
Aug 17: 1,320 units
Aug 18: 1,180 units
...

Accuracy: MASE 0.82 (beats naive), WAPE 11%
Confidence: High (beats baseline consistently)

Explanation:
Intercept: 800 units
+ rolling_mean_7d: +420
+ day_of_week (Saturday): +150
+ week_of_month: -20
= 1,250 units
```

---

#### 10. Risk Center

**Icon**: ▨
**For**: Executives, Risk Managers
**Purpose**: Risk monitoring and alert management

**Alert Types**:
- **Financial**: Revenue decline, margin compression, cash flow issues
- **Operational**: Stock-outs, excess inventory, turnover anomalies
- **Customer**: Churn spike, NPS drop, complaint surge

**Severity Levels**:
- **Critical** (🔴): Immediate action required
- **Warning** (🟡): Monitor closely
- **Info** (🔵): Awareness only

---

#### 11. Executive Briefing

**Icon**: ▩
**For**: Executives
**Purpose**: Scheduled reports and summaries

**Report Types**:
- **Daily**: Revenue, orders, top movers
- **Weekly**: Performance vs. targets, key alerts
- **Monthly**: Financial summary, trends, forecasts
- **Quarterly**: Strategic review, goal progress

**Delivery**:
- **Web**: View in workspace
- **Email**: PDF attachment (if SMTP configured)
- **Export**: Excel, PDF

---

#### 12. Admin

**Icon**: ◎
**For**: System Administrators
**Purpose**: User management, permissions, system config

**Features**:
- User CRUD (create, read, update, delete)
- Role assignment
- Permission management
- API key provisioning
- Audit log

---

## Core Capabilities

### 1. Analytics Engine

**What**: Query execution over 23 governed domains with ~300 metrics

**How**:
- User selects domain (e.g., "revenue") and metrics (e.g., ["net_revenue", "aov"])
- Service validates permissions, builds SQL from metric definitions
- Query executes against Gold layer (DuckDB)
- Results cached in Redis (5-minute TTL)

**Output**: Tabular data + metadata (domain, metrics, dimensions, period)

**Example Use**: "Show me revenue and AOV by store for last week"

---

### 2. Root Cause Analysis (RCA)

**What**: Dimensional sweep to explain metric variances

**How**:
1. Calculate variance (current vs. baseline)
2. Sweep 9 dimensions (store, product, customer, channel, day_of_week, payment_method, promotion, region, segment)
3. Identify drivers via contribution analysis
4. Assign evidence tiers (mechanical → statistical → associative)
5. Generate facts, inferences, caveats

**Output**: `AnalystAnswer` with structured findings

**Example Use**: "Why did revenue drop 15% last week?"

---

### 3. Natural Language Query (NLQ)

**What**: Parse natural language questions into analytics queries

**How**:
1. Tokenize question
2. Pattern match against query templates
3. Resolve domain, metrics, dimensions from vocabulary
4. Build query plan
5. Compile to SQL
6. Execute and return results

**NOT generative AI**: Uses deterministic pattern matching, not LLMs

**Example Use**: "What were sales by category last month?"

---

### 4. Recommendations Engine

**What**: Generate actionable suggestions with impact estimation

**7 Generators**:
1. Reorder - Inventory replenishment
2. Markdown - Price reduction
3. Price Adjust - Competitive pricing
4. Promotion - Campaign suggestions
5. Retention - Customer actions
6. Supplier - Supplier switch
7. Allocation - Inventory distribution

**Impact Estimation Methods** (honesty rule - method MUST be declared):
- `forecast_driven` - Based on forecast model
- `elasticity_model` - Price elasticity curve
- `historical_avg` - Historical average impact
- `rule_of_thumb` - Industry benchmark
- `assumed` - Hypothesis (not verified)

**Decision Loop**:
```
Signal → Investigate → Recommend → Decide → Execute → Measure → Calibrate
```

**Example Use**: "System detects low stock → generates reorder recommendation → user accepts → outcome tracked → generator weight adjusted"

---

### 5. Forecasting

**What**: Demand forecasting with quality gates

**Model**: Hand-written ridge regression (closed-form solution, ~200 LOC)

**Features**: ~15 (calendar + level + trend)

**Quality Gate**: MASE < 1.0 (must beat naive baseline)

**Output**: 7-day forecast with accuracy metrics (MASE, WAPE)

**Example Use**: "Forecast demand for SKU-12345 for next 7 days"

---

### 6. AI Investigation Narration (Optional)

**What**: LLM-powered narration of investigation findings

**Status**: Infrastructure exists, **defaults to mock mode**

**How**:
1. RCA engine produces deterministic `AnalystAnswer`
2. If LLM enabled, narrator builds `EvidencePackage` from findings
3. LLM explains evidence (does NOT generate business numbers)
4. Enhanced headline returned, facts/inferences unchanged

**Grounding**: LLM receives only verified evidence, cannot generate business numbers

**Example**:
- **Without LLM**: "Revenue decreased 15% ($1.25M → $1.06M). Store #42 contributed -$120K."
- **With LLM**: "Revenue fell 15% from $1.25M to $1.06M this week. The decline was concentrated at Store #42, which contributed -$120K—roughly 63% of the total variance."

**Enable**: Set `RM_LLM_ANTHROPIC_API_KEY` environment variable

---

## Use Cases

### Use Case 1: Daily Revenue Check (CEO)

**Persona**: CEO Priya

**Time**: 8:00 AM, before first meeting

**Workflow**:
1. Open **Command Center**
2. Read headline: "Net revenue $125K, up 3% vs. yesterday"
3. Check top action: "Reorder SKU-12345 (+$45K projected)"
4. Check alerts: 2 warnings (inventory low at Store #12, margin compression in electronics)
5. Click alert → opens Risk Center with details

**Time**: 30 seconds

---

### Use Case 2: Investigate Revenue Drop (Analyst)

**Persona**: Analyst Vijay

**Time**: 2:00 PM, CFO asked "Why did revenue drop?"

**Workflow**:
1. Open **AI Investigation**
2. Select metric: "revenue"
3. Select current period: Last 7 days
4. Select baseline period: Prior 7 days
5. Click "Investigate"
6. Review findings:
   - Store #42: -$120K (-63% of variance)
   - Weekend: -22% vs. weekday +3%
   - Electronics: -28% ($85K impact)
7. Click "Store #42" → drills into store analytics
8. Export findings as PDF for CFO

**Time**: 3 minutes

---

### Use Case 3: Accept Recommendation (Inventory Planner)

**Persona**: Inventory Planner Anita

**Time**: 10:00 AM, weekly planning

**Workflow**:
1. Open **Decision Center**
2. Review 6 active recommendations
3. Select "Reorder SKU-12345 at Store #042"
4. Review details:
   - Current stock: 50 units (58% below reorder point)
   - Suggested qty: 500 units
   - Expected impact: +$45K revenue (forecast-driven, high confidence)
5. Click "Accept"
6. Add rationale: "Aligns with Q3 inventory strategy"
7. Recommendation sent to procurement system

**Time**: 2 minutes per recommendation

**Follow-up**: System tracks actual outcome (revenue realized) and compares to projected $45K, adjusting recommendation engine weights

---

### Use Case 4: Ad-hoc Query (Store Manager)

**Persona**: Store Manager Raj

**Time**: 4:00 PM, preparing for regional call

**Workflow**:
1. Open **AI Analyst**
2. Ask: "What were sales by category at my store last week?"
3. Platform interprets:
   - Domain: revenue
   - Metrics: net_revenue, orders
   - Dimensions: product_category
   - Filter: store = (user's assigned store)
   - Window: Last 7 days
4. Review results table
5. Review chart (bar chart showing categories)
6. Ask follow-up: "Why did electronics drop 15%?"
7. Platform routes to RCA engine, performs dimensional sweep
8. Review findings

**Time**: 2 minutes (initial query), 3 minutes (follow-up investigation)

---

### Use Case 5: Forecast Review (Inventory Planner)

**Persona**: Inventory Planner Anita

**Time**: Monday morning, weekly forecast review

**Workflow**:
1. Open **Forecast Intelligence**
2. Select SKU: "SKU-12345"
3. Select horizon: 7 days
4. Review forecast:
   - Aug 16: 1,250 units
   - Aug 17: 1,320 units
   - ...
5. Check accuracy: MASE 0.82 (beats naive), WAPE 11%
6. Click "Explain" → see feature contributions:
   - rolling_mean_7d: +420
   - day_of_week (Saturday): +150
7. Export forecast as CSV for procurement

**Time**: 2 minutes per SKU

---

## User Workflows

### Morning Routine (Executive)

**8:00 AM - 8:05 AM**:
1. Command Center (30 seconds) - Read the day
2. Risk Center (1 minute) - Check critical alerts
3. Decision Center (2 minutes) - Review top 3 recommendations
4. Executive Briefing (1 minute) - Scan weekly summary

**Output**: Situational awareness, 1-2 actions flagged for team

---

### Weekly Planning (Inventory Planner)

**Monday 9:00 AM - 10:00 AM**:
1. Forecast Intelligence (15 minutes) - Review 7-day forecasts for top 50 SKUs
2. Inventory Intelligence (15 minutes) - Check stock-out risks, excess inventory
3. Decision Center (20 minutes) - Review 10-15 reorder recommendations, accept/reject
4. Export decisions to procurement system (10 minutes)

**Output**: Reorder plan, risk mitigation actions

---

### Investigation Deep-Dive (Analyst)

**Ad-hoc, triggered by executive question**:
1. AI Analyst (2 minutes) - Ask initial question
2. AI Investigation (3 minutes) - Run RCA if "why" question
3. Sales Intelligence (5 minutes) - Drill into specific dimension (e.g., store, product)
4. Customer Intelligence (5 minutes) - Check customer behavior patterns (if revenue drop)
5. Compile findings into slide deck (15 minutes)

**Output**: Root cause summary with evidence, presented to executive

---

### Monthly Close (CFO)

**Last day of month, 4:00 PM - 5:00 PM**:
1. Command Center (5 minutes) - Month-end snapshot
2. Sales Intelligence (15 minutes) - Review month performance vs. targets
3. Executive Briefing (20 minutes) - Review auto-generated monthly report
4. AI Analyst (10 minutes) - Ask clarifying questions (e.g., "Why did margin compress?")
5. Export report as PDF for board (10 minutes)

**Output**: Monthly board report with variance explanations

---

## Key Differentiators

### 1. Evidence-Based AI

**Not**:
- ❌ Chatbot that generates prose answers
- ❌ LLM that calculates business metrics
- ❌ Black-box predictions without explanation

**Instead**:
- ✅ Deterministic analytical engines produce facts
- ✅ LLM (optional) explains verified facts, does NOT generate numbers
- ✅ Full transparency: query plan, SQL, data, chart, caveats

**Example**: "Revenue decreased 15%" comes from SQL query, not LLM. LLM may rephrase as "Revenue fell 15% this week" but the number 15% is database-sourced.

---

### 2. Governed Metrics

**Not**:
- ❌ Free-text SQL generation
- ❌ Inconsistent metric definitions across reports
- ❌ Undocumented business logic

**Instead**:
- ✅ 23 analytics domains with ~300 metrics
- ✅ SQL expressions centrally defined (e.g., `aov = sum(net_revenue) / sum(orders)`)
- ✅ Enforced via metric registry, version-controlled

**Example**: Every query for "AOV" uses the same SQL expression, regardless of user or workspace.

---

### 3. Quality Gates

**Not**:
- ❌ Deploy any model to production
- ❌ "AI accuracy: 95%" without definition
- ❌ Forecasts worse than naive baseline

**Instead**:
- ✅ Forecasts must beat naive baseline (MASE < 1.0)
- ✅ Backtest validation (walk-forward, expanding window)
- ✅ Explicit accuracy reporting (WAPE, MASE)

**Example**: If ridge regression MASE = 1.1 (worse than naive), model is rejected, naive baseline is used.

---

### 4. Decision Loop Closes

**Not**:
- ❌ Recommendations disappear into email
- ❌ No tracking of accept/reject decisions
- ❌ No measurement of actual outcomes

**Instead**:
- ✅ Accept/reject/defer tracked with rationale
- ✅ Outcomes measured (actual vs. projected impact)
- ✅ Calibration loop adjusts generator weights based on accuracy

**Example**: Reorder recommendation projected +$45K revenue. Actual outcome: +$48K. Generator weight increased (model is calibrated).

---

### 5. Self-Hosted Data Residency

**Not**:
- ❌ Data uploaded to vendor cloud
- ❌ Metrics calculated externally
- ❌ Dependency on external API availability

**Instead**:
- ✅ All data stays on-premises
- ✅ All analytical engines run locally
- ✅ LLM calls optional (defaults to mock mode)

**Example**: Revenue, inventory, customer data never leaves the server. Claude API (if enabled) receives only text evidence, not raw data.

---

### 6. Transparent Limitations

**Not**:
- ❌ "AI knows everything"
- ❌ Confidence scores without explanation
- ❌ Answers without caveats

**Instead**:
- ✅ Every investigation includes "Did not check" section
- ✅ Evidence tiered by provenance (mechanical → statistical → associative)
- ✅ Caveats always visible (never hidden behind click)

**Example**:
```
Facts:
✓ Store #42 contributed -$120K

Caveats:
• Did not check: supplier delays, local events, weather
• Correlation does not imply causation
• Data as of 2026-08-14 (may lag real-time by hours)
```

---

## Limitations

### What RetailMind AI Is NOT

**Not a transaction system**:
- Does not process orders, payments, or inventory movements
- Reads data from external systems (POS, ERP, CRM)

**Not real-time**:
- Data refreshed on schedule (default: hourly ingest → daily dbt)
- Analytics reflect warehouse state, not live transactions

**Not a forecasting oracle**:
- Forecasts are statistical predictions, not guarantees
- Quality gate ensures they beat naive baseline, not that they're perfect

**Not a chatbot**:
- Natural language interface routes to specialized engines
- Does not engage in open-ended conversation
- Declines questions it cannot answer rather than approximating

**Not a data warehouse**:
- Builds semantic layer (Gold) on top of warehouse (Bronze → Silver → Gold)
- Warehouse (DuckDB) is embedded, not a separate data platform

---

## Success Metrics

### User Adoption

**Target**: 80% weekly active users (logged in past 7 days)

**Measure**: Login events, workspace visits

---

### Time to Insight

**Target**: <3 minutes from question to answer

**Measure**: Median time from workspace open to first action

---

### Decision Velocity

**Target**: 90% of recommendations decided within 48 hours

**Measure**: Time from recommendation creation to accept/reject/defer

---

### Forecast Accuracy

**Target**: MASE < 0.9 (10% better than naive baseline)

**Measure**: Backtest MASE across all SKUs

---

### Investigation Quality

**Target**: 85% of RCA findings actionable (lead to decision)

**Measure**: User feedback, decision tracking

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
