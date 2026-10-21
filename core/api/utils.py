"""Shared helpers reused across public API endpoints."""

from __future__ import annotations

import ipaddress
import os
import time
from functools import lru_cache

from fastapi import HTTPException, Request

# Proxies whose X-Forwarded-For we believe: loopback and private networks, which
# covers nginx on the same host and the Docker bridge. TRUSTED_PROXIES (comma-
# separated IPs/CIDRs) adds more, e.g. a load balancer or Cloudflare's ranges.
_DEFAULT_TRUSTED_PROXIES = (
    "127.0.0.0/8", "::1/128",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "fc00::/7", "fe80::/10",
)


@lru_cache(maxsize=8)
def _trusted_networks(extra: str) -> tuple:
    nets = []
    for cidr in (*_DEFAULT_TRUSTED_PROXIES, *extra.split(",")):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _is_trusted_proxy(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_networks(os.getenv("TRUSTED_PROXIES", "")))


def client_ip(request: Request) -> str:
    """The caller's IP, for rate limits.

    X-Forwarded-For is only read when the direct peer is a trusted proxy, and
    then from the right: nginx appends the address it saw to whatever the
    client sent, so the leftmost entries are attacker-controlled. The first
    untrusted hop from the right is the real client.
    """
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or not _is_trusted_proxy(peer):
        return peer
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop):
            return hop
    return hops[0] if hops else peer


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
