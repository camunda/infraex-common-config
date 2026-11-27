"""Impact analysis: module graph, consumers, owner suites, shared-action users, path-triggered suites."""

from __future__ import annotations

import os
import posixpath
import re

from . import gitutil, hcl, pathglob


class Tree:
    """Read-only view of a repository at one ref (the PR head). Reads are `git` calls on data."""

    def __init__(self, repo: str, ref: str):
        self.repo, self.ref = repo, ref
        self._files = None
        self._cache = {}

    def files(self) -> list:
        if self._files is None:
            self._files = gitutil.ls_tree(self.repo, self.ref)
        return self._files

    def under(self, prefix: str) -> list:
        p = prefix.rstrip("/") + "/"
        return [f for f in self.files() if f.startswith(p)]

    def read(self, path: str) -> str:
        if path not in self._cache:
            self._cache[path] = gitutil.show(self.repo, self.ref, path)
        return self._cache[path]

    def grep(self, pattern: str, pathspecs: list, ref: str | None = None) -> list:
        """[(path, lineno, text)] for fixed-ref `git grep -n -E` (empty on no match)."""
        r = ref or self.ref
        out = gitutil.run(self.repo, "grep", "-n", "-I", "-E", "-e", pattern, r, "--", *pathspecs, check=False)
        res = []
        prefix = r + ":"
        for line in out.splitlines():
            if not line.startswith(prefix):
                continue
            m = re.match(r"^(.*?):(\d+):(.*)$", line[len(prefix):])
            if m:
                res.append((m.group(1), int(m.group(2)), m.group(3)))
        return res


def module_edges(tree: Tree) -> dict:
    """dir -> set of directories its .tf files source via relative `source = "../x"`."""
    edges: dict = {}
    for path, _, text in tree.grep(r'^[[:space:]]*source[[:space:]]*=[[:space:]]*"\.{1,2}/', ["*.tf"]):
        m = hcl.SOURCE_RE.search(text)
        if not m:
            continue
        src_dir = posixpath.dirname(path)
        tgt = posixpath.normpath(posixpath.join(src_dir, m.group(1)))
        if not tgt.startswith(".."):
            edges.setdefault(src_dir, set()).add(tgt)
    return edges


def modules_of(edges: dict) -> list:
    return sorted({t for ts in edges.values() for t in ts})


def module_of(mods: list, path: str):
    best = None
    for m in mods:
        if path == m or path.startswith(m + "/"):
            if best is None or len(m) > len(best):
                best = m
    return best


def consumers_of(edges: dict, module: str) -> list:
    rev: dict = {}
    for s, ts in edges.items():
        for t in ts:
            rev.setdefault(t, set()).add(s)
    seen, stack = set(), [module]
    while stack:
        m = stack.pop()
        for c in rev.get(m, ()):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return sorted(seen)


def ra_key(root: str) -> str:
    parts = root.split("/")
    return "/".join(parts[:parts.index("terraform")]) if "terraform" in parts else root


def ra_roots(edges: dict, mods: list) -> list:
    return sorted(set(edges) - set(mods))


def owner_modules(tree: Tree, mods: list) -> dict:
    """module -> test dir whose Go sources build it (derived from tfModule := "<name>/" in .test/src)."""
    out: dict = {}
    for path, _, text in tree.grep(r'tfModule[A-Za-z]*[[:space:]]*:?=[[:space:]]*"[A-Za-z0-9/_-]+"', ["*/.test/src/*.go", ".test/src/*.go"]):
        m = re.search(r'"([A-Za-z0-9/_-]+?)/?"', text)
        if not m:
            continue
        testdir = path.split("/src/")[0]
        parent = posixpath.dirname(testdir)
        mod = posixpath.normpath(posixpath.join(parent, m.group(1)))
        if mod in mods:
            out.setdefault(mod, set()).add(testdir)
    return out


def owner_suites(registry: dict, owners: dict) -> dict:
    """module -> sorted suite names whose workflow text mentions the module's test dir."""
    res: dict = {}
    for mod, dirs in owners.items():
        names = {s.name for s in registry.values() if not s.is_call_only and any(d in s.text for d in dirs)}
        if names:
            res[mod] = sorted(names)
    return res


def action_users(base_tree: Tree) -> dict:
    """action name -> set of workflow suite names that use it, following composite nesting."""
    direct_wf: dict = {}
    nested: dict = {}
    pat = r"uses:[[:space:]]*\./\.github/actions/[A-Za-z0-9_.-]+"
    for path, _, text in base_tree.grep(pat, [".github/workflows/*.yml", ".github/workflows/*.yaml", ".github/actions/*/action.yml"]):
        m = re.search(r"\./\.github/actions/([A-Za-z0-9_.-]+)", text)
        if not m:
            continue
        a = m.group(1)
        if path.startswith(".github/actions/"):
            nested.setdefault(path.split("/")[2], set()).add(a)
        else:
            direct_wf.setdefault(a, set()).add(posixpath.basename(path))
    rev: dict = {}
    for parent, used in nested.items():
        for u in used:
            rev.setdefault(u, set()).add(parent)
    users: dict = {}
    allacts = set(direct_wf) | set(rev) | {x for v in nested.values() for x in v}
    for a in allacts:
        chain, stack = {a}, [a]
        while stack:
            cur = stack.pop()
            for p in rev.get(cur, ()):
                if p not in chain:
                    chain.add(p)
                    stack.append(p)
        u = set()
        for c in chain:
            u |= direct_wf.get(c, set())
        users[a] = u
    return users


def suite_hit(suite, path: str):
    """Pattern that makes `suite` run on a PR touching `path` (None if it does not)."""
    if suite.pr_event != "pull_request":
        return None
    return pathglob.paths_filter_hit(suite.paths, suite.paths_ignore, path)


def triggered_suites(registry: dict, changed: list) -> dict:
    res: dict = {}
    for s in registry.values():
        if s.is_call_only or s.pr_event != "pull_request":
            continue
        for f in sorted(changed, key=lambda x: (x.startswith(".github/"), x)):
            p = suite_hit(s, f)
            if p:
                e = res.setdefault(s.name, {"patterns": {}, "files": []})
                e["patterns"].setdefault(p, f)
                e["files"].append(f)
    return res


def owning_suites(registry: dict, files: list) -> list:
    """Suites whose PR path filter covers at least one of `files` (the suite 'owns' that area)."""
    out = []
    for s in registry.values():
        if s.is_call_only or s.pr_event != "pull_request":
            continue
        if any(suite_hit(s, f) for f in files):
            out.append(s.name)
    return sorted(out)
