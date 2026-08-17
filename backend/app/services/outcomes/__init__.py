"""Recommendation outcome measurement — data model only; no service yet.

`app.services.outcomes.measurement.OutcomeMeasurementService` does not exist
— `measurement.py` was never written, only `models.py`. This package's
`__init__.py` imported it anyway, which made `from app.services.outcomes
import ...` raise `ModuleNotFoundError` for anyone who tried. Nothing in the
codebase currently does (checked during Prompt 10.5 remediation), so this was
latent rather than an active outage — fixed here by exporting only what
actually exists, not by writing the missing service, which would be new
functionality out of scope for a remediation pass.
"""

from app.services.outcomes.models import MeasurementResult, OutcomeRecord

__all__ = ["MeasurementResult", "OutcomeRecord"]
