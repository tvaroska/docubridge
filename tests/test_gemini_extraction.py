"""Gemini extraction engine + GcpExtraction adapter tests (P7-6).

The credential-free tests run ALWAYS (no ``BRIDGE_TEST_GEMINI`` gate): they exercise
the structural, API-key-free surface — the pure parse helper, a fake-client extract,
the lazy-import guard, the engine-select refuse-loud, and the adapter identity. The
live corpus terminal-outcome parity test is gated on ``BRIDGE_TEST_GEMINI=1`` (the
extraction analogue of ``BRIDGE_TEST_GCP``) and SKIPS in the default suite, exactly
like the live seam tests — it is the "reproduce the Address eval corpus on the
deployed extraction path" gate, run against a provisioned project (P7-9).
"""

from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from bridge.disposition import run_disposition_gate
from bridge.extraction.gemini import (
    MAX_ECHO_CHARS,
    GeminiExtractor,
    _parse_extraction,
    build_extraction_prompt,
    build_response_schema,
    build_retry_correction,
)
from bridge.seams import Seam, SeamMode, get_adapter
from bridge.seams.gcp import GcpExtraction
from contract.extraction import ExtractionResult, fields_requiring_review

_GEMINI = os.environ.get("BRIDGE_TEST_GEMINI") == "1"

CORPUS_PATH = Path(__file__).resolve().parents[1] / "wiki/evals/address/expected.json"
with open(CORPUS_PATH) as f:
    CORPUS_RECORDS = json.load(f)["documents"]

# A canned gov-id extraction JSON in the exact ExtractionResult shape the prompt asks
# the model to return (response_mime_type=application/json).
_CANNED_GOV_ID = json.dumps(
    {
        "fields": {
            "doctype": "gov-id",
            "issuer": None,
            "key_fields": {
                "name": "Jordan Lee",
                "expiry": "2030-01-01",
            },
        },
        "overall_confidence": 0.96,
        "field_confidence": {"name": 0.97, "expiry": 0.95},
        "legible": True,
        "flagged_fields": [],
    }
)


