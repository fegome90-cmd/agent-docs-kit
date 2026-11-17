Both native reviews **completed**. Findings are returned for Elephant adjudication; this single round establishes no convergence.

| Child identity returned by native tool | Dispatch | Status |
|---|---|---|
| `/root/readiness_review` | `agent_type: "default"`, `fork_turns: "none"` | Completed |
| `/root/integration_review` | `agent_type: "default"`, `fork_turns: "none"` | Completed |

These canonical identities were the actual identifiers exposed by `spawn_agent`; no separate opaque IDs were returned. Native waits and `list_agents` confirmed completion.

**Child 1 — Implementation readiness report**

Seven actionable findings. No tests or implementation were performed.

**IR-001 — Blocker: partial review can refresh unreviewed sections**

Sources: `inputs/design.md:89–98,114–128,235–243`; `inputs/plan.md:158–167,180–186,240–242`.

Evidence seals a source set, while `reviewed_sections` can name only some sections. The selected document then acquires the new evidence, but no rule specifies how unreviewed sections retain their previous review obligations. If two sections become stale and only one is reviewed, implementations could refresh both by accepting the new document-wide evidence.

Minimal fix: either require `reviewed_sections` to cover every affected section in the document before accepting, or define per-section retained evidence and freshness. Add a test where two sections change and only one is reviewed; the other must remain pending.

**IR-002 — Blocker: map revisions lack an executable transition**

Sources: `inputs/design.md:59–81,114–128,239`; `inputs/plan.md:184–186,240–243`; `inputs/acceptance-cases.tsv:49`.

Renaming sections or changing structure requires a new map and a complete explicit review. However, existing proposal rules preserve headings, section order and all bytes outside authorized spans. No mode or request field identifies the old/new map transition, defines how old spans map to new ones, or authorizes structural changes. The existing test only checks that changing the map invalidates freshness.

Minimal fix: define the map-transition workflow and its authorization boundary: old/new map references, complete-document review requirement, permitted structural edits and baseline selection. Add a successful structural migration test and a rejection test for an incomplete review.

**IR-003 — Major: resolve has multiple parents but no defined editing baseline**

Sources: `inputs/design.md:114–128,148–150,217–223`; `inputs/plan.md:180–186,203–221`; `inputs/acceptance-cases.tsv:38`.

A resolution must name every conflicting head, but outside-span preservation and `BaseDocument.sha256` assume one document baseline. When conflicting parents contain different documents, the implementer must choose which parent supplies unchanged bytes, or invent a merge policy. That choice determines whether a resolution is accepted.

Minimal fix: specify an explicit baseline parent for `resolve`, or define full-document resolution authorization. State what must match across all parent evidence and what is reviewed anew. Test conflicting parents that differ outside the requested section.

**IR-004 — Major: index-view proposal and acceptance inputs are underspecified**

Sources: `inputs/design.md:29,118–124,132–140,160–169`; `inputs/plan.md:134–143,180–188,203–221`.

All operation reads must use one view, and `propose`/`accept` inherit the evidence view. It is not specified whether request, candidate, proposal and review files must themselves be staged for an index-view operation. A newly written proposal is absent from the index until staging, and `accept` also needs the live acceptance history under its global lock.

Minimal fix: enumerate which inputs come from the selected view and which are explicit control inputs or live publisher state. Define the required staging sequence for index-origin proposals and how concurrent live acceptances participate in parent checks. Add an index-view propose→accept test with divergent local/staged inputs.

**IR-005 — Major: published-event preservation is not specified by P07**

Sources: `inputs/design.md:26,136–144`; `inputs/plan.md:263–267`; `inputs/acceptance-cases.tsv:53`.

R01 preserves published snapshot, event and revision bytes. P07 explicitly protects referenced revision/review artifacts against HEAD, but does not specify preservation of previously published acceptance events. Rewriting or removing an event can alter the DAG while leaving its revision files untouched.

Minimal fix: specify preservation of previously published event bytes, including the permitted append behavior for existing JSONL segments. Include tests for deleting an old acceptance, modifying its parents and truncating a published segment. Clarify whether existing snapshot preservation already covers R01 or requires an additional check.

**IR-006 — Major: exact write allowlist is missing for P08**

Sources: `egm/resources/document_standards/implementation-readiness-standard.md:29–33`; `inputs/plan.md:274–283,293–296`.

P08 names an unspecified “example” of map/requests/review, “the two original modules,” version assertions without paths, and release outputs without exact destinations. This cannot form the required explicit file allowlist.

Minimal fix: enumerate the exact repository-relative source, fixture and documentation paths; identify both version-bearing modules and affected version tests. Give generated distribution outputs a bounded output directory/glob.

**IR-007 — Major: strict shared schemas remain ambiguous**

Sources: `inputs/design.md:73,210–239`; `inputs/plan.md:80,137–143,203–206`.

Validators reject unknown keys, yet `inventory_only` is optional in §4.1 and required in §9. `reviewed_inputs`, essential to freshness, has no declared schema or precise derivation. `AcceptancePayload.revision` also lacks an explicit target definition, while adjacent reference types do not map unambiguously to individual fields.

Minimal fix: reconcile optional versus required fields and define the exact types and reference targets for the shared objects. Specify whether `reviewed_inputs` is persisted or derived, and the deterministic projection used for applicability across views. Add valid minimal fixtures and rejection cases shared across P01–P07.

Read completeness: all five authorized files were fully read, persona first. The superseded planning-only status was not treated as a finding.

**Child 2 — Codebase integration report**

**CB-001 — Major: opt-in `status` lacks a defined adapter for legacy snapshot paths**

Sources:

- `inputs/plan.md:158–159,258–261`
- `inputs/design.md:167–171`
- `baseline/living_docs.py:474–478`
- `baseline/agent_docs_kit.py:364–366,458–461`

Existing `status` resolves `snapshot_path` against the process working directory and permits a snapshot outside ROOT. P07 preserves that signature and promises unchanged snapshot resolution, but its proposed `check_knowledge` consumes a root-relative string path through `EvidenceView`. No conversion or external-snapshot handling is specified. Passing the legacy argument unchanged can select different bytes; converting every argument to ROOT-relative form cannot represent supported external snapshots.

Minimal fix: specify the adapter explicitly. Preserve legacy resolution and pass the already-read, validated snapshot plus its byte hash into the knowledge evaluator, with a defined provenance representation for an external snapshot. Alternatively, explicitly document and test an opt-in restriction for external snapshots, narrowing the existing compatibility promise. Include regression cases with CWD different from ROOT and an external snapshot.

No additional actionable integration findings.

Read completeness: all ten listed files were fully read, persona first. Truncated combined output was subsequently covered through bounded reads.

**Observed isolation limits**

The parent read only `egm/docs/codex.md` and `egm/docs/harnesses.md`. Both children reported reading only their assigned files, with no tests, edits, network, memories, connectors, credentials, or further children.

The runtime exposes a read-only filesystem sandbox. The narrower reading lists were instruction constraints, not demonstrated filesystem allowlists. `fork_turns: "none"` was explicitly supplied; exclusion of host instruction files was not independently verified. No parent-marker isolation probe was performed.