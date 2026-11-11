"""Assembly-level wiring functions — moved from app.state to eliminate
assembly→app upward imports.

Only functions whose entire import graph is downward (infra/runtime/schema)
live here. Functions that import app.web.* stay in app.state and are invoked
via app.state callbacks instead.
"""
from prismclaw.assembly.wiring.runtime_mode import apply_runtime_mode_state
from prismclaw.assembly.wiring.runtime_state import (
    resolve_dream_boundary,
    resolve_dream_scheduler,
)

__all__ = [
    "apply_runtime_mode_state",
    "resolve_dream_boundary",
    "resolve_dream_scheduler",
]
