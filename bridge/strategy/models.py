"""Declarative collection-strategy models (ADR-14 Sense A′).

Strategies are static declarative data authored inside a process skill's
``assets/strategy.yaml`` — never a workflow DSL, never business policy (no LTV
ceilings or approval rules), and never mutated by Calibration (ADR-15).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategyLoadError(ValueError):
    """Raised when a declared ``assets/strategy.yaml`` is malformed or invalid."""


class StrategyRule(BaseModel):
    """One declarative document-sufficiency rule referencing ontology fields by name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    item: str = Field(min_length=1)
    doctype: str = Field(min_length=1)
    required: bool = True
    min_count: int = Field(default=1, ge=1)
    distinct_issuers: bool = False
    recency_days: int | None = Field(default=None, ge=1)
    recency_field: str | None = None
    not_expired_field: str | None = None
    required_fields: tuple[str, ...] = ()
    same_value_fields: tuple[str, ...] = ()
    distinct_issuer_from: tuple[str, ...] = ()
    priority: int = Field(default=100, ge=0)
    message: str = ""

    @model_validator(mode="after")
    def _validate_temporal_fields(self) -> StrategyRule:
        has_days = self.recency_days is not None
        has_field = bool(self.recency_field and self.recency_field.strip())
        if has_days != has_field:
            raise ValueError(
                f"rule {self.id!r}: recency_days and recency_field must both be set or both omitted"
            )
        if self.not_expired_field is not None and not self.not_expired_field.strip():
            raise ValueError(f"rule {self.id!r}: not_expired_field must not be blank")
        return self

    def referenced_fields(self) -> list[str]:
        """Return sorted unique ontology field names referenced by this rule."""
        refs: set[str] = set(self.required_fields) | set(self.same_value_fields)
        if self.recency_field:
            refs.add(self.recency_field)
        if self.not_expired_field:
            refs.add(self.not_expired_field)
        return sorted(refs)


class StrategyAlternative(BaseModel):
    """One alternative branch of rules (all required rules inside a branch must hold)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    label: str = ""
    priority: int = Field(default=100, ge=0)
    rules: tuple[StrategyRule, ...] = Field(min_length=1)


class CollectionStrategy(BaseModel):
    """Top-level declarative collection strategy parsed from ``assets/strategy.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    version: str = Field(min_length=1)
    process: str = Field(min_length=1)
    description: str = ""
    ordering: tuple[str, ...] = ()
    match: Literal["any_of", "all_of"] = "all_of"
    alternatives: tuple[StrategyAlternative, ...] = ()
    rules: tuple[StrategyRule, ...] = ()
    revision: str = ""

    @model_validator(mode="after")
    def _validate_structure(self) -> CollectionStrategy:
        if not self.alternatives and not self.rules:
            raise ValueError(
                f"strategy for {self.process!r} must declare 'alternatives' or 'rules'"
            )
        if self.alternatives and self.rules:
            raise ValueError(
                f"strategy for {self.process!r} cannot declare both top-level 'alternatives' and 'rules'"
            )
        seen_alts: set[str] = set()
        seen_rules: set[str] = set()
        for alt in self.effective_alternatives():
            if alt.id in seen_alts:
                raise ValueError(
                    f"duplicate alternative id {alt.id!r} in strategy {self.process!r}"
                )
            seen_alts.add(alt.id)
            for rule in alt.rules:
                if rule.id in seen_rules:
                    raise ValueError(
                        f"duplicate rule id {rule.id!r} in strategy {self.process!r}"
                    )
                seen_rules.add(rule.id)
        return self

    def effective_alternatives(self) -> tuple[StrategyAlternative, ...]:
        """Normalize flat ``rules`` strategies into a single ``StrategyAlternative``."""
        if self.alternatives:
            return self.alternatives
        return (
            StrategyAlternative(
                id="default",
                label=self.process,
                priority=0,
                rules=self.rules,
            ),
        )

    def all_rules(self) -> list[StrategyRule]:
        """Return every declared rule across all alternatives in declared order."""
        return [rule for alt in self.effective_alternatives() for rule in alt.rules]
