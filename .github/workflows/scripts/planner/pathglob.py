"""GitHub `paths:` filter semantics (patterns, `!` negation applied in order, paths-ignore)."""

from __future__ import annotations

import re

_cache: dict = {}


def _compile(pattern: str):
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "[":
            j = pattern.find("]", i)
            if j > 0:
                out += pattern[i:j + 1]
                i = j + 1
                continue
            out += "\\["
        else:
            out += re.escape(c)
        i += 1
    return re.compile("^" + out + "$")


def match(pattern: str, path: str) -> bool:
    rx = _cache.get(pattern)
    if rx is None:
        rx = _cache[pattern] = _compile(pattern)
    return bool(rx.match(path))


def paths_filter_hit(paths, ignore, changed: str):
    """Return the pattern that makes a workflow run for `changed`, or None.

    `paths` None/empty means "no filter" (the workflow runs for any change).
    """
    hit = None
    if paths:
        for p in paths:
            if p.startswith("!"):
                if hit and match(p[1:], changed):
                    hit = None
            elif match(p, changed):
                hit = p
        if hit is None:
            return None
    else:
        hit = "(no paths filter)"
    for ig in ignore or []:
        if match(ig, changed):
            return None
    return hit
