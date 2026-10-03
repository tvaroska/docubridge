// Domain mapper tests for the Ops Dashboard (P6-4).
//
// Proves snake->camel round-trips a sample snapshot JSON (with HITL, escalated, and done
// exchanges); `reasonCode`/`message` are relayed verbatim (content-not-pixels).

import { describe, expect, it } from "vitest";
import { mapExchange, mapSnapshot, type WireExchange, type WireSnapshot } from "./ops";

describe("Ops Dashboard domain mappers", () => {
  it("round-trips a snapshot with HITL, escalated, and done exchanges", () => {
    const wire: WireSnapshot = {
      exchanges: [
        {
          session_id: "sid-1",
          context_id: "ctx-1",
          process: "address-proof",
          status: "done",
          lifecycle: "on_track",
          entries: [
            {
              doc_id: "d1",
              doctype: "utility-bill",
              issuer: "power-co",
              disposition: "accepted",
              reason_code: "auto_approved",
              message: "The document met all requirements.",
            },
          ],
          hitl: false,
          escalated: false,
        },
        {
          session_id: "sid-2",
          context_id: "ctx-2",
          process: "address-proof",
          status: "in_flight",
          lifecycle: "on_track",
          entries: [
            {
              doc_id: "d2",
              doctype: "utility-bill",
              issuer: "aqua-util",
              disposition: "pending",
              reason_code: "awaiting_human_review",
              message: "This submission was sent for human review.",
            },
          ],
          hitl: true,
          escalated: false,
        },
        {
          session_id: "sid-3",
          context_id: "ctx-3",
          process: "address-proof",
          status: "done",
          lifecycle: "escalated",
          entries: [
            {
              doc_id: "d3",
              doctype: "utility-bill",
              issuer: "aqua-util",
              disposition: "pending",
              reason_code: "escalated_for_review",
              message: "This document could not be read after repeated attempts and was sent for human review.",
            },
          ],
          hitl: false,
          escalated: true,
        },
      ],
      hitl: [
        {
          session_id: "sid-2",
          context_id: "ctx-2",
          process: "address-proof",
          status: "in_flight",
          lifecycle: "on_track",
          entries: [
            {
              doc_id: "d2",
              doctype: "utility-bill",
              issuer: "aqua-util",
              disposition: "pending",
              reason_code: "awaiting_human_review",
              message: "This submission was sent for human review.",
            },
          ],
          hitl: true,
          escalated: false,
        },
      ],
      escalation: [
        {
          session_id: "sid-3",
          context_id: "ctx-3",
          process: "address-proof",
          status: "done",
          lifecycle: "escalated",
          entries: [
            {
              doc_id: "d3",
              doctype: "utility-bill",
              issuer: "aqua-util",
              disposition: "pending",
              reason_code: "escalated_for_review",
              message: "This document could not be read after repeated attempts and was sent for human review.",
            },
          ],
          hitl: false,
          escalated: true,
        },
      ],
    };

    const mapped = mapSnapshot(wire);

    // Exchanges are mapped (snake->camel).
    expect(mapped.exchanges).toHaveLength(3);
    expect(mapped.exchanges[0].sessionId).toBe("sid-1");
    expect(mapped.exchanges[0].contextId).toBe("ctx-1");
    expect(mapped.exchanges[0].status).toBe("done");

    // Entries are mapped; reasonCode/message are verbatim (content-not-pixels).
    const doneEntry = mapped.exchanges[0].entries[0];
    expect(doneEntry.docId).toBe("d1");
    expect(doneEntry.doctype).toBe("utility-bill");
    expect(doneEntry.issuer).toBe("power-co");
    expect(doneEntry.disposition).toBe("accepted");
    expect(doneEntry.reasonCode).toBe("auto_approved");
    expect(doneEntry.message).toBe("The document met all requirements.");

    // HITL queue is mapped.
    expect(mapped.hitl).toHaveLength(1);
    expect(mapped.hitl[0].sessionId).toBe("sid-2");
    expect(mapped.hitl[0].hitl).toBe(true);
    const hitlEntry = mapped.hitl[0].entries[0];
    expect(hitlEntry.disposition).toBe("pending");
    expect(hitlEntry.reasonCode).toBe("awaiting_human_review");
    expect(hitlEntry.message).toBe("This submission was sent for human review.");

    // Escalation queue is mapped.
    expect(mapped.escalation).toHaveLength(1);
    expect(mapped.escalation[0].sessionId).toBe("sid-3");
    expect(mapped.escalation[0].escalated).toBe(true);
    const escEntry = mapped.escalation[0].entries[0];
    expect(escEntry.disposition).toBe("pending");
    expect(escEntry.reasonCode).toBe("escalated_for_review");
    expect(escEntry.message).toBe("This document could not be read after repeated attempts and was sent for human review.");
  });

  it("maps an RFP row's matrix (S0-frontend-1); a session row has rfp null", () => {
    const row: WireExchange = {
      session_id: "acme-vb-2027",
      context_id: "ctx-broker",
      process: "rfp-quote-collection",
      status: "in_flight",
      lifecycle: "overdue",
      entries: [],
      hitl: false,
      escalated: false,
      rfp: {
        rfp_id: "acme-vb-2027",
        process: "rfp-quote-collection",
        context_id: "ctx-broker",
        due_at: "2026-10-03T09:00:00Z",
        lifecycle: "overdue",
        ready: 0,
        waiting: 1,
        counts: { overdue: 1 },
        carriers: [
          { party: "carrier-c", context_id: "leg-c", lifecycle: "overdue", ready: 0, waiting: 1, chases: 1 },
        ],
        cells: [
          {
            party: "carrier-c",
            product_line: "CRITICAL_ILLNESS",
            requirement: "required",
            status: "overdue",
            versions: [],
            attempts: 0,
            escalated: false,
            last_activity: null,
            reason_code: null,
            message: null,
            field_reviews: [],
          },
        ],
        unmatched: [],
      },
    };
    const mapped = mapExchange(row);
    expect(mapped.rfp?.carriers[0].chases).toBe(1);
    expect(mapped.rfp?.cells[0].productLine).toBe("CRITICAL_ILLNESS");
    expect(mapped.rfp?.asOf).toBeNull(); // the SSE row excludes as_of
    const { rfp: _rfp, ...session } = row;
    expect(mapExchange(session).rfp).toBeNull();
  });
});
