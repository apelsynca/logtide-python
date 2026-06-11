"""Single source of truth for the SDK version at runtime.

Kept in its own module (instead of ``__init__``) so internal modules can
import it without circular imports. Bump together with ``pyproject.toml``.
"""

VERSION = "0.9.4"
SDK_NAME = "logtide-python"
