---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: benefits-quote
context_set: employee-benefits-procurement
revision_id: ont-rev-v1-1-baseline
source_priority: 20
aliases: []
declares_issuer: false
issuer_description: "Employee benefits insurance carrier issuing the voluntary or group rate proposal."

entity_description:
  what_it_is: >
    Employee Benefits Carrier Quote proposing voluntary or group coverage rates
    (AD&D, Critical Illness, Hospital Indemnity, or Group Life), benefit design
    provisions, and broker commission schedules for a plan year.
  where_to_read: >
    Identified by carrier proposal cover pages, tiered employee rate tables
    (EE, EE+Spouse, EE+Children, Family), and Benefit Design Summary schedules.
  distinguish_from: >
    Do NOT confuse with an employer's outbound Request for Proposal
    (`benefits-rfp`) or a commercial property & casualty placement quote
    (`commercial-property-quote`).

fields:
  currency:
    type: string
    required: true
    monetary: false
    synonyms: ["Quote Currency"]
    description:
      what_it_is: "ISO 4217 currency code (e.g. USD, EUR, GBP)."
      where_to_read: "Rate table column headers or proposal footer."
      distinguish_from: "Return ISO 4217 code (USD, EUR, GBP), never raw currency symbols."
  language:
    type: string
    required: true
    monetary: false
    synonyms: ["Document Language", "BCP-47 Language"]
    description:
      what_it_is: "BCP-47 two-letter primary language tag of the document text (e.g. en, fr, de)."
      where_to_read: "Inferred from the primary prose language of the quote."
      distinguish_from: "Do NOT return country codes (US, GB) instead of language tags (en, fr, de)."
  premium:
    type: string
    required: true
    monetary: true
    feeds_metric: total_premium
    synonyms: ["Total Annualized Premium", "Headline Monthly/Annual Premium"]
    description:
      what_it_is: "Headline premium amount in the quote's native currency exactly as printed."
      where_to_read: "Proposal Financial Summary box or Total Estimated Annual Premium line."
      distinguish_from: "Do NOT sum per-tier unit rates yourself and do NOT extract broker commission amounts."
  deductible:
    type: string
    required: true
    monetary: true
    feeds_metric: total_deductible
    synonyms: ["Plan Deductible", "Elimination Period / Benefit Deductible"]
    description:
      what_it_is: "Deductible amount in the quote's native currency as printed."
      where_to_read: "Benefit Schedule / Deductible & Cost-Sharing row."
      distinguish_from: "Do NOT confuse with out-of-pocket maximums or benefit coverage limits."
  coverage_limits:
    type: string
    required: true
    monetary: true
    feeds_metric: total_coverage_limit
    synonyms: ["Maximum Benefit Amount", "Guaranteed Issue / Coverage Limit"]
    description:
      what_it_is: "Coverage limits or covered benefit maximums printed in the quote."
      where_to_read: "Schedule of Benefits ('Coverage Limits' / 'Maximum Benefit')."
      distinguish_from: "Do NOT extract per-paycheck employee rate deductions."
  exclusions:
    type: string
    required: true
    monetary: false
    synonyms: ["Policy Exclusions", "Limitations & Carve-Outs"]
    description:
      what_it_is: "Excluded coverages, pre-existing condition limitations, or policy carve-outs."
      where_to_read: "Exclusions & Limitations section of the quote."
      distinguish_from: "Do NOT fabricate exclusions when the document marks them absent."
  effective_date:
    type: string
    required: true
    monetary: false
    synonyms: ["Plan Effective Date", "Coverage Inception Date"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) the quoted plan takes effect."
      where_to_read: "Cover page or Proposal Summary ('Effective Date')."
      distinguish_from: "Do NOT extract Quote Preparation Date or Rate Guarantee Expiry Date."
  expiry_date:
    type: string
    required: true
    monetary: false
    synonyms: ["Quote Expiration Date", "Rate Guarantee End Date"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) when the quote or rate guarantee expires."
      where_to_read: "Proposal validity footer or Rate Guarantee block."
      distinguish_from: "Do NOT extract the effective inception date."
  product_line:
    type: string
    required: false
    monetary: false
    synonyms: ["Voluntary Benefit Line", "Coverage Type"]
    description:
      what_it_is: "Voluntary-benefit product line enum (AD_AND_D, CRITICAL_ILLNESS, HOSPITAL_INDEMNITY, LIFE)."
      where_to_read: "Product header banner and Schedule of Benefits title."
      distinguish_from: "Map strictly to one of AD_AND_D, CRITICAL_ILLNESS, HOSPITAL_INDEMNITY, or LIFE."
  carrier_product_name:
    type: string
    required: false
    monetary: false
    synonyms: ["Proprietary Plan Name"]
    description:
      what_it_is: "The carrier's own proprietary product name, verbatim as printed."
      where_to_read: "Cover banner or title block."
      distinguish_from: "Preserve exact carrier branding rather than generic product line labels."
  plan_year:
    type: string
    required: false
    monetary: false
    synonyms: ["Policy Year"]
    description:
      what_it_is: "4-digit plan year the quote covers (e.g. 2027)."
      where_to_read: "Cover page subtitle or Effective Date year."
      distinguish_from: "Do NOT return full ISO dates when only the 4-digit year is requested."
  commission_basis:
    type: string
    required: false
    monetary: false
    synonyms: ["Broker Commission Structure"]
    description:
      what_it_is: "Broker commission basis verbatim as printed (e.g. heaped, level)."
      where_to_read: "Producer Compensation / Commission Schedule section."
      distinguish_from: "Do NOT infer commission basis if omitted from the quote."
  commission_rate_pct:
    type: string
    required: false
    monetary: false
    synonyms: ["Producer Commission Percentage"]
    description:
      what_it_is: "Broker commission rate as a percentage text exactly as printed (e.g. 20)."
      where_to_read: "Producer Compensation / Commission Schedule table."
      distinguish_from: "Do NOT extract coinsurance percentages (e.g. 80/20 member cost share)."
  commission_notes:
    type: string
    required: false
    monetary: false
    synonyms: ["Compensation Footnotes"]
    description:
      what_it_is: "Any additional commission terms or grading notes, verbatim."
      where_to_read: "Footnotes beneath the Producer Compensation schedule."
      distinguish_from: "Do NOT include general underwriting disclaimers unrelated to commissions."

edges:
  - edge_id: edge-benefits-quote-feeds-premium
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_premium
    on_fields: ["premium"]
    metric_operation: SUM_ACTIVE_DECIMAL
  - edge_id: edge-benefits-quote-feeds-deductible
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_deductible
    on_fields: ["deductible"]
    metric_operation: SUM_ACTIVE_DECIMAL
  - edge_id: edge-benefits-quote-feeds-limit
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_coverage_limit
    on_fields: ["coverage_limits"]
    metric_operation: SUM_ACTIVE_DECIMAL
---
# Employee Benefits Carrier Quote (`benefits-quote`)

Voluntary and group employee benefits quote (`source_priority = 20`) with structured rate tables, benefit design provisions, and contrastive field descriptions.
