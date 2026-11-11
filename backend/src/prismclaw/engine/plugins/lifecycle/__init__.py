"""插件生命周期子包。"""

from prismclaw.engine.plugins.lifecycle.state import (
    IllegalStateTransitionError,
    PluginLifecycleState,
    assert_transition,
    can_transition,
)
from prismclaw.engine.plugins.lifecycle.activator import (
    PluginActivationRecord,
    PluginActivator,
)

__all__ = [
    "IllegalStateTransitionError",
    "PluginActivationRecord",
    "PluginActivator",
    "PluginLifecycleState",
    "assert_transition",
    "can_transition",
]
