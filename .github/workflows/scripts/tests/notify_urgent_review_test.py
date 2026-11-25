import json
import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = (
    pathlib.Path(__file__).resolve().parents[2] / "notify-urgent-review-global.yml"
)
SCOPE_STEP = textwrap.dedent(
    WORKFLOW.read_text()
    .split("            - name: Check scope labels\n", 1)[1]
    .split("              run: |\n", 1)[1]
    .split("\n            # This job", 1)[0]
)


class ScopeLabelsTest(unittest.TestCase):
    def test_scope_labels(self):
        cases = [
            ("[]", [], "true"),
            ('["component:infraex"]', ["component:infraex"], "true"),
            ('["component:infraex"]', ["component:self-managed"], "false"),
            (
                '["component:infraex", "component:self-managed"]',
                ["component:self-managed"],
                "true",
            ),
            ('["component:infraex"]', [], "false"),
            ("[1]", [], None),
            ("{}", [], None),
            ("invalid", [], None),
        ]
        with tempfile.TemporaryDirectory() as directory:
            event = pathlib.Path(directory) / "event.json"
            output = pathlib.Path(directory) / "output"
            for scope, labels, expected in cases:
                with self.subTest(scope=scope, labels=labels):
                    event.write_text(
                        json.dumps(
                            {
                                "pull_request": {
                                    "labels": [{"name": label} for label in labels]
                                }
                            }
                        )
                    )
                    output.write_text("")
                    result = subprocess.run(
                        ["bash", "-c", SCOPE_STEP],
                        env={
                            **os.environ,
                            "SCOPE_LABELS": scope,
                            "GITHUB_EVENT_PATH": str(event),
                            "GITHUB_OUTPUT": str(output),
                        },
                        capture_output=True,
                        text=True,
                    )
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(output.read_text(), f"matched={expected}\n")


if __name__ == "__main__":
    unittest.main()
