"""Validation for caller-supplied URLs before we fetch them.

``/ingest/url`` and the ``index_url`` MCP tool fetch a URL and make the result
searchable, which turns the backend into a fetch-on-demand proxy running inside
the user's network. Without a check, "index this article for me" can be pointed
at a router admin page, a cloud metadata endpoint, or another service on
localhost, and the response comes back out through search.

Every hop is validated, not just the first: a public URL is free to redirect to
169.254.169.254, so the caller must follow redirects manually and re-check.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

#: Redirect hops allowed before we give up.
MAX_REDIRECTS = 5

#: Cap on a fetched body, so one pathological page cannot exhaust memory.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class UrlNotAllowedError(ValueError):
    """Raised when a URL may not be fetched."""


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that is not a normal public internet host."""
    return (
        ip.is_private  # RFC1918, plus IPv6 ULA
        or ip.is_loopback
        or ip.is_link_local  # includes 169.254.169.254, the metadata address
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolved_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address *host* resolves to, so a multi-record name cannot sneak one past."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UrlNotAllowedError(f"Cannot resolve host {host!r}") from exc

    addresses = []
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:  # noqa: PERF203 — non-IP sockaddr, nothing to check
            continue
    if not addresses:
        raise UrlNotAllowedError(f"Cannot resolve host {host!r}")
    return addresses


def check_url(url: str, *, allow_private: bool = False) -> None:
    """Raise UrlNotAllowedError unless *url* is safe to fetch.

    Args:
        url: The URL about to be requested.
        allow_private: Permit private/loopback targets — for an intranet wiki
            the operator explicitly trusts.
    """
    parts = urlsplit(url)

    if parts.scheme not in {"http", "https"}:
        raise UrlNotAllowedError(
            f"Only http and https URLs can be indexed, got {parts.scheme or 'no'} scheme"
        )
    if not parts.hostname:
        raise UrlNotAllowedError(f"URL has no host: {url!r}")

    if allow_private:
        return

    for ip in _resolved_addresses(parts.hostname):
        if _is_blocked_address(ip):
            raise UrlNotAllowedError(
                f"Refusing to fetch {parts.hostname!r}: it resolves to {ip}, which is "
                f"on the local network or otherwise reserved. Set "
                f"OBSIDIAN_SEARCH_ALLOW_PRIVATE_URLS=true to allow this."
            )
