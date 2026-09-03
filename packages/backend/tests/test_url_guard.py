"""Tests for the SSRF guard on caller-supplied URLs."""

from __future__ import annotations

import ipaddress
import unittest.mock as mock

import pytest
from obsidian_search.ingestion.url_guard import UrlNotAllowedError, check_url


def _resolves_to(*addresses: str) -> mock._patch[mock.MagicMock]:
    return mock.patch(
        "obsidian_search.ingestion.url_guard._resolved_addresses",
        return_value=[ipaddress.ip_address(a) for a in addresses],
    )


class TestScheme:
    def test_https_allowed(self) -> None:
        with _resolves_to("93.184.216.34"):
            check_url("https://example.com/post")

    def test_http_allowed(self) -> None:
        with _resolves_to("93.184.216.34"):
            check_url("http://example.com/post")

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com",
            "data:text/plain,hello",
            "/etc/passwd",
        ],
    )
    def test_non_http_schemes_rejected(self, url: str) -> None:
        with pytest.raises(UrlNotAllowedError, match="http and https"):
            check_url(url)

    def test_url_without_host_rejected(self) -> None:
        with pytest.raises(UrlNotAllowedError, match="no host"):
            check_url("http://")


class TestBlockedAddresses:
    @pytest.mark.parametrize(
        ("label", "address"),
        [
            ("cloud metadata", "169.254.169.254"),
            ("loopback", "127.0.0.1"),
            ("rfc1918 /8", "10.0.0.5"),
            ("rfc1918 /12", "172.16.0.5"),
            ("rfc1918 /16", "192.168.1.1"),
            ("link local", "169.254.10.1"),
            ("unspecified", "0.0.0.0"),
            ("multicast", "224.0.0.1"),
            ("ipv6 loopback", "::1"),
            ("ipv6 ula", "fd00::1"),
        ],
    )
    def test_private_and_reserved_targets_rejected(self, label: str, address: str) -> None:
        with _resolves_to(address), pytest.raises(UrlNotAllowedError, match="local network"):
            check_url("https://sneaky.example.com")

    def test_public_target_allowed(self) -> None:
        with _resolves_to("93.184.216.34"):
            check_url("https://example.com")

    def test_rejected_if_any_resolved_address_is_private(self) -> None:
        """A name with several A records must not pass on the strength of one."""
        with (
            _resolves_to("93.184.216.34", "127.0.0.1"),
            pytest.raises(UrlNotAllowedError),
        ):
            check_url("https://split-horizon.example.com")

    def test_allow_private_opt_in_skips_the_check(self) -> None:
        with _resolves_to("192.168.1.1"):
            check_url("http://wiki.local", allow_private=True)

    def test_unresolvable_host_rejected(self) -> None:
        with (
            mock.patch(
                "obsidian_search.ingestion.url_guard._resolved_addresses",
                side_effect=UrlNotAllowedError("Cannot resolve host 'nope'"),
            ),
            pytest.raises(UrlNotAllowedError, match="Cannot resolve"),
        ):
            check_url("https://nope.invalid")
