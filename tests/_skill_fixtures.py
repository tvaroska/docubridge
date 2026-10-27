"""The synthetic ``supplier-onboarding`` use case: one process skill + two doctype skills.

Shared by the live use-case-add Drivers (``tests/test_live_use_case_add.py``,
``tests/test_live_rfp_use_case_add.py``, CUJ-9) and the second-use-case reusability proof
(``tests/test_rfp_second_use_case.py``). Synthetic and
test-only: written into tmp dirs (or installed through the ``skill_registry`` port into a tmp
``BRIDGE_SKILLS_DIR``), never into ``skills/`` or ``wiki/evals/``.

Also the **bad live upload** (S4-usecase-3): five malformed / half-copied folders, each bad
for one reason, written raw onto the filesystem (``write_bad_upload``) because the port's
``install_skill`` now refuses them before any write.
"""

from __future__ import annotations

import json
from pathlib import Path

from bridge.ports.protocols import SkillRegistryPort
from contract.extraction import ExtractionFields, ExtractionResult

PROCESS = "supplier-onboarding"  # the synthetic skill under test (lives in tmp_path)
CANDIDATES = ("w9-form", "insurance-cert")

SKILL_MD = """---
name: supplier-onboarding
description: Collect each supplier's onboarding credentials (a W-9 and a certificate of insurance) to a due date. Synthetic test skill.
license: Proprietary
metadata:
  bridge-kind: process
  bridge-pattern: collect
  bridge-candidate-doctypes: "w9-form insurance-cert"
  bridge-tags: "onboarding collect supplier"
  bridge-policy: assets/policy.yaml
  version: "1.0"
---

Emergent Collect over suppliers x credentials (synthetic, test-only).
"""

POLICY_YAML = """\
disposition:
  auto_approve_at: 0.85
  resubmit_below: 0.55
  max_resubmissions: 3

sla:
  deadline: 5
  cadence: 2
  max_nudges: 2
  overdue_ticks: 1
  reminder_ticks: 3
  escalate_ticks: 5
  ladder: [overdue, reminder, escalated]

rfp:
  tick_seconds: 86400
  item_field: credential
"""


def _doctype_skill_md(name: str, description: str) -> str:
    return f"""---
name: {name}
description: {description} Synthetic test skill.
license: Proprietary
metadata:
  bridge-kind: doctype
  bridge-tags: "doctype onboarding"
  bridge-schema: assets/schema.json
  version: "1.0"
---

Extract the fields in `assets/schema.json` (synthetic, test-only).
"""


W9_SKILL_MD = _doctype_skill_md("w9-form", "A supplier's IRS Form W-9.")
INSURANCE_CERT_SKILL_MD = _doctype_skill_md(
    "insurance-cert", "A supplier's certificate of insurance (COI)."
)


def doctype_schema_json(name: str) -> str:
    """The Path-A schema of a supplier doctype (key fields match ``supplier_result``)."""
    return json.dumps(
        {
            "type": "object",
            "required": ["doctype", "key_fields"],
            "properties": {
                "doctype": {"type": "string", "enum": [name]},
                "key_fields": {
                    "type": "object",
                    "required": ["credential", "name"],
                    "properties": {
                        "credential": {"type": "string"},
                        "name": {"type": "string"},
                    },
                },
            },
        },
        indent=2,
    )


def supplier_result(doctype: str, credential: str) -> ExtractionResult:
    return ExtractionResult(
        fields=ExtractionFields(
            doctype=doctype,
            issuer="synthetic-issuer",
            key_fields={"credential": credential, "name": "Example Supplies LLC"},
        ),
        overall_confidence=0.97,
        legible=True,
        flagged_fields=[],
    )


class StubExtraction:
    """Maps a landed attachment URI to a canned result by its filename."""

    def __init__(self, by_filename: dict[str, ExtractionResult]) -> None:
        self.by_filename = by_filename
        self.calls: list[str] = []

    async def extract(self, uri: str) -> ExtractionResult:
        self.calls.append(uri)
        for name, result in self.by_filename.items():
            if uri.endswith(name):
                return result
        raise KeyError(uri)


