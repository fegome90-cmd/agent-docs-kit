Both native readers completed; `list_agents` retained both completed reports.

- `/root/reader_a`: general-purpose `default`, `fork_turns:"none"`.
- `/root/reader_b`: general-purpose `default`, `fork_turns:"none"`.

Combined findings: **2 Major, 3 Nit**. Runtime validation: **NOT_RUN**. The parent read only the two protocol documents and coordinated the readers; it did not review or modify the product. Host instruction exclusion and per-child filesystem allowlists remain unverified. This single round does **not** establish formal EGM convergence.

The native reports follow unabridged.

### Reader A — `/root/reader_a`

Static audit completed. Two findings; runtime checks **NOT_RUN**.

1. **Major — same-document dependency text changes bypass dependent review.**  
   `stages/P08-fixed/doc_revisions.py:180–185`, together with `stages/P08-fixed/doc_impact.py:69–72`.

   Causal input: adopt sections A and B where B depends on A; keep source bytes unchanged; submit `revise` changing A’s prose with `reviewed_sections=[A]`. Impact is calculated against the pre-candidate envelope, and dependency document comparison only handles different document owners. Nothing requires B’s review after its declared supporting section changes. The bundle can therefore refresh the complete document and serve B as current despite its changed documentary basis.

   This conflicts with the declared section dependency rule that a change in the supporting section activates its consumers. Source-change propagation works; this is the distinct candidate-prose path.

   Minimal correction: compare candidate versus accepted section bytes, propagate changed section IDs through reverse dependencies, and require affected sections in `reviewed_sections` at both proposal and acceptance. Preserve existing unrelated-span restrictions. Add the same-document A→B case with unchanged sources.

2. **Nit — unavailable locking dependency escapes the operational-error contract.**  
   `stages/P08-fixed/doc_revisions.py:79`; CLI handler at `stages/P08-fixed/agent_docs_kit.py:518`.

   Causal input: invoke a publisher through ordinary Python without Portalocker installed. `knowledge_lock` imports Portalocker without translating `ImportError`; the outer handler excludes that exception. Instead of the specified operational error/code 3, the command terminates with a traceback and Python’s default code 1. It may already have created the history directory at line 77.

   Minimal correction: reuse `history.append_event`’s explicit dependency-error translation (`history.py:361–364`), preferably before creating storage. Test the CLI error classification.

Inspected all eleven requested production modules in full, plus `tests/knowledge_support.py`, all six `test_doc_*.py` files, `test_knowledge_gate.py`, `test_knowledge_e2e.py`, `test_knowledge_controller_regressions.py`, `test_knowledge_gate_b_regressions.py`, and `test_final_reaudit.py`. Read the supplied persona, standard, and all four common authorities before conclusions. Initial truncated reads were followed by bounded reads covering the omitted authority/production/focused-test regions.

All eleven production-file SHA-256 values matched before and after inspection. Focused test files were read but not independently hashed before/after. Test source establishes intended coverage, not passing execution.

No imports, product execution, tests, writes, network, memory, original repositories, candidate tree, or review reports were used. Native identity: `/root/reader_a`; model/build identity unavailable. Host-instruction exclusion and filesystem allowlist isolation were not independently verified.

### Reader B — `/root/reader_b`

Static audit found **one Major and two Nits; no demonstrated Blocker**. Runtime validation is **NOT_RUN**.

**Major B-001 — Opt-in index gate materializes oversized sources before the knowledge size guard.**

Affected: `stages/P08-fixed/git_gate.py:235–238,410–411,427–432`; documented guarantee at `KNOWLEDGE.md:67–69,168–169`.

`check_index(..., knowledge_map=...)` first runs `_byte_evidence`, whose source fingerprint calls `git.blob(entries[path])` without `maximum`. Only afterward does it construct the bounded knowledge reader. A staged source substantially exceeding 64 MiB can therefore be fully captured in subprocess memory before the knowledge layer rejects it. The documented preallocation ceiling does not hold for this end-to-end entry point.

