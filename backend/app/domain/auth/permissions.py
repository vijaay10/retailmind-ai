"""Permission catalog and the role → permission matrix (Backend design §9–10).

Design rule that makes this file the *only* place roles are interpreted:
**services ask for permissions, never for roles.** Nothing outside this module
may branch on ``role == "admin"``. That indirection is what lets the role
catalog grow (3 → 7 roles here) without touching a single enforcement site.

Permissions are fine-grained verbs in two families:

``<area>.<verb>``
    Actions — what a principal may *do* (query, export, acknowledge, schedule).
``analytics.<module>.read``
    Module visibility — which analytics surfaces a principal may *see*. These
    are what actually distinguish Marketing from Inventory from Finance; the
    action verbs are largely shared across operating roles.

Data-scope restriction (a Regional Manager seeing only their region, a Store
Manager only their store) is **not** expressible in RBAC and is deliberately
out of scope here: v1 enforces tenant isolation structurally (repositories) and
module/action permissions here. Row-level entitlements are the documented ABAC
follow-on (Backend §9 Future Improvements) and will attach to the Principal as
a scope object without changing this matrix.
"""

from enum import StrEnum


class Permission(StrEnum):
    """Every capability the platform can grant. Additive-only: removing a
    member is a breaking change for stored role grants."""

    # ── Analytics module visibility (Analytics design modules 1–10) ──
    ANALYTICS_REVENUE_READ = "analytics.revenue.read"
    ANALYTICS_CUSTOMER_READ = "analytics.customer.read"
    ANALYTICS_STORE_READ = "analytics.store.read"
    ANALYTICS_INVENTORY_READ = "analytics.inventory.read"
    ANALYTICS_MARKETING_READ = "analytics.marketing.read"
    ANALYTICS_SUPPLIER_READ = "analytics.supplier.read"
    ANALYTICS_PROFITABILITY_READ = "analytics.profitability.read"
    ANALYTICS_OPERATIONS_READ = "analytics.operations.read"

    # ── Dashboards & metrics ──
    DASHBOARDS_READ = "dashboards.read"
    DASHBOARDS_WRITE = "dashboards.write"
    METRICS_QUERY = "metrics.query"
    METRICS_EXPORT = "metrics.export"

    # ── Intelligence surfaces ──
    INSIGHTS_READ = "insights.read"
    ALERTS_READ = "alerts.read"
    ALERTS_ACK = "alerts.ack"
    ALERTS_CONFIGURE = "alerts.configure"
    RCA_RUN = "rca.run"
    NLQ_ASK = "nlq.ask"
    FORECASTS_READ = "forecasts.read"
    FORECASTS_EXPORT = "forecasts.export"
    RECOMMENDATIONS_READ = "recommendations.read"
    RECOMMENDATIONS_ACT = "recommendations.act"
    SCENARIOS_RUN = "scenarios.run"

    # ── Reporting ──
    REPORTS_READ = "reports.read"
    REPORTS_SCHEDULE = "reports.schedule"

    # ── Data platform ──
    DATA_READ = "data.read"
    DATA_MANAGE = "data.manage"

    # ── Administration ──
    ADMIN_USERS = "admin.users"
    ADMIN_ROLES = "admin.roles"
    ADMIN_WORKSPACE = "admin.workspace"
    ADMIN_CONNECTORS = "admin.connectors"
    ADMIN_METRICS_CONFIG = "admin.metrics_config"
    ADMIN_BUDGETS = "admin.budgets"
    AUDIT_READ = "audit.read"


class RoleKey(StrEnum):
    """The enterprise role catalog.

    Ordered by breadth of access, not seniority: ADMIN governs the platform,
    CEO sees the whole business, and the functional roles are scoped to the
    analytics modules they operate.
    """

    ADMIN = "admin"
    CEO = "ceo"
    REGIONAL_MANAGER = "regional_manager"
    STORE_MANAGER = "store_manager"
    MARKETING = "marketing"
    INVENTORY = "inventory"
    FINANCE = "finance"


