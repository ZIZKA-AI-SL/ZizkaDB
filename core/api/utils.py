"""Shared helpers reused across public API endpoints."""

from __future__ import annotations

import ipaddress
import logging
import os
import time

from fastapi import HTTPException, Request


log = logging.getLogger(__name__)

# Docker bridge networks live in 172.16.0.0/12; in production nginx on the host
# proxies to 127.0.0.1:8001, so the API container sees the bridge gateway as peer.
_DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1,172.16.0.0/12"

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_trusted_proxies(raw: str) -> tuple[IPNetwork, ...]:
    """Parse a comma-separated list of IPs/CIDRs into networks, skipping bad entries."""
    networks: list[IPNetwork] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.warning("Ignoring invalid TRUSTED_PROXIES entry: %r", item)
    return tuple(networks)


# Parsed once at import; tests override by monkeypatching this constant.
TRUSTED_PROXIES = parse_trusted_proxies(
    os.getenv("TRUSTED_PROXIES", _DEFAULT_TRUSTED_PROXIES)
)


def _is_trusted_proxy(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXIES)


def client_ip(request: Request) -> str:
    """Return the first untrusted address, walking X-Forwarded-For from the right.

    Proxies such as nginx (``$proxy_add_x_forwarded_for``) append the address
    they saw, so only the right-most hops are trustworthy. Anything left of the
    first untrusted hop is client-controlled and is ignored.
    """
    peer_ip = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(peer_ip):
        return peer_ip
    forwarded = request.headers.get("x-forwarded-for") or ""
    hops = [p.strip() for p in forwarded.split(",") if p.strip()]
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop):
            return hop
    # Every hop is a trusted proxy: the left-most is the closest to the client.
    return hops[0] if hops else peer_ip


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
