"""Path-A validate-only (PATH-A) — a party-with-agent asserts structured extraction
fields; the Bridge **schema-validates + disposes, with NO extraction-port call**.

Dual-path (``design/a2a-edge.md``): when the party has its own agent, the response
is a structured data part carrying the extraction fields the party self-reports. The
Bridge does not re-read a document (there are no bytes) — it validates the assertion's
shape and runs the disposition gate over the **asserted** signals. Path A skips the
extraction (fulfillment) graph entirely.

Schema validation is **Pydantic, not a new ``jsonschema`` dependency** (§2e), and the
typed key-field model is **built from the asserted doctype's skill schema**
(S0-arch-8): the assertion is parsed into ``contract.extraction.ExtractionResult``,
then the doctype skill's ``bridge-schema`` asset — read through the ``skill_registry``
port by ``bridge/doctypes.py``, the same asset the Path-B prompt is built from — is
turned into a ``extra="forbid"`` key-field model with the schema's ``required`` keys
mandatory, and its declared ``tables`` into a typed row model (S0-arch-10). A
missing/extra key raises ``pydantic.ValidationError`` → Path A rejects
**at validation**, before any disposition. Installing a doctype skill folder therefore
gives that doctype Path-A validation with no code change and no redeploy; the
``schema.json`` files stopped being merely the design source and became the
enforcement source.

Skip policy (a skipped check is never silent unless it is a normal state):

============================================  =================================
doctype skill installed, declares key fields   typed check runs
**no skill installed** for that doctype        skip (``debug``) — ``fields.doctype``
                                               stays a free string, and the
                                               disposition **label-space gate**
                                               rejects it ``unsupported``
installed, schema asset declared but ABSENT    skip + ``WARNING`` naming the doctype
                                               (a capability gap is never silent)
installed, asset present but unparsable JSON   ``ValueError`` out of the registry read
                                               — an authoring bug refuses loud, exactly
                                               as on the extraction path
============================================  =================================

Pure: imports ``contract`` + ``bridge.*`` + stdlib only — NO ``google.adk``, NO ``a2a``,
NEVER ``agents/``.
"""

from __future__ import annotations

import logging

from bridge.collect import project_ledger_entry
from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    DispositionThresholds,
    DispositionVerdict,
)
from bridge.doctypes import doctype_schemas, key_field_model, tables_model
from bridge.signals import dispose_signals, signals_from_result
from contract.extraction import ExtractionResult
from contract.wire import ArtifactRef, LedgerEntry

_LOG = logging.getLogger("bridge.patha")


def validate_assertion(
    assertion: dict | ExtractionResult, *, schemas: dict[str, dict] | None = None
) -> ExtractionResult:
    """Schema-validate a structured extraction assertion (§2e), no disposition.

    Parses ``assertion`` into ``ExtractionResult`` (the party's self-reported signals),
    then validates the typed key-field shape the asserted doctype's **installed skill
    schema** declares. A missing/extra required key raises ``pydantic.ValidationError``
    — the caller maps that to a rejected turn, keeping the "schema-validated" step
    visibly distinct from disposition.

    ``schemas`` (doctype -> parsed ``bridge-schema`` document) is an injectable
    catalog, mirroring ``GeminiExtractor(schemas=…)``; by default the registry is read
    per call. That read is deliberately NOT cached: ``BRIDGE_SKILLS_DIR`` changes at
    runtime, and a live-installed doctype must start validating immediately (the same
    posture as ``bridge/edge/skills.py::loaded_skills``, re-read on every card GET).
    """
    result = (
        assertion
        if isinstance(assertion, ExtractionResult)
        else ExtractionResult.model_validate(assertion)
    )
    doctype = result.fields.doctype
    catalog = doctype_schemas() if schemas is None else schemas
    schema = catalog.get(doctype)
    if schema is None:
        # Out of the installed label space — a free-string doctype (the corpus's
        # passport) must stay reportable; the disposition gate owns rejecting it.
        _LOG.debug(
            "path-a: no installed doctype skill for %r; typed check skipped", doctype
        )
        return result
    model = key_field_model(doctype, schema)
    if model is None:
        # Installed but declares no key fields (half-installed asset). Skipping is
        # right, staying quiet about it is not — a capability gap is never silent.
        _LOG.warning(
            "path-a: doctype skill %r declares no key fields; typed check skipped",
            doctype,
        )
        return result
    # Raises ValidationError on a missing/extra required key (extra="forbid").
    model.model_validate(result.fields.key_fields)
    # Row-shaped data (S0-arch-10): an undeclared table, a row missing a required
    # column, an undeclared column or an out-of-vocabulary enum value all raise the
    # same ValidationError, so the edge rejects them `schema_invalid` too.
    tables_model(doctype, schema).model_validate(result.fields.tables)
    return result


async def validate_only(
    assertion: dict | ExtractionResult,
    *,
    candidate_doctypes: list[str],
    thresholds: DispositionThresholds = DEFAULT_THRESHOLDS,
    schemas: dict[str, dict] | None = None,
) -> DispositionVerdict:
    """PATH-A: schema-validate the assertion, then dispose the asserted signals.

    The extraction port is **NEVER called** (validate-only): the party's agent already
    produced the fields. ``async`` to mirror the Path-B ``step`` signature (the executor
    branches on shape, not on sync/async), though no I/O occurs.
    """
    result = validate_assertion(assertion, schemas=schemas)
    signals = signals_from_result(result, candidate_doctypes=candidate_doctypes)
    return dispose_signals(signals, thresholds)


def validate_only_entry(
    assertion: dict | ExtractionResult,
    *,
    doc_id: str,
    candidate_doctypes: list[str],
    thresholds: DispositionThresholds = DEFAULT_THRESHOLDS,
    artifact: ArtifactRef | None = None,
    schemas: dict[str, dict] | None = None,
) -> LedgerEntry:
    """Validate + dispose a Path-A assertion into a ``LedgerEntry`` (no extraction).

    Convenience for the edge Path-A branch: reuses ``project_ledger_entry`` so a
    Path-A entry is indistinguishable in the ledger from a Path-B one. Raises
    ``pydantic.ValidationError`` on a malformed assertion (before disposition).
    """
    result = validate_assertion(assertion, schemas=schemas)
    signals = signals_from_result(result, candidate_doctypes=candidate_doctypes)
    verdict = dispose_signals(signals, thresholds)
    return project_ledger_entry(
        doc_id=doc_id, result=result, verdict=verdict, artifact=artifact
    )
