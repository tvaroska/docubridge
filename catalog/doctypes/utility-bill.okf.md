---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: utility-bill
context_set: kyc-address-verification
revision_id: ont-rev-v1-1-baseline
source_priority: 10
aliases: []
declares_issuer: true
issuer_description: "canonicalized issuer key"

entity_description:
  what_it_is: >
    Recurring residential or commercial utility statement (electric, gas,
    water, or municipal service) corroborating physical service address and
    account holder residency.
  where_to_read: >
    Identified by utility provider masthead, meter/usage summary, "Service
    Address" block, and billing statement date within the recency window.
  distinguish_from: >
    Do NOT confuse with a government-issued photo identity document (`gov-id`),
    and carefully separate the physical `service_address` from a P.O. Box or
    billing/mailing address.

fields:
  name:
    type: string
    required: true
    monetary: false
    synonyms: ["Account Holder Name", "Customer Name"]
    description:
      what_it_is: "Full name of the utility account holder as printed on the statement."
      where_to_read: "Account Summary or Customer Address block at the top of Page 1."
      distinguish_from: "Do NOT extract the utility provider's corporate name or remit-to payee."
  service_address:
    type: string
    required: true
    monetary: false
    synonyms: ["Premise Address", "Meter Location Address"]
    description:
      what_it_is: "Physical street address where utility service is metered and delivered."
      where_to_read: "Explicit 'Service Address' or 'Service Location' field above the meter reading table."
      distinguish_from: "Do NOT extract the 'Mailing Address', 'Billing Address', or utility remittance P.O. Box."
  statement_date:
    type: string
    required: true
    monetary: false
    synonyms: ["Bill Date", "Statement Issue Date"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) when the billing statement was issued."
      where_to_read: "Top-right Statement Summary box ('Statement Date' or 'Bill Date')."
      distinguish_from: "Do NOT extract the 'Payment Due Date' or historical 'Previous Bill Date'."

edges:
  - edge_id: edge-utility-corroborates-gov-id
    relation: REQUIRES_CORROBORATION
    target_id: gov-id
    on_fields: ["name", "service_address"]
    condition_expr: "source.name == target.name and source.service_address == target.address"
---
# Utility Statement (`utility-bill`)

Secondary address corroboration evidence (`source_priority = 10`) paired with `gov-id` for KYC residency verification.