class _FakeResponse:
    """Matches the genai ``GenerateContentResponse.text`` accessor the engine reads."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse(self._text)


class _FakeClient:
    """A minimal genai ``Client``-shaped double: ``.models.generate_content`` returns
    canned JSON. An injected fake client wins → no creds, no network."""

    def __init__(self, text: str) -> None:
        self.models = _FakeModels(text)


class _ScriptedModels:
    """Returns the scripted texts in order (the last one repeats), recording calls."""

    def __init__(self, texts: list[str | None]) -> None:
        self._texts = list(texts)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        return _FakeResponse(self._texts[index])


class _ScriptedClient:
    """A genai ``Client``-shaped double whose answers are scripted per attempt.

    The sibling of ``_FakeClient`` for the schema-validated retry (S0-bug-2): a
    bad-then-good script is what proves attempt 2 is a repair, not a re-roll.
    """

    def __init__(self, *texts: str | None) -> None:
        self.models = _ScriptedModels(list(texts))


class _RaisingClient:
    """A client whose ``generate_content`` raises (a transport/API error)."""

    class _Models:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def generate_content(self, *, model, contents, config):
            self.calls.append({"model": model, "contents": contents, "config": config})
            raise RuntimeError("503 backend unavailable")

    def __init__(self) -> None:
        self.models = self._Models()


# Payloads the ``ExtractionResult`` validator must reject: a stray top-level key
# (extra="forbid") and a truncated JSON token.
_BAD_EXTRA_FIELD = json.dumps({**json.loads(_CANNED_GOV_ID), "surprise": "unexpected"})
_BAD_TRUNCATED = '{"fields": {"doctype": "gov-id", "key_fields": {"name": "Jordan'


# --- pure parse helper ------------------------------------------------------


def test_parse_extraction_returns_valid_result():
    """The pure parse helper turns canned JSON into an ExtractionResult."""
    result = _parse_extraction(_CANNED_GOV_ID)

    assert isinstance(result, ExtractionResult)
    assert result.fields.doctype == "gov-id"
    assert result.legible is True
    assert result.flagged_fields == []
    assert result.fields.key_fields["name"] == "Jordan Lee"


def test_parse_extraction_tolerates_json_fence():
    """A stray ```json ... ``` fence is stripped before validating."""
    fenced = f"```json\n{_CANNED_GOV_ID}\n```"
    result = _parse_extraction(fenced)

    assert result.fields.doctype == "gov-id"


def test_parse_extraction_rejects_extra_field_loudly():
    """ExtractionResult is extra='forbid' — a stray field fails loud, not silent."""
    bad = json.loads(_CANNED_GOV_ID)
    bad["surprise"] = "unexpected"

    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        _parse_extraction(json.dumps(bad))


# --- fake-client extract (credential-free) ---------------------------------


async def test_extract_with_fake_client_returns_parsed_result():
    """A fake client + fake resolver drive extract end-to-end with no creds."""
    client = _FakeClient(_CANNED_GOV_ID)
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'>gov-id</svg>"
    extractor = GeminiExtractor(
        client=client,
        resolver=lambda name: (svg, "image/svg+xml"),
    )

    result = await extractor.extract("gov-id-clean")

    assert isinstance(result, ExtractionResult)
    assert result.fields.doctype == "gov-id"
    # The request carried the prompt + the SVG text (SVG rides as a text part).
    call = client.models.calls[0]
    assert call["config"]["response_mime_type"] == "application/json"
    assert "response_schema" in call["config"]  # schema-driven (S0-arch-5)
    assert svg.decode("utf-8") in call["contents"]


async def test_extract_passes_non_svg_bytes_as_inline_part():
    """A non-SVG mime rides as an inline bytes Part (not decoded to text)."""
    client = _FakeClient(_CANNED_GOV_ID)
    extractor = GeminiExtractor(
        client=client,
        resolver=lambda name: (b"\x89PNG\r\n", "image/png"),
    )

    await extractor.extract("gov-id-clean")

    from google.genai import types as genai_types

    doc = client.models.calls[0]["contents"][1]
    assert isinstance(doc, genai_types.Part)


def test_default_model_and_env_override(monkeypatch):
    """The model defaults to gemini-3.8-flash; BRIDGE_MODEL overrides."""
    monkeypatch.delenv("BRIDGE_MODEL", raising=False)
    assert GeminiExtractor()._model == "gemini-3.8-flash"

    monkeypatch.setenv("BRIDGE_MODEL", "gemini-4-argon")
    assert GeminiExtractor()._model == "gemini-4-argon"


# --- schema-driven prompt + pinned response schema (S0-arch-5) --------------

_TWO_DOCTYPES = {
    "gov-id": {
        "properties": {
            "key_fields": {
                "properties": {
                    "name": {"type": "string"},
                    "issuing_authority": {"type": "string"},
                    "expiry": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD)",
                    },
                }
            }
        }
    },
    "utility-bill": {
        "properties": {
            "issuer": {"type": "string", "description": "canonicalized issuer key"},
            "key_fields": {
                "properties": {
                    "name": {"type": "string"},
                    "service_address": {"type": "string"},
                    "statement_date": {"type": "string"},
                }
            },
        }
    },
}


def test_prompt_injects_installed_doctype_catalog():
    """The prompt carries every installed doctype and the fields it declares."""
    prompt = build_extraction_prompt(_TWO_DOCTYPES)

    assert "gov-id" in prompt and "utility-bill" in prompt
    for field in ("issuing_authority", "expiry", "service_address", "statement_date"):
        assert field in prompt
    assert "ISO date (YYYY-MM-DD)" in prompt  # property descriptions ride along
    # utility-bill declares a top-level issuer; gov-id does not.
    assert "top-level issuer: canonicalized issuer key" in prompt
    # Deterministic ordering (sorted by doctype name).
    assert prompt.index("- gov-id") < prompt.index("- utility-bill")


def test_prompt_renders_a_doctype_with_no_declared_fields():
    """An installed-but-asset-less doctype still reaches the label space, name-only."""
    prompt = build_extraction_prompt({"pay-stub": {}})

    assert "- pay-stub: (no declared fields)" in prompt


def test_response_schema_unions_key_fields_across_doctypes():
    """key_fields/field_confidence are the nullable, non-required union of all doctypes."""
    schema = build_response_schema(_TWO_DOCTYPES)

    fields = schema["properties"]["fields"]
    key_fields = fields["properties"]["key_fields"]
    props = key_fields["properties"]
    assert {"issuing_authority", "expiry", "service_address", "statement_date"} <= set(
        props
    )
    assert all(spec["nullable"] is True for spec in props.values())
    # Not required: one document only ever carries one doctype's fields (and the
    # corpus's blurry bill carries none at all).
    assert "required" not in key_fields
    # A field two doctypes share keeps both doctypes' notes.
    assert "gov-id" in props["name"]["description"]
    assert "utility-bill" in props["name"]["description"]
    # THE passport trap: doctype must stay a FREE STRING — an enum over installed
    # doctypes would make an out-of-label-space doc unreportable and silently kill
    # the `unsupported` terminal outcome.
    assert "enum" not in fields["properties"]["doctype"]
    assert fields["properties"]["doctype"]["type"] == "string"
    # field_confidence covers the union PLUS the top-level issuer (the corpus reports one).
    confidences = schema["properties"]["field_confidence"]["properties"]
    assert "issuer" in confidences
    assert set(props) <= set(confidences)
    # No additionalProperties anywhere (the Gemini OpenAPI subset does not take it).
    assert "additionalProperties" not in json.dumps(schema)


def test_response_schema_is_none_without_declared_fields():
    """No installed doctype declares fields → no schema to pin (never an empty object)."""
    assert build_response_schema({}) is None
    assert build_response_schema({"pay-stub": {}}) is None


async def test_extract_pins_response_schema_from_installed_skills():
    """The real repo skills/ registry drives the prompt AND the pinned schema."""
    client = _FakeClient(_CANNED_GOV_ID)
    extractor = GeminiExtractor(
        client=client, resolver=lambda name: (b"<svg/>", "image/svg+xml")
    )

    await extractor.extract("gov-id-clean")

    call = client.models.calls[0]
    prompt = call["contents"][0]
    for token in (
        "benefits-quote",
        "coverage_limits",
        "effective_date",
        "gov-id",
        "issuing_authority",
        "utility-bill",
        "service_address",
    ):
        assert token in prompt, token
    union = call["config"]["response_schema"]["properties"]["fields"]["properties"][
        "key_fields"
    ]["properties"]
    assert {"coverage_limits", "issuing_authority", "service_address"} <= set(union)


async def test_schema_catalog_is_read_per_extract_not_at_construction(monkeypatch):
    """The registry is read on each extract (live doctype-add), never at construction."""
    from bridge.seams.local import LocalSkillRegistry

    reads = {"n": 0}
    original = LocalSkillRegistry.list_skills

    def _counting(self):
        reads["n"] += 1
        return original(self)

    monkeypatch.setattr(LocalSkillRegistry, "list_skills", _counting)

    extractor = GeminiExtractor(
        client=_FakeClient(_CANNED_GOV_ID),
        resolver=lambda name: (b"<svg/>", "image/svg+xml"),
    )
    assert reads["n"] == 0  # nothing read at construction

    await extractor.extract("a.svg")
    assert reads["n"] == 1
    await extractor.extract("b.svg")
    assert reads["n"] == 2  # re-read per extract; the prompt is rebuilt only on change


async def test_doctype_added_after_first_extract_reaches_the_next_prompt(
    tmp_path, monkeypatch
):
    """S4-usecase-1: ONE long-lived extractor sees a doctype dropped in mid-life."""
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    client = _FakeClient(_CANNED_GOV_ID)
    extractor = GeminiExtractor(
        client=client, resolver=lambda name: (b"<svg/>", "image/svg+xml")
    )
    await extractor.extract("a.svg")
    assert "w9-form" not in str(client.models.calls[0])

    get_adapter(Seam.skill_registry, SeamMode.local).install_skill(
        "w9-form",
        "---\nname: w9-form\ndescription: A W-9.\nmetadata:\n  bridge-kind: doctype\n"
        "---\n\nExtract the W-9.\n",
    )
    await extractor.extract("b.svg")
    assert "w9-form" in str(client.models.calls[1])


async def test_new_doctype_skill_changes_the_prompt_with_no_code_change(
    tmp_path, monkeypatch
):
    """Installing a doctype skill through the seam changes extraction — no code change."""
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    registry = get_adapter(Seam.skill_registry, SeamMode.local)
    registry.install_skill(
        "pay-stub",
        "---\n"
        "name: pay-stub\n"
        "description: A pay stub used as proof of income.\n"
        "metadata:\n"
        "  bridge-kind: doctype\n"
        "  bridge-schema: assets/schema.json\n"
        "---\n\nExtract.\n",
        assets={
            "assets/schema.json": json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "doctype": {"type": "string"},
                        "key_fields": {
                            "type": "object",
                            "properties": {
                                "gross_pay": {
                                    "type": "string",
                                    "description": "gross pay for the period",
                                },
                                "pay_period": {"type": "string"},
                            },
                        },
                    },
                }
            )
        },
    )

    canned = json.dumps(
        {
            "fields": {"doctype": "pay-stub", "key_fields": {"gross_pay": "4200"}},
            "overall_confidence": 0.9,
            "legible": True,
            "flagged_fields": [],
        }
    )
    client = _FakeClient(canned)
    extractor = GeminiExtractor(
        client=client, resolver=lambda name: (b"<svg/>", "image/svg+xml")
    )

    result = await extractor.extract("stub.svg")

    assert result.fields.doctype == "pay-stub"
    call = client.models.calls[0]
    assert "pay-stub" in call["contents"][0]
    assert "gross_pay" in call["contents"][0]
    union = call["config"]["response_schema"]["properties"]["fields"]["properties"][
        "key_fields"
    ]["properties"]
    assert "gross_pay" in union and "pay_period" in union


async def test_no_doctype_schemas_pins_no_response_schema(tmp_path, monkeypatch):
    """Empty registry → JSON mime only, never an empty-properties key_fields object."""
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    client = _FakeClient(_CANNED_GOV_ID)
    extractor = GeminiExtractor(
        client=client, resolver=lambda name: (b"<svg/>", "image/svg+xml")
    )

    await extractor.extract("gov-id-clean")

    config = client.models.calls[0]["config"]
    assert "response_schema" not in config
    assert config["response_mime_type"] == "application/json"
    # S0-perf-1: every request carries an explicit output budget.
    assert config["max_output_tokens"] == 32768


def test_half_installed_doctype_contributes_name_only(tmp_path, monkeypatch):
    """A declared-but-ABSENT schema asset is a legitimate state: name-only, no raise."""
    from bridge.doctypes import doctype_schemas

    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    registry = get_adapter(Seam.skill_registry, SeamMode.local)
    registry.install_skill(
        "pay-stub",
        "---\nname: pay-stub\ndescription: A pay stub.\n"
        "metadata:\n  bridge-kind: doctype\n  bridge-schema: assets/schema.json\n---\n\nx\n",
    )

    assert doctype_schemas() == {"pay-stub": {}}


def test_unparsable_doctype_schema_is_refused_not_propagated(
    tmp_path, monkeypatch, caplog
):
    """A PRESENT but corrupt schema asset is an authoring bug (S0-arch-5, S4-usecase-3).

    Refused loud at the seam's install (``ValueError`` naming the skill, nothing
    written); dropped raw into the registry it is NOT INSTALLED — skipped with a logged
    reason — so it can never take down every other doctype's extraction / Path-A.
    """
    from bridge.doctypes import doctype_schemas

    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    registry = get_adapter(Seam.skill_registry, SeamMode.local)
    skill_md = (
        "---\nname: pay-stub\ndescription: A pay stub.\n"
        "metadata:\n  bridge-kind: doctype\n  bridge-schema: assets/schema.json\n---\n\nx\n"
    )

    # (i) Through the seam: refused before any write.
    with pytest.raises(ValueError, match="pay-stub"):
        registry.install_skill(
            "pay-stub", skill_md, assets={"assets/schema.json": "{not json"}
        )
    assert not (tmp_path / "pay-stub").exists()

    # (ii) A raw filesystem drop: skipped with a logged reason, never raised.
    (tmp_path / "pay-stub" / "assets").mkdir(parents=True)
    (tmp_path / "pay-stub" / "SKILL.md").write_text(skill_md)
    (tmp_path / "pay-stub" / "assets" / "schema.json").write_text("{not json")
    with caplog.at_level(logging.WARNING, logger="bridge.edge.skills"):
        assert doctype_schemas() == {}
    assert any(
        r.name == "bridge.edge.skills" and "pay-stub" in r.getMessage()
        for r in caplog.records
    )


def test_parse_extraction_prunes_null_key_fields_and_confidences():
    """Under the union schema the model nulls the other doctypes' fields — prune them."""
    payload = {
        "fields": {
            "doctype": "benefits-quote",
            "issuer": "Carrier A",
            "key_fields": {
                "currency": "USD",
                "premium": "1200",
                "statement_date": None,
                "expiry": None,
            },
        },
        "overall_confidence": 0.9,
        "field_confidence": {"currency": 0.9, "issuer": 0.8, "expiry": None},
        "legible": True,
        "flagged_fields": [],
    }

    result = _parse_extraction(json.dumps(payload))

    assert result.fields.key_fields == {"currency": "USD", "premium": "1200"}
    assert result.field_confidence == {"currency": 0.9, "issuer": 0.8}


