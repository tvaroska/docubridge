"""The consumer's ``BenefitsQuote``, mirrored, plus the wire -> consumer mapper.

**What this mirrors.** ``~/benefits-consumer/contract/quote.py``, ``~/benefits-consumer/contract/enums.py`` and
``~/benefits-consumer/contract/wire.py`` (``SourceRef`` / ``ArtifactRef``) at Benefits Consumer ``83c86cd``
(2026-09-30): field names, optionality, closed enums and ``extra="forbid"`` are
identical. The mapping rules mirror ``~/benefits-consumer/engine/extraction/gcp.py::_money`` /
``_to_quote`` (money is code-parsed from text; a design row with no carrier term gets
``category_original = category`` AND a review entry).

**Why a mirror, not an import.** The consumer's package is also named ``contract``, so it
can never be imported in-process next to ours; and ``tests/`` must not depend on another
checkout being present. The drift guard is
``test_benefits_conformance.py::test_real_consumer_accepts_mapped_quote`` (gated on
``BRIDGE_TEST_CONSUMER_REPO``): it hands the mapped quote to the consumer's **own**
``BenefitsQuote`` in a subprocess and compares field sets and enums with this mirror.

**One deliberate omission: ``fx``.** The consumer's ``BenefitsQuote.fx`` is always
``None`` at intake (its FX normalize sets it later, when a quote is converted into the
comparison currency), so it is not mirrored; the drift check tolerates exactly that one
field.

**The mapper reads only the wire.** It imports ``contract.*`` and never ``bridge``: it is
consumer-side code, and it sees exactly what a consumer sees — the ``LedgerEntry`` on the
``ExchangeTurn`` — plus the intake context the document never carries
(:class:`ConsumerIntake`). Where the Bridge's vocabulary is wider than the consumer's
(``WEEKLY`` / ``SEMI_MONTHLY`` frequencies, a non-``FLAT`` ``rate_basis``), the mapper
refuses loud with :class:`ConformanceGap` — it never coerces.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum

from pydantic import BaseModel, ConfigDict

from contract.wire import LedgerEntry

CONSUMER_CONTRACT_VERSION = "1"  # the consumer's own contract version, not ours


# --------------------------------------------------------------------------- #
# The mirrored consumer vocabulary (``~/benefits-consumer/contract/enums.py``)               #
# --------------------------------------------------------------------------- #
class ProductLine(str, Enum):
    AD_AND_D = "AD_AND_D"
    CRITICAL_ILLNESS = "CRITICAL_ILLNESS"
    HOSPITAL_INDEMNITY = "HOSPITAL_INDEMNITY"
    LIFE = "LIFE"


class CoverageTier(str, Enum):
    EE = "EE"
    EE_SPOUSE = "EE_SPOUSE"
    EE_CHILDREN = "EE_CHILDREN"
    FAMILY = "FAMILY"


class TobaccoClass(str, Enum):
    UNI = "UNI"
    NON_TOBACCO = "NON_TOBACCO"
    TOBACCO = "TOBACCO"


class RateFrequency(str, Enum):
    """The consumer's billing frequencies — narrower than the Bridge's on purpose."""

    MONTHLY = "MONTHLY"
    BI_WEEKLY = "BI_WEEKLY"
    ANNUAL = "ANNUAL"


class SourcePath(str, Enum):
    upload_xlsx = "upload_xlsx"
    upload_pdf = "upload_pdf"
    email = "email"
    a2a = "a2a"


# --------------------------------------------------------------------------- #
# The mirrored consumer models (``~/benefits-consumer/contract/wire.py`` + ``quote.py``)     #
# --------------------------------------------------------------------------- #
class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    mime_type: str | None = None
    name: str | None = None


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: SourcePath
    artifact: ArtifactRef


class DesignField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    category_original: str | None = None
    value: str
    requested_value: str | None = None


class RateTier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage_tier: CoverageTier
    tobacco: TobaccoClass | None = None
    age_band: str | None = None
    amount: Decimal
    frequency: RateFrequency
    benefit_amount: Decimal | None = None


class CommissionTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basis: str | None = None
    rate_pct: Decimal | None = None
    notes: str | None = None


class BenefitsQuote(BaseModel):
    """The consumer's normalized quote, minus ``fx`` (see the module docstring)."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONSUMER_CONTRACT_VERSION
    carrier: str
    carrier_term_original: str | None = None
    client: str
    program: str
    plan_year: int
    product_line: ProductLine
    currency: str
    language: str
    effective_date: date
    expiration_date: date
    is_incumbent: bool
    source: SourceRef

    design: list[DesignField] = []
    rates: list[RateTier] = []
    commission: CommissionTerms | None = None

    extraction_confidence: float
    fields_requiring_review: list[str] = []
    caveats: list[str] = []


