"""Read YAML without adding a dependency: PyYAML when importable, else the `yq` binary."""

from __future__ import annotations

import json
import subprocess


def loads(text: str):
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        pass
    p = subprocess.run(["yq", "-o=json", "."], input=text, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError("yq failed: %s" % p.stderr.strip()[:200])
    return json.loads(p.stdout)