# --- credential-free corpus replay (the oracle guard) -----------------------


@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
async def test_corpus_replay_keeps_terminal_outcomes(record):
    """Every corpus extraction still round-trips the schema-driven engine to the
    SAME terminal outcome — including passport-unsupported → rejected/unsupported.

    Credential-free sibling of the gated live parity test: a fake client replays the
    corpus's own extraction block through the real prompt/schema/parse path, so a
    change to the pinned schema (e.g. an enum on doctype) breaks here, not only in a
    provisioned run. The corpus is READ, never written (it is the oracle).
    """
    client = _FakeClient(json.dumps(record["extraction"]))
    extractor = GeminiExtractor(
        client=client, resolver=lambda name: (b"<svg/>", "image/svg+xml")
    )

    result = await extractor.extract(record["artifact"])

    verdict = run_disposition_gate(
        doctype=result.fields.doctype,
        legible=result.legible,
        overall_confidence=result.overall_confidence,
        flagged_fields=result.flagged_fields,
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    assert verdict["disposition"] == record["expected_disposition"]
    assert verdict["gate"] == record["expected_gate"]


# --- schema-validated retry (S0-bug-2) --------------------------------------


def _SVG_RESOLVER(name: str) -> tuple[bytes, str]:
    """The credential-free byte resolver these tests share (never touches disk)."""
    return b"<svg/>", "image/svg+xml"


async def test_extract_retries_after_schema_rejection_and_succeeds():
    """A rejected payload is repaired, not lost — and the reason is fed back.

    The follow-up turn reuses the SAME prompt + document (the artifact is not
    re-resolved) and appends the correction carrying the specific validator reason.
    """
    client = _ScriptedClient(_BAD_EXTRA_FIELD, _CANNED_GOV_ID)
    extractor = GeminiExtractor(client=client, schemas={}, resolver=_SVG_RESOLVER)

    result = await extractor.extract("gov-id-clean")

    assert isinstance(result, ExtractionResult)
    assert result.fields.doctype == "gov-id"
    calls = client.models.calls
    assert len(calls) == 2
    assert len(calls[0]["contents"]) == 2  # prompt + document
    assert len(calls[1]["contents"]) == 3  # + the correction turn
    correction = calls[1]["contents"][2]
    assert "REJECTED" in correction
    assert "surprise" in correction  # the SPECIFIC rejection reason, echoed payload
    # Same prompt + document reused: a retry must not re-ask for the artifact.
    assert calls[1]["contents"][:2] == calls[0]["contents"]


async def test_extract_retries_truncated_json():
    """A JSONDecodeError is a retryable rejection too (not only a ValidationError)."""
    client = _ScriptedClient(_BAD_TRUNCATED, _CANNED_GOV_ID)
    extractor = GeminiExtractor(client=client, schemas={}, resolver=_SVG_RESOLVER)

    result = await extractor.extract("gov-id-clean")

    assert result.fields.doctype == "gov-id"
    assert len(client.models.calls) == 2


async def test_extract_retries_empty_response_text():
    """``response.text is None`` (safety block / MAX_TOKENS) is a retryable body."""
    client = _ScriptedClient(None, _CANNED_GOV_ID)
    extractor = GeminiExtractor(client=client, schemas={}, resolver=_SVG_RESOLVER)

    result = await extractor.extract("gov-id-clean")

    assert result.fields.doctype == "gov-id"
    assert len(client.models.calls) == 2


async def test_extract_makes_exactly_one_call_on_the_happy_path():
    """No extra latency or cost per document: a valid payload ends the loop."""
    client = _ScriptedClient(_CANNED_GOV_ID)
    extractor = GeminiExtractor(client=client, schemas={}, resolver=_SVG_RESOLVER)

    await extractor.extract("gov-id-clean")

    assert len(client.models.calls) == 1


async def test_extract_resolves_the_artifact_once_across_retries():
    """The catalog/resolve/document-part happen once per extract, not per attempt."""
    resolved: list[str] = []

    def counting_resolver(name: str) -> tuple[bytes, str]:
        resolved.append(name)
        return b"<svg/>", "image/svg+xml"

    client = _ScriptedClient(_BAD_EXTRA_FIELD, _BAD_TRUNCATED, _CANNED_GOV_ID)
    extractor = GeminiExtractor(client=client, schemas={}, resolver=counting_resolver)

    await extractor.extract("gov-id-clean")

    assert len(client.models.calls) == 3
    assert resolved == ["gov-id-clean"]  # exactly one artifact resolve


async def test_extract_raises_last_validation_error_after_exhausting_attempts():
    """Exhaustion raises the ORIGINAL error, annotated — no fixture fallback."""
    client = _ScriptedClient(_BAD_EXTRA_FIELD)  # the last script entry repeats
    extractor = GeminiExtractor(client=client, schemas={}, resolver=_SVG_RESOLVER)

    with pytest.raises(ValidationError) as excinfo:
        await extractor.extract("gov-id-clean")

    assert len(client.models.calls) == 3
    notes = " ".join(getattr(excinfo.value, "__notes__", []))
    assert "3 schema-validated attempt" in notes
    assert "gov-id-clean" in notes
    assert "no fixture fallback" in notes


async def test_max_attempts_is_injectable():
    """``max_attempts`` bounds the loop in both directions."""
    one = _ScriptedClient(_BAD_EXTRA_FIELD)
    with pytest.raises(ValidationError):
        await GeminiExtractor(
            client=one, schemas={}, resolver=_SVG_RESOLVER, max_attempts=1
        ).extract("gov-id-clean")
    assert len(one.models.calls) == 1

    five = _ScriptedClient(
        _BAD_EXTRA_FIELD,
        _BAD_EXTRA_FIELD,
        _BAD_TRUNCATED,
        _BAD_TRUNCATED,
        _CANNED_GOV_ID,
    )
    result = await GeminiExtractor(
        client=five, schemas={}, resolver=_SVG_RESOLVER, max_attempts=5
    ).extract("gov-id-clean")
    assert result.fields.doctype == "gov-id"
    assert len(five.models.calls) == 5


def test_max_attempts_below_one_is_refused():
    """Refuse-loud on misconfig (the repo's posture) — never a zero-attempt engine."""
    with pytest.raises(ValueError, match="max_attempts"):
        GeminiExtractor(max_attempts=0)


async def test_transport_error_is_not_retried():
    """A 429/500 is not a schema rejection: it propagates and burns no attempt."""
    client = _RaisingClient()
    extractor = GeminiExtractor(client=client, schemas={}, resolver=_SVG_RESOLVER)

    with pytest.raises(RuntimeError, match="503"):
        await extractor.extract("gov-id-clean")

    assert len(client.models.calls) == 1


async def test_retry_surfaces_attempt_count_and_last_error_in_logs(caplog):
    """Attempt count + last error are SURFACED: a warning each, an error on exhaust."""
    client = _ScriptedClient(_BAD_EXTRA_FIELD)
    extractor = GeminiExtractor(client=client, schemas={}, resolver=_SVG_RESOLVER)

    with (
        caplog.at_level(logging.WARNING, logger="bridge.extraction.gemini"),
        pytest.raises(ValidationError),
    ):
        await extractor.extract("gov-id-clean")

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(warnings) == 3
    for n in (1, 2, 3):
        assert any(f"attempt {n}/3" in m for m in warnings)
    assert all("gov-id-clean" in m for m in warnings)
    assert len(errors) == 1
    assert "exhausted 3 schema-validated attempts" in errors[0]
    assert "gov-id-clean" in errors[0]


def test_build_retry_correction_carries_reason_and_bounded_echo():
    """Pure builder: the rejected output AND the reason ride, the echo is bounded."""
    with pytest.raises(ValidationError) as excinfo:
        _parse_extraction(_BAD_EXTRA_FIELD)
    error = excinfo.value

    correction = build_retry_correction(_BAD_EXTRA_FIELD, error)
    assert "REJECTED" in correction
    assert "surprise" in correction
    assert _BAD_EXTRA_FIELD in correction

    runaway = "x" * (MAX_ECHO_CHARS + 5000)
    bounded = build_retry_correction(runaway, error)
    assert f"first {MAX_ECHO_CHARS} characters" in bounded
    assert runaway not in bounded
    assert len(bounded) < len(runaway)


# --- lazy-import guard ------------------------------------------------------


def _google_import_names(node: ast.AST) -> list[str]:
    """Return the ``google*`` module names an import node references (else [])."""
    names: list[str] = []
    if isinstance(node, ast.Import):
        names = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom) and node.module:
        names = [node.module]
    return [n for n in names if n == "google" or n.startswith("google.")]


