// Time-warp domain mapper tests (presenter stage, P6-5).
//
// Proves mapState round-trips the host's snake_case state → camelCase and that
// latestLifecycle derives the most-progressed lifecycle across armed exchanges.

import { describe, expect, it } from "vitest";
import { latestLifecycle, mapState, type WireState } from "./timewarp";

const WIRE: WireState = {
  now: 3,
  paused: false,
  pending: [],
  exchanges: [
    {
      session_id: "sid-1",
      context_id: "ctx-1",
      lifecycle: "escalated",
      done: true,
      events: [
        { step: "overdue", tick: 1 },
        { step: "reminder", tick: 2 },
        { step: "escalated", tick: 3 },
      ],
    },
  ],
};

describe("timewarp domain mapper", () => {
  it("maps state snake→camel", () => {
    const state = mapState(WIRE);
    expect(state.now).toBe(3);
    expect(state.paused).toBe(false);
    expect(state.exchanges).toHaveLength(1);
    const ex = state.exchanges[0];
    expect(ex.sessionId).toBe("sid-1");
    expect(ex.contextId).toBe("ctx-1");
    expect(ex.lifecycle).toBe("escalated");
    expect(ex.done).toBe(true);
    expect(ex.events.map((e) => e.step)).toEqual(["overdue", "reminder", "escalated"]);
    expect(ex.events[0].tick).toBe(1);
  });

  it("maps a pending alarm snake→camel", () => {
    const state = mapState({
      now: 0,
      paused: false,
      pending: [{ key: "followup:ctx-1", fire_at: 1, step: "overdue" }],
      exchanges: [],
    });
    expect(state.pending[0].fireAt).toBe(1);
    expect(state.pending[0].step).toBe("overdue");
  });

  it("latestLifecycle picks the most-progressed non-on_track lifecycle", () => {
    expect(latestLifecycle(mapState(WIRE))).toBe("escalated");
    expect(
      latestLifecycle(mapState({ now: 0, paused: false, pending: [], exchanges: [] })),
    ).toBe("on_track");
  });
});
