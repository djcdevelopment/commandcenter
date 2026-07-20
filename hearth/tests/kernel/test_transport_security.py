"""ADR-0022: the streamable-http DNS-rebinding allowlist is OURS, not the SDK's
incidental default, and it must reflect the host we actually bind.

The defect these tests pin: `build_server` used to call `FastMCP("hearth")` with
no host and assign `settings.host` afterwards. The SDK computes its
transport-security default inside `__init__` from the host it is GIVEN, so the
allowlist was always the loopback triple -- including under ADR-0019's consented
non-loopback bind, which therefore answered every container with 421 Misdirected
Request while looking perfectly healthy on the wire. A bind mode that cannot
influence the guard protecting that bind is not a bind mode.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase

from hearth.kernel import gateway

ALIAS = f"{gateway.CONTAINER_HOST_ALIAS}:*"
LOOPBACK = ["127.0.0.1:*", "localhost:*", "[::1]:*"]


class TransportSecurityPolicyTests(TestCase):
    def test_protection_is_always_enabled(self) -> None:
        """Never silently off. A settings object we construct ourselves could
        regress to protection=False far more quietly than the SDK's default."""
        for host in ("127.0.0.1", "0.0.0.0", "192.168.12.194", "::"):
            with self.subTest(host=host):
                self.assertTrue(
                    gateway._transport_security(host).enable_dns_rebinding_protection)

    def test_loopback_bind_keeps_the_sdk_triple(self) -> None:
        allowed = gateway._transport_security("127.0.0.1").allowed_hosts
        for entry in LOOPBACK:
            self.assertIn(entry, allowed)

    def test_container_alias_allowed_even_on_a_loopback_bind(self) -> None:
        """The mirrored-networking case, and the reason the alias is not gated on
        container-access mode: under WSL2 networkingMode=mirrored a container
        reaches this host over the LOOPBACK bind. Gating the alias behind
        non-loopback mode would deny precisely the configuration that needs it."""
        self.assertIn(ALIAS, gateway._transport_security("127.0.0.1").allowed_hosts)

    def test_non_loopback_bind_allows_its_own_address(self) -> None:
        """ADR-0019's container mode opening an interface the guard then refuses
        is the original defect in its purest form."""
        allowed = gateway._transport_security("192.168.12.194").allowed_hosts
        self.assertIn("192.168.12.194:*", allowed)
        self.assertIn(ALIAS, allowed)
        for entry in LOOPBACK:
            self.assertIn(entry, allowed)

    def test_wildcard_binds_add_no_bogus_entry(self) -> None:
        """0.0.0.0 and :: name every interface, not a reachable address; adding
        them to a Host allowlist would match nothing a client ever sends."""
        for host in ("0.0.0.0", "::"):
            with self.subTest(host=host):
                allowed = gateway._transport_security(host).allowed_hosts
                self.assertNotIn(f"{host}:*", allowed)
                self.assertIn(ALIAS, allowed)

    def test_origins_mirror_hosts(self) -> None:
        settings = gateway._transport_security("192.168.12.194")
        self.assertEqual(
            settings.allowed_origins,
            [f"http://{entry}" for entry in settings.allowed_hosts],
        )


class BuildServerWiringTests(TestCase):
    """The regression guard proper: policy correctness is worthless if
    build_server constructs FastMCP in an order that discards it."""

    def _build(self, host: str):
        with tempfile.TemporaryDirectory() as tmp:
            callers = Path(tmp) / "callers.json"
            callers.write_text("{}", encoding="utf-8")
            return gateway.build_server(
                host=host,
                port=8710,
                callers_path=callers,
                ledger_dir=Path(tmp) / "ledger",
            )

    def test_bind_host_reaches_the_guard(self) -> None:
        server = self._build("192.168.12.194")
        self.assertEqual(server.settings.host, "192.168.12.194")
        allowed = server.settings.transport_security.allowed_hosts
        self.assertIn("192.168.12.194:*", allowed)
        self.assertIn(ALIAS, allowed)

    def test_loopback_default_still_admits_containers(self) -> None:
        server = self._build("127.0.0.1")
        self.assertEqual(server.settings.host, "127.0.0.1")
        self.assertIn(ALIAS, server.settings.transport_security.allowed_hosts)

    def test_port_survives_construction(self) -> None:
        self.assertEqual(self._build("127.0.0.1").settings.port, 8710)
