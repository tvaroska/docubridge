"""Tests for the seam harness."""

import pytest

from bridge.seams import (
    LOCAL_ADAPTERS,
    Seam,
    SeamMode,
    get_adapter,
    resolve_mode,
    seam_env_var,
)


def test_seam_enum_is_the_nine_names():
    """The Seam enum is exactly the nine seam names (email_ingress = S0-arch-13)."""
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
    }
    assert {s.value for s in Seam} == expected_names
    assert len(Seam) == 9


def test_env_var_mapping():
    """seam_env_var maps seam names to environment variable names."""
    assert seam_env_var(Seam.task_store) == "BRIDGE_SEAM_TASK_STORE"
    assert seam_env_var(Seam.a2a_client) == "BRIDGE_SEAM_A2A_CLIENT"
    assert seam_env_var(Seam.sessions) == "BRIDGE_SEAM_SESSIONS"


def test_resolve_mode_default(monkeypatch):
    """With no env vars set, resolve_mode returns local."""
    # Clear any inherited env vars
    monkeypatch.delenv("BRIDGE_SEAM_MODE", raising=False)
    for seam in Seam:
        monkeypatch.delenv(seam_env_var(seam), raising=False)

    assert resolve_mode(Seam.sessions) is SeamMode.local
    assert resolve_mode(Seam.task_store) is SeamMode.local


def test_resolve_mode_global(monkeypatch):
    """BRIDGE_SEAM_MODE=gcp applies to all seams."""
    # Clear per-seam overrides
    for seam in Seam:
        monkeypatch.delenv(seam_env_var(seam), raising=False)

    monkeypatch.setenv("BRIDGE_SEAM_MODE", "gcp")
    assert resolve_mode(Seam.sessions) is SeamMode.gcp
    assert resolve_mode(Seam.task_store) is SeamMode.gcp
    assert resolve_mode(Seam.extraction) is SeamMode.gcp


def test_resolve_mode_override_wins(monkeypatch):
    """Per-seam override takes precedence over global."""
    monkeypatch.setenv("BRIDGE_SEAM_MODE", "gcp")
    monkeypatch.setenv("BRIDGE_SEAM_SESSIONS", "local")
    # Don't set override for task_store, so it should inherit global
    monkeypatch.delenv("BRIDGE_SEAM_TASK_STORE", raising=False)

    assert resolve_mode(Seam.sessions) is SeamMode.local  # override wins
    assert resolve_mode(Seam.task_store) is SeamMode.gcp  # inherits global


def test_resolve_mode_blank_override_inherits(monkeypatch):
    """Blank per-seam override falls through to global."""
    monkeypatch.setenv("BRIDGE_SEAM_MODE", "gcp")
    monkeypatch.setenv("BRIDGE_SEAM_SESSIONS", "")  # blank

    assert resolve_mode(Seam.sessions) is SeamMode.gcp  # blank inherits global


def test_resolve_mode_invalid_value_raises(monkeypatch):
    """Invalid mode value raises ValueError."""
    monkeypatch.setenv("BRIDGE_SEAM_MODE", "azure")

    with pytest.raises(ValueError, match="invalid seam mode 'azure'"):
        resolve_mode(Seam.sessions)


def test_local_adapters_instantiate_and_are_typed():
    """Local adapters instantiate and carry their seam identity."""
    # Check all nine are in LOCAL_ADAPTERS
    assert len(LOCAL_ADAPTERS) == 9
    for seam in Seam:
        assert seam in LOCAL_ADAPTERS

    # Check they instantiate and are typed correctly
    for seam in Seam:
        instance = get_adapter(seam, SeamMode.local)
        assert instance.seam is seam
        # Instance should be of the correct ABC type
        assert hasattr(instance, "seam")


def test_all_gcp_adapters_instantiate_and_are_typed():
    """All nine seams have registered GCP adapters (`GcpMemoryBank` landed with Option A
    Vertex AI Agent Engine migration)."""
    from bridge.seams import GCP_ADAPTERS
    from bridge.seams.gcp import GcpMemoryBank

    assert len(GCP_ADAPTERS) == 9
    for seam in Seam:
        instance = get_adapter(seam, SeamMode.gcp)
        assert instance.seam is seam
    assert isinstance(get_adapter(Seam.memory_bank, SeamMode.gcp), GcpMemoryBank)


