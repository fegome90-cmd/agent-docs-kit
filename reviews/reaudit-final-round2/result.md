Both native readers completed. `list_agents` confirmed and retained their actual final reports.

| Reader | Native identifier | Agent type | Fork setting |
|---|---|---|---|
| A | `/root/reader_a` | `default` (general-purpose) | `fork_turns:"none"` |
| B | `/root/reader_b` | `default` (general-purpose) | `fork_turns:"none"` |

Their reports contain **three distinct Major findings**, including one independently reported by both readers. No Blocker was reported. Runtime verification is **NOT_RUN**. I did not inspect the product, implement changes, access memory/connectors/network, or write artifacts.

Reader B disclosed authority-reading order recovery and extra hash-only scope. Host-instruction exclusion and per-child filesystem allowlists remain unverified. This round does **not** establish formal EGM convergence.

The unabridged native reports follow.

---

### Reader A — `/root/reader_a`

Read-only static audit completed. I fully read both mandated role/standard documents, followed by all four design/ruling inputs. Runtime: **NOT_RUN**; no product imports, commands, or tests were executed.

**Major A-001 — Opt-in worktree status bypasses knowledge read limits**

- Location: `stages/P08-reaudited/living_docs.py:489`, `:503`, `:507`; unbounded helper calls at `:175`, `:197`, `:468`.
- Causal input: use `status ROOT --snapshot SNAPSHOT --knowledge-map MAP` with a directory source scope, then add an oversized source under that directory. Alternatively supply an oversized document or a valid snapshot padded beyond the 8 MiB evidence limit.
- Reasoning: the preliminary `reader.fingerprint()` uses the generic 64 MiB source ceiling and covers only baseline records. The subsequent legacy `_load_manifest`, `_collect_sources`, and `_collect_documents` paths call `_fingerprint` without `maximum_bytes`. New directory descendants bypass preliminary fingerprinting entirely. Knowledge validation may reject later, but only after the oversized input has already been read; a growing file can keep the legacy read loop running without the knowledge ceiling.
- Contract: R14 and the accepted bounded-read correction require rejection during reading, before retaining oversized content. P07 already supplies analogous caps to the index gate.
- Minimal correction: add backward-compatible optional limits to these helpers and pass the snapshot, document, and source limits when `knowledge_map` is supplied. Preserve unlimited legacy defaults for unflagged commands. Cover a newly added directory descendant and oversized snapshot/document through the public opt-in status path.

**Major A-002 — Index knowledge readers interpret an invalid history root as empty history**

- Location: `stages/P08-reaudited/doc_evidence.py:146–147`, through `:95–98`, and `stages/P08-reaudited/doc_revisions.py:311–312`.
- Causal input: stage an ordinary file at `docs/living/history`, with valid staged map, snapshot, origins, and sources; invoke `knowledge check ... --view index` or `knowledge read ... --view index`.
- Reasoning: index `history_paths()` delegates to generic `paths()`, which returns the history root file itself. `load_acceptances()` skips it because its name does not end in `.jsonl`, then resolves an empty acceptance set. The operation reports ordinary `unaccepted`/review-required status instead of rejecting malformed authoritative storage. Worktree readers reject this shape through `history._location`; `index-check` also rejects it separately.
- Contract: R13 distinguishes invalid storage/schema/reference inputs, code 3, from a valid corpus without acceptance, code 1.
- Minimal correction: make index `history_paths()` reject an exact file entry at the requested directory path before enumeration. Test both public index knowledge commands, preserving staged and worktree bytes.

No Blocker or Nit findings established.

**Inspected paths**

All paths below are relative to `<workspace>/agent-docs-kit-build-v0.5.0-rc.1-egm`.

Required inputs, fully read:

- `egm/agents/codebase_details.md`
- `egm/resources/document_standards/re-implementation-standard.md`
- `inputs/design.md`
- `inputs/plan.md`
- `inputs/implementation-rulings.md`
- `inputs/gate-a-review-resolution.md`

Production, fully read under `stages/P08-reaudited/`:

- `agent_docs_kit.py`
- `living_docs.py`
- `git_gate.py`
- `history.py`
- `discovery.py`
- `doc_links.py`
- `doc_evidence.py`
- `doc_impact.py`
- `doc_revisions.py`
- `doc_current.py`
- `knowledge_cli.py`

Focused test source, fully read under `stages/P08-reaudited/tests/`:

