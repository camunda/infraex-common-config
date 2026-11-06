"""GitHub API access through the `gh` CLI (preinstalled on runners), injectable for tests."""

from __future__ import annotations

import json
import re
import subprocess


class GhError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__("HTTP %s: %s" % (status, message[:300]))
        self.status = status
        self.message = message


def _subprocess_runner(args: list, stdin: str | None):
    p = subprocess.run(["gh"] + args, input=stdin, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


class Gh:
    def __init__(self, runner=None):
        self.runner = runner or _subprocess_runner

    def api(self, method: str, path: str, body=None, paginate: bool = False):
        args = ["api", "-X", method, path]
        stdin = None
        if body is not None:
            args += ["--input", "-"]
            stdin = json.dumps(body)
        if paginate:
            args += ["--paginate", "--slurp"]
        rc, out, err = self.runner(args, stdin)
        if rc != 0:
            m = re.search(r"HTTP (\d{3})", err + out)
            raise GhError(int(m.group(1)) if m else 0, (err or out).strip())
        if not out.strip():
            return None
        data = json.loads(out)
        if paginate and isinstance(data, list):
            merged: list = []
            for page in data:
                merged.extend(page if isinstance(page, list) else [page])
            return merged
        return data
