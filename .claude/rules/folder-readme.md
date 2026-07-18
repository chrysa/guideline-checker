# Folder README — Per-Directory Documentation

______________________________________________________________________

> Every meaningful folder in a repository must carry a `README.md` that
> explains what the folder is for. This makes any repo self-describing:
> a reader (human or agent) can open any directory and know its role,
> its structure, and the rules that govern it — without reading the code.

______________________________________________________________________

## Rule

When you create a new folder, or do substantial work inside an existing
folder that has no `README.md`, **add a `README.md` to that folder**.

The README **must** answer, in this order:

1. **Role** — what this folder is for, in one or two sentences.
1. **Structure** — the subfolders / key files and what each is.
1. **Should contain** — what kinds of files belong here.
1. **Should NOT contain** — what must never be placed here (and where it goes instead).
1. **Rules** — conventions that govern this folder (naming, size limits,
   dependencies allowed/forbidden, ownership, test requirements).

Keep it short and scannable. Link to the relevant rule files
(`.claude/rules/thresholds.md`, `class-design.md`, …) instead of repeating them.

______________________________________________________________________

## Scope

- **Apply to**: source folders, package/module folders, `tests/`, `docs/`,
  `scripts/`, `config/`, and any folder whose purpose is not obvious from its name.
- **Skip**: the repo root (covered by the top-level `README.md` / `CLAUDE.md`),
  generated or vendored dirs (`node_modules/`, `.venv/`, `dist/`, `build/`,
  `__pycache__/`, `.git/`), and empty placeholder dirs.
- Do not churn: only add a README to a folder you are already creating or
  substantially modifying. Do not open a mass-README PR unless explicitly asked.

______________________________________________________________________

## Template

```markdown
# <folder-name>

**Role.** <one or two sentences: why this folder exists>

## Structure

| Path            | Purpose                          |
| --------------- | -------------------------------- |
| `sub_a/`        | <what it holds>                  |
| `thing.py`      | <what it does>                   |

## Should contain

- <file kind> — <why>

## Should NOT contain

- <file kind> — put it in `<other location>` instead.

## Rules

- <convention 1 — e.g. one class per file, max 300 lines (see thresholds.md)>
- <convention 2 — e.g. no imports from `../infra`, domain layer stays pure>
```

______________________________________________________________________

## Why

- A repo becomes navigable folder-by-folder; onboarding and agent exploration
  drop straight to the right place.
- "Should NOT contain" prevents drift — files landing in the wrong layer get
  caught at write time, not in review.
- Complements the numeric limits in [`thresholds.md`](thresholds.md) and the
  structural conventions in [`class-design.md`](class-design.md).

______________________________________________________________________

## Manual verification

No dedicated linter. In PR review, check:

- Every new/modified non-trivial folder has a `README.md`.
- The README covers all five sections (Role, Structure, Should/Should-not, Rules).
- No duplication of global rules — links instead.
