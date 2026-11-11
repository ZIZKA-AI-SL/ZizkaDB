from .client import ZizkaDB
from .context import LineageContext
from .instrumentation import Instrumentation, instrument
from .exceptions import ZizkaDBError, AuthError, NotFoundError, RateLimitError, AgentScopeError

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("zizkadb-sdk")
except Exception:
    __version__ = "0.6.0"

__all__ = [
    "ZizkaDB",
    "LineageContext",
    "instrument",
    "Instrumentation",
    "ZizkaDBError",
    "AuthError",
    "NotFoundError",
    "RateLimitError",
    "AgentScopeError",
    "__version__",
]
