import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ci_doctor import ManifestError, audit, load_manifest, main, render_text


GOOD_WORKFLOW = """name: tests
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@8ade135a41bc03ea155e62e844d188df1ea18608
      - run: python -m unittest -v
"""


def write_repo(tmp: str, files, workflow: str = GOOD_WORKFLOW, workflow_name: str = ".github/workflows/tests.yml"):
    root = Path(tmp)
    (root / workflow_name).parent.mkdir(parents=True, exist_ok=True)
    (root / workflow_name).write_text(workflow, encoding="utf-8")
    manifest = {"project": "demo", "files": list(files), "workflows": [workflow_name]}
    path = root / "repo.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class AuditTests(unittest.TestCase):
    def test_good_repo_scores_100(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(
                tmp,
                ["README.md", "LICENSE", ".gitignore", "app.py", "test_app.py", ".github/workflows/tests.yml"],
            )
            report = audit(load_manifest(str(path)))
        self.assertEqual(report.score, 100)
        self.assertEqual(report.findings, ())

    def test_missing_core_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(tmp, ["app.py"], workflow="")
            report = audit(load_manifest(str(path)))
        ids = {finding.check_id for finding in report.findings}
        self.assertIn("missing-readme", ids)
        self.assertIn("missing-tests", ids)
        # An empty workflow file is still a workflow entry; trigger check should fire.
        self.assertIn("workflow-no-trigger", ids)

    def test_pull_request_target_is_high(self) -> None:
        workflow = """name: dangerous
on: pull_request_target
permissions:
  contents: read
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(tmp, ["README.md", "LICENSE", ".gitignore", "app.py", "test_app.py"], workflow=workflow)
            report = audit(load_manifest(str(path)))
        self.assertTrue(any(finding.check_id == "workflow-pr-target" and finding.severity == "high" for finding in report.findings))

    def test_write_all_permissions_is_high(self) -> None:
        workflow = """name: broad
on: [push]
permissions: write-all
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(tmp, ["README.md", "LICENSE", ".gitignore", "app.py", "test_app.py"], workflow=workflow)
            report = audit(load_manifest(str(path)))
        self.assertTrue(any(finding.check_id == "workflow-write-all" for finding in report.findings))

    def test_unpinned_action_is_low(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(
                tmp,
                ["README.md", "LICENSE", ".gitignore", "app.py", "test_app.py"],
                workflow=GOOD_WORKFLOW.replace(
                    "actions/checkout@8ade135a41bc03ea155e62e844d188df1ea18608",
                    "actions/checkout@v4",
                ),
            )
            report = audit(load_manifest(str(path)))
        self.assertTrue(any(finding.check_id == "workflow-unpinned-action" for finding in report.findings))

    def test_committed_env_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(
                tmp,
                ["README.md", "LICENSE", ".gitignore", "app.py", "test_app.py", ".env"],
            )
            report = audit(load_manifest(str(path)))
        self.assertTrue(any(finding.check_id == "committed-env" and finding.severity == "high" for finding in report.findings))

    def test_missing_permissions_is_reported(self) -> None:
        workflow = """name: tests
on: [push]
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(tmp, ["README.md", "LICENSE", ".gitignore", "app.py", "test_app.py"], workflow=workflow)
            report = audit(load_manifest(str(path)))
        self.assertTrue(any(finding.check_id == "workflow-no-permissions" for finding in report.findings))

    def test_text_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(tmp, ["app.py"], workflow=GOOD_WORKFLOW)
            rendered = render_text(audit(load_manifest(str(path))))
        self.assertIn("Score", rendered)
        self.assertIn("[HIGH]", rendered)


class ManifestTests(unittest.TestCase):
    def test_invalid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(str(path))

    def test_missing_workflow_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repo.json"
            path.write_text(json.dumps({"files": ["README.md"], "workflows": [".github/workflows/nope.yml"]}), encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(str(path))


class CliTests(unittest.TestCase):
    def test_cli_json_and_fail_on_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_repo(tmp, ["app.py"], workflow="")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main([str(path), "--json", "--fail-on", "high"])
        self.assertEqual(code, 1)
        data = json.loads(buf.getvalue())
        self.assertGreater(len(data["findings"]), 0)

    def test_cli_missing_manifest(self) -> None:
        self.assertEqual(main(["does-not-exist.json"]), 2)


if __name__ == "__main__":
    unittest.main()
