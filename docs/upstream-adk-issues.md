# Upstream ADK issues — drafts

Custom, non-canonical code carried only because a platform-native path is missing
or broken. Each bespoke workaround has a recorded justification here, together with
the ready-to-file upstream report, so it stays an explicitly-tracked stopgap rather
than a permanent local dialect. When a report is filed, add its URL next to the
entry and to the experimental-surfaces list in [adk](../design/adk.md).

Environment: Issues 1–2 first observed on `google-adk` 2.7.0 and re-confirmed on the locked 2.7.1; Issues 3–5 on 2.7.1. `a2a-sdk` 1.1.2, Python 3.12.

---

## Issue 1 — `RemoteA2aAgent` resume detection breaks inside a `Workflow`

- **Local workaround:** `agents/address/interceptor.py` (`pending_resume_target` + the `resume_stamping_interceptor` send-path `RequestInterceptor`)

### Summary

`RemoteA2aAgent` detects the resume of a parked (`input-required`) A2A task only
when the resolved `FunctionResponse` is the **last** event on the session. Running
the agent as a node inside a `google.adk.workflow.Workflow` breaks that assumption:
the graph orchestrator appends a workflow-start/orchestration event **after** the
user `FunctionResponse` and before the collect node re-runs. As a result the remote
agent never sets the parked `task_id`, re-sends a *fresh* request, and the peer
opens (and re-parks) a **new** task instead of resuming the existing one.

### Where

`google/adk/agents/remote_a2a_agent.py` —
`_create_a2a_request_for_user_function_response` keys off
`ctx.session.events[-1].author == "user"`.

### Steps to reproduce

1. Build a `Workflow` conditional cycle whose collect node is a `RemoteA2aAgent`
   pointed at an A2A peer that parks (`INPUT_REQUIRED`) on the first turn:
   `collect -> gate`, `gate --[again]--> collect`, `gate --[done]--> present`.
2. Run one turn; the leg parks on a `LongRunningFunctionTool` call.
3. Resume by feeding the matching `FunctionResponse` to `runner.run_async`.
4. Observe the outbound `message/send`: `task_id` is empty, so the peer starts a
   new task rather than resuming the parked one.

### Expected vs. actual

- **Expected:** the resume targets the parked `task_id`/`context_id` regardless of
  whether an orchestration event was appended after the `FunctionResponse`.
- **Actual:** resume is detected only when the `FunctionResponse` is literally the
  last session event, so any orchestrator (Workflow, or any composition that
  appends after the response) defeats it.

### Suggested fix

Detect the pending resume by scanning for the most-recent user `FunctionResponse`
that has **no event authored by this agent after it**, rather than requiring it to
be the last event outright — then recover the parked `task_id`/`context_id` from
the matching function-call event's `custom_metadata` (`a2a:task_id` /
`a2a:context_id`). This is exactly what our `pending_resume_target` does; folding
it into `RemoteA2aAgent` would let us delete the workaround.

---

## Issue 2 — no supported way to decode an A2A `DataPart` from an ADK event stream

- **Local workaround:** `agents/address/wire.py` (`extract_exchange_turn`, splitting on the `_TAG_START`/`_TAG_END` delimiters)

### Summary

When a `RemoteA2aAgent` relays a peer's structured A2A `DataPart`
(`media_type=application/json`) into the ADK event stream,
`convert_a2a_part_to_genai_part` wraps the JSON as an `inline_data` blob delimited
by literal `<a2a_datapart_json>` / `</a2a_datapart_json>` tags. A downstream graph
node (e.g. a deterministic gate) sees only ADK **events**, not the raw `a2a.Task`,
so it cannot use the canonical decoder (`a2a.helpers.proto_helpers.get_data_parts`,
which we use on the raw-task path in `task_to_exchange_turn`). The only way to
recover the structured payload from a node is to string-match those wrapper tags —
brittle against any change to the delimiter format.

### Where

`google/adk/a2a/converters/part_converter.py` — `convert_a2a_part_to_genai_part` (the `A2A_DATA_PART_START_TAG` wrapping).

### Ask

A supported, documented helper to decode an A2A data part back out of a
`genai.types.Part` / ADK event — the inverse of `convert_a2a_part_to_genai_part` —
so consumers don't depend on the private wrapper-tag string format. Alternatively,
surface relayed A2A data parts as structured parts rather than an opaque
tag-wrapped `inline_data` blob.

### Impact if unaddressed

