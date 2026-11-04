"""
Centralized exception helpers for ZizkaDB.

Provides a unified factory function `make_exception` and semantic wrapper functions
for raising FastAPI HTTPExceptions across routers and services.
"""

from typing import Any, Dict, Optional
from fastapi import HTTPException, status

def make_exception(
    status_code: int,
    detail: Any,
    headers: Optional[Dict[str, str]] = None,
    **kwargs: Any
) -> HTTPException:
    """
    Centralized factory to construct a FastAPI HTTPException.
    
    Supports arbitrary kwargs to ensure future extendability (e.g., background tasks).
    Automatically attaches WWW-Authenticate headers for 401 Unauthorized responses.
    """
    if headers is None:
        headers = {}
    
    if status_code == status.HTTP_401_UNAUTHORIZED and "WWW-Authenticate" not in headers:
        headers["WWW-Authenticate"] = "Bearer"
        
    exc = HTTPException(
        status_code=status_code,
        detail=detail,
        headers=headers
    )
    for key, value in kwargs.items():
        setattr(exc, key, value)
    return exc

# Semantic wrapper helpers for common statuses using fastapi.status constants

def bad_request(detail: Any = "Bad Request", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 400 Bad Request HTTPException."""
    return make_exception(status.HTTP_400_BAD_REQUEST, detail, headers, **kwargs)

def unauthorized(detail: Any = "Unauthorized", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 401 Unauthorized HTTPException with Bearer WWW-Authenticate header."""
    return make_exception(status.HTTP_401_UNAUTHORIZED, detail, headers, **kwargs)

def forbidden(detail: Any = "Forbidden", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 403 Forbidden HTTPException."""
    return make_exception(status.HTTP_403_FORBIDDEN, detail, headers, **kwargs)

def not_found(detail: Any = "Not Found", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 404 Not Found HTTPException."""
    return make_exception(status.HTTP_404_NOT_FOUND, detail, headers, **kwargs)

def conflict(detail: Any = "Conflict", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 409 Conflict HTTPException."""
    return make_exception(status.HTTP_409_CONFLICT, detail, headers, **kwargs)

def payload_too_large(detail: Any = "Payload Too Large", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 413 Content Too Large HTTPException."""
    return make_exception(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail, headers, **kwargs)

def rate_limit_exceeded(detail: Any = "Rate limit exceeded", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 429 Too Many Requests HTTPException."""
    return make_exception(status.HTTP_429_TOO_MANY_REQUESTS, detail, headers, **kwargs)

def service_unavailable(detail: Any = "Service Unavailable", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 503 Service Unavailable HTTPException."""
    return make_exception(status.HTTP_503_SERVICE_UNAVAILABLE, detail, headers, **kwargs)

def internal_error(detail: Any = "Internal Server Error", headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> HTTPException:
    """Return a 500 Internal Server Error HTTPException."""
    return make_exception(status.HTTP_500_INTERNAL_SERVER_ERROR, detail, headers, **kwargs)
