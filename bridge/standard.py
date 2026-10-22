"""Standard-gap flagger (S1-benefits-3, beat 4 · CUJ-11) — sense A, surfaces the gap.

Beat 4 of the Benefits demo flags each **normalized** carrier quote against the
program-standard rules declared in the process skill (``benefits-procurement``'s
``assets/standard.yaml``). A flagged gap **routes to negotiate**; a conformant quote
routes nowhere. This is **sense A**: the Bridge SURFACES the gap, it never decides the
deal — there is no accept / reject / bind here (bind is the agent's sense-B call in
S1-benefits-5). The verdict carries only ``conformant`` + the list of ``gaps`` + a
``route`` that is either ``"negotiate"`` or ``"none"``.

Flagging runs on the **normalized** quote (post-FX, one currency/language — the output of
``bridge.normalize.normalize_from_extraction``). That is what makes a numeric cap like
"deductible <= 1000" comparable across carriers that quoted in different currencies.

The rule vocabulary is finalized here (three checks): ``max`` (numeric ceiling),
``must_include`` (substring present), ``must_not_include`` (substring absent). Rule
``field`` values are ``NormalizedQuote`` attribute names (flagging is post-normalization);
an unknown ``check`` or ``field`` degrades LOUDLY (``ValueError`` naming it), never
silently.

**Deferrals (no silent cap):**

* ``route`` stays a Bridge-internal string constant (``ROUTE_NEGOTIATE`` / ``ROUTE_NONE``).
  Promoting it to a ``contract/`` enum so it rides the A2A wire to trigger dial-out /
  Negotiate is **S1-benefits-4/5**.
* The revise loop / versioned re-flag on a revised quote (re-running ``flag_quote`` as new
  artifact versions land) is **S1-benefits-5**; this task produces the *initial* gap
  verdict + route only.
* Agent standard-override (swap ``standard.yaml`` per client) is supported by construction
  (``load_program_standard`` + ``overridable: true``); ``flag_quote`` takes an explicit
  ``ProgramStandard``, so an override is a caller concern — wiring an override *channel* is
  out of scope.

Pure: imports stdlib + pydantic + pyyaml + ``bridge.sla`` (``skills_root``) +
``bridge.normalize.NormalizedQuote`` only — NO ``google.adk``, NO ``a2a``, NEVER
``agents/`` (the ``bridge/`` isolation guard, ``tests/test_isolation_guard.py``). The
transform is synchronous — it operates on already-normalized quotes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from bridge.normalize import NormalizedQuote
from bridge.sla import skills_root

# --- Route constants (Bridge-internal; contract/ enum deferred to S1-benefits-4/5). ---
ROUTE_NEGOTIATE = "negotiate"
ROUTE_NONE = "none"

# --- The finalized check vocabulary. ---
CHECK_MAX = "max"
CHECK_MUST_INCLUDE = "must_include"
CHECK_MUST_NOT_INCLUDE = "must_not_include"
_KNOWN_CHECKS = (CHECK_MAX, CHECK_MUST_INCLUDE, CHECK_MUST_NOT_INCLUDE)


@dataclass(frozen=True)
class StandardRule:
    """One declared program-standard rule.

    ``field`` is a ``NormalizedQuote`` attribute name (flagging is post-normalization).
    ``check`` is one of the finalized verbs (``max``/``must_include``/``must_not_include``);
    the operand is ``limit`` for ``max`` and ``value`` for the substring checks.
    """

    id: str
    field: str
    check: str
    limit: float | None = None
    value: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> StandardRule:
        """Build a rule from a YAML rule dict, validating the ``check`` verb.

        Raises ``ValueError`` on an unknown ``check`` (naming the known verbs) or a
        missing operand for the check.
        """
        try:
            rule_id = raw["id"]
            field = raw["field"]
            check = raw["check"]
        except KeyError as e:
            raise ValueError(
                f"standard rule missing required key {e}; got {sorted(raw)}"
            ) from e

        if check not in _KNOWN_CHECKS:
            raise ValueError(
                f"unknown check {check!r} in rule {rule_id!r}; "
                f"known checks: {list(_KNOWN_CHECKS)}"
            )

        limit = raw.get("limit")
        value = raw.get("value")
        if check == CHECK_MAX and limit is None:
            raise ValueError(f"rule {rule_id!r} check 'max' requires a 'limit'")
        if check in (CHECK_MUST_INCLUDE, CHECK_MUST_NOT_INCLUDE) and value is None:
            raise ValueError(f"rule {rule_id!r} check {check!r} requires a 'value'")

        return cls(
            id=rule_id,
            field=field,
            check=check,
            limit=float(limit) if limit is not None else None,
            value=str(value) if value is not None else None,
        )


@dataclass(frozen=True)
class ProgramStandard:
    """The declared program standard: an ordered rule set + the override flag.

    ``overridable`` is the spec design parameter — the agent may swap the standard per
    client (``load_program_standard`` reads whatever ``standard.yaml`` is declared).
    """

    rules: tuple[StandardRule, ...]
    overridable: bool


class StandardGap(BaseModel):
    """One violated rule — what the standard expected vs. what the quote carried.

    ``expected``/``actual`` are human-readable strings (the money value / the field text),
    keeping the gap platform-stable and wire-friendly (no floats).
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    field: str
    check: str
    expected: str
    actual: str
    message: str


