---
type: atom
up: "model.md"
tags: [bridge, adk, artifacts]
---

# Documents as artifacts

> A document is bytes — a photo of a bill, a scanned ID, a PDF. On the Bridge those bytes are a first-class **ADK artifact**: versioned, session-scoped, addressed by name, and moved across the wire **by reference, never inline**. This is the platform's own answer to binary documents, so we use it as-is rather than inventing a store.

## First, two things called "artifact"

The word is overloaded in this project. Keep them apart:

| Term | What it is | Where it lives |
|---|---|---|
| **A2A / aggregate artifact** | a **metadata** payload attached to a task — the classified-ledger entry, the Requirements list (`{payload, version}`) | the [aggregate model](aggregate-model.md) |
| **ADK document artifact** *(this page)* | the actual **document bytes**, versioned and session-scoped | the ADK `ArtifactService` |

They share a name and both *version*, but one carries meaning and the other carries bytes. When this wiki says "artifact" unqualified in a runtime/ADK context, it means the **document** artifact.

## Why the platform's artifact, not our own store

Documents are exactly what `ArtifactService` exists for, so hand-rolling a blob store would both hide the platform we are showcasing and become ours to maintain ([as standard as possible](adk.md)). Using it as-is buys three things for free:

- **Versioning = resubmission history.** Each save returns a version; **attempt N is version N**. A party re-sending a clearer photo is just the next version on the same filename — the disposition history is the version list, no extra bookkeeping.
- **Deletion = a real erase.** An [off-script](adk.md) deletion/erasure request maps to `delete_artifact` — an auditable removal of the bytes, not a soft flag — of all versions, and only within the requesting leg's session (never a `user:`-namespaced artifact, which would span the party's other exchanges).
- **Session-scoped by default.** One case = one session, so a document is reachable only within the exchange that produced it — the trust boundary comes from the platform, not from our own checks.

## Crossing the wire — by reference, both directions

An ADK artifact lives *inside* the Bridge; an A2A **`FilePart`** is how a document travels *between* agents. The [edge](edges.md) is the single place those two worlds meet, and the rule is absolute: **bytes never ride the A2A message.**

- **Inbound.** A submission carries a `FileWithUri`; the edge fetches it and `save_artifact`s it (new version per resubmission), then disposition runs on the artifact.
- **Outbound.** The deliverable is the [classified ledger plus artifact references](a2a-edge.md) — meaning first, bytes on demand. The servicer's agent reads the status, then pulls accepted-document bytes from a **scoped fetch endpoint**.

A **reference is not a capability**: every fetch is authorized on the same per-party leg boundary as the rest of the exchange, so handing over a uri never widens access. This keeps the message small, the boundary honest, and competitors' documents un-cross-reachable even if a uri leaks.

## Resolving an inbound reference

Because documents cross by reference, what actually reaches the extraction engine is *whatever uri the counterparty put on the part* — not a filename the Bridge chose. One resolver owns that dispatch, for both the extraction path and the edge's persistence, over four inbound shapes:

- **`gs://bucket/object.pdf` — by reference all the way.** The Bridge downloads nothing and needs no storage client: the object uri is handed to the model (a native `file_uri` part) and **Vertex reads it**. Readable only on the Vertex backend, so an explicitly non-Vertex client refuses loud rather than sending a part the API will reject. An object whose suffix yields no known mime is also refused — an unclassified part is garbage the backend would reject anyway.
- **`https://…` / `http://…` — a leg-scoped fetch.** A party's signed URL, or our own scoped endpoint. Bounded timeout, bounded body, mime from `Content-Type` with the uri suffix as fallback.
- **`file:///…` — off by default.** A local-dev affordance behind an explicit opt-in. A consumer submitting `file:///etc/passwd` would otherwise get host file contents read by the model and laundered back through the extracted key fields onto the ledger.
- **A bare relative name** — the eval corpus, read under the corpus root with traversal contained. Anything else refuses loud.

The fetch is where "a reference is not a capability" becomes **two-sided**. *Outbound,* our own `/artifacts/{context_id}/{filename}` endpoint 404s a foreign or guessed `(context_id, filename)` **before** minting a signed URL, so a leaked uri of our own endpoint resolves to nothing. *Inbound,* the Bridge refuses to become an open fetch proxy: an `http(s)` target whose host resolves to a **non-global** address (loopback, private, the cloud metadata IP) is refused **with no request issued**, unless its host / `host:port` is on an explicit deployment allowlist. Redirects are followed **manually**, a small bounded number of hops with **every hop re-validated** — necessary because our own fetch endpoint 307s to a GCS signed URL on deploy, and sufficient to stop a counterparty 307ing us into the metadata service. The same egress classification guards the [push-notification webhook](a2a-edge.md); it is one shared, dependency-free guard, not two policies.

The two paths differ deliberately on failure. Extraction **raises**: a document the engine could not read must never be silently replaced by a fixture or a fallback. Persistence **skips**: an unresolvable or by-reference document costs the byte history, not the exchange, so the ref is left unchanged and the disposition turn still runs.

Both halves of that only hold if the failure taxonomy is complete. A party's artifact host being briefly unreachable — a dead host, a DNS failure, a read timeout — is a *failing fetch*, exactly like a 404: the resolver normalizes every transport error into the same refusal type rather than letting the HTTP client's own exception tree leak through, and the persistence wrapper's skip is unconditional. An enumerated list of anticipated failures is how "skips" quietly becomes "fails the turn".

## Local ↔ deployed — one more backend swap

The artifact story swaps backends exactly like sessions do ([runtime](runtime.md)): **`InMemoryArtifactService`** locally, **`GcsArtifactService`** on deploy, with **no agent-code change**. The scoped fetch endpoint serves bytes directly from memory locally and issues a **GCS signed URL** on deploy — same contract, different backend.

## Read next
- [running on Google ADK](adk.md) — why native constructs, and the runtime that saves/loads these
- [A2A edge](a2a-edge.md) — the send-back contract the references travel on
- [aggregate model](aggregate-model.md) — the *other* artifact (the metadata ledger)

## Related
- [disposition](disposition.md), [runtime](runtime.md), [edges](edges.md)
