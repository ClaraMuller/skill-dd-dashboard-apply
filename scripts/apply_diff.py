#!/usr/bin/env python3
"""Apply a Datadog dashboard export (<name>.new.json) onto the tracked <name>.json
of the metis-deployments repo with a minimal diff.

Usage: python3 -I apply_diff.py <path/to/<name>.new.json>

Rules (R1-R8) are described in ../SKILL.md. Exit code is non-zero, with a message on
stderr, on any failure. The script never runs a git command that mutates state.
"""
import copy
import json
import os
import re
import subprocess
import sys
from collections import Counter

DATA_DIR = "terraform/modules/infra/common/datadog-org/data"
REPO_NAME = "metis-deployments"

# R6: root keys of the target that are never changed.
ROOT_IGNORED = {
    "title", "description", "tags", "id", "pause_auto_refresh", "url",
    "author_handle", "author_name", "created_at", "modified_at",
}
# R7: export-only noise, ignored when added or removed with exactly this value.
NOISE = {"time": {}, "on_right_yaxis": False}
# R5: only these layout fields are applied for widgets present in both files.
LAYOUT_APPLIED = {"width", "height"}
# R3: list items are matched on the first of these keys present on every item.
MATCH_KEYS = ("id", "name", "formula")

ignored = Counter()
applied = Counter()


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def find_repo():
    cwd = os.getcwd()
    code, top = git(cwd, "rev-parse", "--show-toplevel")
    candidates = [top] if code == 0 else []
    child = os.path.join(cwd, REPO_NAME)
    if os.path.isdir(os.path.join(child, ".git")):
        candidates.append(child)
    for c in candidates:
        _, url = git(c, "config", "--get", "remote.origin.url")
        if os.path.basename(c) == REPO_NAME or re.search(rf"/{REPO_NAME}(\.git)?$", url):
            return c
    fail(f"{REPO_NAME} repository not found in the current context")


def match_key(items):
    if not items or not all(isinstance(i, dict) for i in items):
        return None
    for k in MATCH_KEYS:
        if all(k in i for i in items):
            vals = [i[k] for i in items]
            if len(set(map(json.dumps, vals))) == len(vals):
                return k
    return None


def is_noise(key, value):
    return key in NOISE and value == NOISE[key]


def merge_layout(tgt, new):
    for k in set(tgt) | set(new):
        if tgt.get(k) == new.get(k):
            continue
        if k in LAYOUT_APPLIED and k in new:
            tgt[k] = new[k]
            applied["R5 layout size"] += 1
        else:
            ignored["R5 layout position/other"] += 1


def merge_dict(tgt, new, root=False):
    out = {}
    keys = list(tgt)
    for k in new:
        if k in tgt:
            continue
        if root and k in ROOT_IGNORED:
            ignored["R6 root metadata"] += 1
            continue
        if is_noise(k, new[k]):
            ignored["R7 export noise"] += 1
            continue
        # R4: insert after the key that precedes it in the new file.
        prev = list(new).index(k) - 1
        pos = keys.index(list(new)[prev]) + 1 if prev >= 0 and list(new)[prev] in keys else 0
        keys.insert(pos, k)
        tgt = {**tgt, k: copy.deepcopy(new[k])}
        applied["added"] += 1
    for k in keys:
        if k not in new:
            if root and k in ROOT_IGNORED:
                ignored["R6 root metadata"] += 1
                out[k] = tgt[k]
            elif is_noise(k, tgt[k]):
                ignored["R7 export noise"] += 1
                out[k] = tgt[k]
            else:
                applied["removed"] += 1
            continue
        out[k] = merge_value(k, tgt[k], new[k], root)
    return out


