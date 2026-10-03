// Presenter stage App tests (P6-5).
//
// Proves the mode switch defaults to Split-Screen, Split-Screen renders BOTH zones + the
// labelled Gateway boundary (with placeholder panels when no sibling URL is set), and the
// mode switch flips to Timeline / Architecture X-Ray. No network: the App fetches only on an
// explicit control click, so the initial render is deterministic and host-free.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("Presenter stage App", () => {
  it("defaults to Split-Screen and renders both zones + the Gateway boundary", () => {
    render(<App />);
    // Both zones + the drawn Gateway boundary are present.
    expect(screen.getByTestId("external-zone")).toBeInTheDocument();
    expect(screen.getByTestId("internal-zone")).toBeInTheDocument();
    expect(screen.getByTestId("gateway-boundary")).toBeInTheDocument();
    expect(screen.getByTestId("gateway-boundary")).toHaveTextContent("Gateway");
    // The always-on control strip is above the mode.
    expect(screen.getByTestId("control-strip")).toBeInTheDocument();
    expect(screen.getByTestId("fast-forward-btn")).toBeInTheDocument();
  });

  it("switches to Timeline mode (scrubber) and Architecture X-Ray (svg)", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("mode-timeline"));
    expect(screen.getByTestId("scrubber")).toBeInTheDocument();
    // Split-screen is no longer mounted.
    expect(screen.queryByTestId("gateway-boundary")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("mode-xray"));
    expect(screen.getByTestId("xray-svg")).toBeInTheDocument();
    // The scheduler/clock node exists in the diagram.
    expect(screen.getByTestId("xray-node-scheduler")).toBeInTheDocument();
  });
});
