class ZizkaDBError(Exception):
    """Base exception for ZizkaDB SDK."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class AuthError(ZizkaDBError):
    """Invalid or missing API key."""


class NotFoundError(ZizkaDBError):
    """Event or agent not found. ``detail`` is the server's message, if any."""

    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None):
        self.detail = detail
        super().__init__(message, status_code)


class CloudOnlyError(ZizkaDBError):
    """The feature needs ZizkaDB Cloud (multi-agent); self-hosted runs one agent."""


class RateLimitError(ZizkaDBError):
    """Too many requests."""


class AgentScopeError(ZizkaDBError):
    """API key is scoped to a different agent than the request."""
