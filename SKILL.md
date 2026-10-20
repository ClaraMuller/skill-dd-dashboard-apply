---
name: dd-dashboard-apply
description: >-
  Apply a Datadog dashboard export (`<name>.new.json`) onto the tracked
  `<name>.json` of the metis-deployments repo with a minimal diff, then show the
  result as a coloured `git diff`. User-invoked only (`/dd-dashboard-apply
  <path/to/dashboard-x.new.json>`). Stops immediately if the file name does not
  end with `.new.json` or if no metis-deployments repository is in the current
  context.
license: Proprietary
disable-model-invocation: true
effort: low
argument-hint: <path/to/dashboard-x.new.json>
metadata:
  author: clara.muller
  version: "0.1.0"
---

# Apply a Datadog dashboard export with a minimal diff

Takes one Datadog dashboard JSON export named `<name>.new.json`, patches the tracked
`terraform/modules/infra/common/datadog-org/data/<name>.json` of `metis-deployments`
in place, and prints the coloured diff, as `git diff` would.

All logic lives in `scripts/apply_diff.py`: do not re-implement it, do not edit the JSON by hand.

## Steps

1. The argument is the path to the `.new.json` file. If it does not end with
   `.new.json`, tell the user and **stop**.
2. Run, from the user's current directory (the script checks that a `metis-deployments`
   repository is the current repo or a direct child):

   ```bash
   python3 -I "<this skill's base directory>/scripts/apply_diff.py" "<argument>"
   ```

3. If the command fails, print its error message and **stop**. Do not try to work
   around it (no `cd`, no other repo, no git write command).
4. Otherwise show the output as-is: the coloured diff, then the `Applied` and `Ignored`
   lines. Do not summarise the diff in your own words.

## Minimised diff rules

Goal: the diff contains only the intentional changes.

- **R1 — Patch, never overwrite.** The target is edited in place; key order,
  indentation and line endings are kept. Aborts if the target cannot round-trip
  byte-identically.
- **R2 — Semantic comparison.** Parsed JSON is compared; formatting differences are
  not changes.
- **R3 — Stable matching.** Widgets match by `id` (recursively), `queries` by `name`,
  `formulas` by `formula`, else by index.
- **R4 — Apply only the delta.** Changed value → replace it; added item → insert after
  the item that precedes it in the new file; removed item → delete it. Siblings are
  untouched.
- **R5 — Size applied, position ignored.** For widgets in both files, `layout.width` /
  `layout.height` differences are applied; `layout.x` / `layout.y` differences are
  ignored (window-dependent). Widgets only in the new file are added with their full
  layout.
- **R6 — Target metadata wins.** Root `title`, `description`, `tags`, `id`,
  `pause_auto_refresh`, `url`, `author_*`, `created_at`, `modified_at` are never changed
  (`ROOT_IGNORED` in the script).
- **R7 — Ignore export-only noise.** Added or removed `"time": {}` and
  `on_right_yaxis: false` (`NOISE` in the script).
- **R8 — Report what was skipped.** Ignored differences are counted per rule; a
  non-idempotent result prints a warning.

## Tests

From this skill's directory: `python3 -I -m unittest discover -s tests` (stdlib only).

## Guardrails

- Never run a git command that mutates state (`add`, `commit`, `checkout`, `stash`, …).
  The script only reads git state and writes the target JSON.
- Refuses to run if the repo is on `main` / `master`: ask the user to create a branch.
- The `.new.json` is never modified or deleted.
- Colours are ANSI escapes from `git diff --color=always`; some UIs show them raw.
