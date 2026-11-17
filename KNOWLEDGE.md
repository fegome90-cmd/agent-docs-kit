# Knowledge continuity — 0.5.0-rc.1

The optional `knowledge check` command explains review obligations for an explicit
curated map. It never executes project code, calls a model, modifies a document,
or grants semantic approval. Version 0.5.0-rc.1 adds opt-in continuity checks.

```sh
python -B agent_docs_kit.py knowledge check /absolute/git/root \
  --map docs/living/maps/links-001.json \
  --snapshot docs/living/snapshots/base.json --view worktree --json
```

All new paths are canonical paths relative to ROOT, even when launched elsewhere.
ROOT must be a Git worktree root. `--view index` reads exact staged blobs and
honors `GIT_INDEX_FILE`; it never falls back to worktree content. Worktree reads
hash original bytes in chunks. Git inventory discovery inspects names and types,
not contents of newly discovered unmapped files. Both views reject unsafe paths,
nonregular files and unstable reads. Race detection is best effort, not an atomic
filesystem transaction or protection against hostile local writers.

A map uses schema `agent-docs-kit/links`, version 1, a canonical UUID `project_id`,
`documents`, and required `inventory_only` (use an empty list for none). Documents
contain `id`, `origin`, and `sections`. Each section contains a globally unique
`id`, `selector`, `claim_kind`, `watches`, and `depends_on`. Selectors are
`{"kind":"atx","heading":"## Exact heading"}` or `{"kind":"document"}`.
A whole-document selector must stand alone. Claims are `observed`, `constraint`,
or `planned`; a section must watch an exact file or depend on another section.
Dependencies must exist and be acyclic. Inventory dispositions are `{path,reason}`
with a nonempty reason and no overlap with watched files.

ATX levels 2–6 select exact UTF-8/LF byte spans outside fenced code. The next
heading of equal or lower level ends a span. Ambiguous, absent, overlapping, or
unclosed selections fail explicitly. Unmapped H2 headings remain visible.
Unknown fields and duplicate JSON keys fail, including nested objects. Limits:
1 MiB map/Markdown, 64 documents, 512 sections, 2048 dependency edges, JSON depth
64, and 8 MiB evidence/report. Oversized results fail rather than truncate.

Evidence identities use domain-prefixed SHA-256 over canonical JSON, excluding
only `observed_at` and `evidence_id`. They include the view and snapshot reference.
Raw content hashes preserve string spaces, indentation and newlines. Reviewed
content obligations remain distinct from snapshot provenance: refreshing a
snapshot cannot erase review debt. The impact algorithm compares explicit
reviewed evidence, propagates every cause, and queues dependencies before their
consumers with stable ID ordering. A constraint source change reports a potential
conflict requiring review, without claiming a semantic contradiction.

Coverage is limited to declared snapshot scopes or discovery roots, plus mapped
files. Unmapped sources and headings are explicit. Inventory-only dispositions
attest inventory only. Neither a clean snapshot nor `no_linked_change` certifies
that prose is correct or that the repository is fully covered.

`check_knowledge` resolves the real acceptance DAG in the selected view. It
captures actual verified accepted bodies, proposal IDs and heads, compares against
their preserved reviewed evidence and reports selection separately from freshness.
Origin documents are bootstrap inputs only. Unaccepted documents remain
unverified. Pure algorithm callers may supply explicit synthetic envelopes, but
CLI coordinators construct context only from verified artifacts.

Default analysis and help write nothing and load no Portalocker or model runtime.
An optional report export requires both `--output NEW --apply`, creates an
exclusive new file, and requires an existing parent. No overwrite or repair is
performed. Reports contain fingerprints, never source bodies. Codes: 0 means no
linked change within scope; 1 means review required or an occupied export target;
2 means invalid usage; 3 means an operational, schema, reference or stability
error. `--json` makes operational errors structured as well.

Raw sources have a 64 MiB ceiling in both views. Index blobs are size-checked
before allocation; worktree reads abort inside the chunk loop before retaining
a chunk beyond the cap. Legacy fingerprint callers keep their previous behavior.

## Exclusive proposals (P04)

`knowledge propose ROOT --request REQUEST --output NEW_BUNDLE --apply` consumes
an explicit safe regular worktree request and candidate, and evaluates map,
snapshot and sources exclusively in `evidence.view`. Requests and bundles use
strict version-1 schemas from the design. Proposal identity replaces the entire
variable evidence file descriptor with the logical evidence ID; original file
sizes and SHA-256 hashes remain mandatory integrity checks.

