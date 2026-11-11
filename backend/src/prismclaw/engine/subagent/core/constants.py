"""Shared constants for the ``subagent`` package.

These constants were previously defined in ``executor.py``,
causing a circular import when ``agent_instance.py`` was added.

Moving them here allows both modules to import without cycles.
"""

# Hard ceiling for sub-agent timeout (seconds).
# Mirrors the old ``_TIMEOUT_HARD_CEILING_S`` in executor.py.
TIMEOUT_HARD_CEILING_S: float = 1800.0

# Maximum number of preloaded-skill files to scan per layer.
MAX_FILES_PER_LAYER: int = 200

# Maximum file size (bytes) for a preloaded-skill manifest.
MAX_FILE_BYTES: int = 256_000
