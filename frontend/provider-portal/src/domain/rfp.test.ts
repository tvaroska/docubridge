// Carrier checklist mapper tests (S0-frontend-1).
//
// Proves mapRfpChecklist maps the host's snake_case checklist snake→camel, relays the
// reason + field reviews verbatim, maps the upload action, and defaults absent optionals.

import { describe, expect, it } from "vitest";
import { mapRfpChecklist, type WireRfpChecklist } from "./rfp";

const WIRE: WireRfpChecklist = {
  kind: "rfp_checklist",
  rfp_id: "acme-vb-2027",
  party: "carrier-a",
  context_id: "leg-a",
  due_at: "2026-10-03T09:00:00Z",
  lifecycle: "on_track",
  items: [
    {
      product_line: "HOSPITAL_INDEMNITY",
      requirement: "required",
      status: "incomplete",
      outstanding: true,
      version_count: 1,
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
  ],
  reply_address: "rfp-quotes+tok@broker.example",
  reply_subject_tag: "[ref:tok]",
  actions: [{ kind: "upload", accept: ["benefits-quote"], prompt: null }],
};

describe("mapRfpChecklist", () => {
  it("maps snake→camel with reason + field review verbatim and the upload action", () => {
    const c = mapRfpChecklist(WIRE);
    expect([c.rfpId, c.party, c.contextId, c.dueAt]).toEqual([
      "acme-vb-2027",
      "carrier-a",
      "leg-a",
      "2026-10-03T09:00:00Z",
    ]);
    expect(c.replyAddress).toBe("rfp-quotes+tok@broker.example");
    expect(c.replySubjectTag).toBe("[ref:tok]");
    expect(c.actions).toEqual([{ kind: "upload", accept: ["benefits-quote"], prompt: null }]);
    expect(c.items[0]).toEqual({
      productLine: "HOSPITAL_INDEMNITY",
      requirement: "required",
      status: "incomplete",
      outstanding: true,
      versionCount: 1,
      lastActivity: "2026-10-02T09:00:00Z",
      reasonCode: "flagged_fields",
      message: "Please confirm the rate frequency.",
      fieldReviews: [
        {
          path: "fields.tables.rates[1].frequency",
          reason: "low_confidence",
          confidence: 0.61,
          detail: "printed as 'per pay period'",
        },
      ],
    });
  });

  it("defaults absent reply fields / actions / field reviews", () => {
    const { reply_address: _r, reply_subject_tag: _t, actions: _a, ...bare } = WIRE;
    const c = mapRfpChecklist({
      ...bare,
      items: [{ ...WIRE.items[0], field_reviews: undefined }],
    });
    expect(c.replyAddress).toBeNull();
    expect(c.replySubjectTag).toBeNull();
    expect(c.actions).toEqual([]);
    expect(c.items[0].fieldReviews).toEqual([]);
  });
});
