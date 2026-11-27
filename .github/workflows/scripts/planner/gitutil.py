"""Thin git helpers. All reads are of data; nothing here executes repository code."""

from __future__ import annotations

import subprocess


class GitError(RuntimeError):
    pass


def run(repo: str, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, timeout=300)
    if p.returncode != 0 and check:
        raise GitError("git %s failed: %s" % (" ".join(args[:3]), p.stderr.strip()[:300]))
    return p.stdout if p.returncode == 0 else ""


def show(repo: str, ref: str, path: str) -> str:
    """File content at `ref`, or '' if absent."""
    return run(repo, "show", "%s:%s" % (ref, path), check=False)


def ls_tree(repo: str, ref: str) -> list:
    out = run(repo, "ls-tree", "-r", "--name-only", "-z", ref)
    return [p for p in out.split("\0") if p]


def merge_base(repo: str, a: str, b: str) -> str:
    out = run(repo, "merge-base", a, b, check=False).strip()
    return out or a


def changed_files(repo: str, base: str, head: str) -> list:
    """Names changed between the merge base of base/head and head (the PR diff), -z safe."""
    mb = merge_base(repo, base, head)
    out = run(repo, "diff", "--name-only", "-z", "%s..%s" % (mb, head))
    return [p for p in out.split("\0") if p]


def diff_text(repo: str, base: str, head: str, path: str) -> str:
    mb = merge_base(repo, base, head)
    return run(repo, "diff", "-U0", "%s..%s" % (mb, head), "--", path, check=False)


def head_sha(repo: str, ref: str = "HEAD") -> str:
    return run(repo, "rev-parse", ref).strip()