# name -> (SKILL.md, assets), in install order: doctypes first, the process last, so no
# request can see a process whose label space is half-installed.
SUPPLIER_ONBOARDING_SKILLS: dict[str, tuple[str, dict[str, str]]] = {
    "w9-form": (W9_SKILL_MD, {"assets/schema.json": doctype_schema_json("w9-form")}),
    "insurance-cert": (
        INSURANCE_CERT_SKILL_MD,
        {"assets/schema.json": doctype_schema_json("insurance-cert")},
    ),
    PROCESS: (SKILL_MD, {"assets/policy.yaml": POLICY_YAML}),
}


# --------------------------------------------------------------------------- #
# The bad live upload (S4-usecase-3): each folder is refused for ONE reason     #
# --------------------------------------------------------------------------- #
# A half-copied SKILL.md: the body survived, the frontmatter fence did not.
TRUNCATED_W9_SKILL_MD = W9_SKILL_MD.split("---\n", 2)[2]
# A typo'd kind: neither ``process`` nor ``doctype``.
UNKNOWN_KIND_INSURANCE_CERT_SKILL_MD = INSURANCE_CERT_SKILL_MD.replace(
    "bridge-kind: doctype", "bridge-kind: doc-type"
)

# A process whose candidates ARE installed (the Address doctypes), so the only thing wrong
# with it is its policy file: without the policy check it WOULD be advertised.
KYC_PROCESS = "kyc-intake"
KYC_SKILL_MD = """---
name: kyc-intake
description: Collect a customer's identity documents for a KYC review. Synthetic test skill.
license: Proprietary
metadata:
  bridge-kind: process
  bridge-pattern: collect
  bridge-candidate-doctypes: "gov-id utility-bill"
  bridge-tags: "identity intake"
  bridge-policy: assets/policy.yaml
  version: "1.0"
---

Emergent Collect over customers x identity documents (synthetic, test-only).
"""
BROKEN_POLICY_YAML = (
    "disposition:\n  auto_approve_at: [0.85\n"  # unclosed flow sequence
)

BANK_LETTER = "bank-letter"
BANK_LETTER_SKILL_MD = _doctype_skill_md(
    BANK_LETTER, "A bank's account-verification letter."
)
CORRUPT_SCHEMA_JSON = "{not json"

# name -> (SKILL.md, assets). ``supplier-onboarding`` itself is structurally fine; it is
# skipped cross-skill because both of its candidate doctypes are refused.
BAD_UPLOAD_SKILLS: dict[str, tuple[str, dict[str, str]]] = {
    "w9-form": (
        TRUNCATED_W9_SKILL_MD,
        {"assets/schema.json": doctype_schema_json("w9-form")},
    ),
    "insurance-cert": (
        UNKNOWN_KIND_INSURANCE_CERT_SKILL_MD,
        {"assets/schema.json": doctype_schema_json("insurance-cert")},
    ),
    PROCESS: (SKILL_MD, {"assets/policy.yaml": POLICY_YAML}),
    KYC_PROCESS: (KYC_SKILL_MD, {"assets/policy.yaml": BROKEN_POLICY_YAML}),
    BANK_LETTER: (BANK_LETTER_SKILL_MD, {"assets/schema.json": CORRUPT_SCHEMA_JSON}),
}


def write_skill(root: Path, name: str, skill_md: str, assets: dict[str, str]) -> None:
    """Write one skill folder straight onto the filesystem (no port)."""
    skill = root / name
    (skill / "assets").mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(skill_md)
    for rel, content in assets.items():
        (skill / rel).write_text(content)


def write_supplier_onboarding(root: Path, *, doctypes: bool = True) -> None:
    """Write the use case under ``root``; ``doctypes=False`` writes the process only."""
    for name, (skill_md, assets) in SUPPLIER_ONBOARDING_SKILLS.items():
        if doctypes or name == PROCESS:
            write_skill(root, name, skill_md, assets)


def install_supplier_onboarding(registry: SkillRegistryPort) -> list:
    """Install the use case through the ``skill_registry`` port; return the SkillMetas."""
    return [
        registry.install_skill(name, skill_md, assets=assets)
        for name, (skill_md, assets) in SUPPLIER_ONBOARDING_SKILLS.items()
    ]


def write_bad_upload(root: Path) -> None:
    """Drop the bad upload under ``root`` raw (a filesystem copy, NOT ``install_skill``)."""
    for name, (skill_md, assets) in BAD_UPLOAD_SKILLS.items():
        write_skill(root, name, skill_md, assets)