- `knowledge_support.py`
- `test_doc_links.py`
- `test_doc_evidence.py`
- `test_doc_impact.py`
- `test_doc_proposals.py`
- `test_doc_acceptance.py`
- `test_doc_current.py`
- `test_knowledge_gate.py`
- `test_knowledge_controller_regressions.py`
- `test_knowledge_gate_b_regressions.py`
- `test_knowledge_e2e.py`
- `test_production_regressions.py`
- `test_egm_reaudit_regressions.py`
- `test_final_reaudit.py`

The tests directory was listed; other legacy test bodies and release-validation programs were not inspected. Test source is not evidence of passing execution.

**Preservation evidence**

SHA-256 was measured before and after production inspection; all eleven values matched:

```text
agent_docs_kit.py 57cafe59b073da6cadadfece018619fc7df926ac2007a72380d98fcaa9871551
living_docs.py   62b7c7cccc2074b28cff6ab8b51929a76417d308d31bf1e1cb191fd2d9a03f05
git_gate.py      a0b93268d9a682be5c7f0a61ca1b6c4cddb66990a6da0fd97b66e583f60a330f
history.py       62ceafd6315abba0dadb5c3f095b3b49d38da39768c5156387e5e61801bc6180
discovery.py     953612bb06e85f0d04afb53e59df5d0c3ca25ef71355d46164c05649ab734ac8
doc_links.py     6a8a0cd74d51412d774ee751b756b34d5e85bc3cbf9a54dd3625ff4ec96e0de1
doc_evidence.py  7ed4abf59f9925d4308fd60ea89002fd3833e2bbdab8d44bb2d53a40141ca928
doc_impact.py    02c029bc5ac47d79c3a57f9da2cde6b593b2628ffe06939ba15a8243df217434
doc_revisions.py baa5649f58e80aaa108eb9a277d6f1fce6fdb65d8761ac07271c23297b99159a
doc_current.py   d8bd921652e57feeaff44aa7c90608394da87902598bc6d9bf8abd016b0a3ef4
knowledge_cli.py 13efff741989556ea59097c14d3dfbe62052ff36f548dc3fe48f2c19004ffe7f
```

No before/after hash claim is made for inputs or tests. No files were written. No prohibited candidate tree, review reports, original repositories, credentials, memory, connectors, or network were accessed.

Native reader identity: `/root/reader_a`. Host-instruction exclusion and filesystem allowlisting were not independently verified. This is one static audit round, not formal EGM convergence.

---

### Reader B — `/root/reader_b`

Two **Major** findings; no Blocker identified by static inspection.

1. **Major — opt-in worktree status bypasses the new source-size bound.**  
   `stages/P08-reaudited/living_docs.py:491–503`, with uncapped calls at `:159`, `:177–179`.

   The initial bounded pass fingerprints only files already listed in the snapshot. `_collect_sources` then traverses current directory scopes using legacy `_fingerprint` without `maximum_bytes`. A new file larger than 64 MiB inside an observed directory therefore gets read completely before knowledge analysis reports it as unmapped. A growing newly discovered file also avoids the bounded-growth safeguard.

   **Reproduction shape:** capture a snapshot of `src/`, add `src/new.bin` exceeding 64 MiB, then invoke `status ROOT --snapshot SNAPSHOT --knowledge-map MAP`. The added file is absent from the bounded precheck but enters `_scan_directory`.

   **Minimal correction:** propagate optional limits through the legacy collection helpers for opt-in calls only, or reuse bounded fingerprints for every file they discover. Preserve unflagged behavior. Add a regression proving rejection before an oversized new source is fully consumed.

2. **Major — temporary Git fixtures inherit environment variables that can redirect mutations outside the fixture.**  
   `stages/P08-reaudited/tests/knowledge_support.py:20–25`, `:28–32`, `:57–69`.

   Both subprocess helpers inherit every ambient `GIT_*` variable. `git -C TEMP` does not override an absolute `GIT_DIR`, `GIT_WORK_TREE`, or `GIT_INDEX_FILE`. Consequently, fixture setup can initialize/configure an external repository or write an external index despite using `TemporaryDirectory`. The production Git reader and standalone validators already sanitize this environment.

   **Reproduction shape:** launch the suite with `GIT_DIR` pointing at a disposable external repository, or `GIT_INDEX_FILE` pointing at a disposable external index. `make_repo` executes mutating `init`, `config`, `add`, and `commit` through the inherited environment. With `GIT_DIR`, external configuration can change before later fixture setup fails.

   **Minimal correction:** remove ambient `GIT_*` variables in both helpers, then apply explicitly supplied `env` overrides so alternate-index tests remain possible. Add an isolated sentinel-repository/index regression.

Full reads completed:

- `egm/agents/design_critic.md`
- `egm/resources/document_standards/feature-spec-standard.md`
- `inputs/design.md`
- `inputs/plan.md`
- `inputs/implementation-rulings.md`
- `inputs/gate-a-review-resolution.md`
- All eleven assigned production modules
- `KNOWLEDGE.md`
- All five assigned test/helper files
- Both `validation/*.py` files

