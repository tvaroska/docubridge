"""Tests for the Hexagonal port harness."""

import pytest

from bridge.ports import (
    LOCAL_ADAPTERS,
    Port,
    PortMode,
    get_adapter,
    port_env_var,
    resolve_mode,
)


def test_port_enum_is_the_thirteen_names():
    """The Port enum contains all 13 managed-service boundaries (9 core + 4 Zone 3–6 stores)."""
    expected_names = {
        "sessions",
        "task_store",
        "exchange_store",
        "skill_registry",
        "memory_bank",
        "scheduler",
        "a2a_client",
        "extraction",
        "email_ingress",
        "audit_vault",
        "operational_store",
        "action_inbox",
        "agent_gym",
    }
    assert {s.value for s in Port} == expected_names
    assert len(Port) == 13


def test_env_var_mapping():
    """port_env_var maps port names to environment variable names."""
    assert port_env_var(Port.task_store) == "BRIDGE_PORT_TASK_STORE"
    assert port_env_var(Port.a2a_client) == "BRIDGE_PORT_A2A_CLIENT"
    assert port_env_var(Port.sessions) == "BRIDGE_PORT_SESSIONS"


def test_resolve_mode_default(monkeypatch):
    """With no env vars set, resolve_mode returns local."""
    monkeypatch.delenv("BRIDGE_PORT_MODE", raising=False)
    for port in Port:
        monkeypatch.delenv(port_env_var(port), raising=False)

    assert resolve_mode(Port.sessions) is PortMode.local
    assert resolve_mode(Port.task_store) is PortMode.local


def test_resolve_mode_global(monkeypatch):
    """BRIDGE_PORT_MODE=gcp applies to all ports."""
    for port in Port:
        monkeypatch.delenv(port_env_var(port), raising=False)

    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    assert resolve_mode(Port.sessions) is PortMode.gcp
    assert resolve_mode(Port.task_store) is PortMode.gcp
    assert resolve_mode(Port.extraction) is PortMode.gcp


def test_resolve_mode_override_wins(monkeypatch):
    """Per-port override takes precedence over global."""
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    monkeypatch.setenv("BRIDGE_PORT_SESSIONS", "local")
    monkeypatch.delenv("BRIDGE_PORT_TASK_STORE", raising=False)

    assert resolve_mode(Port.sessions) is PortMode.local  # override wins
    assert resolve_mode(Port.task_store) is PortMode.gcp  # inherits global


def test_resolve_mode_blank_override_inherits(monkeypatch):
    """Blank per-port override falls through to global."""
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    monkeypatch.setenv("BRIDGE_PORT_SESSIONS", "")  # blank

    assert resolve_mode(Port.sessions) is PortMode.gcp  # blank inherits global


def test_resolve_mode_invalid_value_raises(monkeypatch):
    """Invalid mode value raises ValueError."""
    monkeypatch.setenv("BRIDGE_PORT_MODE", "azure")

    with pytest.raises(ValueError, match="invalid port mode 'azure'"):
        resolve_mode(Port.sessions)


def test_local_adapters_instantiate_and_are_typed():
    """Local adapters instantiate and carry their port identity."""
    assert len(LOCAL_ADAPTERS) == 13
    for port in Port:
        assert port in LOCAL_ADAPTERS

    for port in Port:
        instance = get_adapter(port, PortMode.local)
        assert instance.port is port
        assert hasattr(instance, "port")


def test_all_gcp_adapters_instantiate_and_are_typed():
    """All 13 ports have registered GCP adapters."""
    from bridge.ports import GCP_ADAPTERS
    from bridge.ports.gcp import GcpMemoryBank

    assert len(GCP_ADAPTERS) == 13
    for port in Port:
        instance = get_adapter(port, PortMode.gcp)
        assert instance.port is port
    assert isinstance(get_adapter(Port.memory_bank, PortMode.gcp), GcpMemoryBank)


def test_gcp_shared_store_adapters_dispatch():
    """P7-2: the three shared-store ports return their Gcp* adapter in gcp mode."""
    from bridge.ports.gcp import GcpExchangeStore, GcpSessions, GcpTaskStore

    assert isinstance(get_adapter(Port.sessions, PortMode.gcp), GcpSessions)
    assert isinstance(get_adapter(Port.task_store, PortMode.gcp), GcpTaskStore)
    assert isinstance(get_adapter(Port.exchange_store, PortMode.gcp), GcpExchangeStore)


