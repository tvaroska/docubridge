import { ThemeProvider } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mapSnapshot, type WireSnapshot } from "../domain/console";
import { theme } from "../theme";
import { TurnViewer } from "./TurnViewer";

// The Sense-B-watchable case: two accepted same-issuer bills, satisfied=false, an
// outstanding requirement. The viewer must show BOTH entries, a "not satisfied"
// indicator, and the requirement message (the agent — the gate — withheld done).
const SAMPLE: WireSnapshot = {
  session_id: "sess-1",
  prepared_key: "same-issuer-reject",
  status: "done",
  rounds: 2,
  satisfied: false,
  error: null,
  turn: {
    context_id: "ctx-1",
    terminal: true,
    lifecycle: "on_track",
    status: {
      entries: [
        {
          doc_id: "bill-powerco-clean",
          doctype: "utility-bill",
          disposition: "accepted",
          gate: "auto_approve",
          issuer: "power-co",
          reason_code: "auto_approved",
          message: "The document was automatically approved.",
        },
        {
          doc_id: "bill-powerco-clean-2",
          doctype: "utility-bill",
          disposition: "accepted",
          gate: "auto_approve",
          issuer: "power-co",
          reason_code: "auto_approved",
          message: "The document was automatically approved.",
        },
      ],
      outstanding: {
        requirements: [
          {
            item: "utility-bill",
            status: "required",
            doctype_hint: "utility-bill",
            reason_code: "distinct_issuer_required",
            message: "A second bill from a different issuer is still needed.",
          },
        ],
        done: false,
      },
    },
  },
};

function renderViewer(wire: WireSnapshot) {
  return render(
    <ThemeProvider theme={theme}>
      <TurnViewer snapshot={mapSnapshot(wire)} />
    </ThemeProvider>,
  );
}

describe("TurnViewer (Sense-B watchable)", () => {
  it("renders both accepted same-issuer entries", () => {
    renderViewer(SAMPLE);
    expect(screen.getByText("bill-powerco-clean")).toBeInTheDocument();
    expect(screen.getByText("bill-powerco-clean-2")).toBeInTheDocument();
    // Both are power-co (the same issuer that fails the distinct-issuer rule).
    expect(screen.getAllByText("power-co")).toHaveLength(2);
  });

  it("shows a 'not satisfied' indicator (the gate withheld done)", () => {
    renderViewer(SAMPLE);
    expect(screen.getByText("not satisfied")).toBeInTheDocument();
    expect(screen.getByText("rounds: 2")).toBeInTheDocument();
  });

  it("renders the outstanding requirement message verbatim", () => {
    renderViewer(SAMPLE);
    expect(
      screen.getByText("A second bill from a different issuer is still needed."),
    ).toBeInTheDocument();
  });

  it("shows a 'satisfied' indicator when the gate minted satisfaction", () => {
    const satisfied: WireSnapshot = {
      session_id: "s2",
      prepared_key: "path-a-instant",
      status: "done",
      rounds: 1,
      satisfied: true,
      turn: {
        context_id: "c",
        terminal: true,
        status: {
          entries: [
            {
              doc_id: "gov-id-clean",
              doctype: "gov-id",
              disposition: "accepted",
              gate: "auto_approve",
              issuer: null,
              reason_code: "auto_approved",
              message: "The document was automatically approved.",
            },
          ],
          outstanding: { requirements: [], done: true },
        },
      },
    };
    renderViewer(satisfied);
    expect(screen.getByText("satisfied")).toBeInTheDocument();
    expect(screen.getByText("gov-id-clean")).toBeInTheDocument();
  });
});
