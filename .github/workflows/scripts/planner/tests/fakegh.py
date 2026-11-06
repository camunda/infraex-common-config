"""An in-memory stand-in for `gh api`, enough of the contents and git-data endpoints for the store."""

from __future__ import annotations

import base64
import json
import re


class FakeGh:
    def __init__(self, branch_exists=True):
        self.files = {}
        self.branch = branch_exists
        self.calls = []
        self.fail = []  # queue of (status, text) to raise on the next PUT
        self.comments = []
        self.runs = {}
        self.statuses = []
        self.n = 0

    def __call__(self, args, stdin):
        method, path = args[2], args[3]
        body = json.loads(stdin) if stdin else None
        self.calls.append((method, path))
        err = lambda code, msg: (1, "", "gh: %s (HTTP %d)" % (msg, code))
        m = re.match(r"repos/[^/]+/[^/]+/(.*)$", path)
        rel = m.group(1) if m else path
        relq = rel.split("?")[0]
        mr = re.match(r"actions/workflows/([^/]+)/runs", rel)
        if mr:
            payload = {"workflow_runs": self.runs.get(mr.group(1), [])}
            return 0, json.dumps([payload] if "--paginate" in args else payload), ""
        mr = re.match(r"actions/runs/(\d+)/attempts/(\d+)", rel)
        if mr:
            return 0, json.dumps({"conclusion": "failure"}), ""
        if rel.startswith("git/ref/heads/"):
            return (0, json.dumps({"ref": "x"}), "") if self.branch else err(404, "Not Found")
        if rel == "git/blobs":
            return 0, json.dumps({"sha": "b" * 40}), ""
        if rel == "git/trees":
            return 0, json.dumps({"sha": "t" * 40}), ""
        if rel == "git/commits":
            return 0, json.dumps({"sha": "c" * 40}), ""
        if rel == "git/refs":
            self.branch = True
            return 0, json.dumps({}), ""
        if rel.startswith("contents/"):
            p = rel[len("contents/"):].split("?")[0]
            if method == "PUT":
                if self.fail:
                    code, msg = self.fail.pop(0)
                    return err(code, msg)
                if not self.branch:
                    return err(404, "Branch not found")
                if p in self.files:
                    return err(422, 'Invalid request.\n\n"sha" wasn\'t supplied.')
                self.files[p] = base64.b64decode(body["content"])
                self.n += 1
                return 0, json.dumps({"commit": {"sha": ("%040x" % self.n)}}), ""
            if p in self.files:
                return 0, json.dumps({"content": base64.b64encode(self.files[p]).decode()}), ""
            names = sorted({f[len(p) + 1:].split("/")[0] for f in self.files if f.startswith(p + "/")})
            if names:
                return 0, json.dumps([{"name": n} for n in names]), ""
            return err(404, "Not Found")
        if relq.startswith("issues/") and relq.endswith("/comments") and method == "GET":
            return 0, json.dumps(self.comments), ""
        if relq.startswith("issues/") and relq.endswith("/comments") and method == "POST":
            self.comments.append({"id": len(self.comments) + 1, "body": body["body"], "user": {"type": "Bot"}})
            return 0, json.dumps({}), ""
        if rel.startswith("issues/comments/") and method == "PATCH":
            cid = int(rel.rsplit("/", 1)[1])
            for c in self.comments:
                if c["id"] == cid:
                    c["body"] = body["body"]
            return 0, json.dumps({}), ""
        if rel.startswith("statuses/"):
            self.statuses.append(body)
            return 0, json.dumps({}), ""
        return err(404, "unhandled %s" % rel)