class StandardGapVerdict(BaseModel):
    """Sense-A verdict for one leg — SURFACES the gap, never decides the deal.

    Carries ``conformant`` + the list of ``gaps`` + a ``route`` that is only ever
    ``ROUTE_NEGOTIATE`` (gaps present) or ``ROUTE_NONE`` (conformant). There is
    deliberately NO accept / reject / bind field: bind is the agent's sense-B call
    (S1-benefits-5), not the Bridge's.
    """

    model_config = ConfigDict(extra="forbid")

    party: str
    conformant: bool
    gaps: list[StandardGap]
    route: str


def _load_standard_doc(skill_name: str, *, skills_dir: Path | None = None) -> dict:
    """Resolve + parse a process skill's declared ``standard.yaml`` (the whole doc).

    Mirrors ``bridge.sla._load_policy_doc`` but follows the ``metadata.bridge-program-standard``
    frontmatter pointer (default ``assets/standard.yaml``). Kept local (rather than reusing
    ``_load_policy_doc``, which hardcodes the ``bridge-policy`` pointer) to keep
    ``bridge/sla.py`` off this task's change surface. Raises ``ValueError`` on a missing
    skill / missing frontmatter / missing file, matching the ``sla.py`` error style.
    """
    skill_dir = skills_root(skills_dir) / skill_name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise ValueError(f"skill {skill_name!r} not found at {skill_md}")

    raw = skill_md.read_text()
    parts = raw.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError(f"{skill_md} missing frontmatter fence (---…---)")
    frontmatter = yaml.safe_load(parts[1])

    standard_rel = frontmatter.get("metadata", {}).get(
        "bridge-program-standard", "assets/standard.yaml"
    )
    standard_path = skill_dir / standard_rel
    if not standard_path.exists():
        raise ValueError(f"program-standard file {standard_path} not found")

    return yaml.safe_load(standard_path.read_text())


def load_program_standard(
    skill_name: str, *, skills_dir: Path | None = None
) -> ProgramStandard:
    """Load the declared program standard from a process skill's ``standard.yaml``.

    Resolves the file via the SKILL.md ``metadata.bridge-program-standard`` pointer
    (default ``assets/standard.yaml``), parses the ``rules`` + ``overridable`` keys, and
    builds a ``ProgramStandard``. Raises ``ValueError`` on a missing skill / file /
    malformed rule, or an unknown check verb (from ``StandardRule.from_dict``).
    """
    doc = _load_standard_doc(skill_name, skills_dir=skills_dir)

    raw_rules = doc.get("rules")
    if not raw_rules:
        raise ValueError(
            f"skill {skill_name!r} standard.yaml missing a non-empty 'rules:' list"
        )
    rules = tuple(StandardRule.from_dict(r) for r in raw_rules)
    overridable = bool(doc.get("overridable", False))
    return ProgramStandard(rules=rules, overridable=overridable)


