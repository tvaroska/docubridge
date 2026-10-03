---
name: benefits-rfp
description: Run a group-benefits RFP collection — invite N carriers across product lines, chase every carrier to a due date, and report which quotes are in and which are still waiting. Use when a broker's agent needs to know "which documents are ready, which are waiting on a carrier". An emergent Collect over the carrier distribution list x product lines; the agent owns the requirements and the completeness decision.
license: Proprietary. LICENSE.txt has complete terms
metadata:
  bridge-kind: process
  bridge-pattern: collect
  bridge-candidate-doctypes: "benefits-quote"
  bridge-tags: "benefits rfp collect carrier"
  bridge-policy: assets/policy.yaml
  bridge-satisfaction: >-
    Satisfied when every required (carrier, product line) item on the agent's requirements
    holds an accepted benefits quote, or the agent has waived it (a declined carrier stays
    outstanding until the agent waives it). The agent may add carriers or product lines at
    any turn. The completeness decision is the agent's (sense B); this description is
    advisory only.
  version: "1.0"
---

Emergent Collect (`bridge.rfp`). The agent posts the carrier distribution list x product lines as `CollectRequest.requirements` (each `Requirement.party` a carrier, `item` a product line) with a `due_at`. The Bridge fans out one isolated leg per carrier, classifies + dispositions each arriving quote (sense A), and files it against its (carrier, product line) item: **invited → acknowledged → received / incomplete / declined**, with **overdue** derived once the due date passes. The due date anchors the `assets/policy.yaml` SLA ladder (overdue at the due date, then reminder, then escalated); an item that keeps failing disposition escalates at the resubmit cap. A revised quote lands as a new version on the same item. Completeness is the **agent's** call, asserted per turn, never enforced here.
