"""Shared helpers reused across public API endpoints."""

from __future__ import annotations

import time

from fastapi import HTTPException, Request


import ipaddress
import os

_DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1,testclient"


def _is_trusted_proxy(ip: str | None) -> bool:
    if not ip or ip == "unknown":
        return False
    trusted_raw = os.getenv("TRUSTED_PROXIES", _DEFAULT_TRUSTED_PROXIES)
    for item in trusted_raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ip == item:
            return True
        try:
            if "/" in item and ipaddress.ip_address(ip) in ipaddress.ip_network(item):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    peer_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and _is_trusted_proxy(peer_ip):
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[0]
    return peer_ip


def check_rate(
    store: dict[str, list[float]],
    ip: str,
    window_sec: int,
    max_hits: int,
    detail: str = "Too many requests. Try again later.",
) -> None:
    now = time.time()
    hits = [t for t in store.get(ip, []) if now - t < window_sec]
    if len(hits) >= max_hits:
        raise HTTPException(status_code=429, detail=detail)
    hits.append(now)
    store[ip] = hits