def _field_value(quote: NormalizedQuote, field: str) -> str:
    """Read ``field`` off the normalized quote, raising ``ValueError`` if unknown.

    Degrades LOUDLY (mirrors ``fixed_fx``'s known-codes style): an unknown ``field`` is a
    misconfigured standard, not a silent no-op.
    """
    if field not in NormalizedQuote.model_fields:
        known = sorted(NormalizedQuote.model_fields)
        raise ValueError(
            f"unknown field {field!r} in standard rule; NormalizedQuote fields: {known}"
        )
    return str(getattr(quote, field))


def _clean_money(amount: str) -> Decimal:
    """Parse a monetary string to ``Decimal`` (the ``normalize._convert_money`` idiom).

    Strips a leading currency symbol / grouping commas / whitespace, then parses. Raises
    ``ValueError`` on an unparseable amount.
    """
    cleaned = amount.strip().lstrip("$€£").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"unparseable monetary amount {amount!r}") from exc


def _evaluate(quote: NormalizedQuote, rule: StandardRule) -> StandardGap | None:
    """Evaluate one rule against the normalized quote; return a gap or ``None``.

    Raises ``ValueError`` on an unknown field/check (degrade loudly).
    """
    actual = _field_value(quote, rule.field)

    if rule.check == CHECK_MAX:
        value = _clean_money(actual)
        limit = Decimal(str(rule.limit))
        if value > limit:
            expected = f"<= {rule.limit}"
            return StandardGap(
                rule_id=rule.id,
                field=rule.field,
                check=rule.check,
                expected=expected,
                actual=actual,
                message=f"{rule.field} {actual} exceeds the program standard ({expected}).",
            )
        return None

    if rule.check == CHECK_MUST_INCLUDE:
        if rule.value.lower() not in actual.lower():
            expected = f"must include {rule.value!r}"
            return StandardGap(
                rule_id=rule.id,
                field=rule.field,
                check=rule.check,
                expected=expected,
                actual=actual,
                message=f"{rule.field} {actual!r} does not meet the program standard ({expected}).",
            )
        return None

    if rule.check == CHECK_MUST_NOT_INCLUDE:
        if rule.value.lower() in actual.lower():
            expected = f"must not include {rule.value!r}"
            return StandardGap(
                rule_id=rule.id,
                field=rule.field,
                check=rule.check,
                expected=expected,
                actual=actual,
                message=f"{rule.field} {actual!r} violates the program standard ({expected}).",
            )
        return None

    # Unreachable: StandardRule.from_dict rejects unknown checks at load time.
    raise ValueError(f"unknown check {rule.check!r} in rule {rule.id!r}")


def flag_quote(quote: NormalizedQuote, standard: ProgramStandard) -> StandardGapVerdict:
    """Flag one normalized quote against the program standard (sense A).

    Evaluates every rule, collects the gaps, and decides the route: ``ROUTE_NEGOTIATE``
    when any gap is present, else ``ROUTE_NONE``. NEVER returns accept / reject / bind —
    the Bridge surfaces the gap; the deal is the agent's (sense B).
    """
    gaps = [
        gap for rule in standard.rules if (gap := _evaluate(quote, rule)) is not None
    ]
    conformant = not gaps
    route = ROUTE_NONE if conformant else ROUTE_NEGOTIATE
    return StandardGapVerdict(
        party=quote.party,
        conformant=conformant,
        gaps=gaps,
        route=route,
    )


def flag_program(
    quotes: list[NormalizedQuote], standard: ProgramStandard
) -> list[StandardGapVerdict]:
    """Flag every leg of a program (convenience for the comparison view / rollup).

    Maps ``flag_quote`` over the legs, preserving order.
    """
    return [flag_quote(quote, standard) for quote in quotes]
