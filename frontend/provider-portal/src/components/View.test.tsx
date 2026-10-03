import { ThemeProvider } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mapView, type WireView } from "../domain/a2ui";
import { theme } from "../theme";
import { View } from "./View";

function renderView(wire: WireView) {
  const view = mapView(wire);
  return render(
    <ThemeProvider theme={theme}>
      <View view={view} onUpload={vi.fn()} onResolve={vi.fn()} />
    </ThemeProvider>,
  );
}

describe("View relays content verbatim", () => {
  it("party_status shows a rejected entry's message + reasonCode verbatim", () => {
    const message = "The document was unreadable — please resubmit a clearer copy.";
    renderView({
      kind: "party_status",
      context_id: "ctx-1",
      rejected: [
        {
          doc_id: "d2",
          doctype: "utility-bill",
          disposition: "rejected",
          reason_code: "illegible",
          message,
        },
      ],
    });
    // The exact strings the graph produced appear in the DOM, unmodified.
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("illegible")).toBeInTheDocument();
  });

  it("escalated entry relays REASON_ESCALATED + escalate message (not 'rejected')", () => {
    const message =
      "This document could not be read after repeated attempts and was sent for human review.";
    renderView({
      kind: "party_status",
      context_id: "ctx-e",
      rejected: [
        {
          doc_id: "d9",
          doctype: "utility-bill",
          disposition: "pending",
          reason_code: "escalated_for_review",
          message,
        },
      ],
    });
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("escalated_for_review")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("upload_prompt relays the outstanding requirement message verbatim", () => {
    const message = "The document was unreadable — please resubmit a clearer copy.";
    renderView({
      kind: "upload_prompt",
      context_id: "ctx-u",
      outstanding: [
        {
          item: "utility-bill",
          status: "required",
          doctype_hint: "utility-bill",
          reason_code: "illegible",
          message,
        },
      ],
      actions: [{ kind: "upload", accept: ["gov-id", "utility-bill"], prompt: "Upload." }],
    });
    expect(screen.getByText(message)).toBeInTheDocument();
  });
});
