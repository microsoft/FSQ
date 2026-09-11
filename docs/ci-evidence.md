# Run evidence in CI

Run a reviewed Case once, retain its result, then export evidence even when the test fails. JSON contains the public `fsq.report/v1` contract. JUnit reports one root Case invocation; step and attempt details remain in the report. HTML is standalone and can be inspected offline.

The source-checkout helper `scripts/run-case-with-evidence.py` writes the CLI result, JSON, JUnit, HTML, an evidence bundle, and independent export status. It returns the test's nonzero exit code when the test fails. If the test passes but export fails, it returns `5`. Preflight failure before Run allocation produces no fabricated Run ID.

From the exact registered Workspace root:

```bash
python /path/to/FSQ/scripts/run-case-with-evidence.py --platform web cases/web/reviewed.fsq.yaml --artifacts ci-evidence
```

The browser/device/application and Workspace must already be provisioned on the runner. Provider-free Cases need no planning-model configuration; a Case containing `assertWithAI` still needs its Provider.

GitHub Actions can consume the helper on an already provisioned runner:

```yaml
- name: Execute Case and export evidence
  working-directory: ${{ env.FSQ_WORKSPACE }}
  run: >-
    python "$GITHUB_WORKSPACE/scripts/run-case-with-evidence.py"
    --platform web cases/web/reviewed.fsq.yaml
    --artifacts ci-evidence

- name: Upload Run evidence
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: fsq-evidence
    path: ${{ env.FSQ_WORKSPACE }}/ci-evidence
    if-no-files-found: warn
```

Use the CI system's JUnit publisher for `ci-evidence/junit.xml`. Keep `export-status.json` alongside it so an export problem is not mistaken for a test pass. Reports may contain visible application data; use an explicit share profile when preparing a public copy.

Standalone export does not open a browser or execute a Case:

```bash
fsq --output json --non-interactive runs export RUN_ID --format html --output ci-evidence/report.html
```

The global `--output json` selects the CLI envelope; `runs export --output` names the destination file. Existing output files are rejected. Default exports use a unique directory inside the Run. Historical inputs are read without migration.