Publication uses the persistent cooperative knowledge lock and writes
`document.md`, `evidence.json`, then `proposal.json` as the completion marker.
No partial output is removed or repaired. Equivalent complete retries return the
original bundle; changed inputs or incomplete bundles fail without overwriting.
Only reviewed spans may change, with unchanged headings, order and outside bytes.
Bootstrap copies origin bytes; no-impact preserves the body and requires a reason.
All pending sections of a document require review together, preventing a partial
review from refreshing other stale sections. Proposals alone select no document.
The complete bundle is limited to 12 MiB. No project code or LLM is executed.

## Declared acceptance and lineage (P05)

`knowledge accept ROOT --proposal BUNDLE --review REVIEW --session UUID --key KEY
--confirm-reviewed --apply` preserves the supplied worktree review under
`docs/living/reviews/<session>/<sha256(key)>.json` before appending a
`knowledge.accepted` event through the existing v1 writer. Reviewer kind and ID,
reason and review reference are required declarations, never authentication or
semantic certification. Changed, removed or retyped old constraints require
specific old section IDs, reasons and references to explicit authorization.

The persistent global knowledge lock is acquired before the history session lock.
Expected analysis parents must also match live history. Identical session/key
retries return the original event before new freshness checks, even after source
changes; current freshness is separate. Changed inputs with that key fail.
Unknown write outcomes preserve bytes, events, reviews and incomplete artifacts.
A post-append source change returns `recorded_but_stale` (code 1), retaining the
event. Locks coordinate participating publishers, not external editors.

Sources, map and snapshot always use the origin evidence view. Explicit publisher
control inputs and live acceptance state use safe worktree paths; an index-origin
publication does not stage anything. Index readers cannot see newly published
artifacts/events until the operator stages them. No reader falls back to local
artifacts. All accepted ancestors' bundle, review, map and baseline references are
hash-verified. History reads have a 64 MiB aggregate budget and 4096 acceptance
limit; overflow fails explicitly.

Selection is a parent DAG, never a timestamp winner. Resolve requires all heads,
a complete candidate, all managed sections and a reason. The smallest event ID
is only a conflict comparison anchor. Unmanaged gaps must agree across parents.
A map transition uses a new preserved map path, revise/resolve, a reason and the
exact old/new section union. Structure may change only on this full-review path;
old constraints remain protected and retirement of accepted documents fails.


## Bounded current context (P06)

`knowledge read ROOT --map MAP --document ID --view worktree --json` returns
only a unique accepted revision whose selected-view evidence still matches.
`--section ID` returns the section and transitive declared dependencies, including
other documents, with original claim kinds. Without either selector it returns
only a state index. Stale, unaccepted and conflicting selections expose metadata
and investigation paths, never active bodies. No timestamp chooses a winner.
JSON and Markdown are limited to 32 KiB; document/section reads allow at most
8 managed sections. Limits fail explicitly. Export uses `--output NEW --apply`,
with an existing parent and no overwrite. No automatic staging or integration
into project instructions occurs.

Selected freshness verifies the newly selected body, ignores its own old parent
and base hash, and compares actual transitive external document dependencies by
content hash. External dependencies must have unique accepted selections; equal
content reacceptance does not create drift. The complete sealed raw-source set
remains conservative. New snapshot paths do not erase prior review debt, and old
referenced baseline hashes are always checked. A changed map needs an explicit
full-document transition; an old accepted body cannot inherit the new map's
freshness. `semantic_review` remains `not_attested` throughout.


## Optional legacy gates

`status ROOT --snapshot PATH --knowledge-map links.json --json` adds worktree
review obligations. Legacy snapshot paths remain relative to the process cwd;
opt-in knowledge requires the resolved snapshot inside ROOT and reports
`unsupported_external_snapshot` (exit 3) otherwise. Without the flag, the
legacy contract is unchanged.

`index-check ROOT --snapshot snapshot.json --knowledge-map links.json --json`
uses the same pinned Git/index view as the byte gate, honors `--history`, and
requires all referenced artifacts staged. It preserves HEAD acceptance history
and all ancestor maps, baselines, bundles and reviews by path, bytes and mode.
No local artifact fallback, staging, hook installation or publisher is invoked.
Knowledge cannot clear an existing byte, coverage, history or facts failure.
Unflagged gates make no claim to evaluate knowledge. These cooperative checks
do not protect against bypass or direct filesystem writes.

## Validation and operational limits

