P01, P02, and P03 completed in order. **Stopped at Gate A.**

| Patch | Targeted green | Full regression |
|---|---:|---:|
| P01 | 9 | 253 passed |
| P02 | 12 | 265 passed |
| P03 | 12 | 277 passed |

All final runs exited 0 with Graphify enabled. Red, green, failed-attempt logs, and modified paths are recorded in [Gate-A-result.json](evidence/Gate-A-result.json).

Exact copies: `stages/P01`, `stages/P02`, `stages/P03`. Candidate matches P03; all baseline tests remain unchanged.

[Rulings and ledger](evidence/progress.md) document schema defaults, reviewed-input representation, inventory scope, provenance gaps, and output limits. Proposal provenance verification remains a later resolver obligation. No Gate A blockers.

Actual [impact report](evidence/Gate-A-impact.json) selects catalog → summary only. No P04 work, version change, agents, or independent review.

Risk: items 2/3—path validation and shared CLI registration; independent review remains with the parent.