# --------------------------------------------------------------------------- #
# Consumer-side intake + the mapping                                           #
# --------------------------------------------------------------------------- #
class ConsumerIntake(BaseModel):
    """The context the document never carries — supplied by the consumer, not the Bridge."""

    model_config = ConfigDict(extra="forbid")

    client: str
    program: str
    is_incumbent: bool
    plan_year: int | None = None  # fallback when the document prints no plan year


class ConformanceGap(ValueError):
    """The entry cannot land in the consumer's quote without coercion.

    ``gaps`` lists every offending consumer path with its reason — all of them, so one
    run names the whole gap rather than the first symptom.
    """

    def __init__(self, gaps: list[str], *, doc_id: str | None = None) -> None:
        self.gaps = list(gaps)
        subject = f"entry {doc_id!r}" if doc_id else "entry"
        super().__init__(
            f"{subject} cannot land in the consumer's BenefitsQuote without coercion: "
            + "; ".join(self.gaps)
        )


def consumer_money(raw: object) -> Decimal:
    """Verbatim mirror of the consumer's ``_money``: text only, strip ``$ , space``."""
    if not isinstance(raw, str):
        raise ValueError(f"money must be text; got {type(raw).__name__} {raw!r}")
    cleaned = raw.strip().replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned:
        raise ValueError(f"money must be non-empty text; got {raw!r}")
    try:
        return Decimal(cleaned)
    except InvalidOperation as e:
        raise ValueError(f"money is not a decimal number; got {raw!r}") from e


# Bridge key field -> consumer field (the scalar half of the mapping table).
_KEY_FIELD_MAP = {
    "currency": "currency",
    "language": "language",
    "product_line": "product_line",
    "plan_year": "plan_year",
    "effective_date": "effective_date",
    "expiry_date": "expiration_date",
    "carrier_product_name": "carrier_term_original",
    "commission_basis": "commission.basis",
    "commission_rate_pct": "commission.rate_pct",
    "commission_notes": "commission.notes",
}
# Table columns the consumer models. ``rate_basis`` has no consumer column: a review
# flag on it addresses the whole row (``rates[i]``), since it qualifies ``amount``.
_TABLE_COLUMNS = {
    "rates": {
        "coverage_tier",
        "tobacco",
        "age_band",
        "amount",
        "frequency",
        "benefit_amount",
        "rate_basis",
    },
    "design": {"category", "category_original", "value"},
}
_ROW_PATH = re.compile(r"^fields\.tables\.(rates|design)(?:\[(\d+)\](?:\.(\w+))?)?$")
_LOSSLESS_RATE_BASES = {None, "FLAT"}


