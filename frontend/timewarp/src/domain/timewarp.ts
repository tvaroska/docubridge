// Time-warp state model + snake→camel mapper (presenter stage, P6-5).
//
// Mirrors the shape the Python time-warp host emits from `GET /timewarp/state` (see
// `bridge/timewarp/host.py::state`): the virtual clock `now`, the `paused` flag, the list of
// pending alarms (one per armed ladder between fires), and the armed exchanges with their
// stamped lifecycle + fired-event trail. The `lifecycle`/`step` values are the shared
// LifecycleStatus vocabulary (relayed verbatim — content-not-pixels).

import type { LifecycleStatus } from "@docbride/shared";

// --------------------------------------------------------------------------- //
// Wire shapes (snake_case from the time-warp host)                            //
// --------------------------------------------------------------------------- //
export interface WirePendingAlarm {
  key: string;
  fire_at: number;
  step: string | null;
}

export interface WireFiredEvent {
  step: string;
  tick: number;
}

export interface WireExchange {
  session_id: string;
  context_id: string;
  lifecycle: string;
  done: boolean;
  events: WireFiredEvent[];
}

export interface WireState {
  now: number;
  paused: boolean;
  pending: WirePendingAlarm[];
  exchanges: WireExchange[];
}

// --------------------------------------------------------------------------- //
// App-side types (camelCase)                                                  //
// --------------------------------------------------------------------------- //
export interface PendingAlarm {
  key: string;
  fireAt: number;
  step: LifecycleStatus | null;
}

export interface FiredEvent {
  step: LifecycleStatus;
  tick: number;
}

export interface Exchange {
  sessionId: string;
  contextId: string;
  lifecycle: LifecycleStatus;
  done: boolean;
  events: FiredEvent[];
}

export interface TimewarpState {
  now: number;
  paused: boolean;
  pending: PendingAlarm[];
  exchanges: Exchange[];
}

// --------------------------------------------------------------------------- //
// Mapper (snake→camel)                                                        //
// --------------------------------------------------------------------------- //
function mapPending(w: WirePendingAlarm): PendingAlarm {
  return {
    key: w.key,
    fireAt: w.fire_at,
    step: (w.step as LifecycleStatus | null) ?? null,
  };
}

function mapEvent(w: WireFiredEvent): FiredEvent {
  return { step: w.step as LifecycleStatus, tick: w.tick };
}

function mapExchange(w: WireExchange): Exchange {
  return {
    sessionId: w.session_id,
    contextId: w.context_id,
    lifecycle: w.lifecycle as LifecycleStatus,
    done: w.done,
    events: w.events.map(mapEvent),
  };
}

export function mapState(w: WireState): TimewarpState {
  return {
    now: w.now,
    paused: w.paused,
    pending: w.pending.map(mapPending),
    exchanges: w.exchanges.map(mapExchange),
  };
}

/** The last-fired lifecycle across all armed exchanges (drives the X-Ray active set). */
export function latestLifecycle(state: TimewarpState | null): LifecycleStatus {
  let latest: LifecycleStatus = "on_track";
  if (!state) return latest;
  for (const ex of state.exchanges) {
    if (ex.lifecycle !== "on_track") latest = ex.lifecycle;
  }
  return latest;
}
