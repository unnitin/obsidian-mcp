"""Shared test fixtures.

Keeps the suite hermetic: nothing here should perform DNS or network I/O.
"""

from __future__ import annotations

import ipaddress
import unittest.mock as mock
from collections.abc import Iterator

import pytest

#: A public address, so the SSRF guard passes for the example.com URLs the
#: fetch tests use. Tests that exercise the guard itself patch this themselves.
_PUBLIC_IP = ipaddress.ip_address("93.184.216.34")


@pytest.fixture(autouse=True)
def _no_dns_in_tests() -> Iterator[None]:
    """Stub hostname resolution in the URL guard so tests never hit a resolver."""
    with mock.patch(
        "obsidian_search.ingestion.url_guard._resolved_addresses",
        return_value=[_PUBLIC_IP],
    ):
        yield
