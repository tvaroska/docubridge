---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: mai-appraisal
context_set: commercial-and-residential-lending
revision_id: ont-rev-v1-1-baseline
source_priority: 20
aliases:
  - appraisal
  - sov
  - term-sheet
  - loan-estimate
declares_issuer: true
issuer_description: "Independent MAI appraisal firm or certified real estate appraiser."

entity_description:
  what_it_is: >
    Independent MAI Commercial Appraisal or Uniform Residential Appraisal
    Report (Form 1004) certifying the subject property's market collateral
    value and stabilized Net Operating Income (NOI).
  where_to_read: >
    Identified by "Appraisal Report", "MAI Certification", or "Uniform
    Residential Appraisal Report (Form 1004)" cover page and Executive
    Valuation Summary table.
  distinguish_from: >
    Do NOT confuse with a Credit Agreement (`cre-credit-agreement`) or Closing
    Disclosure (`trid-closing-disclosure`); an appraisal establishes collateral
    valuation (`appraised_value`) but never sets binding loan terms or fees.

fields:
  appraised_value:
    type: string
    required: true
    monetary: true
    feeds_metric: total_insured_value
    synonyms: ["As-Is Market Value", "As-Stabilized Appraised Collateral Value", "Market Value Conclusion"]
    description:
      what_it_is: "Certified market appraised value of the real estate collateral."
      where_to_read: "Executive Summary / Reconciliation & Final Value Conclusion table."
      distinguish_from: "Do NOT extract insurable replacement cost, land-only value, or tax assessment value."
  property_address:
    type: string
    required: true
    monetary: false
    synonyms: ["Subject Property Address", "Collateral Location"]
    description:
      what_it_is: "Full street address, city, state, and postal code of the appraised subject property."
      where_to_read: "Cover page and Property Identification block."
      distinguish_from: "Do NOT extract the appraisal firm's office address or comparable sale addresses."
  net_operating_income:
    type: string
    required: false
    monetary: true
    synonyms: ["Stabilized Annual NOI", "Income Capitalization NOI"]
    description:
      what_it_is: "Appraiser's projected stabilized annual Net Operating Income (NOI)."
      where_to_read: "Income Capitalization Approach / Direct Capitalization Pro Forma table."
      distinguish_from: "Do NOT extract Potential Gross Income (PGI) or post-debt-service cash flow."
  currency:
    type: string
    required: true
    monetary: false
    synonyms: ["Valuation Currency"]
    description:
      what_it_is: "ISO 4217 3-letter currency code for the valuation (e.g. USD)."
      where_to_read: "Executive Summary table header or monetary notation."
      distinguish_from: "Normalize '$' to 'USD'."
  valuation_date:
    type: string
    required: true
    monetary: false
    synonyms: ["Effective Date of Value", "Inspection & Valuation Date"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) as of which the appraised market value applies."
      where_to_read: "Letter of Transmittal and Executive Summary ('Effective Date of Appraisal')."
      distinguish_from: "Do NOT extract the report signature/transmittal date if distinct from the effective valuation date."

edges:
  - edge_id: edge-appraisal-feeds-tiv
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_insured_value
    on_fields: ["appraised_value"]
    metric_operation: SUM_ACTIVE_DECIMAL
---
# MAI Commercial & Form 1004 Appraisal (`mai-appraisal`)

Independent collateral valuation (`source_priority = 20`) supplying `appraised_value` into the `total_insured_value` (collateral value) metric bucket.