The role and standard were read first. Initial combined input-document output was truncated; bounded rereads recovered the missing portions before this conclusion, although some recovery occurred after production reading began. No assigned file remains partially read.

Static inspection supports the presence of exact-byte evidence, selected-view reads, lineage selection, explicit inventory-only disclosures, relative-link provenance, output bounds, real Git-index fixtures, and new-process CLI journeys. The pilot and distribution verifier are separate scripts. **Their source does not establish successful execution or independent semantic review.**

Runtime: **NOT_RUN**. No imports, product commands, tests, network, connectors, memory, original repositories, candidate tree, or other review reports were used. No files were changed or artifacts created. A broad input hash glob additionally hashed—but did not read the contents of—other `inputs/*.md`; the initial production hash glob similarly included `facts.py`.

SHA-256 values below matched before/after inspection for the assigned candidate files:

```text
agent_docs_kit.py 57cafe59b073da6cadadfece018619fc7df926ac2007a72380d98fcaa9871551
living_docs.py 62b7c7cccc2074b28cff6ab8b51929a76417d308d31bf1e1cb191fd2d9a03f05
git_gate.py a0b93268d9a682be5c7f0a61ca1b6c4cddb66990a6da0fd97b66e583f60a330f
history.py 62ceafd6315abba0dadb5c3f095b3b49d38da39768c5156387e5e61801bc6180
discovery.py 953612bb06e85f0d04afb53e59df5d0c3ca25ef71355d46164c05649ab734ac8
doc_links.py 6a8a0cd74d51412d774ee751b756b34d5e85bc3cbf9a54dd3625ff4ec96e0de1
doc_evidence.py 7ed4abf59f9925d4308fd60ea89002fd3833e2bbdab8d44bb2d53a40141ca928
doc_impact.py 02c029bc5ac47d79c3a57f9da2cde6b593b2628ffe06939ba15a8243df217434
doc_revisions.py baa5649f58e80aaa108eb9a277d6f1fce6fdb65d8761ac07271c23297b99159a
doc_current.py d8bd921652e57feeaff44aa7c90608394da87902598bc6d9bf8abd016b0a3ef4
knowledge_cli.py 13efff741989556ea59097c14d3dfbe62052ff36f548dc3fe48f2c19004ffe7f
KNOWLEDGE.md 989fa817b0c6e5ce7d207e4e03332d5056507a8c1e5dda7a29a135e29cdb502f
tests/test_final_reaudit.py 393577b12e7776c035912f6fa5eb60faa4cdd211f168cc02996166ee3c2b8710
tests/test_egm_reaudit_regressions.py 9daa7df3c511bf471644deddf74343d948091a616e44a2022397722d4123623f
tests/test_knowledge_e2e.py b144047987f152ec6c04ed59a54d2c7d61f7bcf38c81394a9e96bf8765c181ce
tests/test_knowledge_gate.py 7ca6ce27ecaa7177e20b13da1b377ab6e460f6a31f4f353d6575cf2372080c0b
tests/knowledge_support.py 7ca6732a933c0726dcf5f4157275ecdde5b188011887e7d76228886eacd20a5a
validation/pilot_knowledge.py b75e290deb609a6e26d3c803d478c19a93586399a6aab1a3bd05421be867f542
validation/verify_distribution.py 10c57a91f8f070bda178bb7e15722000c9cb3972d1751bb16f7972e45b2b0a08
```

Input hashes also matched before/after:

```text
design.md fc6efd4702c998cd584efefdf6319572de7ba36c68bb4a528d39a2ba1ed4ac0b
plan.md f9c9977a3b083acb7f11adf053fc423e015f6e56e36b37ffd8e00468c19781fa
implementation-rulings.md 85b0b4d266432884f3b7a32793e90e1d09da553ac6d6dcc628a3602399a44cc7
gate-a-review-resolution.md 788574c056ad9837a7c22d132373d99969dccb434472a3d2c252a2ac325e75aa
```

Role/standard hashes were taken at closure only:

```text
design_critic.md cee6294735ce2bd4fea19245852b6eb07cbe8ede7e72a4bfc829f586e4a39d0f
feature-spec-standard.md 8e9821e7bfd7c074dd1cec7ba02e3ea09a3fd32e35b91a1463a37c1ee3b9d6a4
```

Native identity: `/root/reader_b`. Model/build identity unavailable. Host-instruction exclusion and per-child filesystem allowlists remain unverified. This audit round does not establish formal EGM convergence.