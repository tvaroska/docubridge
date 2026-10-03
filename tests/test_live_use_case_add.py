"""CUJ-9 Driver: a whole use case added live while an exchange of another process is in flight.

One ``build_a2a_app`` (hybrid edge, ``ScriptedLlm``, in-memory stores) over a tmp
``BRIDGE_SKILLS_DIR`` seeded with the Address skills only. The phases:

- **(a)** park an Address exchange mid-flight (POWER + AQUA bills: PowerCo accepted, AquaUtil
  parks on the R3 HITL handoff, so the task is ``INPUT_REQUIRED``);
- **(b)** a control, identical second core runs the same park + reviewer resolution to its
  terminal outcome, before any upload;
- **(c)** the synthetic ``supplier-onboarding`` use case (two doctype skills, then the
  process) is installed through the ``skill_registry`` seam — same app object, no rebuild;
- **(d)** the next Agent Card GET advertises the new process with both doctype tags;
- **(e)** the parked exchange is finished by the reviewer: its terminal outcome, reason code
  and ledger equal the control's;
- **(f)** a ``supplier-onboarding`` Collect on the live core is accepted (Path-B), and its
  Path-A assertions are validated against the live doctype schema.

Traps (hard-fail): the app rebuilt / the process restarted (one app, one executor, one LLM
leg; a rebuild could not find the parked task in the in-memory stores), the in-flight task
disturbed by the upload (proto-equal snapshot, no new model call), and the new process
advertised but unusable (the same request is ``unsupported_doctype`` before the upload).

``BRIDGE_SKILLS_DIR`` is process-global, so the control core runs to completion BEFORE the
upload (b before c is load-bearing): otherwise it would see the new skills too.

**Companion: a bad live upload is refused, not propagated (S4-usecase-3).**
``test_bad_live_upload_is_refused_not_propagated`` drops five malformed / half-copied
folders raw into the same kind of live registry while an Address exchange is parked:
``w9-form`` (no frontmatter fence), ``insurance-cert`` (unknown ``bridge-kind``),
``kyc-intake`` (unparsable ``policy.yaml``), ``bank-letter`` (corrupt ``schema.json``) and
``supplier-onboarding`` (structurally fine, but both its candidate doctypes are refused).
Each is skipped with a logged reason and absent from the card; discovery, Address Path-A
and the parked exchange are undisturbed; a process with no installed candidate rejects
everything ``unsupported_doctype`` (never Address's label space, S0-arch-18); a malformed
seam re-upload is refused before any write; and each folder is picked up once repaired.

Synthetic data only (the corpus party ``jordan-lee``; suppliers ``supplier-x``).
"""

from __future__ import annotations

import logging
import shutil

import pytest
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, TaskState

from bridge.edge import encode_collect_request
from bridge.edge.skills import loaded_skills
from bridge.edge.wire import META_REASON_CODE, META_REVIEW_CALL_ID
from bridge.seams import Seam, get_adapter, resolve_mode
from bridge.sla import SKILLS_DIR
from contract.enums import Disposition, Gate
from contract.wire import CollectRequest
from tests import _a2a_live as a2a
from tests._a2a_live import (
    ADDRESS_CTX,
    ADDRESS_SKILLS,
    AQUA,
    BASE,
    COI,
    ENV_TO_CLEAR,
    PARTY,
    POWER,
    REVIEWER,
    W9,
)
from tests._skill_fixtures import (
    BANK_LETTER,
    CANDIDATES,
    KYC_PROCESS,
    POLICY_YAML,
    PROCESS,
    SUPPLIER_ONBOARDING_SKILLS,
    TRUNCATED_W9_SKILL_MD,
    doctype_schema_json,
    install_supplier_onboarding,
    write_bad_upload,
)

SUPPLIER = "supplier-x"  # synthetic
GOV_ID = ("gov-id-clean", "images/gov-id-clean.svg")  # corpus doc
SKILLS_LOGGER = "bridge.edge.skills"


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """A tmp registry seeded with the Address skills only, and an env the ambient shell
    cannot steer."""
    for name in ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "skills"
    root.mkdir()
    for name in ADDRESS_SKILLS:
        shutil.copytree(SKILLS_DIR / name, root / name)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(root))
    return root


