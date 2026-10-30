---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: trid-closing-disclosure
context_set: commercial-and-residential-lending
revision_id: ont-rev-v1-1-baseline
source_priority: 40
aliases:
  - closing-disclosure
  - addendum
  - endorsement
declares_issuer: true
issuer_description: "Settlement agent, lender, or issuing mortgage institution."

entity_description:
  what_it_is: >
    Final statutory TILA-RESPA Integrated Disclosure (TRID) Closing Disclosure
    or binding Schedule B closing fee addendum setting authoritative settlement
    terms, origination charges, and rate-lock extension fees at loan closing.
  where_to_read: >
    Identified by the "Closing Disclosure" banner on Page 1 ("This form is a
    statement of final loan terms and closing costs") or a signed "Schedule B
    Fee Addendum / Closing Cost Details" table on Pages 2-4.
  distinguish_from: >
    Do NOT confuse with an initial TRID Loan Estimate ("Loan Estimate" header),
    a non-binding CRE Term Sheet, or a Credit Agreement (`cre-credit-agreement`)
    whose preliminary fee estimates are superseded at closing.

fields:
  loan_amount:
    type: string
    required: true
    monetary: true
    feeds_metric: total_coverage_limit
    synonyms: ["Principal Loan Amount", "Final Loan Amount", "Total Loan Amount"]
    description:
      what_it_is: "Final binding principal loan amount at settlement in the disclosure's currency."
      where_to_read: "Page 1, Loan Terms table, first row ('Loan Amount')."
      distinguish_from: "Do NOT extract 'Cash to Close', 'Sale Price of Property', or 'Total of Payments' from Page 5."
  origination_fee:
    type: string
    required: true
    monetary: true
    feeds_metric: total_deductible
    synonyms: ["Origination Charges", "Section A Total", "Schedule B Origination & Rate-Lock Fee"]
    description:
      what_it_is: >
        Binding total origination and rate-lock extension fee charged by the
        lender at closing, including any Schedule B rate-lock extension override.
      where_to_read: >
        Page 2, Closing Cost Details, Section A ('A. Origination Charges' total)
        or the signed Schedule B Fee Addendum override row if attached.
      distinguish_from: >
        Do NOT extract the superseded base origination fee from an earlier Loan
        Estimate or Term Sheet (`cre-credit-agreement`), and do NOT extract
        Section B ('Services Borrower Did Not Shop For') or Total Closing Costs (J).
  annual_debt_service:
    type: string
    required: false
    monetary: true
    feeds_metric: total_premium
    synonyms: ["Annual P&I Payment", "Annualized Monthly Principal & Interest"]
    description:
      what_it_is: "Total annualized principal and interest debt service obligation."
      where_to_read: "Page 1, Projected Payments table or Debt Service Summary schedule."
      distinguish_from: "Do NOT extract a single month's escrow or HOA payment."
  interest_rate:
    type: string
    required: false
    monetary: false
    synonyms: ["Note Rate", "Fixed Interest Rate"]
    description:
      what_it_is: "Final locked annual note interest rate as printed (e.g. 6.125%)."
      where_to_read: "Page 1, Loan Terms table, 'Interest Rate' row."
      distinguish_from: "Do NOT extract APR (Annual Percentage Rate) from Page 5."
  currency:
    type: string
    required: true
    monetary: false
    synonyms: ["Currency Code", "Settlement Currency"]
    description:
      what_it_is: "ISO 4217 3-letter currency code for all monetary amounts in the disclosure."
      where_to_read: "Header currency symbol or explicit ISO currency notation (default USD for US TRID)."
      distinguish_from: "Do NOT return currency symbols like '$'—normalize to ISO 4217 (e.g. USD)."
  effective_date:
    type: string
    required: true
    monetary: false
    synonyms: ["Closing Date", "Disbursement Date", "Settlement Date"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) when the closing disclosure and loan terms take effect."
      where_to_read: "Page 1, Closing Information block, 'Closing Date' field."
      distinguish_from: "Do NOT extract 'Date Issued' or 'Application Date' when 'Closing Date' is printed."

edges:
  - edge_id: edge-trid-supersedes-credit-agreement
    relation: SUPERSEDES
    target_id: cre-credit-agreement
    priority_rank: 40
    on_fields: ["origination_fee", "loan_amount", "annual_debt_service", "interest_rate"]
    condition_expr: "source.effective_from >= target.effective_from"
  - edge_id: edge-trid-feeds-limit
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_coverage_limit
    on_fields: ["loan_amount"]
    metric_operation: SUM_ACTIVE_DECIMAL
  - edge_id: edge-trid-feeds-deductible
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_deductible
    on_fields: ["origination_fee"]
    metric_operation: SUM_ACTIVE_DECIMAL
  - edge_id: edge-trid-feeds-premium
    relation: FEEDS_METRIC
    target_id: FinancialSummary.total_premium
    on_fields: ["annual_debt_service"]
    metric_operation: SUM_ACTIVE_DECIMAL
---
# TRID Closing Disclosure & Schedule B Fee Addendum (`trid-closing-disclosure`)

Authoritative settlement disclosure (`source_priority = 40`) that supersedes preliminary loan estimates and credit agreements (`cre-credit-agreement`) on overlapping closing cost and principal terms.
