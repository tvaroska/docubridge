// Shared contract mapper tests (@docbride/shared, P6-5).
//
// Proves mapEntry / mapRequirement round-trip snake→camel over the superset wire shape and
// RELAY reasonCode/message verbatim (content-not-pixels). Missing optional fields map to null.

import { describe, expect, it } from "vitest";
import { mapEntry, mapRequirement } from "./contract";

describe("shared contract mappers", () => {
  it("mapEntry maps the full superset entry snake→camel, verbatim reason/message", () => {
    const entry = mapEntry({
      doc_id: "d1",
      doctype: "utility-bill",
      disposition: "accepted",
      gate: "auto_approve",
      issuer: "power-co",
      doctype_hint: "utility-bill",
      reason_code: "auto_approved",
      message: "The document met all requirements.",
    });
    expect(entry).toEqual({
      docId: "d1",
      doctype: "utility-bill",
      disposition: "accepted",
      gate: "auto_approve",
      issuer: "power-co",
      doctypeHint: "utility-bill",
      reasonCode: "auto_approved", // verbatim
      message: "The document met all requirements.", // verbatim
    });
  });

  it("mapEntry defaults absent optional fields to null", () => {
    const entry = mapEntry({ doc_id: "d2", doctype: "gov-id", disposition: "pending" });
    expect(entry.gate).toBeNull();
    expect(entry.issuer).toBeNull();
    expect(entry.doctypeHint).toBeNull();
    expect(entry.reasonCode).toBeNull();
    expect(entry.message).toBeNull();
  });

  it("mapRequirement maps snake→camel and relays reason/message verbatim", () => {
    const req = mapRequirement({
      item: "proof-of-address",
      status: "required",
      doctype_hint: "utility-bill",
      reason_code: "missing_document",
      message: "Please upload a utility bill.",
    });
    expect(req).toEqual({
      item: "proof-of-address",
      status: "required",
      doctypeHint: "utility-bill",
      reasonCode: "missing_document", // verbatim
      message: "Please upload a utility bill.", // verbatim
    });
  });

  it("mapRequirement defaults absent optional fields to null", () => {
    const req = mapRequirement({ item: "x", status: "optional" });
    expect(req.doctypeHint).toBeNull();
    expect(req.reasonCode).toBeNull();
    expect(req.message).toBeNull();
  });
});