Any ADK agent that consumes another agent's structured (non-text) A2A output from
inside a graph must re-implement this string-parsing and will silently break if the
wrapper format changes.

---

## Issue 3 — A `user:` filename prefix silently switches an artifact to the party-wide user namespace

- **Local workaround:** `bridge/offscript.py` (`USER_NAMESPACE_PREFIX` and the guard at the top of `_erase_artifact`)

### Summary

A filename starting with `user:` (e.g., `user:bill.pdf`) escalates the artifact's scope from session-scoped to party-wide user namespace, and `list_artifact_keys(session_id=...)` mixes those keys in with session keys. A client-chosen filename can therefore cause `delete_artifact` on one session to erase another session's artifact, breaking the per-context isolation the session boundary is meant to provide.

### Where

- `google/adk/artifacts/in_memory_artifact_service.py::_file_has_user_namespace` (lines ~60–69)
- `google/adk/artifacts/gcs_artifact_service.py` (module docstring line 18 and lines ~193–196)

### Steps to reproduce

1. Save an artifact named `user:bill.pdf` in session A.
2. In session B (different context), call `delete_artifact(name="user:bill.pdf")`.
3. Observe session A's artifact is gone — the delete crossed session boundaries.
4. Covered by `tests/test_edge_offscript_a2a.py::test_user_namespace_and_traversal_names_never_erase_across_contexts`.

### Expected vs. actual

- **Expected:** artifacts scoped to the requesting session/context, with explicit opt-in required for cross-session sharing.
- **Actual:** a magic name prefix silently escalates scope, and artifact deletion can cross session boundaries.

### Suggested fix

An explicit `scope=` parameter (session vs. user) rather than a magic name prefix, or at minimum a `list_artifact_keys` that separates session keys from user keys. Also apply the scoping on the persist and fetch side (not currently fixed — see S3-edge-5 follow-up G7 in DECISIONS.md).

---

## Issue 4 — `LongRunningFunctionTool` pause and resume semantics are implicit and permissive

- **Local workaround:** `bridge/edge/llm_leg.py` (`pending_review_calls` and `resolved_review_calls` bookkeeping) + `bridge/edge/executor.py` (`CollectExecutor.admit` pre-dispatch guard)

### Summary

Three related issues with long-running function tool pause/resume:

(a) **Only a `None` return pauses a resumable invocation.** A non-None return emits the function_response and re-calls the model. This is pinned by the repo's `request_human_review` returning `None` (`bridge/offscript.py:139`), documented in DECISIONS.md S3-edge-2 G1.

(b) **ADK re-accepts a FunctionResponse for an already-answered long-running call id.** It raises only on an unknown id. The workaround is the executor's own pending/answered bookkeeping (`pending_review_calls` and `resolved_review_calls`) plus the pre-dispatch `CollectExecutor.admit` guard, covered by `tests/test_edge_hitl_a2a.py::test_resume_already_resolved_id_refused`.

(c) **Resuming one of several paused calls yields no events until the last one is answered** (S3-edge-6 gotcha).

### Where

- `google/adk/flows/llm_flows/functions.py` (long_running_tool_ids, lines ~300–433)
- `google/adk/agents/invocation_context.py` (lines ~518–523)
- The re-accept path can be located with `grep -n "function_call_id\|long_running" .venv/lib/python3.12/site-packages/google/adk/runners.py`

### Suggested fix

- Document the None-pause contract explicitly in the API reference.
- Reject a response whose id is already answered.
- Expose a public "pending long-running calls" helper on the session.

---

## Issue 5 — `AgentTool` returns the sub-agent's merged final model text, not its tool output

- **Local workaround:** `bridge/gatepin.py::record_extraction` (line 129), called inside `extract_document`

### Summary

When an `AgentTool` invokes a sub-agent, `run_async` returns the sub-agent's merged final model **text**, not its tool output. A parent therefore cannot get a structured, non-forgeable result from a sub-agent's tool. Signals (extraction results, gate verdicts) must be written to session state and forwarded through the sub-run `state_delta`, then pinned from state by the root `before_tool_callback` (DECISIONS.md S3-edge-4 context and D1–D2).

### Where

`google/adk/tools/agent_tool.py::run_async` (`merged_text`, lines ~315–322)

### Suggested fix

An `AgentTool` option to return the sub-agent's last function_response (or a typed output) rather than its text, so parent agents can consume structured sub-agent results without session-state side channels.