def _assertion_parts(process: str, assertion: dict, *, ctx: str, party: str = SUPPLIER):
    parts = encode_collect_request(
        CollectRequest(process=process, party=party, context_id=ctx)
    )
    parts.append(ph.new_data_part(assertion))
    return parts


def _entry(task, doc_id: str):
    return next(e for e in a2a.turn(task).status.entries if e.doc_id == doc_id)


def _assertion(doctype: str, credential: str, **extra_key_fields) -> dict:
    return {
        "fields": {
            "doctype": doctype,
            "key_fields": {
                "credential": credential,
                "name": "Example Supplies LLC",
                **extra_key_fields,
            },
        },
        "overall_confidence": 0.97,
        "legible": True,
        "flagged_fields": [],
    }


def _gov_id_clean_assertion() -> dict:
    """A well-formed gov-id-clean Path-A assertion (all required key fields; synthetic)."""
    return {
        "fields": {
            "doctype": "gov-id",
            "key_fields": {
                "name": "Jordan Lee",
                "address": "14 Elm Row, Springfield",
                "doc_number": "DL-8392047",
                "issuing_authority": "State DMV",
                "expiry": "2030-01-01",
            },
        },
        "overall_confidence": 0.96,
        "legible": True,
        "flagged_fields": [],
    }