# ── Composable permission bundles ────────────────────────────────────
# Bundles exist so the matrix below reads as intent ("this role investigates")
# rather than as a wall of verbs. They are an authoring convenience only —
# authorization always resolves to the flat permission set.

_BASE_READ = frozenset(
    {
        Permission.DASHBOARDS_READ,
        Permission.INSIGHTS_READ,
        Permission.REPORTS_READ,
        Permission.ANALYTICS_REVENUE_READ,
    }
)
"""Everyone who can log in can see the business at headline level."""

_ANALYST_TOOLKIT = frozenset(
    {
        Permission.METRICS_QUERY,
        Permission.METRICS_EXPORT,
        Permission.DASHBOARDS_WRITE,
        Permission.NLQ_ASK,
        Permission.RCA_RUN,
    }
)
"""Self-service investigation: query, explore, ask, and dig into causes."""

_ALERT_OPERATOR = frozenset(
    {
        Permission.ALERTS_READ,
        Permission.ALERTS_ACK,
    }
)
"""Sees alerts *and* owns their lifecycle — acknowledging is accountability."""

_ACTION_TAKER = frozenset(
    {
        Permission.RECOMMENDATIONS_READ,
        Permission.RECOMMENDATIONS_ACT,
        Permission.SCENARIOS_RUN,
    }
)
"""Closes the Detected → Diagnosed → **Decided** loop."""

_FORECAST_CONSUMER = frozenset(
    {
        Permission.FORECASTS_READ,
        Permission.FORECASTS_EXPORT,
    }
)

_ADMIN_SUITE = frozenset(
    {
        Permission.ADMIN_USERS,
        Permission.ADMIN_ROLES,
        Permission.ADMIN_WORKSPACE,
        Permission.ADMIN_CONNECTORS,
        Permission.ADMIN_METRICS_CONFIG,
        Permission.ADMIN_BUDGETS,
        Permission.AUDIT_READ,
        Permission.ALERTS_CONFIGURE,
        Permission.DATA_MANAGE,
    }
)

_ALL_ANALYTICS = frozenset(
    {
        Permission.ANALYTICS_REVENUE_READ,
        Permission.ANALYTICS_CUSTOMER_READ,
        Permission.ANALYTICS_STORE_READ,
        Permission.ANALYTICS_INVENTORY_READ,
        Permission.ANALYTICS_MARKETING_READ,
        Permission.ANALYTICS_SUPPLIER_READ,
        Permission.ANALYTICS_PROFITABILITY_READ,
        Permission.ANALYTICS_OPERATIONS_READ,
    }
)


