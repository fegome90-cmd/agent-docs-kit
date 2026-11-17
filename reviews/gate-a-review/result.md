Both native reviewers **completed** and returned reports. Neither report is inferred from a timeout. Findings below are their static audit results; I did not independently adjudicate them.

| Reviewer | Actual native identity | Context | Audit | Tests/code execution |
|---|---|---|---|---|
| A | `/root/reviewer_a` | `default`, `fork_turns:"none"` | completed | not_run |
| B | `/root/reviewer_b` | `default`, `fork_turns:"none"` | completed | not_run |

Both received standalone assignments without conversation history or parent findings. No model override was supplied; authoritative model identities were not returned. Conversation separation does **not** establish isolation from host instructions or a filesystem allowlist.

## Reviewer A — full report

Read-only audit completed for frozen `stages/P03`. Five findings require correction before Gate A can be considered satisfied.

**A-001 — Major — Required wire field silently synthesized**

Location: `stages/P03/doc_links.py:114`

`parse_links` inserts `inventory_only: []` when the field is missing. Design §9 requires that field, and IR-007 explicitly prohibits treating its omission as authoring convenience at the wire boundary.

Reproduction shape: serialize an otherwise valid links map without `inventory_only`. The parser adds the field and accepts the object instead of rejecting the incomplete schema.

Minimal correction: remove the insertion. If an authoring helper supplies defaults, keep that separate from strict wire parsing.

**A-002 — Major — Unicode separators create false Markdown section boundaries**

Location: `stages/P03/doc_links.py:149`

`text.splitlines(keepends=True)` recognizes separators beyond LF, including U+0085, U+2028 and U+2029. The contract requires exact UTF-8/LF section boundaries. A heading following one of these characters inside an LF-delimited line becomes a separate parser line and can terminate or select a span.

Reproduction shape: document bytes equivalent to `## A\ntext\u2028## B\nbody\n`, with `## A` selected. The implementation recognizes `## B` as another heading, although it does not begin after an LF. Consequently, the selected raw-byte span ends early.

Minimal correction: iterate exclusively over LF-delimited lines while retaining their exact byte offsets. Add fixtures containing non-LF Unicode separators before heading-like text.

**A-003 — Blocker — Revalidation cannot represent accepted bases and heads**

Location: `stages/P03/doc_evidence.py:252–259`

`revalidate` reconstructs every base from its mutable `origin`, then calls `capture_evidence` with empty heads for every document. Proposal revalidation compares those synthetic empty heads and bootstrap bases against the supplied envelope.

Reproduction shape: supply valid evidence for a previously accepted document with a nonempty expected-head list and an accepted immutable document differing from its original adoption file. Calling `revalidate(..., phase='proposal')` reports `heads_changed` and/or `base_documents_changed` even when the actual accepted selection and reviewed bytes remain unchanged.

Deferred resolver wiring at P06 is expected, but this prerequisite API has no parameter or resolution mechanism through which callers can supply the real current accepted bases and heads.

Minimal correction: provide a usable, explicit mechanism for revalidation to obtain selected-view accepted bases and current heads. Preserve bootstrap handling separately; do not substitute empty history for an accepted state.

**A-004 — Major — Selected freshness checks unrelated documents and event identities**

Location: `stages/P03/doc_evidence.py:269–274`

Selected revalidation compares every other envelope document, including its heads and complete `BaseDocument` record. IR-007 requires only actual transitive external section dependencies, compared by document content hash; unrelated documents and content-equal replacement acceptances must not invalidate freshness.

Reproduction shape: map independent documents A and B, with no A→B section dependency. Seal evidence containing both, then change B while A and A’s obligations remain identical. Selected freshness for A reports `dependency_document_changed`. A nonempty recorded head for B also differs from the synthetic empty heads constructed at line 254.

Minimal correction: derive A’s transitive external dependency document set from section relationships. Resolve those dependencies uniquely in the selected view and compare their content hashes, excluding unrelated documents and acceptance-event identity changes.

**A-005 — Major — Evidence reads do not consistently enforce bounded memory**

Locations: `stages/P03/doc_evidence.py:59`, `stages/P03/doc_evidence.py:68–72`, `stages/P03/git_gate.py:113–121`, `stages/P03/living_docs.py:122–126`

Index fingerprints call `_Git.blob` without a maximum. That method captures the entire blob in memory, so fingerprinting a large binary is not chunk bounded.

Worktree bounded reads check file size before calling `_fingerprint`, but `_fingerprint` accumulates every chunk without enforcing the requested maximum during reading. A file growing after the preliminary size check can exceed the memory bound before the eventual size/race rejection.

Reproduction shapes:

- Select a very large staged source and capture index evidence: the full blob is materialized solely to compute size/hash.
- Grow a worktree file after the preliminary size check: reading continues and accumulates data past `maximum`, even though the operation ultimately fails.

Minimal correction: stream index fingerprints in bounded chunks. For bounded worktree content reads, enforce the maximum inside the read loop and stop immediately once exceeded.

Audit status: **completed** through static file inspection.  
Tests, imports, project-code execution, network and mutations: **not_run** as required.  
Legacy compatibility execution: **not_run**; no runtime compatibility verdict is claimed.

This report covers P01–P03 prerequisites only and is **not final release approval**.

