---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: commercial-property-quote
context_set: commercial-property-placement
revision_id: ont-rev-v1-1-baseline
source_priority: 20
aliases:
  - quote
  - carrier-quote
declares_issuer: true
issuer_description: "Underwriting carrier, Lloyd's syndicate, or managing general agent (MGA)."

entity_description:
  what_it_is: >
    Commercial Property Insurance Quote, Carrier Binder, Schedule of Values
    (SOV), or Named Windstorm / Schedule B Deductible Endorsement specifying
    layer limits, total insured value, annual premium, and peril deductibles.
  where_to_read: >
    Identified by "Commercial Property Quote", "Slip / Binder", "Schedule of
    Values (SOV)", or "Schedule B Named Windstorm Deductible Endorsement"
    headers on Pages 1-4 and 11.
  distinguish_from: >
    Do NOT confuse with voluntary employee group benefits rate sheets
    (`benefits-quote`) or real estate loan credit agreements
    (`cre-credit-agreement`).

fields:
  coverage_limit:
    type: string
    required: true
    monetary: true
    feeds_metric: total_coverage_limit
    synonyms: ["Primary Layer Property Coverage Limit", "Policy Limit of Liability", "Layer Limit"]
    description:
      what_it_is: "Maximum primary or layer property coverage limit of liability."
      where_to_read: "Page 1-2 Declarations / Coverage Schedule ('Primary Layer Property Coverage Limit')."
      distinguish_from: "Do NOT extract Total Insured Value (TIV) from the SOV or sublimits for debris removal."
  total_insured_value:
    type: string
    required: false
    monetary: true
    feeds_metric: total_insured_value
    synonyms: ["Schedule of Values (SOV) TIV", "100% Statement of Values"]
    description:
      what_it_is: "100% Total Insured Value (TIV) across scheduled locations in the Statement/Schedule of Values."
      where_to_read: "Schedule of Values (SOV) summary row (typically Page 11 or attached SOV schedule)."
      distinguish_from: "Do NOT extract the carrier's Layer Coverage Limit (`coverage_limit`), which is often capped below TIV."
  deductible:
    type: string
    required: true
    monetary: true
    feeds_metric: total_deductible
    synonyms: ["All Other Perils Base Deductible", "Schedule B Named Windstorm Deductible Override", "SIR"]
    description:
      what_it_is: >
        Applicable property peril deductible or self-insured retention,
        preferring the Schedule B Named Windstorm / Coastal Override when
        present for coastal placements.
      where_to_read: >
        Page 1 Deductible block ('All Other Perils Base Deductible') AND Page 4
        Schedule B Endorsement ('Named Windstorm Deductible Override'); when a
        Schedule B coastal/wind endorsement is attached, extract the Schedule B
        override amount.
      distinguish_from: >
        Do NOT extract waiting-period time deductibles (e.g. '72 hours') or
        superseded All-Other-Perils base deductibles when a Schedule B peril
        endorsement overrides them.
  premium:
    type: string
    required: true
    monetary: true
    feeds_metric: total_premium
    synonyms: ["Total Annual Premium", "Layer Gross Written Premium"]
    description:
      what_it_is: "Total annual policy or layer premium in the quote's currency."
      where_to_read: "Page 1-2 Premium Summary block ('Total Annual Premium')."
      distinguish_from: "Do NOT extract surplus lines stamping fees, terrorism (TRIA) optional add-ons, or broker commission."
  currency:
    type: string
    required: true
    monetary: false
    synonyms: ["Policy Currency", "ISO Currency Code"]
    description:
      what_it_is: "ISO 4217 3-letter currency code (e.g. USD, GBP, EUR)."
      where_to_read: "Declarations header or monetary column header."
      distinguish_from: "Normalize '$' to 'USD' and '£' to 'GBP'."
  effective_date:
    type: string
    required: true
    monetary: false
    synonyms: ["Policy Effective Date", "Inception Date"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) when policy coverage or endorsement takes effect."
      where_to_read: "Page 1 Policy Period block ('Effective Date' / 'Inception')."
      distinguish_from: "Do NOT extract the Quote Issue Date or Policy Expiration Date."

edges:
  - edge_id: edge-prop-quote-feeds-limit
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_coverage_limit
    on_fields: ["coverage_limit"]
    metric_operation: SUM_ACTIVE_DECIMAL
  - edge_id: edge-prop-quote-feeds-tiv
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_insured_value
    on_fields: ["total_insured_value"]
    metric_operation: SUM_ACTIVE_DECIMAL
  - edge_id: edge-prop-quote-feeds-deductible
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_deductible
    on_fields: ["deductible"]
    metric_operation: SUM_ACTIVE_DECIMAL
  - edge_id: edge-prop-quote-feeds-premium
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_premium
    on_fields: ["premium"]
    metric_operation: SUM_ACTIVE_DECIMAL
---
# Commercial Property Quote & SOV (`commercial-property-quote`)

Commercial property placement quote (`source_priority = 20`; endorsements `source_priority = 40`) mapping coverage limit, TIV, deductible, and annual premium into Zone 3 deterministic consolidation.