def test_gcp_extraction_adapter_dispatches():
    """P7-6: extraction dispatches to GcpExtraction in gcp mode (no longer raises)."""
    from bridge.ports.gcp import GcpExtraction

    adapter = get_adapter(Port.extraction, PortMode.gcp)
    assert isinstance(adapter, GcpExtraction)
    assert adapter.port is Port.extraction


@pytest.mark.port("sessions")
def test_adapter_fixture_parametrization(adapter):
    """The adapter fixture yields PortMode and works with get_adapter."""
    instance = get_adapter(Port.sessions, adapter)
    assert instance.port is Port.sessions

    if adapter is PortMode.local:
        assert hasattr(instance, "port")


def test_collection_guard_rejects_invalid_port(pytester):
    """Collection guard aborts on invalid port marker name."""
    pytester.makeconftest(
        """
        import pytest
        from bridge.ports import is_valid_port, PORT_NAMES

        def pytest_collection_modifyitems(config, items):
            bad = []
            for item in items:
                for marker in item.iter_markers(name="port"):
                    name = marker.args[0] if marker.args else None
                    if not (isinstance(name, str) and is_valid_port(name)):
                        bad.append((item.nodeid, name))
            if bad:
                lines = "\\n".join(f"  {nodeid}: port={name!r}" for nodeid, name in bad)
                raise pytest.UsageError(
                    f"invalid @pytest.mark.port name(s); valid ports are {sorted(PORT_NAMES)}:\\n{lines}"
                )
        """
    )

    pytester.makeini(
        """
        [pytest]
        asyncio_default_fixture_loop_scope = function
        markers =
            port: shared port-parity tests
        """
    )

    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.port("bogus")
        def test_invalid_port():
            pass
        """
    )

    result = pytester.runpytest()
    assert result.ret != 0
    output = "\n".join(result.outlines)
    assert "invalid" in output.lower() or "UsageError" in output


def test_collection_guard_accepts_valid_port(pytester):
    """Collection guard passes valid port marker names."""
    pytester.makeconftest(
        """
        import pytest
        from bridge.ports import is_valid_port, PORT_NAMES

        def pytest_collection_modifyitems(config, items):
            bad = []
            for item in items:
                for marker in item.iter_markers(name="port"):
                    name = marker.args[0] if marker.args else None
                    if not (isinstance(name, str) and is_valid_port(name)):
                        bad.append((item.nodeid, name))
            if bad:
                lines = "\\n".join(f"  {nodeid}: port={name!r}" for nodeid, name in bad)
                raise pytest.UsageError(
                    f"invalid @pytest.mark.port name(s); valid ports are {sorted(PORT_NAMES)}:\\n{lines}"
                )
        """
    )

    pytester.makeini(
        """
        [pytest]
        asyncio_default_fixture_loop_scope = function
        markers =
            port: shared port-parity tests
        """
    )

    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.port("sessions")
        def test_valid_port():
            assert True
        """
    )

    result = pytester.runpytest()
    assert result.ret == 0


def test_gcp_param_gated_by_env(pytester):
    """The gcp adapter param is skipped without BRIDGE_TEST_GCP=1."""
    pytester.makeconftest(
        """
        import os
        import pytest
        from bridge.ports import PortMode

        _GCP = os.environ.get("BRIDGE_TEST_GCP") == "1"

        @pytest.fixture(
            params=[
                pytest.param(PortMode.local, id="local"),
                pytest.param(
                    PortMode.gcp,
                    id="gcp",
                    marks=pytest.mark.skipif(
                        not _GCP, reason="set BRIDGE_TEST_GCP=1 to run gcp adapters"
                    ),
                ),
            ]
        )
        def adapter(request):
            return request.param
        """
    )

    pytester.makepyfile(
        """
        def test_with_adapter(adapter):
            assert adapter is not None
        """
    )

    pytester.makeini(
        """
        [pytest]
        asyncio_default_fixture_loop_scope = function
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    output = "\n".join(result.outlines)

    assert "local" in output
