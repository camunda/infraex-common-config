#!/usr/bin/env python3
"""Check that no Renovate annotation ends up unusable once Renovate has read it.

`check_renovate_annotations.py` proves an annotation extracts exactly one dependency.
That is not enough: an annotation can extract cleanly and still never produce an update,
because the version it points at cannot be parsed by the versioning it was given. Renovate
does not treat that as an error -- it drops the dependency with `skipReason: invalid-value`
and moves on, so the annotation looks maintained and silently is not. That is how
`custom.aurora-mysql-camunda` sat inert until the datasource was given a versioning:
`8.4.mysql_aurora.8.4.7` is not valid semver, and `semver` is what the preset's
`versioningTemplate` falls back to when an annotation carries no inline `versioning=`.

Nothing static can decide this, because the answer depends on the versioning
implementation Renovate would use. So the check runs Renovate itself -- `--platform=local
--dry-run=lookup`, which reads the repository and resolves nothing else -- and reads the
report it writes.

Deliberately parked annotations exist: a chart pinned to `15-dev-latest` or a connector on
`8.10.0-SNAPSHOT` cannot match the regex that is waiting for the released version, and
that is the intended state for the length of a development cycle. Append
`renovate-inert-ok` to such an annotation, after `datasource=`, to say so:

    # renovate: datasource=helm depName=camunda-platform versioning=regex:^15\\.… renovate-inert-ok

The marker is free-form after that token, so the reason can follow it. It sits on the
annotation itself, which means it disappears with the pin it excuses instead of rotting in
a list somewhere.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Renovate skip reasons that mean "this dependency was read, and then dropped because its
# own current value could not be parsed". Everything else Renovate can report -- a
# datasource that failed, a registry that timed out, a disabled dependency -- is either
# transient or intentional, and must not turn a lint job red.
INERT_SKIP_REASONS = {
    "invalid-value",
    "invalid-version",
}

# Written after `datasource=` so that it lands inside the `replaceString` Renovate reports,
# which starts there rather than at the `# renovate:` marker.
EXEMPTION = "renovate-inert-ok"


@dataclass
class Finding:
    path: str
    line: int
    dep_name: str
    current_value: str
    versioning: str
    skip_reason: str
    text: str


def dependencies(report: dict):
    """Every dependency in the report, with the package file it came from."""
    for repository in report.get("repositories", {}).values():
        for package_files in repository.get("packageFiles", {}).values():
            for package_file in package_files:
                path = package_file.get("packageFile", "<unknown>")
                for dep in package_file.get("deps", []):
                    yield path, dep


def locate(root: Path, path: str, annotation: str) -> int:
    """Line number of `annotation` in `path`, or 0 when it cannot be found."""
    try:
        content = (root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    # Anchored on the end of the line: an annotation is a prefix of the same annotation
    # carrying an exemption marker, so a plain substring search reports the wrong one of
    # the two when a file holds both.
    match = re.search(re.escape(annotation) + r"[ \t]*\r?$", content, re.MULTILINE)
    if not match:
        return 0
    return content.count("\n", 0, match.start()) + 1


def collect(report: dict, root: Path) -> tuple[int, int, list[Finding]]:
    checked = 0
    exempt = 0
    findings = []
    for path, dep in dependencies(report):
        checked += 1
        skip_reason = dep.get("skipReason")
        if skip_reason not in INERT_SKIP_REASONS:
            continue
        # `replaceString` is what the manager matched: the annotation, then the line
        # carrying the version. Only the first line is the annotation.
        annotation = (dep.get("replaceString") or "").split("\n")[0]
        if EXEMPTION in annotation:
            exempt += 1
            continue
        findings.append(
            Finding(
                path=path,
                line=locate(root, path, annotation),
                dep_name=dep.get("depName") or "<unnamed>",
                current_value=str(dep.get("currentValue")),
                versioning=dep.get("versioning") or "<default>",
                skip_reason=skip_reason,
                text=annotation,
            )
        )
    return checked, exempt, findings


def report_finding(finding: Finding) -> None:
    message = (
        f"Renovate read this annotation and then dropped {finding.dep_name} "
        f"({finding.skip_reason}): the current value {finding.current_value!r} is not a "
        f"version under `{finding.versioning}`, so no update can ever be proposed for it. "
        f"Fix the versioning or the value, or append `{EXEMPTION}` to the annotation if "
        "the dependency is parked on a development version on purpose."
    )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        location = f"file={finding.path}" + (f",line={finding.line}" if finding.line else "")
        print(f"::error {location}::{message}")
    print(f"{finding.path}:{finding.line or '?'}: {message}", file=sys.stderr)
    print(f"  {finding.text}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a Renovate annotation resolves to a dependency Renovate "
        "cannot update."
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="the JSON report written by `renovate --report-type=file`",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="repository the report was produced from, used to locate the annotations",
    )
    args = parser.parse_args()

    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(
            f"error: {args.report} does not exist. Renovate writes it only when it ran to "
            "completion; check the step above for the failure it hit."
        )
    except json.JSONDecodeError as error:
        sys.exit(f"error: {args.report} is not valid JSON: {error}")

    checked, exempt, findings = collect(report, args.root.resolve())

    for finding in findings:
        report_finding(finding)

    print(
        f"checked {checked} dependenc(ies) resolved from Renovate annotations: "
        f"{checked - len(findings) - exempt} usable, {exempt} parked on purpose, "
        f"{len(findings)} unusable"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
