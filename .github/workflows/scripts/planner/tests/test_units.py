import copy
import json
import unittest

from .. import hcl, pathglob, schema, util


class GlobTest(unittest.TestCase):
    def test_semantics(self):
        self.assertTrue(pathglob.match("aws/modules/**", "aws/modules/aurora/main.tf"))
        self.assertTrue(pathglob.match("aws/modules/**.tf", "aws/modules/aurora/main.tf"))
        self.assertFalse(pathglob.match("aws/*.tf", "aws/modules/main.tf"))
        self.assertTrue(pathglob.match("**/*.md", "a/b/c.md"))
        self.assertTrue(pathglob.match("**/*.md", "c.md"))

    def test_negation_is_ordered(self):
        paths = ["aws/**", "!aws/x/golden/**"]
        self.assertEqual(pathglob.paths_filter_hit(paths, [], "aws/y/main.tf"), "aws/**")
        self.assertIsNone(pathglob.paths_filter_hit(paths, [], "aws/x/golden/p.json"))
        # a positive pattern after the negation re-includes (GitHub semantics)
        self.assertEqual(pathglob.paths_filter_hit(["!aws/x/golden/**", "aws/**"], [], "aws/x/golden/p.json"), "aws/**")

    def test_no_filter_and_ignore(self):
        self.assertEqual(pathglob.paths_filter_hit([], [], "any"), "(no paths filter)")
        self.assertIsNone(pathglob.paths_filter_hit([], ["docs/**"], "docs/a.md"))


class HclTest(unittest.TestCase):
    def test_interface_diff_detects_rename_removal_and_required(self):
        old = {"variables.tf": 'variable "a" {\n  type = string\n  default = "x"\n}\nvariable "b" {\n  type = string\n}\n',
               "outputs.tf": 'output "o1" {\n  value = 1\n}\n'}
        new = {"variables.tf": 'variable "a" {\n  type = number\n  default = "x"\n}\nvariable "c" {\n  type = string\n}\n',
               "outputs.tf": 'output "o2" {\n  value = 1\n}\n'}
        d = hcl.iface_diff(old, new, "m")
        self.assertEqual(d["removed_outputs"], ["o1"])
        self.assertEqual(d["added_outputs"], ["o2"])
        self.assertTrue(d["possible_output_rename"])
        self.assertEqual(d["removed_variables"], ["b"])
        self.assertEqual(d["new_required_variables"], ["c"])
        self.assertEqual(d["changed_variable_types"], ["a"])
        self.assertTrue(d["significant"])

    def test_default_only_change_is_not_significant(self):
        old = {"variables.tf": 'variable "a" {\n  type = string\n  default = "1"\n}\n', "outputs.tf": ""}
        new = {"variables.tf": 'variable "a" {\n  type = string\n  default = "2"\n}\n', "outputs.tf": ""}
        d = hcl.iface_diff(old, new, "m")
        self.assertFalse(d["significant"])
        self.assertEqual(d["changed_defaults"], ["a"])

    def test_pins_and_majors(self):
        old = '# renovate: datasource=x depName=aurora-postgresql versioning=loose\ndefault = "17.9"\n'
        new = old.replace("17.9", "18.3")
        ch = hcl.pin_changes(old, new, {})
        self.assertTrue(ch[0]["major"])
        self.assertFalse(hcl.pin_changes(old, old.replace("17.9", "17.10"), {})[0]["major"])
        eks = '# renovate: datasource=x depName=amazon-eks\ndefault = "1.36"\n'
        self.assertTrue(hcl.pin_changes(eks, eks.replace("1.36", "1.37"), {"amazon-eks": 2})[0]["major"])
        self.assertFalse(hcl.pin_changes(eks, eks.replace("1.36", "1.37"), {})[0]["major"])

    def test_pin_only_diff(self):
        self.assertTrue(hcl.pin_only_diff('-  engine_version = "17.9"\n+  engine_version = "18.3"\n', "db.tf"))
        self.assertTrue(hcl.pin_only_diff("anything", "x/test/golden/tfplan-golden.json"))
        self.assertFalse(hcl.pin_only_diff('+resource "a" "b" {}\n', "main.tf"))

    def test_hostile_input_is_capped(self):
        big = "variable \"a\" {" + "x" * (hcl.MAX_BYTES * 2)
        self.assertLessEqual(len(hcl.blocks(big, "variable").get("a", "")), hcl.MAX_BYTES)  # capped, no crash
        self.assertLess(len(hcl.blocks("variable \"a\" {\n}\n" * 3, "variable")), 4)


class SchemaTest(unittest.TestCase):
    def good_outcome(self):
        return {"schema": "planner.event/1", "type": "outcome", "id": "out_0123456789ab", "ts": "2026-11-06T09:00:00Z",
                "repo": "o/r", "decision_id": None, "pr": 0, "head_sha": "a" * 40, "suite": "x_tests.yml", "run_id": 5,
                "attempt": 1, "event": "schedule", "branch": "main", "conclusion": "success", "started": "2026-11-06T07:00:00Z",
                "completed": "2026-11-06T09:00:00Z", "minutes": 120.0, "url": "https://github.com/o/r/actions/runs/5", "flake_hint": False}

    def test_valid_and_closed(self):
        o = self.good_outcome()
        self.assertEqual(schema.validate(o), [])
        o["surprise"] = 1
        self.assertTrue(schema.validate(o))

    def test_rejects_bad_values(self):
        for k, v in (("head_sha", "zz"), ("suite", "../x"), ("pr", -1), ("ts", "yesterday"), ("type", "nope"), ("minutes", "1")):
            o = self.good_outcome()
            o[k] = v
            self.assertTrue(schema.validate(o), k)

    def test_size_cap(self):
        o = self.good_outcome()
        o["url"] = "u" * 400
        self.assertTrue(schema.validate(o))
        self.assertTrue(schema.validate({"type": "weights", "x": "y" * (schema.MAX_EVENT_BYTES + 1)}))
        self.assertTrue(schema.validate("not a dict"))

    def test_util_helpers(self):
        self.assertEqual(util.compact_ts("2026-11-06T09:41:12Z"), "20261106T094112Z")
        self.assertEqual(util.cadence("0 3 * * 3"), "weekly Wed 03:00 UTC")
        self.assertEqual(util.cadence("0 4 * * *"), "daily 04:00 UTC")
        self.assertEqual(util.event_id("out", {"a": 1}), util.event_id("out", {"a": 1}))


if __name__ == "__main__":
    unittest.main()
