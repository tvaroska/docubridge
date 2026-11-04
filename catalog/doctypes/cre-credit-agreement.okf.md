---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: cre-credit-agreement
context_set: commercial-and-residential-lending
revision_id: ont-rev-v1-1-baseline
source_priority: 30
aliases:
  - credit-agreement
  - loan-agreement
  - policy
  - binder
declares_issuer: true
issuer_description: "Administrative agent, lead arranger, syndicated lender group, or issuing bank."

entity_description:
  what_it_is: >
    Commercial Real Estate (CRE) Credit Agreement, Syndicated Term Sheet, or
    Initial Loan Estimate establishing committed facility limits, base
    origination fees, and underwritten Net Operating Income (NOI) covenants.
  where_to_read: >
    Identified by "Credit Agreement", "Senior Term Loan Facility Term Sheet",
    or "Loan Estimate" headers and the Summary of Principal Terms schedule on
    Pages 1-3.
  distinguish_from: >
    Do NOT confuse with a final TRID Closing Disclosure or signed Schedule B
    Fee Addendum (`trid-closing-disclosure`), which carries higher settlement
    authority (`priority 40`), or with a standalone property valuation report
    (`mai-appraisal`).

fields:
  loan_amount:
    type: string
    required: true
    monetary: true
    feeds_metric: total_coverage_limit
    synonyms: ["Senior Term Loan Facility", "Committed Principal Amount", "Loan Amount"]
    description:
      what_it_is: "Committed principal loan facility amount in the agreement's currency."
      where_to_read: "Summary of Terms / Article II Commitments ('Senior Term Loan Facility' or 'Loan Amount')."
      distinguish_from: "Do NOT extract uncommitted accordion expansion options or appraised collateral value."
  origination_fee:
    type: string
    required: true
    monetary: true
    feeds_metric: total_deductible
    synonyms: ["Base Origination Fee", "Upfront Arrangement Fee", "Section A Origination Charges"]
    description:
      what_it_is: "Base upfront origination or arrangement fee stated in the initial credit agreement or term sheet."
      where_to_read: "Fee Letter summary or Section A Origination Charges row."
      distinguish_from: "Do NOT extract late payment penalties, exit fees, or annual administrative agent fees."
  net_operating_income:
    type: string
    required: false
    monetary: true
    synonyms: ["Underwritten NOI", "Stabilized Net Operating Income"]
    description:
      what_it_is: "Underwritten annual Net Operating Income (NOI) used for debt-service coverage covenants."
      where_to_read: "Financial Covenants schedule or Underwriting Summary table."
      distinguish_from: "Do NOT extract Gross Potential Rent (GPR) or Effective Gross Income (EGI) before operating expenses."
  facility_limit:
    type: string
    required: false
    monetary: true
    feeds_metric: total_coverage_limit
    synonyms: ["Maximum Facility Commitment", "Total Credit Commitment"]
    description:
      what_it_is: "Maximum aggregate credit facility commitment limit when distinct from initial draw."
      where_to_read: "Definitions / Schedule 2.01 Commitments table."
      distinguish_from: "Do NOT extract swingline sublimits or letter-of-credit sublimits."
  currency:
    type: string
    required: true
    monetary: false
    synonyms: ["Facility Currency", "ISO Currency"]
    description:
      what_it_is: "ISO 4217 3-letter currency code (e.g. USD)."
      where_to_read: "Definitions section ('Dollars' or 'Alternative Currency') or header monetary notation."
      distinguish_from: "Normalize currency symbols ('$') to ISO 4217 code ('USD')."
  effective_date:
    type: string
    required: true
    monetary: false
    synonyms: ["Effective Date", "Agreement Date", "Closing Date"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) when the credit agreement or term sheet becomes effective."
      where_to_read: "Preamble or Summary of Terms header block."
      distinguish_from: "Do NOT extract the Maturity Date or Stated Termination Date."

edges:
  - edge_id: edge-credit-agreement-supersedes-appraisal
    relation: SUPERSEDES
    target_id: mai-appraisal
    priority_rank: 30
    on_fields: ["net_operating_income"]
    condition_expr: "source.effective_from >= target.effective_from"
  - edge_id: edge-credit-agreement-feeds-limit
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_coverage_limit
    on_fields: ["loan_amount", "facility_limit"]
    metric_operation: SUM_ACTIVE_DECIMAL
  - edge_id: edge-credit-agreement-feeds-deductible
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_deductible
    on_fields: ["origination_fee"]
    metric_operation: SUM_ACTIVE_DECIMAL
---
# CRE Credit Agreement & Term Sheet (`cre-credit-agreement`)

Governing credit facility agreement (`source_priority = 30`) that supersedes preliminary appraisal NOI estimates (`mai-appraisal`) while yielding to final settlement disclosures (`trid-closing-disclosure`).
