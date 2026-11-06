"""Human views of a decision: PR comment and job summary. Built only from validated data."""

from __future__ import annotations

import re

MARKER = "<!-- planner-decision -->"
_SAFE = re.compile(r"[^A-Za-z0-9_./:@ ()+=,;~%#>-]")


def esc(s, n=160) -> str:
    """Escape untrusted strings for Markdown tables: strip markup, cap length."""
    return _SAFE.sub("?", str(s))[:n].replace("|", "/")


def _code(s, n=100) -> str:
    return "`%s`" % esc(s, n).replace("`", "'")


def summary_md(d: dict, store_url: str | None = None) -> str:
    run = [(n, v) for n, v in sorted(d["decision"].items()) if v["action"] == "run" and v["layer"] not in ("L0", "L1", "L2")]
    skip = [(n, v) for n, v in sorted(d["decision"].items()) if v["action"] == "skip"]
    cheap = [n for n, v in d["decision"].items() if v["action"] == "run" and v["layer"] in ("L0", "L1", "L2")]
    L = [MARKER, "## Test selection", "",
         "Mode **%s**. Planner `%s`, rules `%s`, weights `%s`. Decision `%s`%s." % (
             esc(d["mode"]), esc(d["planner_version"][:12]), esc(d["rules_version"]), esc(d["weights_version"]), esc(d["id"]),
             (" ([record](%s))" % store_url) if store_url else ""), ""]
    if d["mode"] == "report":
        L += ["Report only: nothing is skipped by this decision.", ""]
    L += ["**Runs:** %d cloud suites and %d cheap checks. **Skips:** %d suites." % (len(run), len(cheap), len(skip)), ""]
    if run:
        L += ["| Suite | Layer | Why |", "|---|---|---|"]
        for n, v in run:
            L.append("| %s | %s | %s |" % (_code(n), esc(v["layer"]), esc("; ".join(v["reasons"]), 300)))
        L.append("")
    if skip:
        L += ["<details><summary>Skipped suites and when they run next</summary>", "", "| Suite | Enforced | Next from-scratch run |", "|---|---|---|"]
        for n, v in skip:
            sn = d["safety_net"].get(n, {})
            L.append("| %s | %s | %s |" % (_code(n), "yes" if v["enforced"] else "no", esc(sn.get("next_scheduled", "-"))))
        L += ["", "</details>", ""]
    if d.get("rules_fired"):
        L += ["<details><summary>Rules fired</summary>", ""]
        for r in d["rules_fired"][:30]:
            L.append("- **%s** %s: %s" % (esc(r["rule"]), _code(r["subject"], 120), esc(r["reason"], 240)))
        L += ["", "</details>", ""]
    if d.get("rotation"):
        rot = d["rotation"]
        L += ["Rotation picked %s (%s)." % (_code(rot["picked"]), esc("; ".join(rot["reasons"]), 240)), ""]
    ov = d["overrides"]
    if ov["applied"]:
        L += ["Overrides applied: %s." % ", ".join(_code(x) for x in ov["applied"]), ""]
    L += ["Optional escape hatches: label `planner:run-all` restores the full path-triggered set; `run_<suite>` forces one suite. "
          "No label or approval is needed for the normal path."]
    return "\n".join(L) + "\n"
