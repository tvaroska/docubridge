import { describe, expect, it } from "vitest";
import { mapSnapshot, mapTurn, type WireSnapshot } from "./console";

// A sample of exactly what the console_server emits: a same-issuer-reject snapshot —
// two accepted power-co bills but satisfied=false (the gate withheld done).
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

describe("mapSnapshot", () => {
  it("maps snake_case -> camelCase", () => {
    const snap = mapSnapshot(SAMPLE);
    expect(snap.sessionId).toBe("sess-1");
    expect(snap.preparedKey).toBe("same-issuer-reject");
    expect(snap.status).toBe("done");
    expect(snap.rounds).toBe(2);
    expect(snap.satisfied).toBe(false);
    expect(snap.turn?.contextId).toBe("ctx-1");
    expect(snap.turn?.entries).toHaveLength(2);
    expect(snap.turn?.entries[0].docId).toBe("bill-powerco-clean");
    expect(snap.turn?.entries[0].issuer).toBe("power-co");
    expect(snap.turn?.outstanding[0].doctypeHint).toBe("utility-bill");
  });

  it("relays reason_code + message verbatim (no rewrite)", () => {
    const snap = mapSnapshot(SAMPLE);
    expect(snap.turn?.entries[0].reasonCode).toBe("auto_approved");
    expect(snap.turn?.entries[0].message).toBe(
      "The document was automatically approved.",
    );
    expect(snap.turn?.outstanding[0].reasonCode).toBe("distinct_issuer_required");
    expect(snap.turn?.outstanding[0].message).toBe(
      "A second bill from a different issuer is still needed.",
    );
  });

  it("tolerates a null turn (a fresh running snapshot)", () => {
    const running: WireSnapshot = {
      session_id: "s",
      prepared_key: "path-a-instant",
      status: "running",
      turn: null,
    };
    const snap = mapSnapshot(running);
    expect(snap.turn).toBeNull();
    expect(snap.rounds).toBe(0);
    expect(snap.satisfied).toBe(false);
  });
});

describe("mapTurn", () => {
  it("flattens status.entries and status.outstanding.requirements", () => {
    const turn = mapTurn(SAMPLE.turn!);
    expect(turn.entries).toHaveLength(2);
    expect(turn.outstanding).toHaveLength(1);
    expect(turn.terminal).toBe(true);
  });
});
