"""`planner record`: the privileged half. Validates the artifact, stores it, posts the human view.

The artifact comes from a job that read an untrusted pull request, so nothing in it is trusted:
it is validated against the closed schema, and the PR number, head sha and repository must match
the values the workflow took from the GitHub event itself.
"""

from __future__ import annotations

import json
import os
import sys

from . import render, schema, store
from .gh import Gh, GhError


def _comment(gh: Gh, repo: str, pr: int, body: str) -> None:
    existing = None
    for c in gh.api("GET", "repos/%s/issues/%d/comments?per_page=100" % (repo, pr), paginate=True) or []:
        if render.MARKER in (c.get("body") or "") and (c.get("user") or {}).get("type") == "Bot":
            existing = c["id"]
            break
    if existing:
        gh.api("PATCH", "repos/%s/issues/comments/%d" % (repo, existing), {"body": body})
    else:
        gh.api("POST", "repos/%s/issues/%d/comments" % (repo, pr), {"body": body})


def run(a, gh: Gh | None = None) -> int:
    gh = gh or Gh()
    event = json.load(open(a.artifact))
    errs = schema.validate(event)
    if errs:
        print("::error::decision artifact rejected: %s" % "; ".join(errs[:5]))
        return 2
    if event["type"] != "decision" or event["repo"] != a.repo or event["pr"] != int(a.pr) or event["head_sha"] != a.head_sha:
        print("::error::decision artifact does not match the pull request context (repo, pr, head sha)")
        return 2
    st = store.GhStore(gh, a.repo, a.branch)
    path = store.path_for(event)
    res = st.put(path, event, "chore(ci-decisions): decision pr %d %s" % (event["pr"], event["head_sha"][:8]))
    url = "https://github.com/%s/blob/%s/%s" % (a.repo, a.branch, path)
    print("recorded %s (%s)" % (path, "new" if res["created"] else "already present"))
    md = render.summary_md(event, url)
    if a.summary_file:
        with open(a.summary_file, "a") as fh:
            fh.write(md)
    if not event["head_repo_is_fork"] and not a.no_comment:
        try:
            _comment(gh, a.repo, event["pr"], md)
        except GhError as e:
            print("::warning::could not post the planner comment: %s" % e)
    if not a.no_status:
        run_n = sum(1 for v in event["decision"].values() if v["action"] == "run" and v["layer"] in ("L3", "L4"))
        skip_n = sum(1 for v in event["decision"].values() if v["action"] == "skip")
        try:
            gh.api("POST", "repos/%s/statuses/%s" % (a.repo, event["head_sha"]),
                   {"state": "success", "context": "planner / decision", "target_url": url,
                    "description": ("%s: run %d cloud, skip %d" % (event["mode"], run_n, skip_n))[:140]})
        except GhError as e:
            print("::warning::could not set the planner status: %s" % e)
    return 0