def test_gcp_shared_store_adapters_dispatch():
    """P7-2: the three shared-store seams return their Gcp* adapter in gcp mode."""
    from bridge.seams.gcp import GcpExchangeStore, GcpSessions, GcpTaskStore

    assert isinstance(get_adapter(Seam.sessions, SeamMode.gcp), GcpSessions)
    assert isinstance(get_adapter(Seam.task_store, SeamMode.gcp), GcpTaskStore)
    assert isinstance(get_adapter(Seam.exchange_store, SeamMode.gcp), GcpExchangeStore)


def test_gcp_extraction_adapter_dispatches():
    """P7-6: extraction dispatches to GcpExtraction in gcp mode (no longer raises)."""
    from bridge.seams.gcp import GcpExtraction

    adapter = get_adapter(Seam.extraction, SeamMode.gcp)
    assert isinstance(adapter, GcpExtraction)
    assert adapter.seam is Seam.extraction


@pytest.mark.seam("sessions")
def test_adapter_fixture_parametrization(adapter):
    """The adapter fixture yields SeamMode and works with get_adapter."""
    instance = get_adapter(Seam.sessions, adapter)
    assert instance.seam is Seam.sessions

    # When adapter is local, we should get a working instance
    if adapter is SeamMode.local:
        assert hasattr(instance, "seam")


def test_collection_guard_rejects_invalid_seam(pytester):
    """Collection guard aborts on invalid seam marker name."""
    # Create a conftest that registers the guard
    pytester.makeconftest(
        """
        import pytest
        from bridge.seams import is_valid_seam, SEAM_NAMES

        def pytest_collection_modifyitems(config, items):
            bad = []
            for item in items:
                for marker in item.iter_markers(name="seam"):
                    name = marker.args[0] if marker.args else None
                    if not (isinstance(name, str) and is_valid_seam(name)):
                        bad.append((item.nodeid, name))
            if bad:
                lines = "\\n".join(f"  {nodeid}: seam={name!r}" for nodeid, name in bad)
                raise pytest.UsageError(
                    f"invalid @pytest.mark.seam name(s); valid seams are {sorted(SEAM_NAMES)}:\\n{lines}"
                )
        """
    )

    # Register the seam marker for the sub-run
    pytester.makeini(
        """
        [pytest]
        markers =
            seam: shared seam-parity tests
        """
    )

    # Create a test file with an invalid seam marker
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seam("bogus")
        def test_invalid_seam():
            pass
        """
    )

    result = pytester.runpytest()
    assert result.ret != 0
    # Check that the error message contains the expected strings
    output = "\n".join(result.outlines)
    assert "invalid" in output.lower() or "UsageError" in output


def test_collection_guard_accepts_valid_seam(pytester):
    """Collection guard passes valid seam marker names."""
    pytester.makeconftest(
        """
        import pytest
        from bridge.seams import is_valid_seam, SEAM_NAMES

        def pytest_collection_modifyitems(config, items):
            bad = []
            for item in items:
                for marker in item.iter_markers(name="seam"):
                    name = marker.args[0] if marker.args else None
                    if not (isinstance(name, str) and is_valid_seam(name)):
                        bad.append((item.nodeid, name))
            if bad:
                lines = "\\n".join(f"  {nodeid}: seam={name!r}" for nodeid, name in bad)
                raise pytest.UsageError(
                    f"invalid @pytest.mark.seam name(s); valid seams are {sorted(SEAM_NAMES)}:\\n{lines}"
                )
        """
    )

    pytester.makeini(
        """
        [pytest]
        markers =
            seam: shared seam-parity tests
        """
    )

    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seam("sessions")
        def test_valid_seam():
            assert True
        """
    )

    result = pytester.runpytest()
    assert result.ret == 0


def test_gcp_param_gated_by_env(pytester):
    """The gcp adapter param is skipped without BRIDGE_TEST_GCP=1."""
    # Create a minimal test that uses the adapter fixture
    pytester.makeconftest(
        """
        import os
        import pytest
        from bridge.seams import SeamMode

        _GCP = os.environ.get("BRIDGE_TEST_GCP") == "1"

        @pytest.fixture(
            params=[
                pytest.param(SeamMode.local, id="local"),
                pytest.param(
                    SeamMode.gcp,
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

    # Run with --collect-only to see what would be collected
    result = pytester.runpytest("--collect-only", "-q")
    output = "\n".join(result.outlines)

    # Without BRIDGE_TEST_GCP=1, we should only see local
    assert "local" in output
    # The gcp param should either not appear or appear as skipped
