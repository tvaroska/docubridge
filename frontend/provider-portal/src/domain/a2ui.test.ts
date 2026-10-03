import { describe, expect, it } from "vitest";
import { mapView, type WireView } from "./a2ui";

// A sample of exactly what the host's encode_a2ui_view emits (snake_case).
const SAMPLE: WireView = {
  kind: "party_status",
  context_id: "ctx-1",
  accepted: [
    {
      doc_id: "d1",
      doctype: "gov-id",
      disposition: "accepted",
      reason_code: "auto_approved",
      message: "The document was automatically approved.",
    },
  ],
  rejected: [
    {
      doc_id: "d2",
      doctype: "utility-bill",
      disposition: "rejected",
      reason_code: "illegible",
      message: "The document was unreadable — please resubmit a clearer copy.",
    },
  ],
  outstanding: [
    {
      item: "utility-bill",
      status: "required",
      doctype_hint: "utility-bill",
      reason_code: "illegible",
      message: "The document was unreadable — please resubmit a clearer copy.",
    },
  ],
  actions: [{ kind: "upload", accept: ["gov-id", "utility-bill"], prompt: "Upload." }],
  lifecycle: "overdue",
};

const PIXEL_TOKENS = ["style", "color", "css", "width", "height", "pixel", "font"];

describe("mapView", () => {
  it("maps snake_case -> camelCase", () => {
    const view = mapView(SAMPLE);
    expect(view.contextId).toBe("ctx-1");
    expect(view.accepted[0].docId).toBe("d1");
    expect(view.rejected[0].docId).toBe("d2");
    expect(view.outstanding[0].doctypeHint).toBe("utility-bill");
    expect(view.actions[0].accept).toEqual(["gov-id", "utility-bill"]);
    expect(view.lifecycle).toBe("overdue");
  });

  it("relays reason_code + message verbatim (no rewrite)", () => {
    const view = mapView(SAMPLE);
    expect(view.accepted[0].reasonCode).toBe("auto_approved");
    expect(view.accepted[0].message).toBe("The document was automatically approved.");
    expect(view.rejected[0].reasonCode).toBe(SAMPLE.rejected![0].reason_code);
    expect(view.rejected[0].message).toBe(SAMPLE.rejected![0].message);
    expect(view.outstanding[0].message).toBe(SAMPLE.outstanding![0].message);
  });

  it("carries no styling field (content-not-pixels)", () => {
    const view = mapView(SAMPLE);
    const keys = new Set<string>();
    const walk = (obj: unknown) => {
      if (Array.isArray(obj)) obj.forEach(walk);
      else if (obj && typeof obj === "object") {
        for (const [k, v] of Object.entries(obj)) {
          keys.add(k.toLowerCase());
          walk(v);
        }
      }
    };
    walk(view);
    for (const token of PIXEL_TOKENS) {
      for (const key of keys) {
        expect(key).not.toContain(token);
      }
    }
  });

  it("tolerates absent optional fields", () => {
    const minimal: WireView = { kind: "upload_prompt", context_id: "c" };
    const view = mapView(minimal);
    expect(view.accepted).toEqual([]);
    expect(view.outstanding).toEqual([]);
    expect(view.lifecycle).toBe("on_track");
  });
});
