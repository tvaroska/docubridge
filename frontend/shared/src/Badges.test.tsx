// Shared Badges + theme tests (@docubridge/shared, P6-5).
//
// Proves the badges render the VALUE as text (label = the verbatim vocabulary value, never
// a rewritten explanation) and that makeTheme carries the caller's primary accent.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  DispositionBadge,
  LifecycleBadge,
  RequirementBadge,
  RfpStatusBadge,
} from "./Badges";
import { makeTheme } from "./theme";

describe("shared Badges", () => {
  it("DispositionBadge renders the disposition value as its label", () => {
    render(<DispositionBadge value="accepted" />);
    expect(screen.getByText("accepted")).toBeInTheDocument();
  });

  it("RequirementBadge renders the requirement status value", () => {
    render(<RequirementBadge value="satisfied" />);
    expect(screen.getByText("satisfied")).toBeInTheDocument();
  });

  it("LifecycleBadge renders the lifecycle value + carries a data-lifecycle attribute", () => {
    render(<LifecycleBadge value="escalated" />);
    const badge = screen.getByTestId("lifecycle-badge");
    expect(badge).toHaveTextContent("escalated");
    expect(badge).toHaveAttribute("data-lifecycle", "escalated");
  });

  it("RfpStatusBadge renders the verbatim RFP item status + data-status", () => {
    render(<RfpStatusBadge value="incomplete" />);
    const badge = screen.getByTestId("rfp-status-badge");
    expect(badge).toHaveTextContent("incomplete");
    expect(badge).toHaveAttribute("data-status", "incomplete");
  });

  it("RfpStatusBadge colours by status class (received success, overdue warning)", () => {
    render(
      <>
        <RfpStatusBadge value="received" />
        <RfpStatusBadge value="overdue" />
        <RfpStatusBadge value="invited" />
      </>,
    );
    const [received, overdue, invited] = screen.getAllByTestId("rfp-status-badge");
    expect(received.className).toMatch(/colorSuccess/);
    expect(overdue.className).toMatch(/colorWarning/);
    expect(invited.className).toMatch(/colorInfo/);
  });
});

describe("shared theme factory", () => {
  it("makeTheme carries the caller's primary accent (per-zone identity)", () => {
    expect(makeTheme("#0b7285").palette.primary.main).toBe("#0b7285");
    expect(makeTheme("#1f6feb").palette.primary.main).toBe("#1f6feb");
    // Shared shape is common across zones.
    expect(makeTheme("#0b7285").shape.borderRadius).toBe(10);
  });
});
