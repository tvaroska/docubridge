"""Disposition-policy loader — reads the ``disposition:`` block of a process skill's
``policy.yaml`` (PATH-B / A2UI-1 support).

The policy file already declares the graph's knobs (``auto_approve_at``,
``resubmit_below``, ``max_resubmissions``); ``bridge.sla`` loads the ``sla:`` block,
this module loads the ``disposition:`` block via the same shared
``bridge.sla._load_policy_doc`` frontmatter+file resolution. ``DispositionThresholds``
in ``bridge.disposition`` hardcodes the same defaults with the comment "policy is the
authoritative source" — this makes that literal: the Path-B graph builds its resubmit
cap + gate thresholds from the pack.

Pure: imports ``contract`` + ``bridge.*`` + stdlib/``pyyaml`` only — NO ``google.adk``,
NO ``a2a``, NEVER ``agents/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bridge.disposition import DispositionThresholds
from bridge.sla import _load_policy_doc


@dataclass(frozen=True)
class DispositionPolicy:
    """The disposition policy read from a process skill's ``policy.yaml``
    ``disposition:`` block. ``auto_approve_at``/``resubmit_below`` feed the gate
    thresholds; ``max_resubmissions`` caps the Path-B resubmit loop."""

    auto_approve_at: float
    resubmit_below: float
    max_resubmissions: int

    def thresholds(self) -> DispositionThresholds:
        """Project onto the gate's ``DispositionThresholds`` (drops the cap knob)."""
        return DispositionThresholds(
            auto_approve_at=self.auto_approve_at,
            resubmit_below=self.resubmit_below,
        )


def load_disposition_policy(
    skill_name: str, *, skills_dir: Path | None = None
) -> DispositionPolicy:
    """Load the ``disposition:`` block of a process skill's declared ``policy.yaml``.

    Resolves the policy file via the same SKILL.md ``metadata.bridge-policy`` pointer
    ``load_sla_policy`` uses (shared ``_load_policy_doc``). Validates the three keys
    are present + numeric, that ``max_resubmissions >= 0`` and that the thresholds
    satisfy ``DispositionThresholds``' invariant. This is what the portal
    (P6-2) uses to build ``PathBState(max_resubmissions=…, thresholds=…)``.
    """
    return disposition_policy_from_doc(
        skill_name, _load_policy_doc(skill_name, skills_dir=skills_dir)
    )


def disposition_policy_from_doc(skill_name: str, policy_doc: dict) -> DispositionPolicy:
    """Parse + validate the ``disposition:`` block of an already-loaded policy doc.

    The pure half of ``load_disposition_policy``, shared with the live seam reader (the
    policy doc read through the ``skill_registry`` seam). Same checks, same messages.
    """
    disposition = policy_doc.get("disposition")
    if not disposition:
        raise ValueError(
            f"skill {skill_name!r} policy.yaml missing 'disposition:' block"
        )

    try:
        auto_approve_at = float(disposition["auto_approve_at"])
        resubmit_below = float(disposition["resubmit_below"])
        max_resubmissions = int(disposition["max_resubmissions"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(
            f"skill {skill_name!r} 'disposition:' block must declare numeric "
            f"auto_approve_at, resubmit_below, max_resubmissions: {e}"
        ) from e

    if max_resubmissions < 0:
        raise ValueError(
            f"max_resubmissions must be >= 0; got {max_resubmissions}"
        )

    policy = DispositionPolicy(
        auto_approve_at=auto_approve_at,
        resubmit_below=resubmit_below,
        max_resubmissions=max_resubmissions,
    )
    # Refuse an inverted / out-of-range pair at load, not silently at gate time.
    try:
        policy.thresholds()
    except ValueError as e:
        raise ValueError(
            f"skill {skill_name!r} 'disposition:' thresholds invalid: {e}"
        ) from e
    return policy
