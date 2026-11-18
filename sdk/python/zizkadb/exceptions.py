class ZizkaDBError(Exception):
    """Base exception for ZizkaDB SDK."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class AuthError(ZizkaDBError):
    """Invalid or missing API key."""


class NotFoundError(ZizkaDBError):
    """Event or agent not found."""


class RateLimitError(ZizkaDBError):
    """Too many requests."""


class AgentScopeError(ZizkaDBError):
    """API key is scoped to a different agent than the request."""