# --------------------------------------------------------------------------- #
# The Driver                                                                   #
# --------------------------------------------------------------------------- #
async def test_whole_use_case_added_live_while_an_exchange_is_in_flight(
    clean_env, monkeypatch
):
    live = a2a.core()
    control = a2a.core()

    async with a2a.client(live.app, REVIEWER) as (hc, client):
        # --- Baseline: Address only; the supplier process is unknown (red-before) -----
        card = await a2a.card_skills(hc)
        assert set(card) == {"address-proof"}
        before = await a2a.send(
            client,
            a2a.submission((W9, COI), ctx="onboard-pre", process=PROCESS, party=SUPPLIER),
            context_id="onboard-pre",
        )
        assert before.status.state == TaskState.TASK_STATE_COMPLETED
        assert {
            (e.doc_id, e.disposition, e.reason_code)
            for e in a2a.turn(before).status.entries
        } == {
            (W9[0], Disposition.rejected, "unsupported_doctype"),
            (COI[0], Disposition.rejected, "unsupported_doctype"),
        }

        # --- (a) Park an Address exchange mid-flight ----------------------------------
        parked = await a2a.park(client, ctx=ADDRESS_CTX)
        status_meta = a2a.meta(parked.status.message)
        assert status_meta[META_REASON_CODE] == "awaiting_human_review"
        call_id = status_meta[META_REVIEW_CALL_ID]
        assert call_id
        power = _entry(parked, POWER[0])
        assert power.disposition is Disposition.accepted
        aqua = _entry(parked, AQUA[0])
        assert aqua.disposition is Disposition.pending
        assert aqua.gate is Gate.hitl_review
        parked_before = await client.get_task(GetTaskRequest(id=parked.id))
        calls_parked = live.llm.call_count

        # --- (b) Control: the same exchange, no upload, to its terminal outcome -------
        # Load-bearing order: BRIDGE_SKILLS_DIR is process-global, so the control must
        # finish before the upload or it would see the new skills as well.
        control_task = await a2a.control_outcome(control)
        assert {s.name for s in loaded_skills()} == set(
            ADDRESS_SKILLS
        )  # still Address-only

        # --- Trap guards: from here on nothing may be rebuilt or reconstructed --------
        constructed = a2a.arm_trap_guards(monkeypatch)

        # --- (c) Upload the whole use case through the skill_registry seam ------------
        registry = get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))
        metas = install_supplier_onboarding(registry)
        assert [m.kind for m in metas] == ["doctype", "doctype", "process"]
        assert tuple(metas[-1].candidate_doctypes) == CANDIDATES
        # The in-flight exchange is untouched: same task, no model call.
        assert await client.get_task(GetTaskRequest(id=parked.id)) == parked_before
        assert live.llm.call_count == calls_parked

        # --- (d) The next card GET advertises the new process -------------------------
        card = await a2a.card_skills(hc)
        assert PROCESS in card
        assert {"doctype:w9-form", "doctype:insurance-cert"} <= set(card[PROCESS])
        assert {"doctype:gov-id", "doctype:utility-bill"} <= set(card["address-proof"])
        assert await client.get_task(GetTaskRequest(id=parked.id)) == parked_before

        # --- (e) Finish the parked exchange: same outcome as the control --------------
        task = await a2a.send(
            client,
            a2a.resolution(call_id),
            context_id=parked.context_id,
            task_id=parked.id,
        )
        assert task.id == parked.id
        assert task.context_id == parked.context_id
        a2a.assert_same_outcome(task, control_task)
        calls_resumed = live.llm.call_count

        # --- (f) The new process is usable on the live core ---------------------------
        # Path-B: a W-9 + a COI, both accepted; artifacts persist and are fetchable.
        collected = await a2a.send(
            client,
            a2a.submission((W9, COI), ctx="onboard-live", process=PROCESS, party=SUPPLIER),
            context_id="onboard-live",
        )
        assert collected.status.state == TaskState.TASK_STATE_COMPLETED
        collect_turn = a2a.turn(collected)
        assert collect_turn.terminal is True
        assert [
            (e.doc_id, e.doctype, e.disposition, e.gate, e.reason_code)
            for e in collect_turn.status.entries
        ] == [
            (
                W9[0],
                "w9-form",
                Disposition.accepted,
                Gate.auto_approve,
                "auto_approved",
            ),
            (
                COI[0],
                "insurance-cert",
                Disposition.accepted,
                Gate.auto_approve,
                "auto_approved",
            ),
        ]
        assert collect_turn.status.outstanding.requirements == []
        for entry in collect_turn.status.entries:
            assert entry.artifact.uri.startswith(f"{BASE}/artifacts/onboard-live/")
            fetched = await hc.get(entry.artifact.uri.removeprefix(BASE))
            assert fetched.status_code == 200
            assert fetched.content.startswith(
                b"%PDF-1.4 synthetic synthetic/supplier-x-"
            )

        # Path-A: the live doctype SCHEMA is enforced, not just its name.
        good = await a2a.send(
            client,
            _assertion_parts(
                PROCESS, _assertion("w9-form", "W9"), ctx="onboard-path-a-ok"
            ),
            context_id="onboard-path-a-ok",
        )
        assert good.status.state == TaskState.TASK_STATE_COMPLETED
        (good_entry,) = a2a.turn(good).status.entries
        assert good_entry.disposition is Disposition.accepted
        bad = await a2a.send(
            client,
            _assertion_parts(
                PROCESS,
                _assertion("w9-form", "W9", tin="00-0000000"),
                ctx="onboard-path-a-bad",
            ),
            context_id="onboard-path-a-bad",
        )
        (bad_entry,) = a2a.turn(bad).status.entries
        assert bad_entry.disposition is Disposition.rejected
        assert bad_entry.reason_code == "schema_invalid"

        # Structured traffic is model-free.
        assert live.llm.call_count == calls_resumed

    # --- Identity traps: one app, one executor, one leg, nothing reconstructed --------
    # (a rebuilt app / restarted process could not have found ``parked.id``: the task
    # store and the session service are in-memory.)
    assert constructed == []
    assert len(live.executor.executed) == 6  # pre-probe, park, resolve, collect, 2 x A
    assert live.executor.executed[1] == live.executor.executed[2] == parked.id
    assert live.executor.llm_leg is live.leg


# --------------------------------------------------------------------------- #
# Companion: a bad live upload is refused, not propagated (S4-usecase-3)       #
# --------------------------------------------------------------------------- #
# Each bad folder and the reason keyword its WARNING must carry.
BAD_UPLOAD_REASONS = {
    "w9-form": "frontmatter fence",
    "insurance-cert": "bridge-kind",
    KYC_PROCESS: "policy",
    BANK_LETTER: "schema",
    PROCESS: "none of its candidate doctypes",
}