def consumer_review_path(bridge_path: str) -> str:
    """Map one Bridge review path onto the consumer's ``fields_requiring_review`` path.

    | Bridge path (``LedgerEntry.field_reviews[].path``)   | consumer path            |
    |------------------------------------------------------|--------------------------|
    | ``fields.key_fields.{currency,language,product_line,plan_year,effective_date}`` | same name |
    | ``fields.key_fields.expiry_date``                    | ``expiration_date``      |
    | ``fields.key_fields.carrier_product_name``           | ``carrier_term_original``|
    | ``fields.key_fields.commission_{basis,rate_pct,notes}`` | ``commission.{basis,rate_pct,notes}`` |
    | ``fields.issuer``                                    | ``carrier``              |
    | ``fields.tables.rates[i].{col}``                     | ``rates[i].{col}``       |
    | ``fields.tables.rates[i].rate_basis``                | ``rates[i]`` (no column) |
    | ``fields.tables.rates[i]`` / ``fields.tables.rates`` | ``rates[i]`` / ``rates`` |
    | ``fields.tables.design…``                            | ``design…`` (same rules) |

    Anything else — ``fields.doctype``, a key field the consumer does not model
    (``premium``, ``deductible``, ``coverage_limits``, ``exclusions``), an unknown
    table or column — raises :class:`ConformanceGap`, never a silent drop: a review
    flag must survive the mapping.
    """
    if bridge_path == "fields.issuer":
        return "carrier"
    prefix = "fields.key_fields."
    if bridge_path.startswith(prefix) and bridge_path[len(prefix) :] in _KEY_FIELD_MAP:
        return _KEY_FIELD_MAP[bridge_path[len(prefix) :]]
    match = _ROW_PATH.match(bridge_path)
    if match:
        table, index, column = match.groups()
        if index is None:
            return table
        if column is None or (table == "rates" and column == "rate_basis"):
            return f"{table}[{index}]"
        if column in _TABLE_COLUMNS[table]:
            return f"{table}[{index}].{column}"
    raise ConformanceGap(
        [
            f"fields_requiring_review: {bridge_path} (no consumer field to carry the flag)"
        ]
    )


def _enum_value(enum_cls: type[Enum], raw: str | None) -> bool:
    return raw is not None and raw in {m.value for m in enum_cls}


def _dedup(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(paths))


