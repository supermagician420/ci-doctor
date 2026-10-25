# ci-doctor

A zero-dependency repository and GitHub Actions auditor. It turns a small inventory manifest into a scored, actionable report.

## Quick start

Requires Python 3.8+, no third-party packages.

```bash
python ci_doctor.py examples/repo.json
```

Example output:

```text
Score 100/100: 0 high, 0 medium, 0 low.
No findings. Still review the repository manually.
```

Inspect a risky workflow:

```bash
python ci_doctor.py examples/risky-repo.json
python ci_doctor.py examples/risky-repo.json --json
python ci_doctor.py examples/risky-repo.json --fail-on high
```

`--fail-on high` exits `1` when any high-severity finding exists, which makes the tool usable as a CI gate.

## Manifest

The manifest is a JSON inventory, not a full repository scan:

```json
{
  "project": "demo-service",
  "files": [
    "README.md",
    "LICENSE",
    ".gitignore",
    "app.py",
    "test_app.py",
    ".github/workflows/tests.yml"
  ],
  "workflows": ["tests.yml"]
}
```

- `files`: required, non-empty list of repository-relative paths;
- `workflows`: required for workflow checks; paths are read relative to the manifest;
- `project`: optional display name.

Workflow paths must stay inside the manifest directory.

## Checks

| Check | Severity | Trigger |
| --- | --- | --- |
| `missing-readme` | high | No `README*` file in the inventory |
| `missing-tests` | high | Source files exist but no test files |
| `missing-ci` | high | No workflow provided |
| `committed-env` | high | A tracked `.env` file |
| `workflow-pr-target` | high | `pull_request_target` in a workflow |
| `workflow-write-all` | high | `permissions: write-all` |
| `workflow-no-trigger` | medium | No top-level `on:` |
| `workflow-no-permissions` | low | No explicit top-level permissions |
| `workflow-unpinned-action` | low | `uses:` is not a 40-character commit SHA |
| `missing-license` | low | No `LICENSE` file |
| `missing-gitignore` | low | No `.gitignore` |

Scoring: start at 100; subtract 30 per high, 12 per medium, and 4 per low finding (floor 0). The score is a conversation starter, not a certification.

## Design choices

- **Inventory, not a crawler.** The input is explicit, so the tool is fast and predictable.
- **Workflow-aware.** It catches two expensive GitHub Actions mistakes: `pull_request_target` and `write-all` permissions.
- **Actionable IDs.** Every finding has a stable `check_id`, suitable for allowlists or dashboards.
- **CI-friendly.** JSON output and `--fail-on` make it scriptable.

## Limitations

- It does not parse YAML fully; it uses targeted text checks.
- It cannot prove that tests are meaningful or that CI actually passes.
- Pinning actions to a SHA is a recommendation; the tool does not verify the SHA's provenance.
- It does not scan file contents for secrets beyond the `.env` filename.
- The score is heuristic.

## Tests

```bash
python -m unittest -v
```

12 tests cover the clean case, missing files/tests/CI, risky workflow triggers, permissions, unpinned actions, committed `.env`, manifest validation, and CLI behavior.

## Files

```text
ci-doctor/
├── ci_doctor.py
├── test_ci_doctor.py
├── examples/repo.json
├── examples/tests.yml
├── examples/risky-repo.json
├── examples/risky-workflow.yml
├── README.md
├── .gitignore
└── .github/workflows/tests.yml
```
