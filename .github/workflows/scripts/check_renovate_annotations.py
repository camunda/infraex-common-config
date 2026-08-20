#!/usr/bin/env python3
"""Check that every Renovate version annotation extracts exactly one dependency.

The shared preset (`default.json5` in camunda/infraex-common-config) updates versions
written in shell scripts, Terraform, Go, YAML, `justfile` and `.tool-versions` when they
carry an annotation naming the datasource and the dependency:

    # renovate: datasource=docker depName=camunda/keycloak versioning=regex:^...$
    image: docker.io/camunda/keycloak:quay-optimized-26.7.0

Both ways this can go wrong are silent:

  - the annotation matches nothing -- the version is not on the line immediately after it,
    or its shape is not one the preset reads -- and that dependency is simply never
    updated, with no warning anywhere;
  - the annotation matches several times, which is what happens when two of the preset's
    patterns overlap. Renovate then reports the same dependency two or three times, and
    the copies can disagree on `versioning` or `registryUrl`.

So this reads the preset's own `customManagers` and applies them the way Renovate does,
rather than reimplementing what they are supposed to accept. Exactly one match per
annotation, no more, no less.

The patterns are written for RE2, which Renovate uses for user-supplied regular
expressions. Python's `re` covers every construct they use -- no lookaround, no
backreference, named groups only -- so the sole translation needed is the group syntax.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import json5
except ImportError:  # pragma: no cover - reached by humans, not by the test suite
    sys.exit(
        "error: this script needs the json5 module to read the preset.\n"
        "       pip install json5"
    )

# `(?<name>...)` in RE2 is `(?P<name>...)` in Python. `(?<=` and `(?<!` are lookbehind,
# which RE2 rejects outright, but the negative class keeps this substitution honest were a
# pattern ever to grow one.
RE2_GROUP = re.compile(r"\(\?<(?![=!])")

# An annotation names both a datasource and a dependency. Requiring both is what keeps
# prose that merely mentions `datasource=` -- documentation, a comment about Renovate
# itself -- from being reported as a broken annotation.
ANNOTATION = re.compile(r"datasource=\S+.*depName=|depName=\S+.*datasource=")


@dataclass
class Manager:
    """One `customManagers` entry: which files it reads, and with which patterns."""

    file_patterns: list[str]
    regexes: list[re.Pattern[str]]

    def reads(self, relative_path: str) -> bool:
        for pattern in self.file_patterns:
            as_regex = re.fullmatch(r"/(.*)/", pattern)
            if as_regex:
                if re.search(as_regex.group(1), relative_path):
                    return True
            elif fnmatch.fnmatch(relative_path, pattern):
                return True
        return False


@dataclass
class Finding:
    path: str
    line: int
    count: int
    text: str


def load_managers(preset_path: Path) -> list[Manager]:
    preset = json5.loads(preset_path.read_text(encoding="utf-8"))
    managers = []
    for index, manager in enumerate(preset.get("customManagers", [])):
        if manager.get("customType") != "regex":
            continue
        regexes = []
        for position, match_string in enumerate(manager.get("matchStrings", [])):
            try:
                regexes.append(re.compile(RE2_GROUP.sub("(?P<", match_string)))
            except re.error as error:
                sys.exit(
                    f"error: customManagers[{index}].matchStrings[{position}] "
                    f"does not compile: {error}"
                )
        managers.append(
            Manager(
                file_patterns=manager.get("managerFilePatterns")
                or manager.get("fileMatch")
                or [],
                regexes=regexes,
            )
        )
    if not managers:
        sys.exit(f"error: {preset_path} declares no regex customManagers")
    return managers


def tracked_files(root: Path) -> list[str]:
    """Files git knows about, so a second checkout left in the workspace is ignored."""
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        )
    return [entry for entry in listing.split("\0") if entry]


def check_file(
    content: str, relative: str, managers: list[Manager]
) -> tuple[int, list[Finding]]:
    """Return how many annotations the file holds, and which of them do not extract once."""
    # Every pattern in the preset starts at `datasource=`, so a match begins exactly where
    # its annotation does: the two can be keyed on the same offset.
    extractions: dict[int, int] = {}
    for manager in managers:
        if not manager.reads(relative):
            continue
        for regex in manager.regexes:
            for match in regex.finditer(content):
                extractions[match.start()] = extractions.get(match.start(), 0) + 1

    annotations = 0
    findings = []
    offset = 0
    for line_number, line in enumerate(content.split("\n"), start=1):
        column = line.find("datasource=")
        if column >= 0 and ANNOTATION.search(line):
            annotations += 1
            count = extractions.get(offset + column, 0)
            if count != 1:
                findings.append(
                    Finding(
                        path=relative,
                        line=line_number,
                        count=count,
                        text=line.strip(),
                    )
                )
        offset += len(line) + 1
    return annotations, findings


def report(finding: Finding) -> None:
    if finding.count == 0:
        message = (
            "this Renovate annotation extracts nothing, so the dependency it names is "
            "never updated. The version has to be on the line immediately after the "
            "annotation, and to be the last quoted run on that line or the last thing "
            "on it."
        )
    else:
        message = (
            f"this Renovate annotation extracts {finding.count} dependencies instead of "
            "one, so the same update is reported several times. Two patterns in the "
            "preset's customManagers overlap here."
        )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error file={finding.path},line={finding.line}::{message}")
    print(f"{finding.path}:{finding.line}: {message}", file=sys.stderr)
    print(f"  {finding.text}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that every Renovate version annotation extracts exactly one "
        "dependency."
    )
    parser.add_argument(
        "--preset",
        type=Path,
        required=True,
        help="the default.json5 whose customManagers are applied",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="repository to scan (default: the working directory)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="name every file holding an annotation, not just the broken ones",
    )
    args = parser.parse_args()

    managers = load_managers(args.preset)
    root = args.root.resolve()

    findings: list[Finding] = []
    annotations = 0
    scanned = 0
    for relative in tracked_files(root):
        path = root / relative
        if not path.is_file() or not any(m.reads(relative) for m in managers):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        found, broken = check_file(content, relative, managers)
        annotations += found
        findings.extend(broken)
        if args.verbose and found:
            print(f"{relative}: {found} annotation(s), {len(broken)} broken")

    for finding in findings:
        report(finding)

    print(
        f"checked {annotations} Renovate annotation(s) across {scanned} file(s): "
        f"{annotations - len(findings)} extract exactly one dependency, "
        f"{len(findings)} do not"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
