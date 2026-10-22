// RFP status mapper tests (@docubridge/shared, S0-frontend-1).
//
// Proves mapRfpStatus maps both wire forms — the consumer API (as_of, api_version, reply
// fields) and the dashboard SSE row (none of those) — snake→camel, relays reasons and field
// reviews verbatim, and that isChaseable mirrors bridge.rfp.chaseable_items.

import { describe, expect, it } from "vitest";
import { mapFieldReview } from "./contract";
import { isChaseable, mapRfpStatus, type WireRfpStatus } from "./rfp";

const WIRE: WireRfpStatus = {
  rfp_id: "acme-vb-2027",
  process: "rfp-quote-collection",
  context_id: "ctx-broker",
  due_at: "2026-10-03T09:00:00Z",
  as_of: "2026-10-02T09:00:00Z",
  api_version: "1",
  lifecycle: "on_track",
  ready: 1,
  waiting: 2,
  counts: { received: 1, incomplete: 1, invited: 1 },
  carriers: [
    {
      party: "carrier-a",
      context_id: "leg-a",
      lifecycle: "on_track",
      ready: 1,
      waiting: 1,
      chases: 2,
      last_chased_at: "2026-10-02T08:00:00Z",
      reply_address: "rfp-quotes+tok@broker.example",
      reply_subject_tag: "[ref:tok]",
    },
    { party: "carrier-c", context_id: "leg-c", lifecycle: "on_track", ready: 0, waiting: 1 },
  ],
  cells: [
    {
      party: "carrier-a",
      product_line: "CRITICAL_ILLNESS",
      requirement: "required",
      status: "received",
      versions: ["q1"],
      attempts: 1,
      escalated: false,
      last_activity: "2026-10-02T09:00:00Z",
      reason_code: "auto_approved",
      message: null,
      field_reviews: [],
    },
    {
      party: "carrier-a",
      product_line: "HOSPITAL_INDEMNITY",
      requirement: "required",
      status: "incomplete",
      versions: ["q2"],
      attempts: 1,
      escalated: false,
      last_activity: "2026-10-02T09:00:00Z",
      reason_code: "flagged_fields",
      message: "Please confirm the rate frequency.",
      field_reviews: [
        {
          path: "fields.tables.rates[1].frequency",
          reason: "low_confidence",
          confidence: 0.61,
          detail: "printed as 'per pay period'",
        },
      ],
    },
    {
      party: "carrier-c",
      product_line: "CRITICAL_ILLNESS",
      requirement: "required",
      status: "invited",
      versions: [],
      attempts: 0,
      escalated: false,
      last_activity: null,
      reason_code: null,
      message: null,
    },
  ],
  unmatched: [],
};

describe("mapRfpStatus", () => {
  it("maps the consumer API form snake→camel with reasons + field reviews verbatim", () => {
    const rfp = mapRfpStatus(WIRE);
    expect(rfp.rfpId).toBe("acme-vb-2027");
    expect(rfp.asOf).toBe("2026-10-02T09:00:00Z");
    expect(rfp.apiVersion).toBe("1");
    expect(rfp.carriers[0]).toEqual({
      party: "carrier-a",
      contextId: "leg-a",
      lifecycle: "on_track",
      ready: 1,
      waiting: 1,
      chases: 2,
      lastChasedAt: "2026-10-02T08:00:00Z",
      replyAddress: "rfp-quotes+tok@broker.example",
      replySubjectTag: "[ref:tok]",
    });
    const hi = rfp.cells[1];
    expect(hi.productLine).toBe("HOSPITAL_INDEMNITY");
    expect(hi.reasonCode).toBe("flagged_fields");
    expect(hi.message).toBe("Please confirm the rate frequency.");
    expect(hi.fieldReviews).toEqual([
      {
        path: "fields.tables.rates[1].frequency",
        reason: "low_confidence",
        confidence: 0.61,
        detail: "printed as 'per pay period'",
      },
    ]);
  });

  it("tolerates the SSE row form (no as_of / api_version / reply / chase fields)", () => {
    const { as_of: _a, api_version: _v, ...sse } = WIRE;
    const rfp = mapRfpStatus(sse);
    expect(rfp.asOf).toBeNull();
    expect(rfp.apiVersion).toBeNull();
    const c = rfp.carriers[1];
    expect([c.chases, c.lastChasedAt, c.replyAddress, c.replySubjectTag]).toEqual([
      0,
      null,
      null,
      null,
    ]);
    expect(rfp.cells[2].fieldReviews).toEqual([]);
  });

  it("isChaseable: outstanding tracked non-declined items only", () => {
    const rfp = mapRfpStatus(WIRE);
    expect(isChaseable(rfp, "carrier-a")).toBe(true); // HI incomplete
    expect(isChaseable(rfp, "carrier-c")).toBe(true);
    const allIn = mapRfpStatus({
      ...WIRE,
      cells: WIRE.cells.map((c) =>
        c.party === "carrier-a" ? { ...c, status: "received" as const } : c,
      ),
    });
    expect(isChaseable(allIn, "carrier-a")).toBe(false);
    const declined = mapRfpStatus({
      ...WIRE,
      cells: WIRE.cells.map((c) =>
        c.party === "carrier-c" ? { ...c, status: "declined" as const } : c,
      ),
    });
    expect(isChaseable(declined, "carrier-c")).toBe(false);
    const waived = mapRfpStatus({
      ...WIRE,
      cells: WIRE.cells.map((c) =>
        c.party === "carrier-c" ? { ...c, requirement: "waived" as const } : c,
      ),
    });
    expect(isChaseable(waived, "carrier-c")).toBe(false);
  });
});

describe("mapFieldReview", () => {
  it("defaults confidence/detail to null and keeps path verbatim", () => {
    expect(mapFieldReview({ path: "fields.x", reason: "missing" })).toEqual({
      path: "fields.x",
      reason: "missing",
      confidence: null,
      detail: null,
    });
  });
});