def test_gemini_imports_genai_lazily_not_at_module_level():
    """google.genai is imported lazily INSIDE methods, never at module top.

    ``bridge.agent`` already pulls ``google.adk`` (→ genai) into ``sys.modules`` on
    ``import bridge``, so a ``sys.modules`` check is meaningless. The load-bearing
    guarantee is structural: ``bridge/extraction/gemini.py`` adds NO module-level
    google import (it stays edge-free/cycle-free like ``fixture.py``) and builds no
    client eagerly — the credential-free test seam.
    """
    src = (
        Path(__file__).resolve().parents[1] / "bridge/extraction/gemini.py"
    ).read_text()
    tree = ast.parse(src)

    # No top-level (module body) google import.
    for stmt in tree.body:
        assert not _google_import_names(stmt), (
            f"google imported at module level: {ast.dump(stmt)} — must be lazy"
        )

    # But a lazy google import DOES exist somewhere (nested in a function).
    nested = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom)) and _google_import_names(n)
    ]
    assert nested, "expected a lazy google.genai import inside a method"

    # Construction builds no client eagerly (built on first extract).
    assert GeminiExtractor()._client is None


# --- engine-select refuse-loud ---------------------------------------------


def test_gcp_extraction_default_is_gemini(monkeypatch):
    """No engine configured → the deployed Gemini engine (default), never fixture."""
    monkeypatch.delenv("BRIDGE_EXTRACTION_ENGINE", raising=False)
    engine = GcpExtraction()._resolve_engine()
    assert isinstance(engine, GeminiExtractor)


