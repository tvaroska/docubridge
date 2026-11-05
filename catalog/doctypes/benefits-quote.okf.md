---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: benefits-quote
context_set: employee-benefits-procurement
revision_id: ont-rev-v1-1-baseline
source_priority: 20
schema_version: "2.0"
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
    enum: [AD_AND_D, CRITICAL_ILLNESS, HOSPITAL_INDEMNITY, LIFE]
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
  plan_type:
    type: string
    required: false
    monetary: false
    synonyms: ["Health Plan Design", "Medical Tier Type"]
    description:
      what_it_is: "Plan design structure (e.g. HDHP, PPO, HMO, Group Life)."
      where_to_read: "Schedule of Benefits header ('Plan: HDHP')."
      distinguish_from: "When Plan == HDHP, linked tax-advantaged Employer Account Seed must be classified as HSA (Health Savings Account), never standard FSA."
  employer_account_seed:
    type: string
    required: false
    monetary: true
    feeds_metric: total_deductible
    synonyms: ["Employer HSA Seed", "Employer Account Contribution", "Employer Account Seed"]
    description:
      what_it_is: "Annual employer contribution to the employee tax-advantaged benefit account (e.g. $1,000.00)."
      where_to_read: "Page 4 Schedule of Benefits ('Plan: HDHP · Employer Account Seed: $1,000.00')."
      distinguish_from: "When the quoted plan is an HDHP (High Deductible Health Plan), Employer Account Seed is strictly an HSA (Part Of HDHP) and CANNOT be a standard Healthcare FSA (NOT Part Of HDHP)."
  waiting_period_days:
    type: string
    required: false
    monetary: false
    synonyms: ["Elimination Period", "Qualifying Period", "Benefit Waiting Period"]
    description:
      what_it_is: "Number of days a covered employee waits after a disability or benefit claim before benefits begin (e.g. 90 days)."
      where_to_read: "Page 14 Schedule of Benefits or Policy Riders ('Elimination Period / Waiting Period: 90 Days')."
      distinguish_from: "Do NOT confuse the 90-day Benefit Waiting Period on Page 14 with the 30-day New-Hire Enrollment Waiting Period on Page 2."

tables:
  rates:
    description: "one row per premium rate printed in the quote"
    required: [coverage_tier, amount, frequency]
    columns:
      coverage_tier:
        type: string
        enum: [EE, EE_SPOUSE, EE_CHILDREN, FAMILY]
        description: "who the rate covers: employee, employee + spouse, employee + children, family"
      tobacco:
        type: string
        enum: [UNI, NON_TOBACCO, TOBACCO]
        description: "tobacco rating class (UNI = unismoker / not distinguished)"
      age_band:
        type: string
        description: "age band as printed (e.g. 30-34)"
      amount:
        type: string
        description: "the rate exactly as printed, in the quote's native currency; never computed"
      frequency:
        type: string
        enum: [MONTHLY, BI_WEEKLY, WEEKLY, SEMI_MONTHLY, ANNUAL]
        description: "billing frequency the rate amount is expressed in, per row (carriers mix them)"
      rate_basis:
        type: string
        enum: [FLAT, PER_1000, PER_UNIT]
        description: "FLAT premium, per $1,000 of benefit, or per unit of benefit"
      benefit_amount:
        type: string
        description: "benefit amount the rate is priced at, as printed"
  design:
    description: "one row per benefit-design provision printed in the quote"
    required: [category, value]
    columns:
      category:
        type: string
        description: "the benefit provision, in standard terms where obvious (e.g. Heart Attack)"
      category_original:
        type: string
        description: "the carrier's own term for the provision, verbatim"
      value:
        type: string
        description: "the provision's value as printed (e.g. $10,000, 6 months, Yes)"

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
