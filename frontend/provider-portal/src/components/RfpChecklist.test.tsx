// Carrier RFP checklist render test (S0-frontend-1).
//
// Proves the checklist renders one row per product line with its status badge, relays the
// gate's reason + field review verbatim, shows the reply address, offers the benefits
// conformance artifacts for an outstanding row, and Upload calls onUpload(productLine,
// artifact). A received row has no upload control.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RfpChecklist as Checklist } from "../domain/rfp";
import { QUOTE_ARTIFACTS, RfpChecklist } from "./RfpChecklist";

function checklist(): Checklist {
  return {
    kind: "rfp_checklist",
    rfpId: "acme-vb-2027",
    party: "carrier-a",
    contextId: "leg-a",
    dueAt: "2026-10-03T09:00:00Z",
    lifecycle: "on_track",
    items: [
      {
        productLine: "CRITICAL_ILLNESS",
        requirement: "required",
        status: "received",
        outstanding: false,
        versionCount: 1,
        lastActivity: "2026-10-02T09:00:00Z",
        reasonCode: "auto_approved",
        message: null,
        fieldReviews: [],
      },
      {
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
      },
    ],
    replyAddress: "rfp-quotes+tok@broker.example",
    replySubjectTag: "[ref:tok]",
    actions: [{ kind: "upload", accept: ["benefits-quote"], prompt: null }],
  };
}

describe("RfpChecklist", () => {
  it("renders each line's status with reasons + field review verbatim and the reply address", () => {
    render(<RfpChecklist checklist={checklist()} onUpload={() => {}} />);
    const ci = screen.getByTestId("rfp-item-CRITICAL_ILLNESS");
    const hi = screen.getByTestId("rfp-item-HOSPITAL_INDEMNITY");
    expect(within(ci).getByTestId("rfp-status-badge")).toHaveAttribute("data-status", "received");
    expect(within(hi).getByTestId("rfp-status-badge")).toHaveAttribute("data-status", "incomplete");
    expect(hi).toHaveAttribute("data-outstanding", "true");
    expect(ci).toHaveAttribute("data-outstanding", "false");
    expect(hi).toHaveTextContent("flagged_fields");
    expect(hi).toHaveTextContent("Please confirm the rate frequency.");
    expect(hi).toHaveTextContent(
      "fields.tables.rates[1].frequency · low_confidence · printed as 'per pay period'",
    );
    expect(screen.getByTestId("rfp-reply")).toHaveTextContent(
      "rfp-quotes+tok@broker.example with subject tag [ref:tok]",
    );
    expect(screen.getByTestId("lifecycle-badge")).toHaveAttribute("data-lifecycle", "on_track");
  });

  it("offers an upload only on the outstanding row; Upload calls onUpload", () => {
    const onUpload = vi.fn();
    render(<RfpChecklist checklist={checklist()} onUpload={onUpload} />);
    expect(screen.queryByRole("button", { name: "Upload CRITICAL_ILLNESS" })).toBeNull();

    // Pick the flagged HI artifact from the picker, then upload it into the HI row.
    fireEvent.mouseDown(screen.getByRole("combobox"));
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(QUOTE_ARTIFACTS);
    fireEvent.click(screen.getByRole("option", { name: "benefits-conformance-hi-usd-flagged" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload HOSPITAL_INDEMNITY" }));
    expect(onUpload).toHaveBeenCalledWith(
      "HOSPITAL_INDEMNITY",
      "benefits-conformance-hi-usd-flagged",
    );
  });

  it("no upload controls without an upload action; reply line hidden without an address", () => {
    const c = checklist();
    c.actions = [];
    c.replyAddress = null;
    render(<RfpChecklist checklist={c} onUpload={() => {}} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByTestId("rfp-reply")).toBeNull();
  });
});