def _warned(caplog, name: str, reason: str) -> bool:
    return any(
        r.name == SKILLS_LOGGER
        and r.levelno == logging.WARNING
        and repr(name) in r.getMessage()
        and reason in r.getMessage()
        for r in caplog.records
    )


def _outcomes(task) -> list[tuple]:
    return [
        (e.doc_id, e.disposition, e.gate, e.reason_code)
        for e in a2a.turn(task).status.entries
    ]


async def test_bad_live_upload_is_refused_not_propagated(
    clean_env, monkeypatch, caplog
):
    live = a2a.core()
    control = a2a.core()
    registry = get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))

    with caplog.at_level(logging.WARNING, logger=SKILLS_LOGGER):
        async with a2a.client(live.app, REVIEWER) as (hc, client):
            assert set(await a2a.card_skills(hc)) == {"address-proof"}

            # --- (a) Park an Address exchange mid-flight ------------------------------
            parked = await a2a.park(client, ctx=ADDRESS_CTX)
            call_id = a2a.meta(parked.status.message)[META_REVIEW_CALL_ID]
            assert call_id
            parked_before = await client.get_task(GetTaskRequest(id=parked.id))
            calls_parked = live.llm.call_count

            # --- (b) Control: the same exchange, no upload, to its terminal outcome ---
            # Load-bearing order: BRIDGE_SKILLS_DIR is process-global, so the control
            # must finish before the drop or it would see the bad folders as well.
            control_task = await a2a.control_outcome(control)
            assert {s.name for s in loaded_skills()} == set(ADDRESS_SKILLS)

            constructed = a2a.arm_trap_guards(monkeypatch)

            # --- (c) Drop the bad upload raw into the live registry -------------------
            write_bad_upload(clean_env)
            # The in-flight exchange is untouched: same task, no model call.
            assert await client.get_task(GetTaskRequest(id=parked.id)) == parked_before
            assert live.llm.call_count == calls_parked
            # Layer (a) refuses the four malformed folders; supplier-onboarding is
            # structurally fine, so the raw listing keeps it ...
            assert {s.name for s in registry.list_skills()} == set(ADDRESS_SKILLS) | {
                PROCESS
            }
            # ... and layer (b) drops it: none of its candidate doctypes is installed.
            assert {s.name for s in loaded_skills()} == set(ADDRESS_SKILLS)
            for name, reason in BAD_UPLOAD_REASONS.items():
                assert _warned(caplog, name, reason), (name, reason)

            # --- (d) Discovery is intact: Address only, both of its doctype tags ------
            card = await a2a.card_skills(hc)
            assert set(card) == {"address-proof"}
            assert {"doctype:gov-id", "doctype:utility-bill"} <= set(
                card["address-proof"]
            )
            assert await client.get_task(GetTaskRequest(id=parked.id)) == parked_before

            # --- S0-arch-18: a process with no installed doctype rejects everything ---
            # The gov-id is NOT dispositioned against Address's label space, and the
            # W-9 is not accepted without a schema.
            probe = await a2a.send(
                client,
                a2a.submission(
                    (W9, GOV_ID), ctx="onboard-bad", process=PROCESS, party=SUPPLIER
                ),
                context_id="onboard-bad",
            )
            assert probe.status.state == TaskState.TASK_STATE_COMPLETED
            assert {
                (e.doc_id, e.disposition, e.reason_code)
                for e in a2a.turn(probe).status.entries
            } == {
                (W9[0], Disposition.rejected, "unsupported_doctype"),
                (GOV_ID[0], Disposition.rejected, "unsupported_doctype"),
            }

            # --- Path-A is not propagated: the corrupt bank-letter schema is skipped --
            path_a = await a2a.send(
                client,
                _assertion_parts(
                    "address-proof",
                    _gov_id_clean_assertion(),
                    ctx="addr-path-a",
                    party=PARTY,
                ),
                context_id="addr-path-a",
            )
            assert path_a.status.state == TaskState.TASK_STATE_COMPLETED
            (path_a_entry,) = a2a.turn(path_a).status.entries
            assert path_a_entry.doctype == "gov-id"
            assert path_a_entry.disposition is Disposition.accepted

            # --- (e) Finish the parked exchange while the bad folders are present -----
            task = await a2a.send(
                client,
                a2a.resolution(call_id),
                context_id=parked.context_id,
                task_id=parked.id,
            )
            assert task.id == parked.id
            assert task.context_id == parked.context_id
            a2a.assert_same_outcome(task, control_task)
            calls_resumed = live.llm.call_count

            # --- (f) Refused through the seam ... -------------------------------------
            address_md = (clean_env / "address-proof" / "SKILL.md").read_text()
            with pytest.raises(ValueError):
                registry.install_skill("address-proof", TRUNCATED_W9_SKILL_MD)
            assert (clean_env / "address-proof" / "SKILL.md").read_text() == address_md
            assert "address-proof" in await a2a.card_skills(hc)

            # ... and picked up once repaired. Partial: only w9-form; the label space
            # (== the card's doctype tags) is narrowed to it.
            w9_md, w9_assets = SUPPLIER_ONBOARDING_SKILLS["w9-form"]
            registry.install_skill("w9-form", w9_md, assets=w9_assets)
            card = await a2a.card_skills(hc)
            assert PROCESS in card
            assert "doctype:w9-form" in card[PROCESS]
            assert "doctype:insurance-cert" not in card[PROCESS]
            partial = await a2a.send(
                client,
                a2a.submission(
                    (W9, COI), ctx="onboard-partial", process=PROCESS, party=SUPPLIER
                ),
                context_id="onboard-partial",
            )
            assert partial.status.state == TaskState.TASK_STATE_COMPLETED
            assert _outcomes(partial) == [
                (W9[0], Disposition.accepted, Gate.auto_approve, "auto_approved"),
                (COI[0], Disposition.rejected, Gate.unsupported, "unsupported_doctype"),
            ]

            # Full repair: insurance-cert re-uploaded good through the seam.
            coi_md, coi_assets = SUPPLIER_ONBOARDING_SKILLS["insurance-cert"]
            registry.install_skill("insurance-cert", coi_md, assets=coi_assets)
            card = await a2a.card_skills(hc)
            assert {"doctype:w9-form", "doctype:insurance-cert"} <= set(card[PROCESS])
            repaired = await a2a.send(
                client,
                a2a.submission(
                    (W9, COI), ctx="onboard-repaired", process=PROCESS, party=SUPPLIER
                ),
                context_id="onboard-repaired",
            )
            assert repaired.status.state == TaskState.TASK_STATE_COMPLETED
            assert _outcomes(repaired) == [
                (W9[0], Disposition.accepted, Gate.auto_approve, "auto_approved"),
                (COI[0], Disposition.accepted, Gate.auto_approve, "auto_approved"),
            ]

            # Repair the policy (a raw write is fine): kyc-intake is advertised.
            (clean_env / KYC_PROCESS / "assets" / "policy.yaml").write_text(POLICY_YAML)
            card = await a2a.card_skills(hc)
            assert {"doctype:gov-id", "doctype:utility-bill"} <= set(card[KYC_PROCESS])

            # Repair the schema: bank-letter is installed again.
            (clean_env / BANK_LETTER / "assets" / "schema.json").write_text(
                doctype_schema_json(BANK_LETTER)
            )
            assert BANK_LETTER in {s.name for s in registry.list_skills()}

            assert set(await a2a.card_skills(hc)) == {
                "address-proof",
                PROCESS,
                KYC_PROCESS,
            }
            # Structured traffic is model-free.
            assert live.llm.call_count == calls_resumed

    # --- Identity traps: one app, one executor, one leg, nothing reconstructed --------
    assert constructed == []
    # park, S0-arch-18 probe, Path-A probe, resolve, partial collect, repaired collect
    assert len(live.executor.executed) == 6
    assert live.executor.executed[0] == live.executor.executed[3] == parked.id
    assert live.executor.llm_leg is live.leg
