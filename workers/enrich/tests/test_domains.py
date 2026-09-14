"""Tests for domains.py - cleaning, same-organisation checks, and the host
guard that keeps the worker out of the local network."""

import asyncio
import socket

import pytest

from workers.enrich.domains import base_domain, clean_domain, platform_of, resolve_guard, static_block_reason


@pytest.mark.parametrize("raw, expected", [
    ("romashka.kz", "romashka.kz"),
    ("  Romashka.KZ  ", "romashka.kz"),
    ("https://www.romashka.kz/about?utm=hh", "www.romashka.kz"),
    ("romashka.kz:8080", "romashka.kz"),
    ("romashka.kz.", "romashka.kz"),
    ("ромашка.рф", "xn--80aa3agjl3d.xn--p1ai"),  # Cyrillic domains are fetched as punycode
    ("192.168.1.10", None),
    ("localhost", None),
    ("не домен", None),
    ("", None),
    (None, None),
])
def test_clean_domain(raw, expected):
    assert clean_domain(raw) == expected


@pytest.mark.parametrize("host, expected", [
    ("romashka.kz", "romashka.kz"),
    ("www.romashka.kz", "romashka.kz"),
    ("shop.romashka.kz", "romashka.kz"),
    ("romashka.com.kz", "romashka.com.kz"),
    ("shop.romashka.com.kz", "romashka.com.kz"),
    ("romashka.msk.ru", "romashka.msk.ru"),
])
def test_base_domain(host, expected):
    assert base_domain(host) == expected


@pytest.mark.parametrize("host, expected", [
    ("vk.com", "vk.com"),
    ("m.vk.com", "vk.com"),
    ("taplink.cc", "taplink.cc"),
    ("romashka.kz", None),
    ("romashka.tilda.ws", None),  # a site-builder subdomain IS the company's site
])
def test_platform_of(host, expected):
    assert platform_of(host) == expected


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "[::1]", "localhost", "intranet",
                                  "printer.local", "db.internal", "romashka.test"])
def test_static_block_reason_refuses_internal_hosts(host):
    assert static_block_reason(host) is not None


def test_static_block_reason_allows_a_public_name():
    assert static_block_reason("romashka.kz") is None


@pytest.mark.asyncio
async def test_resolve_guard_refuses_a_public_name_that_points_into_a_private_network(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.7", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    reason = await resolve_guard("evil-redirect.kz")

    assert reason is not None and "192.168.0.7" in reason


@pytest.mark.asyncio
async def test_resolve_guard_allows_a_public_address(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    assert await resolve_guard("romashka.kz") is None


@pytest.mark.asyncio
async def test_resolve_guard_leaves_an_unresolvable_name_to_fail_as_unavailable(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    assert await resolve_guard("no-such-site.kz") is None
