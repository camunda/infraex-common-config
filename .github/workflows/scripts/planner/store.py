"""The decision store: append-only event files on a dedicated branch.

Layout (see design.md section 3.2):
  decisions/pr/<N>/<head_sha>/<ts>-<labels8>.json   decision events, newest wins for the gate
  events/YYYY/MM/DD/<ts>-<type>-<id>.json            outcome | escape | weights | checkpoint | watermark | override

Writers use the contents API: one verified commit per file, never an edit. A conflict with a
concurrent writer is retried with jittered backoff.
"""

from __future__ import annotations

import base64
import json
import os
import random
import time

from .gh import Gh, GhError
from .util import compact_ts, parse_iso


def decision_path(pr: int, head_sha: str, ts: str, labels_hash: str) -> str:
    return "decisions/pr/%d/%s/%s-%s.json" % (pr, head_sha, compact_ts(ts), labels_hash[:8])


def event_path(ts: str, typ: str, ident: str) -> str:
    d = parse_iso(ts)
    return "events/%04d/%02d/%02d/%s-%s-%s.json" % (d.year, d.month, d.day, compact_ts(ts), typ, ident)


def path_for(event: dict) -> str:
    if event["type"] == "decision":
        return decision_path(event["pr"], event["head_sha"], event["ts"], event["labels_hash"])
    return event_path(event["ts"], event["type"], event["id"])


def encode(obj) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode()


class DirStore:
    """Read access to a checked-out copy of the store (also used for local runs and tests)."""

    def __init__(self, root: str | None):
        self.root = root

    def walk(self):
        if not self.root or not os.path.isdir(self.root):
            return
        for sub in ("decisions", "events"):
            base = os.path.join(self.root, sub)
            for dirpath, dirs, files in os.walk(base):
                dirs.sort()
                for f in sorted(files):
                    if f.endswith(".json"):
                        yield os.path.relpath(os.path.join(dirpath, f), self.root)

    def read_json(self, rel: str):
        with open(os.path.join(self.root, rel), "rb") as fh:
            return json.loads(fh.read().decode())

    def events(self, types=None) -> list:
        out = []
        for rel in self.walk():
            try:
                e = self.read_json(rel)
            except (ValueError, OSError):
                continue
            if isinstance(e, dict) and (types is None or e.get("type") in types):
                e["_path"] = rel
                out.append(e)
        out.sort(key=lambda e: (e.get("ts", ""), e.get("id", "")))
        return out

    def write(self, rel: str, obj) -> None:
        """Local write for tests and dry runs (the CI writer is GhStore)."""
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(encode(obj))


class StoreConflict(RuntimeError):
    pass


class GhStore:
    def __init__(self, gh: Gh, repo: str, branch: str, retries: int = 8, sleep=time.sleep, rng=random.random):
        self.gh, self.repo, self.branch = gh, repo, branch
        self.retries, self.sleep, self.rng = retries, sleep, rng

    def _contents(self, path: str) -> str:
        return "repos/%s/contents/%s" % (self.repo, path)

    def ensure_branch(self) -> None:
        """Create the orphan branch with a README if it does not exist (first run only)."""
        try:
            self.gh.api("GET", "repos/%s/git/ref/heads/%s" % (self.repo, self.branch))
            return
        except GhError as e:
            if e.status != 404:
                raise
        readme = ("# ci-decisions\n\nAppend-only store of test-selection planner events (schema planner.event/1).\n"
                  "Written only by the planner workflows; never edit or rewrite. Query with `planner audit`.\n")
        blob = self.gh.api("POST", "repos/%s/git/blobs" % self.repo, {"content": readme, "encoding": "utf-8"})
        tree = self.gh.api("POST", "repos/%s/git/trees" % self.repo,
                           {"tree": [{"path": "README.md", "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
        commit = self.gh.api("POST", "repos/%s/git/commits" % self.repo,
                             {"message": "chore(ci-decisions): initialise the store", "tree": tree["sha"], "parents": []})
        try:
            self.gh.api("POST", "repos/%s/git/refs" % self.repo, {"ref": "refs/heads/%s" % self.branch, "sha": commit["sha"]})
        except GhError as e:
            if e.status != 422:  # 422: another writer created it first
                raise

    def get(self, path: str):
        try:
            r = self.gh.api("GET", self._contents(path) + "?ref=%s" % self.branch)
        except GhError as e:
            if e.status == 404:
                return None
            raise
        if isinstance(r, dict) and r.get("content") is not None:
            return base64.b64decode(r["content"])
        return None

    def list_dir(self, path: str) -> list:
        try:
            r = self.gh.api("GET", self._contents(path) + "?ref=%s" % self.branch)
        except GhError as e:
            if e.status == 404:
                return []
            raise
        return sorted(x["name"] for x in r) if isinstance(r, list) else []

    def put(self, path: str, obj, message: str) -> dict:
        """Create `path`. Idempotent for identical content; never overwrites different content."""
        data = encode(obj)
        body = {"message": message, "content": base64.b64encode(data).decode(), "branch": self.branch}
        created_branch = False
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = self.gh.api("PUT", self._contents(path), body)
                return {"commit": (r or {}).get("commit", {}).get("sha"), "created": True, "path": path}
            except GhError as e:
                last = e
                text = e.message.lower()
                if e.status == 404 and not created_branch:
                    self.ensure_branch()
                    created_branch = True
                    continue
                if e.status == 422 and (('"sha"' in text and "supplied" in text) or "already exists" in text):
                    existing = self.get(path)
                    if existing == data:
                        return {"commit": None, "created": False, "path": path}
                    raise StoreConflict("path exists with different content: %s" % path)
                retryable = e.status in (409, 422, 429, 500, 502, 503, 504) or e.status == 0
                if not retryable or attempt == self.retries:
                    raise
                self.sleep(min(30.0, (2 ** attempt) * 0.25 * (0.5 + self.rng())))
        raise last  # pragma: no cover
