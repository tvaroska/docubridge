"""Tests for artifact service (ART-1 — versioning + real erase)."""

import google.genai.types as genai_types
from google.adk.artifacts import InMemoryArtifactService


async def test_artifact_versioning():
    """Saving the same filename twice creates versions 0 and 1."""
    service = InMemoryArtifactService()

    # Save first version
    part1 = genai_types.Part.from_bytes(data=b"version 0 content", mime_type="image/svg+xml")
    version1 = await service.save_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="test_session",
        filename="test.svg",
        artifact=part1,
    )

    # Should be version 0
    assert version1 == 0

    # Save second version with same filename
    part2 = genai_types.Part.from_bytes(data=b"version 1 content", mime_type="image/svg+xml")
    version2 = await service.save_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="test_session",
        filename="test.svg",
        artifact=part2,
    )

    # Should be version 1
    assert version2 == 1

    # List versions should show both
    versions = await service.list_versions(
        app_name="test_app",
        user_id="test_user",
        filename="test.svg",
        session_id="test_session",
    )
    assert len(versions) == 2
    assert 0 in versions
    assert 1 in versions


async def test_delete_artifact_real_erase():
    """delete_artifact is a real erase, not a soft flag."""
    service = InMemoryArtifactService()

    # Save an artifact
    part = genai_types.Part.from_text(text="test content")
    await service.save_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="test_session",
        filename="test.txt",
        artifact=part,
    )

    # Verify it exists
    loaded = await service.load_artifact(
        app_name="test_app",
        user_id="test_user",
        filename="test.txt",
        session_id="test_session",
    )
    assert loaded is not None

    # Delete it
    await service.delete_artifact(
        app_name="test_app",
        user_id="test_user",
        filename="test.txt",
        session_id="test_session",
    )

    # Load should return None
    loaded_after = await service.load_artifact(
        app_name="test_app",
        user_id="test_user",
        filename="test.txt",
        session_id="test_session",
    )
    assert loaded_after is None

    # List versions should be empty
    versions = await service.list_versions(
        app_name="test_app",
        user_id="test_user",
        filename="test.txt",
        session_id="test_session",
    )
    assert len(versions) == 0


# --------------------------------------------------------------------------- #
# P4-5 ART-3 retention / no-GC lock                                            #
# --------------------------------------------------------------------------- #
async def test_artifact_versions_retained_for_exchange_life():
    """P4-5 §5.5a: resubmission versions are retained (ART-3).

    After two save_artifacts on the same filename (versions 0 and 1), both versions
    remain listable — nothing prunes older versions. They are retained for the life
    of the exchange.
    """
    service = InMemoryArtifactService()

    # Save version 0
    part0 = genai_types.Part.from_bytes(data=b"original submission", mime_type="image/svg+xml")
    v0 = await service.save_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="exchange-123",
        filename="bill.svg",
        artifact=part0,
    )
    assert v0 == 0

    # Save version 1 (resubmission)
    part1 = genai_types.Part.from_bytes(data=b"resubmitted clear copy", mime_type="image/svg+xml")
    v1 = await service.save_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="exchange-123",
        filename="bill.svg",
        artifact=part1,
    )
    assert v1 == 1

    # Both versions remain listable (retention for the exchange life)
    versions = await service.list_versions(
        app_name="test_app",
        user_id="test_user",
        filename="bill.svg",
        session_id="exchange-123",
    )
    assert len(versions) == 2
    assert 0 in versions
    assert 1 in versions

    # Both versions are loadable
    loaded_v0 = await service.load_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="exchange-123",
        filename="bill.svg",
        version=0,
    )
    assert loaded_v0 is not None
    assert loaded_v0.inline_data.data == b"original submission"

    loaded_v1 = await service.load_artifact(
        app_name="test_app",
        user_id="test_user",
        session_id="exchange-123",
        filename="bill.svg",
        version=1,
    )
    assert loaded_v1 is not None
    assert loaded_v1.inline_data.data == b"resubmitted clear copy"


def test_no_bridge_gc_symbols():
    """P4-5 §5.5b: no-GC guard — bridge/ has no artifact TTL/GC code.

    The only deletion path is the off-script explicit erase (handle_deletion_request
    in bridge/offscript.py), which is a user action, not retention GC. Assert that
    bridge/ contains no auto-expiry/TTL/cleanup scheduler on artifacts.
    """
    from pathlib import Path

    bridge_root = Path(__file__).resolve().parents[1] / "bridge"

    # Scan all bridge/ python files for forbidden GC symbols
    # (excluding the sanctioned off-script erase in offscript.py)
    forbidden_patterns = ["ttl", "expire", "cleanup", "prune", "retention_days"]
    violations = []

    for py_file in bridge_root.rglob("*.py"):
        # Skip the sanctioned off-script deletion path
        if py_file.name == "offscript.py":
            continue

        content = py_file.read_text().lower()
        for pattern in forbidden_patterns:
            # Check for the pattern as a word (not substring of a larger identifier)
            # We're looking for artifact-related GC, not unrelated use of these words
            if pattern in content and "artifact" in content:
                # A heuristic check: if both pattern and "artifact" appear,
                # it might be GC code. More precise: look for them near each other.
                # For simplicity, we assert they don't co-occur.
                # Refined: check if pattern appears in an artifact-related context
                lines = py_file.read_text().splitlines()
                for i, line in enumerate(lines):
                    if pattern in line.lower() and "artifact" in line.lower():
                        stripped = line.strip()
                        # Exclude docstrings, comments, and lines documenting the no-GC policy
                        # Look for actual code: function calls, assignments, conditionals
                        if (stripped.startswith("#") or
                            stripped.startswith('"""') or
                            stripped.startswith("'''") or
                            "**" in stripped or  # RST/Markdown markup (docstring)
                            "must keep, zero" in stripped.lower() or  # Our doc comment
                            "no artifact" in stripped.lower() or
                            "not bespoke" in stripped.lower()):
                            continue
                        violations.append((py_file.name, i + 1, line.strip()))

    # Assert no violations (the Bridge has no artifact GC/TTL code)
    # The only deletion is handle_deletion_request in offscript.py (user action)
    assert not violations, (
        f"Found potential artifact GC/TTL code in bridge/: {violations}. "
        "The Bridge must have zero artifact-GC/TTL code (ART-3 / P4-5). "
        "Retention is enforced by the backend's object-lifecycle policy (GCS at P7)."
    )
