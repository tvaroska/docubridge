// Component render test for the RFP exchange view (S0-frontend-1).
//
// Proves the carriers x product-lines matrix renders, overdue / escalated cells and rows
// carry their highlight, the gate's reason + field review path are relayed VERBATIM, and the
// "Chase now" button calls onChase with the carrier (disabled for a fully received carrier).
// Props-driven — no EventSource / fetch (jsdom lacks EventSource).

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RfpCarrier, RfpCell, RfpStatus } from "@docbride/shared";
import { RfpExchangeView } from "./RfpExchangeView";

const LINES = ["CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY"] as const;

function cell(party: string, productLine: string, over: Partial<RfpCell> = {}): RfpCell {
  return {
    party,
    productLine,
    requirement: "required",
    status: "invited",
    versions: [],
    attempts: 0,
    escalated: false,
    lastActivity: null,
    reasonCode: null,
    message: null,
    fieldReviews: [],
    ...over,
  };
}

function carrier(party: string, over: Partial<RfpCarrier> = {}): RfpCarrier {
  return {
    party,
    contextId: `leg-${party}`,
    lifecycle: "on_track",
    ready: 0,
    waiting: 2,
    chases: 0,
    lastChasedAt: null,
    replyAddress: null,
    replySubjectTag: null,
    ...over,
  };
}

function rfp(): RfpStatus {
  return {
    rfpId: "acme-vb-2027",
    process: "rfp-quote-collection",
    contextId: "ctx-broker",
    dueAt: "2026-10-03T09:00:00Z",
    asOf: null,
    apiVersion: null,
    lifecycle: "escalated",
    ready: 2,
    waiting: 4,
    counts: {},
    carriers: [
      carrier("carrier-a", { ready: 2, waiting: 0 }),
      carrier("carrier-b", { lifecycle: "overdue", chases: 1, lastChasedAt: "2026-10-03T10:00:00Z" }),
      carrier("carrier-c", { lifecycle: "escalated" }),
    ],
    cells: [
      cell("carrier-a", "CRITICAL_ILLNESS", {
        status: "received",
        versions: ["q1"],
        lastActivity: "2026-10-02T09:00:00Z",
        reasonCode: "auto_approved",
      }),
      cell("carrier-a", "HOSPITAL_INDEMNITY", {
        status: "received",
        versions: ["q2", "q3"],
        escalated: true,
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
      }),
      cell("carrier-b", "CRITICAL_ILLNESS", { status: "overdue" }),
      cell("carrier-b", "HOSPITAL_INDEMNITY", { status: "overdue" }),
      cell("carrier-c", "CRITICAL_ILLNESS", { status: "overdue" }),
      cell("carrier-c", "HOSPITAL_INDEMNITY", { status: "overdue" }),
    ],
    unmatched: [],
  };
}

describe("RfpExchangeView", () => {
  it("renders the 3x2 carriers x product-lines matrix", () => {
    render(<RfpExchangeView rfp={rfp()} onChase={() => {}} />);
    for (const line of LINES) {
      expect(screen.getByRole("columnheader", { name: line })).toBeInTheDocument();
    }
    for (const party of ["carrier-a", "carrier-b", "carrier-c"]) {
      expect(screen.getByTestId(`rfp-row-${party}`)).toBeInTheDocument();
      for (const line of LINES) {
        expect(screen.getByTestId(`rfp-cell-${party}-${line}`)).toBeInTheDocument();
      }
    }
    expect(screen.getAllByTestId("rfp-status-badge")).toHaveLength(6);
    const ci = screen.getByTestId("rfp-cell-carrier-a-CRITICAL_ILLNESS");
    expect(within(ci).getByTestId("rfp-status-badge")).toHaveAttribute("data-status", "received");
    expect(ci).toHaveTextContent("1 version");
    expect(ci).toHaveTextContent("2026-10-02 09:00Z");
    expect(screen.getByText(/2 ready · 4 waiting/)).toBeInTheDocument();
  });

  it("highlights overdue cells and escalated cells / carrier rows", () => {
    render(<RfpExchangeView rfp={rfp()} onChase={() => {}} />);
    expect(screen.getByTestId("rfp-cell-carrier-b-CRITICAL_ILLNESS")).toHaveAttribute(
      "data-highlight",
      "overdue",
    );
    expect(screen.getByTestId("rfp-cell-carrier-a-HOSPITAL_INDEMNITY")).toHaveAttribute(
      "data-highlight",
      "escalated",
    );
    expect(screen.getByTestId("rfp-cell-carrier-a-CRITICAL_ILLNESS")).not.toHaveAttribute(
      "data-highlight",
    );
    expect(screen.getByTestId("rfp-row-carrier-c")).toHaveAttribute("data-highlight", "escalated");
    expect(screen.getByTestId("rfp-row-carrier-b")).not.toHaveAttribute("data-highlight");
  });

  it("relays the reason and field review verbatim; shows chase counts", () => {
    render(<RfpExchangeView rfp={rfp()} onChase={() => {}} />);
    const hi = screen.getByTestId("rfp-cell-carrier-a-HOSPITAL_INDEMNITY");
    expect(hi).toHaveTextContent("flagged_fields: Please confirm the rate frequency.");
    expect(hi).toHaveTextContent(
      "fields.tables.rates[1].frequency · low_confidence · printed as 'per pay period'",
    );
    expect(within(screen.getByTestId("rfp-row-carrier-b")).getByTestId("rfp-chases")).toHaveTextContent(
      "chased 1× · last 2026-10-03 10:00Z",
    );
  });

  it("Chase now calls onChase with the carrier; disabled for a fully received carrier", () => {
    const onChase = vi.fn();
    render(<RfpExchangeView rfp={rfp()} onChase={onChase} />);
    fireEvent.click(screen.getByRole("button", { name: "Chase carrier-b now" }));
    expect(onChase).toHaveBeenCalledWith("acme-vb-2027", "carrier-b");
    expect(screen.getByRole("button", { name: "Chase carrier-a now" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Chase all waiting" }));
    expect(onChase).toHaveBeenLastCalledWith("acme-vb-2027", null);
  });

  it("busy disables every chase; an absent combo renders an empty cell; portal link", () => {
    const status = rfp();
    status.cells = status.cells.filter(
      (c) => !(c.party === "carrier-c" && c.productLine === "HOSPITAL_INDEMNITY"),
    );
    render(
      <RfpExchangeView rfp={status} onChase={() => {}} busy portalAppUrl="http://portal.test" />,
    );
    for (const btn of screen.getAllByRole("button")) {
      expect(btn).toBeDisabled();
    }
    expect(screen.getByTestId("rfp-cell-empty")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "carrier checklist" });
    expect(links[1]).toHaveAttribute("href", "http://portal.test/?leg=leg-carrier-b");
  });
});
