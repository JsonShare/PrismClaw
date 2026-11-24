"""插件生命周期子包。"""

from prismclaw.engine.plugins.lifecycle.activator import (
    PluginActivationRecord,
    PluginActivator,
)
from prismclaw.engine.plugins.lifecycle.state import (
    IllegalStateTransitionError,
    PluginLifecycleState,
    assert_transition,
    can_transition,
)

__all__ = [
    "IllegalStateTransitionError",
    "PluginActivationRecord",
    "PluginActivator",
    "PluginLifecycleState",
    "assert_transition",
    "can_transition",
]
