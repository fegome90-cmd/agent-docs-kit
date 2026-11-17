# Knowledge authoring examples

These three strict-schema JSON files were captured from an actual temporary
synthetic bootstrap. They contain no credentials or human approval. They are
wire examples, not reusable evidence for another repository. Their hashes and
proposal ID bind that particular fixture; never substitute your prose under them.

The map's Catalog section watches `src/catalog.py`; Summary is a planned claim
that depends on Catalog; Runtime is a constraint watching `src/runtime.py`.
Inventory-only is explicitly empty. These exact headings must exist in PROJECT.md.

To author a real request, first curate a new map, then create a NEW snapshot and
run `knowledge check ROOT --map MAP --snapshot SNAPSHOT --json`. Copy the returned
`evidence` object unchanged into your request. Supply a fresh request UUID,
complete candidate path, expected parents from `evidence.heads[document_id]`,
mode, reason and every pending section ID. Bootstrap adopts the exact origin;
revise may edit only authorized spans. A snapshot is evidence, never approval.

Run `knowledge propose ROOT --request REQUEST --output NEW_BUNDLE --apply --json`.
Read the resulting document, sources and evidence. Only after that review, author
a review JSON with the returned `proposal_id`, explicit reviewer declaration and
reference. Copy the review shape, not this example's proposal ID or decision.
Accept with `knowledge accept ROOT --proposal NEW_BUNDLE --review REVIEW
--session UUID --key KEY --confirm-reviewed --apply --json`. Open a new process
with `knowledge read ROOT --map MAP --document project --json` to verify selection
and freshness. Nothing stages or replaces the origin document.
