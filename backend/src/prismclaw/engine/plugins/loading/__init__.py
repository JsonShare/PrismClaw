"""插件加载子包。"""

from prismclaw.engine.plugins.loading.load_result import (
    LoadedPlugin,
    ManifestParseError,
    ManifestValidationError,
    PluginActivationError,
    PluginActivationRuntimeError,
    PluginDependencyError,
    PluginError,
    PluginLoadBatch,
    PluginLoadResult,
)
from prismclaw.engine.plugins.loading.load_strategy import (
    DeclarativePluginLoadStrategy,
    NativePythonPluginLoadStrategy,
    PluginLoadStrategy,
    TraePluginLoadStrategy,
    default_strategies,
)
from prismclaw.engine.plugins.loading.agent_loader import PluginAgentLoader
from prismclaw.engine.plugins.loading.cache import PluginLoadCache
from prismclaw.engine.plugins.loading.discovery_engine import (
    PluginDirectorySource,
    PluginDiscoveryEngine,
    PluginDiscoveryResult,
    PluginLoadOrchestrator,
)
from prismclaw.engine.plugins.loading.merge import (
    MergedResult,
    merge_plugin_sources,
)
from prismclaw.engine.plugins.loading.dependency import (
    DependencyCheckResult,
    parse_dependency_ref,
    verify_and_demote,
)
from prismclaw.engine.plugins.loading.install_store import (
    InstalledPlugin,
    InstalledPluginsFile,
    InstalledPluginsStore,
)
from prismclaw.engine.plugins.loading.installer import (
    PluginInstallError,
    PluginSourceSpec,
    install_plugin,
    parse_source_spec,
)
from prismclaw.engine.plugins.loading.data_dir import (
    DEFAULT_DATA_BASE_DIRNAME,
    get_plugin_data_dir,
    resolve_data_base_dir,
)

__all__ = [
    "DEFAULT_DATA_BASE_DIRNAME",
    "DeclarativePluginLoadStrategy",
    "DependencyCheckResult",
    "InstalledPlugin",
    "InstalledPluginsFile",
    "InstalledPluginsStore",
    "LoadedPlugin",
    "ManifestParseError",
    "ManifestValidationError",
    "MergedResult",
    "NativePythonPluginLoadStrategy",
    "PluginActivationError",
    "PluginActivationRuntimeError",
    "PluginAgentLoader",
    "PluginDependencyError",
    "PluginDirectorySource",
    "PluginDiscoveryEngine",
    "PluginDiscoveryResult",
    "PluginError",
    "PluginInstallError",
    "PluginLoadBatch",
    "PluginLoadCache",
    "PluginLoadOrchestrator",
    "PluginLoadResult",
    "PluginLoadStrategy",
    "PluginSourceSpec",
    "TraePluginLoadStrategy",
    "default_strategies",
    "get_plugin_data_dir",
    "install_plugin",
    "merge_plugin_sources",
    "parse_dependency_ref",
    "parse_source_spec",
    "resolve_data_base_dir",
    "verify_and_demote",
]
