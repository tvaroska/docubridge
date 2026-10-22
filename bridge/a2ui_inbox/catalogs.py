"""Governed A2UI v0.9 JSON Schema Catalogs (Enterprise Inbox vs External Customer Portal)."""

from __future__ import annotations

from typing import Any, Literal

A2UI_V09_VERSION = "v0.9"
A2UI_V09_MIME_TYPE = "application/a2ui+json"

INBOX_CATALOG_ID = (
    "https://docubridge.io/a2ui/v0_9/catalogs/enterprise_inbox_catalog.json"
)
PORTAL_CATALOG_ID = (
    "https://docubridge.io/a2ui/v0_9/catalogs/portal_customer_catalog.json"
)

TaskLifecycleState = Literal[
    "submitted",
    "working",
    "input-required",
    "completed",
    "failed",
]

ActionStepStatus = Literal[
    "pending",
    "working",
    "input-required",
    "completed",
    "failed",
]

GovernanceDecision = Literal[
    "accept",
    "edit",
    "respond",
    "reject",
    "escalate",
]

RiskTier = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

ENTERPRISE_INBOX_CATALOG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "catalogId": INBOX_CATALOG_ID,
    "description": (
        "Enterprise Agent Inbox & Action Progress Catalog v0.9 extending the A2UI "
        "Basic Catalog with live step progress, provenance, and governance widgets."
    ),
    "allOf": [
        {"$ref": "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"}
    ],
    "components": {
        "ActionProgressCard": {
            "type": "object",
            "description": (
                "Live action progress container bound to JSON Pointer step state "
                "(/progress/percent, /progress/steps) across submitted, working, "
                "input-required, and completed states."
            ),
            "properties": {
                "id": {"type": "string"},
                "component": {"const": "ActionProgressCard"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "taskState": {
                    "type": "string",
                    "enum": [
                        "submitted",
                        "working",
                        "input-required",
                        "completed",
                        "failed",
                    ],
                },
                "progressPath": {"type": "string"},
                "stepsPath": {"type": "string"},
                "children": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "id",
                "component",
                "title",
                "taskState",
                "progressPath",
                "stepsPath",
            ],
        },
        "ApprovalCard": {
            "type": "object",
            "description": (
                "Top-level governance container displaying risk tier, confidence badge, "
                "policy subtitle, and child components when an action pauses."
            ),
            "properties": {
                "id": {"type": "string"},
                "component": {"const": "ApprovalCard"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "riskLevel": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                },
                "confidenceScore": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "children": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "component", "title", "riskLevel", "children"],
        },
        "FactConflictTable": {
            "type": "object",
            "description": (
                "Multi-row comparison table bound to a JSON Pointer array of "
                "cross-document discrepancies."
            ),
            "properties": {
                "id": {"type": "string"},
                "component": {"const": "FactConflictTable"},
                "rowsPath": {"type": "string"},
            },
            "required": ["id", "component", "rowsPath"],
        },
        "SourceCitationPill": {
            "type": "object",
            "description": (
                "Clickable provenance badge that opens the document viewer to a "
                "specific source PDF page and highlight."
            ),
            "properties": {
                "id": {"type": "string"},
                "component": {"const": "SourceCitationPill"},
                "documentTitle": {"type": "string"},
                "docId": {"type": "string"},
                "pageNumber": {"type": "integer", "minimum": 1},
                "excerpt": {"type": "string"},
                "viewerHref": {"type": "string"},
            },
            "required": ["id", "component", "documentTitle", "pageNumber"],
        },
    },
}

PORTAL_CUSTOMER_CATALOG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "catalogId": PORTAL_CATALOG_ID,
    "description": (
        "External Client & Carrier Portal Catalog v0.9 — restricted to external-safe "
        "ActionProgressCard, SourceCitationPill, and self-service inputs (excludes "
        "internal FactConflictTable, confidence scores, and escalation controls)."
    ),
    "components": {
        "ActionProgressCard": ENTERPRISE_INBOX_CATALOG_SCHEMA["components"][
            "ActionProgressCard"
        ],
        "SourceCitationPill": ENTERPRISE_INBOX_CATALOG_SCHEMA["components"][
            "SourceCitationPill"
        ],
    },
}
