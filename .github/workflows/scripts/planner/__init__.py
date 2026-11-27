"""Test-selection planner: decides which CI workflows a pull request needs.

Standard library only (PyYAML or `yq` is used to read workflow files when available).
See planner/README.md and the design in the A6 notes. Entry point: `python3 -m planner`.
"""

__version__ = "1.0.0"
RULES_VERSION = "1.0.0"
SCHEMA = "planner.event/1"