Reproduction shape: take a valid snapshot covering a source directory, stage an oversized replacement or new descendant, then invoke `index-check --knowledge-map ...`. Static call order proves the unbounded blob call precedes knowledge validation; memory behavior was not executed.

Minimal correction: add an optional bound to `_byte_evidence` and apply the knowledge source ceiling when `knowledge_map` is supplied, preserving unflagged legacy behavior. Add a regression proving the bounded size check occurs before the blob-content request on this public entry point.

**Nit B-002 — Explicit history entry ceiling is checked after unrestricted enumeration.**

Affected: `stages/P08-fixed/doc_evidence.py:128–130`; documented ceiling at `KNOWLEDGE.md:171–175`.

`entries = list(iterator)` retains the whole directory before checking the 16,384-entry limit. A directory containing millions of unrelated files consumes memory and completes traversal before the intended bounded failure.

Reproduction shape: knowledge check/read against a history directory exceeding that ceiling, including files without `.jsonl` suffixes.

Minimal correction: consume `scandir` incrementally, abort immediately at entry 16,385, then sort/process only the bounded collection. Keep all current validation and instability checks.

**Nit B-003 — Host pilot requires historical failures instead of accommodating an improved baseline.**

Affected: `stages/P08-fixed/validation/pilot_knowledge.py:152–154`.

The pilot saves actual native outcomes, but refuses to continue unless the tests fail with exactly the two historical starter failures. The approved P08 plan explicitly allows their state to change and requires reporting the newly observed baseline. A prepared fixture where those tests now pass cannot complete the documentary pilot.

Minimal correction: establish and record the actual before baseline, then compare after outcomes against that baseline; distinguish resolved historical failures and new failures explicitly. Retain logs and never repair host tests.

Positive static observations:

- Selected bodies are withheld for stale, unaccepted and conflicting selections; metadata distinguishes selection from freshness.
- Original document paths and relative-link bases remain visible; preserved bodies are not rewritten.
- Partial coverage and inventory-only dispositions remain separate from semantic attestation.
- Test sources contain real subprocess CLI journeys, Git branch merging and alternate-index use.
- Host pilot and distribution verifier are standalone; the inspected unit-test sources do not recursively invoke them.
- Conservative hashes, immutable provenance and append-only history checks remain present.

Complete reading list:

- Authorities, read before conclusions: `egm/agents/design_critic.md`, `egm/resources/document_standards/feature-spec-standard.md`, `inputs/design.md`, `inputs/plan.md`, `inputs/implementation-rulings.md`, `inputs/gate-a-review-resolution.md`.
- Full target production files: `agent_docs_kit.py`, `living_docs.py`, `git_gate.py`, `history.py`, `discovery.py`, `doc_links.py`, `doc_evidence.py`, `doc_impact.py`, `doc_revisions.py`, `doc_current.py`, `knowledge_cli.py`.
- Full target documentation/tests: `KNOWLEDGE.md`, `tests/test_final_reaudit.py`, `tests/test_knowledge_e2e.py`, `tests/test_knowledge_gate.py`, `tests/knowledge_support.py`.
- Full `validation/*.py`: `pilot_knowledge.py`, `verify_distribution.py`.

All target paths above are beneath `stages/P08-fixed/`. Required reading is complete. Initially truncated authority output was supplemented with bounded reads before conclusions.

Limits and scope:

- Static shell text inspection and hashing only. No product imports, executions, tests, writes, artifacts, network, memory or connectors.
- Before/after SHA-256 values matched for all 18 requested target files.
- Minor scope deviation: the initial `*.py` hashing glob additionally hashed `stages/P08-fixed/facts.py`; its contents were not read.
- Other tests, distribution artifacts, host contents and evaluation protocol were outside this inspection. Their execution or correctness is not established.
- Native identity known here: Reader B, `/root/reader_b`. No native review receipt or approval identity was supplied.
- Host instruction exclusion and per-child filesystem allowlists remain unverified. This report does not establish isolation.
- Test source demonstrates intended checks, **not passing results**. Parent reports were not used as evidence.