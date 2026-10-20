"""Tests for scripts/apply_diff.py. Run: python3 -I -m unittest discover -s tests"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "apply_diff.py")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import apply_diff as ad  # noqa: E402

DATA_DIR = ad.DATA_DIR


def read(path):
    with open(path) as f:
        return f.read()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def widget(wid, query="q", **layout):
    return {
        "id": wid,
        "definition": {"requests": [{
            "formulas": [{"formula": "query1"}],
            "queries": [{"name": "query1", "query": query}],
        }]},
        "layout": {"x": 0, "y": 0, "width": 4, "height": 2, **layout},
    }


def dash(*widgets, **root):
    return {"title": "[Managed] T", "tags": ["team:x"], "widgets": list(widgets), **root}


class MergeRules(unittest.TestCase):
    def setUp(self):
        ad.applied.clear()
        ad.ignored.clear()

    def merge(self, tgt, new):
        return ad.merge_dict(copy.deepcopy(tgt), copy.deepcopy(new), root=True)

    def test_changed_value_only_touches_that_value(self):
        res = self.merge(dash(widget(1, "a")), dash(widget(1, "b")))
        self.assertEqual(res["widgets"][0]["definition"]["requests"][0]["queries"][0]["query"], "b")
        self.assertEqual(dict(ad.applied), {"changed": 1})

    def test_idempotent(self):
        new = dash(widget(1, "b"), widget(2))
        once = self.merge(dash(widget(1, "a")), new)
        ad.applied.clear()
        self.assertEqual(self.merge(once, new), once)
        self.assertEqual(sum(ad.applied.values()), 0)

    def test_added_query_and_formula_follow_new_file_position(self):
        tgt = dash(widget(1))
        new = copy.deepcopy(tgt)
        req = new["widgets"][0]["definition"]["requests"][0]
        req["formulas"].append({"formula": "query2"})
        req["queries"].append({"name": "query2", "query": "q2"})
        res = self.merge(tgt, new)
        self.assertEqual(res, new)
        self.assertEqual(dict(ad.applied), {"added": 2})

    def test_added_widget_is_inserted_after_its_predecessor(self):
        res = self.merge(dash(widget(1), widget(3)), dash(widget(1), widget(2), widget(3)))
        self.assertEqual([w["id"] for w in res["widgets"]], [1, 2, 3])

    def test_removed_widget(self):
        res = self.merge(dash(widget(1), widget(2)), dash(widget(2)))
        self.assertEqual([w["id"] for w in res["widgets"]], [2])
        self.assertEqual(dict(ad.applied), {"removed": 1})

    def test_matching_is_by_key_not_position(self):
        res = self.merge(dash(widget(1, "a"), widget(2, "b")), dash(widget(2, "b"), widget(1, "a")))
        self.assertEqual([w["id"] for w in res["widgets"]], [1, 2])
        self.assertEqual(sum(ad.applied.values()), 0)

    def test_layout_size_applied_position_ignored(self):
        res = self.merge(dash(widget(1)), dash(widget(1, x=5, y=9, width=6, height=3)))
        self.assertEqual(res["widgets"][0]["layout"], {"x": 0, "y": 0, "width": 6, "height": 3})
        self.assertEqual(dict(ad.applied), {"R5 layout size": 2})
        self.assertEqual(dict(ad.ignored), {"R5 layout position/other": 2})

    def test_new_widget_keeps_full_layout(self):
        res = self.merge(dash(widget(1)), dash(widget(1), widget(2, x=7, y=7)))
        self.assertEqual(res["widgets"][1]["layout"]["x"], 7)

    def test_root_metadata_wins(self):
        new = dash(widget(1), title="Other", tags=[], pause_auto_refresh=False)
        res = self.merge(dash(widget(1)), new)
        self.assertEqual(res["title"], "[Managed] T")
        self.assertEqual(res["tags"], ["team:x"])
        self.assertNotIn("pause_auto_refresh", res)
        self.assertEqual(ad.ignored["R6 root metadata"], 3)

    def test_export_noise_ignored_both_ways(self):
        tgt, new = dash(widget(1)), dash(widget(1))
        tgt["widgets"][0]["definition"]["requests"][0]["on_right_yaxis"] = False
        new["widgets"][0]["definition"]["time"] = {}
        res = self.merge(tgt, new)
        definition = res["widgets"][0]["definition"]
        self.assertNotIn("time", definition)
        self.assertIn("on_right_yaxis", definition["requests"][0])
        self.assertEqual(ad.ignored["R7 export noise"], 2)
        self.assertEqual(sum(ad.applied.values()), 0)

    def test_non_noise_values_are_applied(self):
        new = dash(widget(1))
        new["widgets"][0]["definition"]["time"] = {"live_span": "1h"}
        res = self.merge(dash(widget(1)), new)
        self.assertEqual(res["widgets"][0]["definition"]["time"], {"live_span": "1h"})

    def test_list_without_key_merges_by_index(self):
        res = ad.merge_list([{"a": 1}, {"a": 2}], [{"a": 1}, {"a": 3}, {"a": 4}])
        self.assertEqual(res, [{"a": 1}, {"a": 3}, {"a": 4}])
        self.assertEqual(dict(ad.applied), {"changed": 1, "added": 1})


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.repo = os.path.join(self.root, "metis-deployments")
        os.makedirs(os.path.join(self.repo, DATA_DIR))
        self.target = os.path.join(self.repo, DATA_DIR, "dashboard-x.json")
        with open(self.target, "w") as f:
            f.write(json.dumps(dash(widget(1, "a")), indent=4) + "\n")
        self.git("init", "-q", "-b", "main")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        self.git("checkout", "-qb", "feat/test")
        self.new = os.path.join(self.root, "dashboard-x.new.json")
        with open(self.new, "w") as f:
            json.dump(dash(widget(1, "b"), title="Other"), f, indent=2)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True)

    def run_script(self, arg, cwd=None):
        return subprocess.run([sys.executable, "-I", SCRIPT, arg], cwd=cwd or self.repo,
                              capture_output=True, text=True)

    def test_applies_and_prints_coloured_diff(self):
        r = self.run_script(self.new)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\x1b[", r.stdout)
        self.assertIn("Applied: changed=1", r.stdout)
        self.assertIn("R6 root metadata=1", r.stdout)
        self.assertEqual(json.loads(read(self.target))["title"], "[Managed] T")
        self.assertTrue(read(self.target).endswith("\n"))
        self.assertIn("    \"title\"", read(self.target))

    def test_works_from_parent_directory(self):
        self.assertEqual(self.run_script(self.new, cwd=self.root).returncode, 0)

    def test_second_run_applies_nothing(self):
        self.run_script(self.new)
        self.assertIn("Applied: nothing", self.run_script(self.new).stdout)

    def test_new_file_is_untouched(self):
        before = read(self.new)
        self.run_script(self.new)
        self.assertEqual(read(self.new), before)

    def test_rejects_bad_suffix(self):
        bad = os.path.join(self.root, "dashboard-x.json")
        write(bad, "{}")
        r = self.run_script(bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn(".new.json", r.stderr)

    def test_fails_without_repo(self):
        with tempfile.TemporaryDirectory() as empty:
            r = self.run_script(self.new, cwd=empty)
        self.assertEqual(r.returncode, 1)
        self.assertIn("metis-deployments repository not found", r.stderr)

    def test_refuses_main_branch(self):
        self.git("checkout", "-q", "main")
        r = self.run_script(self.new)
        self.assertEqual(r.returncode, 1)
        self.assertIn("dedicated branch", r.stderr)

    def test_missing_target(self):
        other = os.path.join(self.root, "dashboard-y.new.json")
        write(other, "{}")
        r = self.run_script(other)
        self.assertEqual(r.returncode, 1)
        self.assertIn("tracked dashboard not found", r.stderr)

    def test_refuses_target_that_does_not_round_trip(self):
        write(self.target, '{"a":1}\n')
        r = self.run_script(self.new)
        self.assertEqual(r.returncode, 1)
        self.assertIn("round-trip", r.stderr)


if __name__ == "__main__":
    unittest.main()