ROLE_PERMISSIONS: dict[RoleKey, frozenset[Permission]] = {
    # Platform governance. Deliberately NOT a business-analytics superuser:
    # an admin manages users, connectors, budgets, and audit. They can read
    # dashboards (they must verify the platform works) but do not get
    # module-level business visibility by default — least privilege applied to
    # the most powerful role, and the reason admin ≠ CEO here.
    RoleKey.ADMIN: _BASE_READ
    | _ADMIN_SUITE
    | {
        Permission.ALERTS_READ,
        Permission.DATA_READ,
        Permission.METRICS_QUERY,
    },
    # Whole-business visibility with full self-service, but no platform
    # configuration and no operational lifecycle actions — an executive
    # consumes and decides, they do not tune detectors or replay pipelines.
    RoleKey.CEO: _BASE_READ
    | _ALL_ANALYTICS
    | _ANALYST_TOOLKIT
    | _FORECAST_CONSUMER
    | {
        Permission.ALERTS_READ,
        Permission.RECOMMENDATIONS_READ,
        Permission.SCENARIOS_RUN,
        Permission.REPORTS_SCHEDULE,
        Permission.DATA_READ,
    },
    # Owns a region's P&L: broad operating visibility, full investigation
    # toolkit, and authority to act on alerts and recommendations.
    RoleKey.REGIONAL_MANAGER: _BASE_READ
    | _ANALYST_TOOLKIT
    | _ALERT_OPERATOR
    | _ACTION_TAKER
    | _FORECAST_CONSUMER
    | {
        Permission.ANALYTICS_STORE_READ,
        Permission.ANALYTICS_INVENTORY_READ,
        Permission.ANALYTICS_CUSTOMER_READ,
        Permission.ANALYTICS_MARKETING_READ,
        Permission.ANALYTICS_PROFITABILITY_READ,
        Permission.ANALYTICS_OPERATIONS_READ,
        Permission.REPORTS_SCHEDULE,
    },
    # Runs one store: sees store and inventory performance, acts on what lands
    # in their inbox. No export (data leaves the building via managers), no
    # scenario modelling, no scheduling.
    RoleKey.STORE_MANAGER: _BASE_READ
    | _ALERT_OPERATOR
    | {
        Permission.ANALYTICS_STORE_READ,
        Permission.ANALYTICS_INVENTORY_READ,
        Permission.ANALYTICS_OPERATIONS_READ,
        Permission.METRICS_QUERY,
        Permission.NLQ_ASK,
        Permission.RCA_RUN,
        Permission.RECOMMENDATIONS_READ,
        Permission.RECOMMENDATIONS_ACT,
        Permission.FORECASTS_READ,
    },
    # Promotions, customers, and campaign effectiveness.
    RoleKey.MARKETING: _BASE_READ
    | _ANALYST_TOOLKIT
    | _ALERT_OPERATOR
    | _FORECAST_CONSUMER
    | {
        Permission.ANALYTICS_MARKETING_READ,
        Permission.ANALYTICS_CUSTOMER_READ,
        Permission.ANALYTICS_STORE_READ,
        Permission.RECOMMENDATIONS_READ,
        Permission.RECOMMENDATIONS_ACT,
        Permission.SCENARIOS_RUN,
        Permission.REPORTS_SCHEDULE,
    },
    # Demand planning and replenishment — the heaviest forecast consumer.
    RoleKey.INVENTORY: _BASE_READ
    | _ANALYST_TOOLKIT
    | _ALERT_OPERATOR
    | _ACTION_TAKER
    | _FORECAST_CONSUMER
    | {
        Permission.ANALYTICS_INVENTORY_READ,
        Permission.ANALYTICS_SUPPLIER_READ,
        Permission.ANALYTICS_STORE_READ,
        Permission.ANALYTICS_OPERATIONS_READ,
        Permission.DATA_READ,
    },
    # Margin, cost, and supplier economics. Reads everything money touches and
    # exports freely; does not act on operational recommendations.
    RoleKey.FINANCE: _BASE_READ
    | _ANALYST_TOOLKIT
    | _FORECAST_CONSUMER
    | {
        Permission.ANALYTICS_PROFITABILITY_READ,
        Permission.ANALYTICS_SUPPLIER_READ,
        Permission.ANALYTICS_INVENTORY_READ,
        Permission.ANALYTICS_STORE_READ,
        Permission.ALERTS_READ,
        Permission.RECOMMENDATIONS_READ,
        Permission.SCENARIOS_RUN,
        Permission.REPORTS_SCHEDULE,
        Permission.DATA_READ,
    },
}

ROLE_DESCRIPTIONS: dict[RoleKey, str] = {
    RoleKey.ADMIN: "Platform administration: users, connectors, budgets, audit",
    RoleKey.CEO: "Whole-business visibility, self-service analysis, scenario modelling",
    RoleKey.REGIONAL_MANAGER: "Regional performance ownership; acts on alerts and recommendations",
    RoleKey.STORE_MANAGER: (
        "Store-level performance and inventory; acts on assigned recommendations"
    ),
    RoleKey.MARKETING: "Promotions, campaigns, and customer analytics",
    RoleKey.INVENTORY: "Demand planning, replenishment, and supplier performance",
    RoleKey.FINANCE: "Margin, profitability, and cost analytics with export rights",
}


def permissions_for(roles: frozenset[RoleKey] | set[RoleKey]) -> frozenset[Permission]:
    """Resolve a role set to its flat permission set (union — roles are additive).

    A principal holding several roles gets the union of their permissions;
    there is no deny-list, because a subtractive rule in an additive model is
    the kind of thing nobody can reason about at 3 a.m.
    """
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(granted)
