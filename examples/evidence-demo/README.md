# Evidence demo

The public artifacts are the [reviewed standalone report](evidence-demo-reviewed.html) and [reviewed evidence bundle](evidence-demo-reviewed.zip), exported from three real runs of this public target. The report is self-contained; the bundle also includes JSON, JUnit, source snapshots, selected screenshots/UI snapshots, journals, and `SHA256SUMS`. The older unqualified files remain historical local exports and are not the reviewed publication artifacts.

| Role | Persisted Run | Outcome |
| --- | --- | --- |
| Explore source | `open-http-127-0-0-1-8848-in-the-browser-click-add-to-cart-fo-20260909T024344Z-0305dc` | success |
| Strict baseline | `fsq-evidence-demo-20260909T024503Z-a9bf62` | success |
| Strict changed target | `fsq-evidence-demo-20260909T024531Z-ecd82b` | failed |

The three runs use the same source Case digest, `995e77b5d5557ee1a1ff5b73fdf7aca5753179040224fe765e67db4e3876a5ee`. New generated Case YAML contains no Run provenance; the Explore Run retains its source identity and command mapping in `recording.json` and lineage. The export-only user review declaration binds the safely retained Case copy digest `a55f7dafe90f88b9e32a248a3d398ebc5a5ce86a6a932bf6904a76b5d9ef9858`, while the original source digest remains separately recorded. The files are demonstration evidence, not a claim that their contents are safe for an unrelated public target.

This public, account-free target has two controlled versions. The baseline adds a notebook to a cart; the changed version displays an error after the same click. All report captures come from actual FSQ execution.

Start the baseline from a source checkout:

```bash
python scripts/serve-evidence-demo.py --variant baseline
```

In a dedicated registered Web Workspace, create a Case through real exploration:

```bash
fsq case create --platform web --goal 'Open http://127.0.0.1:8848/, click Add to cart, verify Added to cart with a deterministic visible-text assertion, then close the browser.'
```

Inspect the generated Case and its evidence. Review the stable published Case name reported by `case create`, then replay that unchanged file:

```bash
fsq case test --platform web cases/web/CASE_NAME.fsq.yaml
```

Stop the target server and start the changed version on the same port:

```bash
python scripts/serve-evidence-demo.py --variant changed
```

Execute the same Case again. The expected assertion fails because the actual UI displays `Unable to add item`. Export that failed Run with the successful Strict Run as baseline and the originating Explore Run as a related Run:

```bash
fsq runs export FAILED_RUN_ID --platform web --format html --baseline PASSED_RUN_ID --related-run EXPLORE_RUN_ID --output evidence-demo.html
fsq runs export FAILED_RUN_ID --platform web --format bundle --baseline PASSED_RUN_ID --related-run EXPLORE_RUN_ID --output evidence-demo.zip
```

The report retains each outcome, pairs comparable steps, shows UI snapshot differences, and exposes missing or truncated evidence. A generated or saved Case is not automatically reviewed. An optional share-profile review declaration is an export-scoped user statement bound to the retained Case digest.

The target is public source served locally; the Control Plane remains local. Publish only an inspected static export. No payment, production account, or private target data is needed.
