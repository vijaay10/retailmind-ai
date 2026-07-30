"""SQLAlchemy model registry.

Importing this package registers every table on ``Base.metadata`` — the single
metadata object Alembic (env.py), the genesis migration, and tests all consume.
A model not imported here does not exist as far as migrations are concerned,
so keep this list exhaustive.
"""

from app.infrastructure.db.models.ai import (
    Insight,
    InsightFeedback,
    LlmUsage,
    NlqSession,
    NlqTurn,
    RcaResult,
)
from app.infrastructure.db.models.alerts import Alert, AlertEvent, AlertMute
from app.infrastructure.db.models.auth import (
    ApiKey,
    AppUser,
    RefreshToken,
    Role,
    Tenant,
    UserRole,
)
from app.infrastructure.db.models.base import Base
from app.infrastructure.db.models.config import (
    AlertRule,
    ChannelPref,
    ConnectorConfig,
    FeatureFlagState,
    MetricConfig,
)
from app.infrastructure.db.models.dashboards import Dashboard, DashboardTile, SavedQuery
from app.infrastructure.db.models.notifications import Notification
from app.infrastructure.db.models.pipeline import (
    DqResult,
    JobRun,
    PipelineRun,
    QuarantineBatch,
)
from app.infrastructure.db.models.platform import (
    AuditEvent,
    AuthEvent,
    DataSnapshot,
    MetricRegistryVersion,
)
from app.infrastructure.db.models.recommendations import (
    Recommendation,
    RecommendationFeedback,
    RecommendationOutcome,
)
from app.infrastructure.db.models.reports import ReportRun, ReportSchedule, ReportSection
from app.infrastructure.db.models.scenarios import Scenario, ScenarioRun

__all__ = [
    "Alert",
    "AlertEvent",
    "AlertMute",
    "AlertRule",
    "ApiKey",
    "AppUser",
    "AuditEvent",
    "AuthEvent",
    "Base",
    "ChannelPref",
    "ConnectorConfig",
    "Dashboard",
    "DashboardTile",
    "DataSnapshot",
    "DqResult",
    "FeatureFlagState",
    "Insight",
    "InsightFeedback",
    "JobRun",
    "LlmUsage",
    "MetricConfig",
    "MetricRegistryVersion",
    "NlqSession",
    "NlqTurn",
    "Notification",
    "PipelineRun",
    "QuarantineBatch",
    "RcaResult",
    "Recommendation",
    "RecommendationFeedback",
    "RecommendationOutcome",
    "RefreshToken",
    "ReportRun",
    "ReportSchedule",
    "ReportSection",
    "Role",
    "SavedQuery",
    "Scenario",
    "ScenarioRun",
    "Tenant",
    "UserRole",
]