## Reviewer B — full report

Audit: **completed** for the P01–P03 contracts and relevant frozen implementation/test paths. Tests and execution: **not_run**, as required. No files were changed. This round is **not final release approval**.

**B-001 — Major — Required wire field is silently synthesized**

Affected: `stages/P03/doc_links.py:114`.

IR-007 requires `inventory_only` at the wire level, including `[]` when empty. `parse_links` adds it when absent, so a nonconforming serialized map passes validation.

Reproduction shape: serialize an otherwise valid fixture after deleting `inventory_only`; `parse_links` restores the missing field and accepts it. `tests/test_doc_links.py:76` checks other missing/unknown schema cases but omits this required-field case.

Minimal correction: remove the default insertion from the wire parser. If authoring convenience is desired, implement it before serialization and require the parser to reject omission.

**B-002 — Major — Revalidation cannot represent reviewed accepted bases or applicable parent heads**

Affected: `stages/P03/doc_evidence.py:252–259`; `stages/P03/doc_evidence.py:269–274`.

`revalidate` reconstructs every base from its mutable `origin` and hardcodes every current head list to `[]`. Consequently:

- Proposal evidence containing a legitimate nonempty parent head necessarily becomes stale through `heads_changed`, even when that parent remains current.
- Selected freshness compares every other mapped document, including unrelated documents, and compares parent IDs rather than only relevant dependency content.
- Accepted immutable revisions cannot be supplied as current base documents through this API.

This contradicts IR-007: proposal applicability includes actual expected heads/base documents; selected freshness requires only actual transitive dependency obligations, content comparison, and unique external selections. P06 resolver wiring is deferred, but this prerequisite API provides no parameter through which that resolver can supply those facts.

Reproduction shape: capture valid evidence with a nonempty head via `capture_evidence`; call proposal revalidation without changing anything. The reconstructed empty heads differ. Alternatively, add an unrelated mapped document with a nonempty captured head: selected revalidation reports `dependency_heads_changed` despite no dependency edge or content change.

The latter incorrect behavior is explicitly required by `stages/P03/tests/test_doc_impact.py:156–176`, rather than detected as a regression.

Minimal correction: let revalidation receive verified resolved bases and selections/heads. Derive external obligations from the selected document’s transitive section dependencies; compare their content hashes and require unique selection. Add unchanged-parent applicability and unrelated content-equal acceptance fixtures.

**B-003 — Major — Unicode line separators can create nonexistent ATX anchors**

Affected: `stages/P03/doc_links.py:149–161`.

`str.splitlines(keepends=True)` recognizes separators beyond LF, including vertical tab and Unicode line separator. The parser then treats the following text as a fresh Markdown line, although the contract specifies exact UTF-8/LF spans.

Reproduction shape: a document containing `prefix\v## Catalog\nbody\n` can produce a selectable `## Catalog` heading after the vertical tab. There is no LF boundary before that heading. The same issue can introduce false terminating headings, changing authorized edit spans.

Existing raw-byte tests cover CRLF rejection and multibyte characters, but not non-LF separators.

Minimal correction: split exclusively on `\n`, preserving byte offsets and final-line behavior. Add fixtures proving vertical tab and Unicode separators do not introduce anchors or terminate spans.

**B-004 — Major — Index source fingerprinting materializes unbounded blobs**

Affected: `stages/P03/doc_evidence.py:59–60`, `stages/P03/doc_evidence.py:86–88`; underlying reader `stages/P03/git_gate.py:113–121`.

Worktree fingerprints use chunked hashing, but index fingerprints call `_git.blob(entry, maximum=None)`. That reader captures the entire blob in memory. `capture_evidence` invokes this for every sealed source, and `assert_unchanged` reads them again. The 8 MiB envelope limit constrains serialized metadata, not source blob allocation.

Reproduction shape: stage a very large watched binary source. Index capture reads the entire binary through `capture_output=True` before hashing; it has no blob-size ceiling or streaming fingerprint path. A small evidence envelope does not prevent excessive memory consumption.

This misses P02’s explicit bounded-chunk source hashing requirement and R14’s bounded processing contract.

Minimal correction: provide a streaming index fingerprint operation with bounded buffering, or enforce a documented finite source-size limit before allocation. Keep bounded `read` for prose/control inputs. Add a test that verifies the fingerprint path does not request an unlimited materialized blob.

**B-005 — Major — Snapshot-refresh test never evaluates the refreshed snapshot**

Affected: `stages/P03/tests/test_doc_evidence.py:134–143`.

The test changes `snapshot.json` only in the worktree, then revalidates using the index view. Because the fixture staged its original snapshot, the revalidator reads the unchanged original snapshot. `matches_review` therefore does not establish that refreshed snapshot metadata preserves content obligations. The changed evidence ID is already explained by switching from worktree to index.

This leaves an advertised P02 freshness property unproven and would not catch a revalidator that incorrectly rejects refreshed staged snapshot metadata.

Minimal correction: exercise the changed snapshot in the worktree view, then separately stage the refreshed snapshot and exercise the index view. Assert unchanged content remains fresh and a source change remains stale after snapshot refresh. Keep cross-view identity as a separate assertion.

No finding above depends on implementing P04–P07 publishing, conflict resolution, or accepted-envelope wiring early.