The map ceiling is 1 MiB, 64 documents, 512 sections and 2048 edges; Markdown
1 MiB; evidence/report 8 MiB; proposal bundle 12 MiB; raw source 64 MiB; JSON
depth 64. Index source reads perform a size check before bounded allocation;
this is not streaming Git blob hashing. Current reads permit 32 KiB and eight
sections including dependencies. Existing history permits 1 MiB per event and
64 MiB per segment. Knowledge readers additionally cap aggregate history at
64 MiB and 4096 acceptance records, and explicit history directories at 16384
entries. History enumeration is nonrecursive and independent of discovery ignore
rules in worktree view; index view remains strictly staged-only. Every overflow
fails explicitly, with no silent truncation.

There is no watcher, database or LLM client. Approval records carry a declared
identity, not authentication or a semantic attestation. Preserve old maps,
baselines, proposals, source fingerprints, history and reviews; use new paths
for every new baseline and proposal. A snapshot refresh never clears review debt.

`validation/pilot_knowledge.py --repo PREPARED_FIXTURE --kit KIT --output NEW`
runs a separate documentary host pilot. It requires the prepared diagram-library
fixture and existing dependencies, copies them to a disjoint output, and runs
native lint/build/tests before and after there. It does not install anything or
repair the two known starter-test failures. It curates exact headings, records
reasoned inventory-only dispositions and partial coverage, preserves the original
prose, and exercises stale rejection, fresh agent review, acceptance and new-process
reads. All command bodies and native logs stay in files. `pilot.json` identifies
successful intervention JSON/Markdown exports. A failed attempt remains evidence,
not a passing pilot. Unit tests do not run this host-specific workflow.

`validation/verify_distribution.py --base BASE --candidate KIT --patches PATCH_DIR
--output NEW --python PYTHON --graphify` is a separate post-packaging validator.
PATCH_DIR contains `series.json` and eight ordered patches; its parent contains
the combined patch and versioned ZIP. The validator applies both forms in separate
copies, compares content and executable modes, safely extracts the ZIP, verifies
the complete manifest and runs the full extracted suite. Inputs/output must be
disjoint. Python defaults to the current interpreter; timeout defaults to 1200
seconds. It never packages a release or installs dependencies. Unittest never
invokes this validator recursively. A timeout is failure, never a partial pass.
The parent packages only after independent final review; distribution verification
and the separate fresh-context study must be reported as pending until actually run.
See [the protocol](validation/context_eval_protocol.md) and
[authoring examples](examples/knowledge/README.md).


## Final review corrections (PC-001 / PC-002)

A well-formed proposal whose sources or expected parent selection have changed
requires a new review: `knowledge propose` and `knowledge accept` return code 1
and a `review_required` report with a stable `reason_code`. Schema errors,
corrupted or missing referenced artifacts, permission failures, unsafe paths and
unstable reads remain code 3. Generic exceptions are not classified by matching
message text. A rejected stale proposal does not append an acceptance.

Current JSON and Markdown expose `origin` and `relative_link_base` for each
selected document. Relative links in the preserved Markdown are interpreted
from that original document directory, relative to repository ROOT, not from
the immutable revision directory or the chosen export location. Bodies and
historical links are never rewritten to make an export relocate itself.

Read coverage includes every explicit `inventory_only` path with its reason.
Those entries have inventory tracking, not semantic review; they are not
silently promoted to section dependencies. An explicit disposition is not an
unresolved obligation. Truly unmapped sections/sources, or other unresolved
mapped documents, produce exit 1 even when an explicitly selected document is
fresh and its body is returned. Selection/freshness, global coverage, and
semantic attestation remain separate. All metadata counts toward the existing
32 KiB output limit; overflow is an explicit error, not silent truncation.


## Goldfish re-audit safeguards

Changing the prose of a supporting section now requires an explicit review of
its transitive consumers in the same document even when source files have not
changed. This validation runs at both proposal creation and acceptance; unchanged
consumer prose may remain byte-identical after its explicit review. Unrelated
sections are not forced into the edit scope. A declared review still does not
prove semantic correctness.

The opt-in index gate applies source (64 MiB), document (1 MiB), and snapshot
(8 MiB) limits before its initial blob-content requests. Unflagged legacy calls
retain their prior behavior. Explicit history enumeration stops on entry 16,385
rather than materializing an unlimited directory before checking its limit.
A missing Portalocker dependency produces operational code 3 before publisher
storage is created, not a Python traceback misclassified as review-needed.

The host pilot records the actual before-test baseline. The two historical
starter failures are reference information, not a required failure state. It
reports resolved historical failures, different preexisting failures, and any
new failures relative to that observed baseline. A new regression or an
incoherent/failed runner stops the pilot; host tests are never repaired here.
