"""Known failures: a checked-in catalogue that classifies a failed run.

A failure that is `blocked` (the suite could not run: quota, outage, missing secret, lost runner) or
`known_failure` (already tracked in an issue) is reported as such. Neither feeds the risk of a
module/suite pair and neither confirms an escape. Everything else stays `fail`.

Catalogue file (`.github/planner-known-failures.yaml`):

    entries:
      - id: kf-capacity-slot            # stable id, shown in the outcome
        class: blocked                  # blocked | known_failure
        pattern: "(?i)capacity slot"    # regex on the failed job and step names
        suites: ["aws_*_tests.yml"]     # optional globs on the workflow file name
        issue: https://github.com/...   # tracking issue (required for known_failure)
        expires: 2027-03-01             # optional: after this date the entry is ignored
"""

from __future__ import annotations

import fnmatch
import re
from datetime import date

from . import yamlio

CLASSES = ("blocked", "known_failure")


def load(text: str | None) -> list:
    if not text or not text.strip():
        return []
    data = yamlio.loads(text) or {}
    out = []
    for e in data.get("entries") or []:
        if not isinstance(e, dict) or e.get("class") not in CLASSES or not e.get("pattern") or not e.get("id"):
            continue
        try:
            rx = re.compile(str(e["pattern"]))
        except re.error:
            continue
        out.append({"id": str(e["id"])[:100], "class": e["class"], "rx": rx, "suites": [str(s) for s in e.get("suites") or []],
                    "issue": str(e.get("issue") or ""), "expires": str(e.get("expires") or "")})
    return out


def classify(entries: list, suite: str, failed_names: list, today: date | None = None):
    """First matching entry -> (class, id, reason); otherwise ("fail", None, None)."""
    today = today or date.today()
    text = "\n".join(failed_names)[:4000]
    for e in entries:
        if e["expires"]:
            try:
                if date.fromisoformat(e["expires"]) < today:
                    continue
            except ValueError:
                continue
        if e["suites"] and not any(fnmatch.fnmatch(suite, g) for g in e["suites"]):
            continue
        if e["rx"].search(text):
            return e["class"], e["id"], ("known failure %s" % e["issue"] if e["class"] == "known_failure" else "blocked: matches %s" % e["id"])[:200]
    return "fail", None, None
