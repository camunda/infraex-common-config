"""Offline event files: the layout used by the Python reference tools (replay, learner, audit).

The system of record in production is Elasticsearch, written by the Camunda process; there is no
branch store any more. These helpers read and write the same event documents as plain files, for
`planner audit` over an export, for the replay and for the tests.

Layout:
  decisions/pr/<N>/<head_sha>/<ts>-<labels8>.json
  events/YYYY/MM/DD/<ts>-<type>-<id>.json            outcome | escape | weights | checkpoint | watermark | override
"""

from __future__ import annotations

import json
import os

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
