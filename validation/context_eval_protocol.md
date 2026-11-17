# Fresh-context utility study (separate from deterministic verification)

Status: NOT_RUN by the implementation executor. The parent conducts the study
after the final freeze. Neither unit tests nor the standalone host pilot establish
agent usefulness, independent review, or semantic correctness.

The controller's `inputs/context-eval-tasks.json` is the authoritative, already
frozen task and six-item rubric. Preserve its bytes. Do not rewrite the questions,
change the scoring rubric after observing answers, or treat this document as a
replacement task. That input belongs to the controller, not the kit distribution.

Use two fresh contexts with the same pinned isolated repository, model, tool
permissions and source access. Control gets existing instructions and sources;
intervention gets those same materials plus the actual successful `knowledge read`
export. The pilot's `pilot.json` names the JSON and Markdown intervention files.
Verify both conditions have identical source bytes and retain those fingerprints.
Give neither reader the rubric, prior findings, nor the other answer. Readers
plan only: no mutations, execution or product implementation.

Retain actual reader identities/model metadata, full answers, cited paths/lines,
consulted files when available, supplied materials and command/exit/hash for the
intervention export. Score each frozen rubric item against observed citations;
separate literal term checks from substantive scoring judgment. Preserve worse
intervention results and ties. A crashed/unavailable reader is NOT_RUN, never a
successful zero-error answer. If readers or comparable source snapshots are not
available, record the precise limitation and leave the utility study pending.

Report the complete pair of answers and item scores alongside deterministic pilot
and suite evidence, counted separately. One exploratory observation per condition
supports no statistical claim, speed guarantee, or general accuracy improvement.
The declared agent approval is not authenticated; `semantic_review` remains
`not_attested` even when every mechanical check passes.