def merge_list(tgt, new):
    key = match_key(tgt) if match_key(tgt) and match_key(tgt) == match_key(new) else None
    if key is None:
        if tgt == new:
            return tgt
        if all(isinstance(i, dict) for i in tgt + new):
            # R3: no stable key, merge item by item on the index.
            common = min(len(tgt), len(new))
            res = [merge_dict(t, n) for t, n in zip(tgt, new)]
            if len(tgt) > common:
                applied["removed"] += len(tgt) - common
            if len(new) > common:
                applied["added"] += len(new) - common
            return res + copy.deepcopy(new[common:])
        applied["replaced"] += 1
        return copy.deepcopy(new)
    by_new = {json.dumps(i[key]): i for i in new}
    res = []
    for item in tgt:
        n = by_new.get(json.dumps(item[key]))
        if n is None:
            applied["removed"] += 1
        else:
            res.append(merge_dict(item, n))
    kept = {json.dumps(i[key]) for i in res}
    # R4: new items go after the item that precedes them in the new file.
    for idx, n in enumerate(new):
        k = json.dumps(n[key])
        if k in kept:
            continue
        pos = 0
        if idx > 0:
            prev = json.dumps(new[idx - 1][key])
            pos = next((i + 1 for i, r in enumerate(res) if json.dumps(r[key]) == prev), len(res))
        res.insert(pos, copy.deepcopy(n))
        kept.add(k)
        applied["added"] += 1
    return res


def merge_value(key, tgt, new, root=False):
    if root and key in ROOT_IGNORED:
        if tgt != new:
            ignored["R6 root metadata"] += 1
        return tgt
    if key == "layout" and isinstance(tgt, dict) and isinstance(new, dict):
        merge_layout(tgt, new)
        return tgt
    if isinstance(tgt, dict) and isinstance(new, dict):
        return merge_dict(tgt, new)
    if isinstance(tgt, list) and isinstance(new, list):
        return merge_list(tgt, new)
    if tgt != new:
        applied["changed"] += 1
        return copy.deepcopy(new)
    return tgt


def main():
    if len(sys.argv) != 2:
        fail("usage: apply_diff.py <path/to/name.new.json>")
    src = os.path.abspath(sys.argv[1])
    if not src.endswith(".new.json"):
        fail("input file name must end with .new.json")
    if not os.path.isfile(src):
        fail(f"file not found: {src}")
    try:
        new = json.load(open(src))
    except ValueError as e:
        fail(f"{src} is not valid JSON: {e}")

    repo = find_repo()
    name = os.path.basename(src)[: -len(".new.json")] + ".json"
    target = os.path.join(repo, DATA_DIR, name)
    if not os.path.isfile(target):
        fail(f"tracked dashboard not found: {os.path.relpath(target, repo)}")
    _, branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch in ("main", "master"):
        fail(f"{REPO_NAME} is on '{branch}': ask to create a dedicated branch first")

    text = open(target).read()
    m = re.search(r"^( +)\S", text, re.M)
    indent = len(m.group(1)) if m else 2
    obj = json.loads(text)
    dumped = json.dumps(obj, indent=indent, ensure_ascii=False)
    nl = "\n" if text.endswith("\n") else ""
    if dumped + nl != text:
        fail("target cannot round-trip byte-identically; refusing to reformat it")

    result = merge_dict(obj, new, root=True)
    # Self-check: a second pass must have nothing left to apply.
    snap = (Counter(applied), Counter(ignored))
    applied.clear(), ignored.clear()
    merge_dict(copy.deepcopy(result), new, root=True)
    leftovers = sum(applied.values())
    applied.clear(), applied.update(snap[0])
    ignored.clear(), ignored.update(snap[1])

    open(target, "w").write(json.dumps(result, indent=indent, ensure_ascii=False) + nl)

    subprocess.run(["git", "-C", repo, "--no-pager", "diff", "--color=always", "--", target])
    print(f"\nTarget: {os.path.relpath(target, repo)}")
    print("Applied: " + (", ".join(f"{k}={v}" for k, v in sorted(applied.items())) or "nothing"))
    print("Ignored: " + (", ".join(f"{k}={v}" for k, v in sorted(ignored.items())) or "nothing"))
    if leftovers:
        print(f"warning: {leftovers} difference(s) still pending after apply", file=sys.stderr)


if __name__ == "__main__":
    main()
