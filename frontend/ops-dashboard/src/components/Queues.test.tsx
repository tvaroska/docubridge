// Component render tests for the Ops Dashboard (P6-4).
//
// Proves the ledger + HITL queue + escalation queue render with reasonCode/message
// verbatim (content-not-pixels). Does NOT test live EventSource (jsdom lacks it) — tests
// the mapper + component render with props (mirror agent-console's approach).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { OpsExchange } from "../domain/ops";
import { EscalationQueue } from "./EscalationQueue";
import { HitlQueue } from "./HitlQueue";
import { LedgerView } from "./LedgerView";

describe("Ops Dashboard components", () => {
  it("renders the ledger with reasonCode/message verbatim", () => {
    const exchange: OpsExchange = {
      sessionId: "sid-1",
      contextId: "ctx-1",
      process: "address-proof",
      status: "done",
      lifecycle: "on_track",
      entries: [
        {
          docId: "d1",
          doctype: "utility-bill",
          issuer: "power-co",
          disposition: "accepted",
          reasonCode: "auto_approved",
          message: "The document met all requirements.",
        },
      ],
      hitl: false,
      escalated: false,
    };

    render(<LedgerView exchange={exchange} />);

    // The ledger table renders the entry's fields verbatim.
    expect(screen.getByText("d1")).toBeInTheDocument();
    expect(screen.getByText("utility-bill")).toBeInTheDocument();
    expect(screen.getByText("power-co")).toBeInTheDocument();
    expect(screen.getByText("accepted")).toBeInTheDocument();
    expect(screen.getByText("auto_approved")).toBeInTheDocument();
    expect(screen.getByText("The document met all requirements.")).toBeInTheDocument();
  });

  it("renders the HITL queue with a pending entry and resolve controls", () => {
    const hitlExchange: OpsExchange = {
      sessionId: "sid-2",
      contextId: "ctx-2",
      process: "address-proof",
      status: "in_flight",
      lifecycle: "on_track",
      entries: [
        {
          docId: "d2",
          doctype: "utility-bill",
          issuer: "aqua-util",
          disposition: "pending",
          reasonCode: "awaiting_human_review",
          message: "This submission was sent for human review.",
        },
      ],
      hitl: true,
      escalated: false,
    };

    render(<HitlQueue queue={[hitlExchange]} onResolved={() => {}} />);

    // The HITL queue shows the exchange and the parked entry's message.
    expect(screen.getByText(/ctx-2/)).toBeInTheDocument();
    expect(screen.getByText(/awaiting_human_review/)).toBeInTheDocument();
    expect(screen.getByText(/This submission was sent for human review/)).toBeInTheDocument();
    // The approve/reject buttons are present.
    expect(screen.getByRole("button", { name: /Approve/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reject/i })).toBeInTheDocument();
  });

  it("renders the escalation queue with REASON_ESCALATED message verbatim", () => {
    const escalatedExchange: OpsExchange = {
      sessionId: "sid-3",
      contextId: "ctx-3",
      process: "address-proof",
      status: "done",
      lifecycle: "escalated",
      entries: [
        {
          docId: "d3",
          doctype: "utility-bill",
          issuer: "aqua-util",
          disposition: "pending",
          reasonCode: "escalated_for_review",
          message: "This document could not be read after repeated attempts and was sent for human review.",
        },
      ],
      hitl: false,
      escalated: true,
    };

    render(<EscalationQueue queue={[escalatedExchange]} />);

    // The escalation queue shows the exchange and the escalated entry's message.
    expect(screen.getByText(/ctx-3/)).toBeInTheDocument();
    expect(screen.getByText(/escalated_for_review/)).toBeInTheDocument();
    expect(screen.getByText(/This document could not be read after repeated attempts/)).toBeInTheDocument();
  });
});