def test_gcp_extraction_docai_refuses_without_a_processor(monkeypatch):
    """docai without BRIDGE_DOCAI_PROCESSOR refuses loud — never plain Gemini.

    The engine itself (Gemini + the Document AI OCR pre-pass) is
    ``tests/test_docai_extraction.py``'s.
    """
    monkeypatch.delenv("BRIDGE_DOCAI_PROCESSOR", raising=False)
    with pytest.raises(RuntimeError, match="BRIDGE_DOCAI_PROCESSOR"):
        GcpExtraction(engine="docai")._resolve_engine()


def test_gcp_extraction_unknown_engine_raises_value_error():
    """A misconfigured engine fails loud (refuse-loud, no silent fixture fallback)."""
    with pytest.raises(ValueError, match="unknown extraction engine"):
        GcpExtraction(engine="bogus")._resolve_engine()


def test_gcp_extraction_fixture_is_explicit_opt_in():
    """fixture is selectable only by EXPLICIT opt-in, never a silent fallback."""
    from bridge.extraction.fixture import FixtureExtractor

    engine = GcpExtraction(engine="fixture")._resolve_engine()
    assert isinstance(engine, FixtureExtractor)


def test_gcp_extraction_env_selects_engine(monkeypatch):
    """BRIDGE_EXTRACTION_ENGINE selects the engine when no arg is given."""
    from bridge.extraction.docai import DocAIPrepass

    monkeypatch.setenv("BRIDGE_EXTRACTION_ENGINE", "docai")
    monkeypatch.setenv(
        "BRIDGE_DOCAI_PROCESSOR", "projects/p/locations/us/processors/abc"
    )
    engine = GcpExtraction()._resolve_engine()
    assert isinstance(engine, GeminiExtractor)
    assert isinstance(engine._prepass, DocAIPrepass)