def map_entry_to_quote(entry: LedgerEntry, intake: ConsumerIntake) -> BenefitsQuote:
    """Map one ``LedgerEntry`` onto the consumer's ``BenefitsQuote`` (mirrors ``_to_quote``).

    Every gap is collected, then raised once as :class:`ConformanceGap`. Money is parsed
    by :func:`consumer_money` from the wire's text; dates by ``date.fromisoformat``;
    ``extraction_confidence`` is the wire's ``overall_confidence`` (required — a consumer
    reading only the ``ExchangeTurn`` must not invent it).
    """
    gaps: list[str] = []
    kf = dict(entry.key_fields)

    # Key fields the consumer does not model would be dropped: refuse, never lose data.
    for name in kf:
        if name not in _KEY_FIELD_MAP:
            gaps.append(f"key_fields.{name} (no consumer field)")
    for table in entry.tables:
        if table not in _TABLE_COLUMNS:
            gaps.append(f"tables.{table} (no consumer field)")

    if not entry.issuer:
        gaps.append("carrier (the entry carries no issuer)")

    product_line = kf.get("product_line")
    if not _enum_value(ProductLine, product_line):
        gaps.append(f"product_line={product_line} (outside the consumer's ProductLine)")

    plan_year = intake.plan_year
    if "plan_year" in kf:
        try:
            plan_year = int(kf["plan_year"].strip())
        except ValueError:
            gaps.append(f"plan_year={kf['plan_year']!r} (not an integer year)")
    elif plan_year is None:
        gaps.append("plan_year (absent from the document and from intake)")

    for name in ("currency", "language"):
        if not kf.get(name):
            gaps.append(f"{name} (absent)")

    dates: dict[str, date] = {}
    for wire_name in ("effective_date", "expiry_date"):
        target = _KEY_FIELD_MAP[wire_name]
        raw = kf.get(wire_name)
        if raw is None:
            gaps.append(f"{target} (absent)")
            continue
        try:
            dates[target] = date.fromisoformat(raw.strip())
        except ValueError:
            gaps.append(f"{target}={raw!r} (not an ISO date)")

    if entry.artifact is None:
        gaps.append("source.artifact (the entry carries no artifact reference)")

    rates: list[RateTier] = []
    for i, row in enumerate(entry.tables.get("rates", [])):
        row_gaps: list[str] = []
        for column in row:
            if column not in _TABLE_COLUMNS["rates"]:
                row_gaps.append(f"rates[{i}].{column} (no consumer column)")
        frequency = row.get("frequency")
        if not _enum_value(RateFrequency, frequency):
            row_gaps.append(
                f"rates[{i}].frequency={frequency} (outside the consumer's RateFrequency)"
            )
        tier = row.get("coverage_tier")
        if not _enum_value(CoverageTier, tier):
            row_gaps.append(f"rates[{i}].coverage_tier={tier} (outside CoverageTier)")
        tobacco = row.get("tobacco")
        if tobacco is not None and not _enum_value(TobaccoClass, tobacco):
            row_gaps.append(f"rates[{i}].tobacco={tobacco} (outside TobaccoClass)")
        basis = row.get("rate_basis")
        if basis not in _LOSSLESS_RATE_BASES:
            # Dropping a per-$1,000 / per-unit basis would change what `amount` means.
            row_gaps.append(
                f"rates[{i}].rate_basis={basis} (the consumer's RateTier has no "
                f"rate_basis; only FLAT drops losslessly)"
            )
        amounts: dict[str, Decimal | None] = {"benefit_amount": None}
        for column in ("amount", "benefit_amount"):
            if column not in row:
                if column == "amount":
                    row_gaps.append(f"rates[{i}].amount (absent)")
                continue
            try:
                amounts[column] = consumer_money(row[column])
            except ValueError:
                row_gaps.append(f"rates[{i}].{column}={row[column]!r} (not money)")
        gaps.extend(row_gaps)
        if row_gaps:
            continue
        rates.append(
            RateTier(
                coverage_tier=tier,
                tobacco=tobacco,
                age_band=row.get("age_band"),
                amount=amounts["amount"],
                frequency=frequency,
                benefit_amount=amounts["benefit_amount"],
            )
        )

    design: list[DesignField] = []
    derived_review: list[str] = []
    for i, row in enumerate(entry.tables.get("design", [])):
        row_gaps = [
            f"design[{i}].{column} (no consumer column)"
            for column in row
            if column not in _TABLE_COLUMNS["design"]
        ]
        for column in ("category", "value"):
            if not row.get(column):
                row_gaps.append(f"design[{i}].{column} (absent)")
        gaps.extend(row_gaps)
        if row_gaps:
            continue
        original = (row.get("category_original") or "").strip()
        if not original:
            # The consumer's rule: close the hole honestly AND surface it for review.
            original = row["category"]
            derived_review.append(f"design[{i}].category_original")
        design.append(
            DesignField(
                category=row["category"],
                category_original=original,
                value=row["value"],
            )
        )

    commission = None
    if any(name.startswith("commission_") for name in kf):
        rate_pct = None
        if "commission_rate_pct" in kf:
            try:
                rate_pct = consumer_money(kf["commission_rate_pct"])
            except ValueError:
                gaps.append(
                    f"commission.rate_pct={kf['commission_rate_pct']!r} (not a number)"
                )
        commission = CommissionTerms(
            basis=kf.get("commission_basis"),
            rate_pct=rate_pct,
            notes=kf.get("commission_notes"),
        )

    if entry.overall_confidence is None:
        gaps.append("extraction_confidence (the wire carried no overall_confidence)")

    review: list[str] = []
    for field_review in entry.field_reviews:
        try:
            review.append(consumer_review_path(field_review.path))
        except ConformanceGap as gap:
            gaps.extend(gap.gaps)

    if gaps:
        raise ConformanceGap(gaps, doc_id=entry.doc_id)

    return BenefitsQuote(
        carrier=entry.issuer,
        carrier_term_original=kf.get("carrier_product_name"),
        client=intake.client,
        program=intake.program,
        plan_year=plan_year,
        product_line=product_line,
        currency=kf["currency"],
        language=kf["language"],
        effective_date=dates["effective_date"],
        expiration_date=dates["expiration_date"],
        is_incumbent=intake.is_incumbent,
        source=SourceRef(
            path=SourcePath.a2a,
            artifact=ArtifactRef(**entry.artifact.model_dump()),
        ),
        design=design,
        rates=rates,
        commission=commission,
        extraction_confidence=entry.overall_confidence,
        fields_requiring_review=_dedup(review + derived_review),
        caveats=[],
    )
