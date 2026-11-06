"""Bounded regex readers for the few Terraform facts the planner needs.

Not a parser: it reads `variable`/`output` block names, `type`/`default` of variables, relative
module `source` lines and `# renovate:` version pins. Input files are untrusted, so every
function caps its input size and uses non-backtracking-prone patterns.
"""

from __future__ import annotations

import re

MAX_BYTES = 512 * 1024
SOURCE_RE = re.compile(r'^[ \t]*source[ \t]*=[ \t]*"(\.{1,2}/[^"\n]{1,300})"', re.M)
PIN_RE = re.compile(r"#[ \t]*renovate:[^\n]*?depName=(\S+)")
VAL_RE = re.compile(r'(?:default[ \t]*=|version[ \t]*=|:[ \t])[ \t]*"?([0-9][\w.\-]*)"?')
BLOCK_HEAD = r'^%s[ \t]+"([^"\n]{1,100})"[ \t]*\{'


def _cap(text: str) -> str:
    return text[:MAX_BYTES]


def blocks(text: str, kind: str) -> dict:
    """name -> block body for top-level `kind "name" { ... }` blocks (brace matched)."""
    text = _cap(text)
    out = {}
    for m in re.finditer(BLOCK_HEAD % re.escape(kind), text, re.M):
        i, depth = m.end(), 1
        while i < len(text) and depth:
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        out[m.group(1)] = text[m.end():i - 1]
    return out


def var_info(body: str) -> dict:
    t = re.search(r"^[ \t]*type[ \t]*=[ \t]*([^\n]+)$", body, re.M)
    d = re.search(r"^[ \t]*default[ \t]*=[ \t]*([^\n]+)$", body, re.M)
    return {"type": t.group(1).strip() if t else None, "has_default": bool(d), "default": d.group(1).strip() if d else None}


def module_sources(text: str) -> list:
    return SOURCE_RE.findall(_cap(text))


def interface(texts: dict) -> dict:
    """texts: file name -> content for one module directory. Returns {'variables':{}, 'outputs':set}."""
    variables, outputs = {}, set()
    for name, text in texts.items():
        if name.endswith("outputs.tf"):
            outputs |= set(blocks(text, "output"))
        else:
            variables.update(blocks(text, "variable"))
    return {"variables": variables, "outputs": outputs}


def iface_diff(old_texts: dict, new_texts: dict, module: str) -> dict:
    old, new = interface(old_texts), interface(new_texts)
    rem_o, add_o = sorted(old["outputs"] - new["outputs"]), sorted(new["outputs"] - old["outputs"])
    rem_v = sorted(set(old["variables"]) - set(new["variables"]))
    req = set(v for v in set(new["variables"]) - set(old["variables"]) if not var_info(new["variables"][v])["has_default"])
    chg_t, chg_d = [], []
    for v in set(old["variables"]) & set(new["variables"]):
        a, b = var_info(old["variables"][v]), var_info(new["variables"][v])
        if a["type"] != b["type"]:
            chg_t.append(v)
        if a["default"] != b["default"]:
            chg_d.append(v)
        if a["has_default"] and not b["has_default"]:
            req.add(v)
    significant = bool(rem_o or rem_v or req or chg_t)
    return {
        "module": module, "removed_outputs": rem_o, "added_outputs": add_o,
        "possible_output_rename": bool(rem_o and add_o), "removed_variables": rem_v,
        "new_required_variables": sorted(req), "changed_variable_types": sorted(chg_t),
        "changed_defaults": sorted(chg_d), "significant": significant,
    }


def pins(text: str) -> list:
    """[(depName, value)] for `# renovate:` annotated pins, in file order."""
    lines = _cap(text).splitlines()
    out = []
    for i, line in enumerate(lines):
        m = PIN_RE.search(line)
        if not m:
            continue
        for nxt in lines[i + 1:i + 4]:
            if nxt.strip().startswith("#"):
                continue
            v = VAL_RE.search(nxt)
            if v:
                out.append((m.group(1), v.group(1)))
                break
    return out


def major(value: str, dep: str, major_level: dict):
    n = int(major_level.get(dep, 1))
    parts = re.findall(r"\d+", value)[:n]
    return tuple(int(p) for p in parts) if parts else None


def pin_changes(old_text: str, new_text: str, major_level: dict) -> list:
    res = []
    for (da, va), (db, vb) in zip(pins(old_text), pins(new_text)):
        if da == db and va != vb:
            res.append({"dep": da, "from": va, "to": vb, "major": major(va, da, major_level) != major(vb, da, major_level)})
    return res


PIN_ONLY_RE = re.compile(r'^[-+][ \t]*(default[ \t]*=|[\w]*version[ \t]*[=:])[ \t]*"?[\w.\-~>= ]+"?[ \t]*$')


def pin_only_diff(diff_body: str, path: str) -> bool:
    """True if a file change is only version-default lines, goldens or docs."""
    if re.search(r"(\.md|\.MD|\.txt|golden/.*\.json)$", path):
        return True
    body = [l for l in diff_body.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return bool(body) and all(PIN_ONLY_RE.match(l) for l in body)
