"""Small shared helpers: canonical JSON, hashing, timestamps, ids."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SUITE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
PATH_RE = re.compile(r"^[A-Za-z0-9_./@+=,~-]{1,300}$")


def canonical(obj) -> bytes:
    """Deterministic JSON encoding used for hashes and ids."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compact_ts(value: str) -> str:
    """2026-11-06T09:41:12Z -> 20261106T094112Z (sortable, filename safe)."""
    return parse_iso(value).strftime("%Y%m%dT%H%M%SZ")


def event_id(prefix: str, payload) -> str:
    return "%s_%s" % (prefix, sha256_hex(canonical(payload))[:12])


def days_between(later: str, earlier: str) -> float:
    return (parse_iso(later) - parse_iso(earlier)).total_seconds() / 86400.0


def suite_name(path: str) -> str:
    """Suites are identified by workflow file name, e.g. aws_ecs_dual_region_fargate_tests.yml."""
    return path.rsplit("/", 1)[-1]


def _field(spec: str, lo: int, hi: int) -> set:
    vals: set = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
        vals.update(range(a, b + 1, step))
    return vals


def cadence(cron: str) -> str:
    """Human description of a cron expression ('weekly Wed 03:00 UTC', 'daily 04:00 UTC')."""
    try:
        m, h, dom, mon, dow = cron.split()
        per_day = len(_field(h, 0, 23)) * len(_field(m, 0, 59))
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        if dow != "*":
            days = sorted(d % 7 for d in _field(dow, 0, 7))
            if len(days) < 7:
                if per_day > 1:
                    return "%d runs/day on %s" % (per_day, "/".join(names[d] for d in days))
                return "weekly %s %s:%s UTC" % ("/".join(names[d] for d in days), h.zfill(2), m.zfill(2))
        if dom != "*":
            return "monthly (day %s)" % dom
        if per_day > 1:
            return "%d runs/day" % per_day
        return "daily %s:%s UTC" % (h.zfill(2), m.zfill(2))
    except (ValueError, KeyError):
        return "cron %s" % cron[:40]