# --- adapter identity -------------------------------------------------------


def test_get_adapter_extraction_gcp_is_gcp_extraction():
    """P7-6: extraction dispatches to GcpExtraction in gcp mode (no longer raises)."""
    adapter = get_adapter(Seam.extraction, SeamMode.gcp)
    assert isinstance(adapter, GcpExtraction)
    assert adapter.seam is Seam.extraction


# --- live corpus terminal-outcome parity (gated) ---------------------------


@pytest.mark.skipif(
    not _GEMINI, reason="set BRIDGE_TEST_GEMINI=1 to run the live Gemini engine"
)
@pytest.mark.parametrize(
    "record", CORPUS_RECORDS, ids=[r["id"] for r in CORPUS_RECORDS]
)
async def test_live_corpus_terminal_outcome_parity(record):
    """The REAL Gemini engine reproduces the corpus TERMINAL OUTCOME (not floats).

    For each corpus doc, extract with the live engine, feed the signals into the same
    disposition gate the fixture path uses, and assert the same
    expected_disposition/expected_gate. Terminal-outcome parity only — a real Gemini
    call will not reproduce the corpus's exact confidence floats (CLAUDE.md;
    design/seams.md), so confidences are NOT asserted.
    """
    extractor = GeminiExtractor()
    result = await extractor.extract(record["artifact"])

    verdict = run_disposition_gate(
        doctype=result.fields.doctype,
        legible=result.legible,
        overall_confidence=result.overall_confidence,
        flagged_fields=result.flagged_fields,
        candidate_doctypes=["gov-id", "utility-bill"],
    )

    assert verdict["disposition"] == record["expected_disposition"]
    assert verdict["gate"] == record["expected_gate"]


