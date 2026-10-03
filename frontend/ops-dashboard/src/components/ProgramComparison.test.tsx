// Component render test for the program comparison view (S1-benefits-7).
//
// Proves the view renders N legs SIDE BY SIDE in one basis with gaps flagged VERBATIM
// (content-not-pixels), and that a post-revise (conformant) recompute drops the flag. Does
// NOT test live EventSource (jsdom lacks it) — renders with props (mirror Queues.test.tsx).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Comparison, ComparisonLeg } from "../domain/ops";
import { ProgramComparison } from "./ProgramComparison";

const GAP_MESSAGE =
  "exclusions 'orthodontics' violates the program standard (must not include 'orthodontics').";

function leg(
  party: string,
  { flagged }: { flagged: boolean },
): ComparisonLeg {
  return {
    party,
    quote: {
      party,
      currency: "USD",
      language: "en",
      premium: "1500.00",
      deductible: "500.00",
      coverageLimits: "dental and vision care",
      exclusions: flagged ? "orthodontics" : "cosmetic surgery",
      effectiveDate: "2026-01-01",
      expiryDate: "2026-12-31",
      originalCurrency: "USD",
      originalLanguage: "en",
    },
    verdict: flagged
      ? {
          party,
          conformant: false,
          route: "negotiate",
          gaps: [
            {
              ruleId: "no_orthodontics_exclusion",
              field: "exclusions",
              check: "must_not_include",
              expected: "must not include 'orthodontics'",
              actual: "orthodontics",
              message: GAP_MESSAGE,
            },
          ],
        }
      : { party, conformant: true, route: "none", gaps: [] },
  };
}

function program(carrierBFlagged: boolean): Comparison {
  return {
    programId: "acme-2026",
    currency: "USD",
    language: "en",
    legs: [
      leg("carrier-a", { flagged: false }),
      leg("carrier-b", { flagged: carrierBFlagged }),
      leg("carrier-c", { flagged: false }),
      leg("carrier-d", { flagged: false }),
    ],
  };
}

describe("ProgramComparison", () => {
  it("renders 4 legs side by side in one basis, carrier-b flagged verbatim", () => {
    render(<ProgramComparison programs={[program(true)]} />);

    // All 4 carriers appear as columns.
    for (const party of ["carrier-a", "carrier-b", "carrier-c", "carrier-d"]) {
      expect(screen.getByText(party)).toBeInTheDocument();
    }

    // The shared canonical basis (USD) is stamped once in the header.
    expect(screen.getByText(/USD \/ en/)).toBeInTheDocument();

    // carrier-b's gap flag + the gap message + rule_id are relayed verbatim.
    expect(screen.getByText("gap — negotiate")).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp("no_orthodontics_exclusion")),
    ).toBeInTheDocument();
    expect(screen.getByText(new RegExp("must not include"))).toBeInTheDocument();

    // The three conformant legs each show a conformant marker (a-c-d).
    expect(screen.getAllByText("conformant")).toHaveLength(3);
  });

  it("drops the flag after a conformant recompute (post-revise render)", () => {
    render(<ProgramComparison programs={[program(false)]} />);

    // carrier-b is now conformant — no gap flag anywhere.
    expect(screen.queryByText("gap — negotiate")).not.toBeInTheDocument();
    expect(
      screen.queryByText(new RegExp("no_orthodontics_exclusion")),
    ).not.toBeInTheDocument();
    // All four legs conformant now.
    expect(screen.getAllByText("conformant")).toHaveLength(4);
  });
});
