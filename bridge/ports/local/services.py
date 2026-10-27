"""Local skill registry, scheduler, A2A client, extraction, and email ingress adapters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bridge.extraction.fixture import FixtureExtractor

from ..protocols import (
    EMAIL_KEY_PATTERN,
    A2AClientPort,
    EmailIngressPort,
    ExtractionPort,
    SchedulerPort,
    SkillRegistryPort,
    validate_attachment_slot,
    validate_landing_key,
)

# The landing directory of the local email_ingress adapter (S0-arch-13). No silent
# default: an unset dir fails loud at first use (never at construction).
EMAIL_DIR_ENV = "BRIDGE_EMAIL_DIR"


class LocalSkillRegistry(SkillRegistryPort):
    """Local `skill_registry` adapter (SKILL-4): a directory of Agent Skills folders.

    Root is `BRIDGE_SKILLS_DIR` (default the repo `skills/` dir).
    `install_skill` writes a folder at runtime so the Agent Card regenerates with
    no redeploy (`loaded_skills()` reads through this port on every discovery GET).
    GCP = managed Skill Registry (GCS) at P7. The parser is imported lazily inside
    methods, mirroring `LocalScheduler` (avoids a `bridge.edge` ↔ `bridge.ports` cycle).

    A bad live upload is refused, not propagated (S4-usecase-3): `list_skills` skips
    a malformed folder with a logged reason (`listed_skill_meta`) and keeps listing;
    `install_skill` refuses a malformed upload (`validate_skill_upload`) before any
    write, so a bad re-upload never overwrites a good skill.
    """

    def _root(self) -> Path:
        from bridge.sla import SKILLS_DIR

        return Path(os.environ.get("BRIDGE_SKILLS_DIR", "").strip() or SKILLS_DIR)

    def list_skills(self) -> list[Any]:
        """List all skill packages discovered in the local filesystem skills directory."""
        from bridge.edge.skills import listed_skill_meta

        root = self._root()
        skills = []
        if root.exists():
            for child in sorted(root.iterdir()):
                if child.is_dir() and (child / "SKILL.md").exists():
                    meta = listed_skill_meta(child)
                    if meta is not None:
                        skills.append(meta)
        return skills

    def get_skill(self, name: str) -> Any:
        """Load a single skill package definition from the local filesystem by name."""
        from bridge.edge.skills import skill_meta_from_dir

        skill_dir = self._root() / name
        if not (skill_dir / "SKILL.md").exists():
            raise KeyError(name)
        return skill_meta_from_dir(skill_dir)

    def install_skill(
        self, name: str, skill_md: str, assets: dict[str, str] | None = None
    ) -> Any:
        """Copy and register a skill package directory into the local skills root."""
        import re

        from bridge.edge.skills import skill_meta_from_dir, validate_skill_upload

        if not re.fullmatch(r"[a-z0-9-]+", name or ""):
            raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9-]+$")
        root = self._root().resolve()
        skill_dir = (root / name).resolve()
        if skill_dir.parent != root:
            raise ValueError(f"invalid skill name {name!r}: escapes skills root")
        assets = assets or {}
        for rel in assets:
            dest = (skill_dir / rel).resolve()
            if dest != skill_dir and skill_dir not in dest.parents:
                raise ValueError(f"asset path {rel!r} escapes skill dir")

        validate_skill_upload(name, skill_md, assets)

        (skill_dir / "assets").mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_md)
        for rel, content in assets.items():
            dest = (skill_dir / rel).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        return skill_meta_from_dir(skill_dir)

    def read_asset(self, name: str, rel: str) -> str:
        """Read a raw asset file from a locally installed skill directory."""
        import re

        if not re.fullmatch(r"[a-z0-9-]+", name or ""):
            raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9-]+$")
        root = self._root().resolve()
        skill_dir = (root / name).resolve()
        if skill_dir.parent != root:
            raise ValueError(f"invalid skill name {name!r}: escapes skills root")
        path = (skill_dir / rel).resolve()
        if skill_dir not in path.parents:
            raise ValueError(f"asset path {rel!r} escapes skill dir")
        if not path.exists():
            raise KeyError(f"{name}/{rel}")
        return path.read_text()


class LocalScheduler(SchedulerPort):
    """Local scheduler adapter (PORT-6): an in-process VirtualClock."""

    def build_scheduler(self) -> Any:
        """Construct a deterministic virtual-clock scheduler for local execution."""
        from bridge.scheduler import VirtualClock

        return VirtualClock()


class LocalA2AClient(A2AClientPort):
    """Local `a2a_client` adapter (PORT-7, OUT-1): outbound client over an injected fake transport."""

    def build_client(self, transport: Any = None) -> Any:
        """Construct an in-memory outbound A2A client and transport for local execution."""
        from bridge.outbound import OutboundClient

        return OutboundClient(transport)


class LocalExtraction(ExtractionPort):
    """Local extraction adapter — the deterministic fixture engine (PORT-8)."""

    def __init__(self) -> None:
        self._engine = FixtureExtractor()

    async def extract(self, artifact_name: str) -> Any:
        """Run deterministic local document extraction over bytes and filename."""
        return await self._engine.extract(artifact_name)


class LocalEmailIngress(EmailIngressPort):
    """Local `email_ingress` adapter (S0-arch-13): a landing directory.

    Root is the `root` ctor arg, else `BRIDGE_EMAIL_DIR` — resolved on FIRST USE
    (`get_adapter` builds every local adapter with no env, so construction never
    raises); neither set -> `RuntimeError` naming the env, never a silent default.

    Layout: `inbox/<key>.eml`, `attachments/<key>/<index>/<filename>`,
    `processed/<key>.json`. Every write is create-only and torn-write safe: the
    bytes go to a temp file in the destination dir, then `os.link` publishes it
    (`FileExistsError` = a duplicate), so a half-written file never looks landed.
    Keys / filenames are validated before any I/O and every resolved path is
    re-checked for containment (the S0-sec-1 `read_asset` posture).
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root_arg = root

    def _root(self) -> Path:
        raw = self._root_arg or os.environ.get(EMAIL_DIR_ENV, "").strip()
        if not raw:
            raise RuntimeError(
                f"local email_ingress requires a landing directory ({EMAIL_DIR_ENV} "
                "env or root=); refusing a silent default."
            )
        return Path(raw).resolve()

    def _contained(self, *parts: str) -> Path:
        root = self._root()
        path = root.joinpath(*parts).resolve()
        if root not in path.parents:
            raise ValueError(f"email_ingress path {'/'.join(parts)!r} escapes the root")
        return path

    @staticmethod
    def _create_only(path: Path, data: bytes) -> bool:
        import tempfile

        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return False
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".landing-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                return False
            return True
        finally:
            os.unlink(tmp)

    def land(self, key: str, raw: bytes) -> bool:
        """Store an inbound raw MIME email message in the local ingress directory."""
        validate_landing_key(key)
        return self._create_only(self._contained("inbox", f"{key}.eml"), raw)

    def pending(self) -> list[str]:
        """List pending message IDs in the local email ingress queue."""
        import logging
        import re

        inbox = self._root() / "inbox"
        if not inbox.is_dir():
            return []
        done = self._root() / "processed"
        rows = []
        for child in inbox.iterdir():
            if child.name.startswith(".landing-"):
                continue  # an in-flight temp file, not a landed message
            key = child.name.removesuffix(".eml")
            if child.suffix != ".eml" or not re.fullmatch(EMAIL_KEY_PATTERN, key):
                logging.getLogger("bridge.ports.local").warning(
                    "email_ingress: skipping unexpected inbox entry %r", child.name
                )
                continue
            if (done / f"{key}.json").exists():
                continue
            rows.append((child.stat().st_mtime_ns, key))
        return [key for _, key in sorted(rows)]

    def read(self, key: str) -> bytes:
        """Read the raw MIME bytes for a message ID from the local ingress store."""
        validate_landing_key(key)
        path = self._contained("inbox", f"{key}.eml")
        if not path.exists():
            raise KeyError(key)
        return path.read_bytes()

    def put_attachment(
        self, key: str, index: int, filename: str, data: bytes, mime_type: str
    ) -> str:
        """Store an extracted MIME attachment in the local ingress attachment store."""
        validate_landing_key(key)
        validate_attachment_slot(index, filename)
        path = self._contained("attachments", key, str(index), filename)
        self._create_only(path, data)
        return f"file://{path.as_posix()}"

    def mark_processed(self, key: str, outcome: dict) -> bool:
        """Mark an inbound email message ID as processed in the local ingress store."""
        import json

        validate_landing_key(key)
        body = json.dumps(outcome, sort_keys=True).encode()
        return self._create_only(self._contained("processed", f"{key}.json"), body)

    def processed(self, key: str) -> dict | None:
        """Return the processed outcome marker for `key`, or `None` if unprocessed."""
        import json

        validate_landing_key(key)
        path = self._contained("processed", f"{key}.json")
        if not path.exists():
            return None
        return json.loads(path.read_text())