# --- review provenance (S0-debt-2) ------------------------------------------


def test_response_schema_asks_for_review_reasons_without_requiring_them():
    """field_reviews is an OPTIONAL property: flagged_fields stays the required signal,
    so an engine that says nothing about WHY regresses nothing."""
    schema = build_response_schema(_TWO_DOCTYPES)

    reviews = schema["properties"]["field_reviews"]
    assert "field_reviews" not in schema["required"]
    assert "flagged_fields" in schema["required"]

    item = reviews["items"]
    assert item["required"] == ["path", "reason"]
    assert item["properties"]["reason"]["enum"] == [
        "low_confidence",
        "derived",
        "absent_from_document",
        "model_flagged",
    ]
    # Still no additionalProperties (the Gemini OpenAPI subset does not take it).
    assert "additionalProperties" not in json.dumps(schema)


def test_prompt_asks_the_model_to_state_the_reason():
    """The prompt rule is what makes the optional schema property get populated."""
    prompt = build_extraction_prompt(_TWO_DOCTYPES)

    assert "field_reviews" in prompt
    assert "absent_from_document" in prompt


def test_parse_extraction_accepts_field_reviews():
    """A canned payload carrying provenance parses; nulls in it survive pruning."""
    payload = json.loads(_CANNED_GOV_ID)
    payload["flagged_fields"] = ["expiry"]
    payload["field_reviews"] = [
        {
            "path": "expiry",
            "reason": "absent_from_document",
            "confidence": None,
            "detail": "not printed on this copy",
        }
    ]

    result = _parse_extraction(json.dumps(payload))

    assert [r.reason.value for r in result.field_reviews] == ["absent_from_document"]
    # The bare engine path collapses onto its own flag — one review, not two.
    assert fields_requiring_review(result) == ["fields.key_fields.expiry"]


def test_parse_extraction_rejects_an_unknown_review_reason():
    """A malformed reason fails loud (and is repaired by the retry loop, S0-bug-2)."""
    payload = json.loads(_CANNED_GOV_ID)
    payload["field_reviews"] = [{"path": "expiry", "reason": "vibes"}]

    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        _parse_extraction(json.dumps(payload))
