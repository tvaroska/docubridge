---
okf_version: "0.2"
entry_type: semantic-entity
entity_id: gov-id
context_set: kyc-address-verification
revision_id: ont-rev-v1-1-baseline
source_priority: 10
aliases: []
declares_issuer: false
issuer_description: "State DMV, passport agency, or national civil registry."

entity_description:
  what_it_is: >
    Government-issued photo identity card (driver's license, state ID, or
    national identity card) establishing legal holder name, residential address,
    document number, issuing authority, and expiration date.
  where_to_read: >
    Identified by state/national seal, portrait photograph, AAMVA numbered
    fields (1, 2, 4b, 4d, 8), and machine-readable zone.
  distinguish_from: >
    Do NOT confuse with a utility billing statement (`utility-bill`) or
    employer badge lacking government statutory authority.

fields:
  name:
    type: string
    required: true
    monetary: false
    synonyms: ["Full Legal Name", "Holder Name"]
    description:
      what_it_is: "Full legal name of the credential holder normalized as First Last."
      where_to_read: "AAMVA fields 1 (family name) and 2 (given names) next to the portrait."
      distinguish_from: "Do NOT extract the state governor or DMV commissioner signature name."
  address:
    type: string
    required: true
    monetary: false
    synonyms: ["Residential Address", "Street Address"]
    description:
      what_it_is: "Holder's primary residential street address printed on the credential face."
      where_to_read: "AAMVA field 8 directly below the holder's name."
      distinguish_from: "Do NOT extract the issuing agency's capitol city header."
  doc_number:
    type: string
    required: true
    monetary: false
    synonyms: ["DLN", "License Number", "ID Number"]
    description:
      what_it_is: "Unique alphanumeric credential number assigned by the issuing authority."
      where_to_read: "AAMVA field 4d ('DL', 'ID', or 'LIC#')."
      distinguish_from: "Do NOT extract the document discriminator (DD / field 5) or donor/class codes."
  issuing_authority:
    type: string
    required: true
    monetary: false
    synonyms: ["Issuing State", "Issuing Jurisdiction"]
    description:
      what_it_is: "Government jurisdiction or agency that issued the credential."
      where_to_read: "Top banner header and state/national seal."
      distinguish_from: "Do NOT extract the holder's city of residence as the issuing authority."
  expiry:
    type: string
    required: true
    monetary: false
    synonyms: ["Expiration Date", "EXP"]
    description:
      what_it_is: "ISO date (YYYY-MM-DD) when the identity document expires."
      where_to_read: "AAMVA field 4b ('EXP')."
      distinguish_from: "Do NOT extract Date of Birth ('DOB' / field 3) or Issue Date ('ISS' / field 4a)."

edges:
  - edge_id: edge-gov-id-corroborates-utility
    relation: REQUIRES_CORROBORATION
    target_id: utility-bill
    on_fields: ["name", "address"]
    condition_expr: "source.name == target.name and source.address == target.service_address"
---
# Government Identity Credential (`gov-id`)

Primary identity credential (`source_priority = 10`) paired with `utility-bill` for KYC address verification.
