---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: loan-payoff-statement
context_set: commercial-and-residential-lending
revision_id: ont-rev-v1-1-baseline
source_priority: 35
aliases:
  - payoff-statement
  - payoff-demand
declares_issuer: true
issuer_description: "Existing commercial mortgage, equipment finance, or working-capital lender issuing an official payoff demand or structured A2A payoff assertion."

entity_description:
  what_it_is: >
    Official lender payoff demand statement or cryptographically signed A2A
    payoff assertion establishing exact principal, accrued interest, per-diem
    accrual, good-through date, and closing wire routing instructions for an
    existing facility scheduled for retirement.
  where_to_read: >
    Identified by "Official Payoff Statement", "Payoff Demand", or structured
    A2A `loan-payoff-statement` assertions carrying a loan account reference,
    total payoff amount, and good-through date.
  distinguish_from: >
    Do NOT confuse with a recurring monthly installment invoice, a preliminary
    credit agreement (`cre-credit-agreement`), or a final borrower closing
    statement (`trid-closing-disclosure`).

fields:
  loan_account:
    type: string
    required: true
    monetary: false
    synonyms: ["Loan Account Reference", "Facility Number", "Account Ref"]
    description:
      what_it_is: "Lender-assigned loan or facility account identifier being retired."
      where_to_read: "Header or Loan Account Reference row (e.g. #FNB-4402, #7712, #8841)."
      distinguish_from: "Do NOT extract the ABA wire routing number or borrower tax ID."
  payoff_amount:
    type: string
    required: true
    monetary: true
    feeds_metric: total_coverage_limit
    synonyms: ["Total Official Payoff Amount", "Total Payoff Demand", "Payoff Balance"]
    description:
      what_it_is: "Total principal, accrued interest, and release fees required to retire the facility in full."
      where_to_read: "Total Official Payoff Amount row or structured payoff assertion."
      distinguish_from: "Do NOT extract the original loan commitment or a recurring monthly installment."
  good_through_date:
    type: string
    required: true
    monetary: false
    synonyms: ["Payoff Quote Good Through", "Expiration Date", "Valid Through"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) through which the stated payoff amount remains valid before additional per-diem interest accrues."
      where_to_read: "Payoff Quote Good Through clause or footer."
      distinguish_from: "Do NOT extract the original loan origination date."
  currency:
    type: string
    required: true
    monetary: false
    synonyms: ["Currency", "ISO Currency"]
    description:
      what_it_is: "ISO 4217 3-letter currency code (e.g. USD)."
      where_to_read: "Payoff summary table or monetary unit indicator."
      distinguish_from: "Normalize '$' to 'USD'."
  borrower_name:
    type: string
    required: false
    monetary: false
    synonyms: ["Borrower", "Obligor", "Account Holder"]
    description:
      what_it_is: "Legal name of the borrowing entity obligated on the facility."
      where_to_read: "Borrower header block."
      distinguish_from: "Do NOT extract the issuing lender or administrative agent name."
  per_diem_interest:
    type: string
    required: false
    monetary: true
    synonyms: ["Daily Per-Diem Accrual", "Per Diem Interest"]
    description:
      what_it_is: "Daily interest accrual amount charged after the good-through date."
      where_to_read: "Per-diem interest line item."
      distinguish_from: "Do NOT extract the annual interest rate percentage."
  wire_routing_aba:
    type: string
    required: false
    monetary: false
    synonyms: ["Fedwire ABA", "ABA Routing Number", "Wire Routing"]
    description:
      what_it_is: "9-digit ABA routing number for closing Fedwire settlement."
      where_to_read: "Wire Instructions / Fedwire ABA block."
      distinguish_from: "Do NOT extract the loan account number."
  wire_reference:
    type: string
    required: false
    monetary: false
    synonyms: ["OBI Memo", "Wire Reference", "Payoff Reference"]
    description:
      what_it_is: "Beneficiary wire reference or OBI memo confirming final payoff settlement."
      where_to_read: "Wire instructions or bank statement outgoing wire OBI/memo line."
      distinguish_from: "Do NOT extract recurring monthly payment memos."
  payoff_waiver_status:
    type: string
    required: false
    monetary: false
    synonyms: ["Payoff Waiver Status", "Settlement Status"]
    description:
      what_it_is: "Underwriting payoff waiver status when reconciled against verified wire proof."
      where_to_read: "Derived or distilled cross-document settlement status."
      distinguish_from: "Leave unset unless verified payoff wire proof is present."

edges:
  - edge_id: edge-payoff-feeds-limit
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_coverage_limit
    on_fields: ["payoff_amount"]
    metric_operation: SUM_ACTIVE_DECIMAL
---
# Commercial Loan Payoff Statement (`loan-payoff-statement`)

Official lender payoff demand or structured `Path-A` A2A payoff assertion (`source_priority = 35`) establishing the exact retirement balance, per-diem accrual, good-through date, and Fedwire settlement routing for debt consolidation closings.
