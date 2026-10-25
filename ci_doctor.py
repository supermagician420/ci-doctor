#!/usr/bin/env python3
"""Audit a repository inventory and its GitHub Actions workflows."""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


class ManifestError(ValueError):
    """The manifest is missing or malformed."""


@dataclass(frozen=True)
class Manifest:
    root: Path
    files: Tuple[str, ...]
    workflows: Dict[str, str]
    project: str


@dataclass(frozen=True)
class Finding:
    check_id: str
    severity: str
    message: str
    path: Optional[str] = None


@dataclass(frozen=True)
class Report:
    score: int
    summary: str
    findings: Tuple[Finding, ...]


_LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2}
_SEVERITY_WEIGHT = {"high": 30, "medium": 12, "low": 4}
_SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php", ".cs", ".c", ".cpp"}
_TEST_RE = re.compile(r"(^|/)(test_.*\.py|.*_test\.py|.*\.test\.[jt]sx?|.*\.spec\.[jt]sx?)$")
_ACTION_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*[\"']?([^\"'\s#]+)", re.MULTILINE)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _normalize(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def load_manifest(path: str) -> Manifest:
    manifest_path = Path(path).resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError("cannot read manifest: %s" % exc) from exc
    except json.JSONDecodeError as exc:
        raise ManifestError("invalid manifest JSON: %s" % exc.msg) from exc
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a JSON object")

    files_raw = raw.get("files")
    if not isinstance(files_raw, list) or not files_raw or any(not isinstance(item, str) or not item.strip() for item in files_raw):
        raise ManifestError("files must be a non-empty array of strings")
    files = tuple(_normalize(item) for item in files_raw)

    workflows_raw = raw.get("workflows", [])
    if not isinstance(workflows_raw, list) or any(not isinstance(item, str) or not item.strip() for item in workflows_raw):
        raise ManifestError("workflows must be an array of relative file paths")
    workflows: Dict[str, str] = {}
    for relative in workflows_raw:
        relative = _normalize(relative)
        workflow_path = (manifest_path.parent / relative).resolve()
        try:
            workflow_path.relative_to(manifest_path.parent)
        except ValueError as exc:
            raise ManifestError("workflow path escapes the manifest directory: %s" % relative) from exc
        try:
            workflows[relative] = workflow_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError("cannot read workflow %s: %s" % (relative, exc)) from exc

    project = raw.get("project", "")
    if project is None:
        project = ""
    if not isinstance(project, str):
        raise ManifestError("project must be a string")
    return Manifest(manifest_path.parent, files, workflows, project)


def _find_readme(files: Sequence[str]) -> bool:
    names = {Path(path).name.lower() for path in files}
    return any(name.startswith("readme") and name.endswith((".md", ".rst", ".txt")) for name in names)


def _find_file(files: Sequence[str], name: str) -> bool:
    return any(Path(path).name.lower() == name.lower() for path in files)


def _has_source(files: Sequence[str]) -> bool:
    return any(Path(path).suffix.lower() in _SOURCE_EXTENSIONS and not _TEST_RE.search(path) for path in files)


def _has_tests(files: Sequence[str]) -> bool:
    return any(_TEST_RE.search(path) or path.startswith("tests/") for path in files)


def _workflow_findings(path: str, text: str) -> List[Finding]:
    findings: List[Finding] = []
    lower = text.lower()
    has_on = bool(re.search(r"^on:\s*", text, re.MULTILINE)) or bool(re.search(r"^on:\s*$", text, re.MULTILINE))
    if not has_on:
        findings.append(Finding("workflow-no-trigger", "medium", "Workflow has no top-level 'on:' trigger.", path))
    if "pull_request_target" in lower:
        findings.append(Finding("workflow-pr-target", "high", "pull_request_target can expose secrets to fork code; use pull_request unless required.", path))
    if re.search(r"permissions:\s*write-all", lower):
        findings.append(Finding("workflow-write-all", "high", "Workflow grants permissions: write-all; use least privilege.", path))
    if not re.search(r"^permissions:", text, re.MULTILINE):
        findings.append(Finding("workflow-no-permissions", "low", "No explicit top-level permissions; default token permissions may be broad.", path))

    unpinned = []
    for action in _ACTION_RE.findall(text):
        if action.startswith(("./", "docker://")):
            continue
        if "@" not in action:
            unpinned.append(action)
            continue
        ref = action.rsplit("@", 1)[1]
        if not _SHA_RE.match(ref):
            unpinned.append(action)
    if unpinned:
        examples = ", ".join(unpinned[:3])
        findings.append(
            Finding(
                "workflow-unpinned-action",
                "low",
                "Actions are not pinned to a full commit SHA: %s" % examples,
                path,
            )
        )
    return findings


def audit(manifest: Manifest) -> Report:
    findings: List[Finding] = []
    files = manifest.files

    if not _find_readme(files):
        findings.append(Finding("missing-readme", "high", "No README file found; contributors cannot onboard."))
    if not _find_file(files, "LICENSE") and not any(Path(path).name.lower().startswith("license") for path in files):
        findings.append(Finding("missing-license", "low", "No LICENSE file found; reuse terms are unclear."))
    if not _find_file(files, ".gitignore"):
        findings.append(Finding("missing-gitignore", "low", "No .gitignore; local artifacts may be committed."))
    if _has_source(files) and not _has_tests(files):
        findings.append(Finding("missing-tests", "high", "Source files exist but no test files were found."))
    if not manifest.workflows:
        findings.append(Finding("missing-ci", "high", "No GitHub Actions workflow was provided."))
    if any(Path(path).name.lower() == ".env" or path.lower().endswith("/.env") for path in files):
        findings.append(Finding("committed-env", "high", "A .env file is tracked; secrets may leak.", ".env"))

    for path in sorted(manifest.workflows):
        findings.extend(_workflow_findings(path, manifest.workflows[path]))

    findings.sort(key=lambda finding: (_LEVEL_ORDER.get(finding.severity, 9), finding.check_id, finding.path or ""))
    penalty = sum(_SEVERITY_WEIGHT.get(finding.severity, 0) for finding in findings)
    score = max(0, 100 - penalty)
    counts = {level: sum(1 for finding in findings if finding.severity == level) for level in _LEVEL_ORDER}
    summary = "Score %d/100: %d high, %d medium, %d low." % (
        score,
        counts["high"],
        counts["medium"],
        counts["low"],
    )
    return Report(score, summary, tuple(findings))


def render_text(report: Report) -> str:
    lines = [report.summary]
    if not report.findings:
        lines.append("No findings. Still review the repository manually.")
    else:
        for finding in report.findings:
            location = " (%s)" % finding.path if finding.path else ""
            lines.append("[%s] %s: %s%s" % (finding.severity.upper(), finding.check_id, finding.message, location))
    return "\n".join(lines).rstrip() + "\n"


def render_json(report: Report) -> str:
    return json.dumps(asdict(report), ensure_ascii=False, indent=2)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit repository files and GitHub Actions workflows")
    parser.add_argument("manifest", help="JSON manifest describing the repository inventory")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--fail-on", choices=("high", "medium", "low"), help="exit 1 if a finding at this severity or worse exists")
    args = parser.parse_args(argv)

    try:
        report = audit(load_manifest(args.manifest))
    except ManifestError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    print(render_json(report) if args.json else render_text(report), end="")
    if args.fail_on:
        threshold = _LEVEL_ORDER[args.fail_on]
        if any(_LEVEL_ORDER.get(finding.severity, 9) <= threshold for finding in report.findings):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
