"""Init command: scaffold default instruction files in a project."""

from __future__ import annotations

from pathlib import Path

_DEFAULT_INSTRUCTIONS: dict[str, str] = {
    "python.instructions.md": """\
---
applyTo: "**/*.py"
description: "Python coding standards"
---

- No print() calls in production code
- No bare except clauses
- No eval() or exec() calls
- No import * (wildcard imports)
- No hardcoded password or secret or credential or token
- No TODO or FIXME comments in production code
- No type: ignore comments
- No global statement usage
- Max file length: 500
""",
    "typescript.instructions.md": """\
---
applyTo: "**/*.{ts,tsx}"
description: "TypeScript / React coding standards"
---

- No any type annotations
- No @ts-ignore comments
- No @ts-nocheck directives
- No console.log() calls
- No inline styles in JSX components
- No TODO or FIXME comments in production code
""",
    "django.instructions.md": """\
---
applyTo: "**/*.py"
description: "Django / DRF coding standards"
---

- No DEBUG = True in committed settings
- No wildcard ALLOWED_HOSTS (no ["*"])
- No CORS_ALLOW_ALL_ORIGINS = True
- No raw SQL (no .raw( or .extra()
- No hardcoded SECRET_KEY (load secret_key from env)
- No print() calls in production code
- No bare except clauses
""",
    "security.instructions.md": """\
---
applyTo: "**/*"
description: "Security best practices (OWASP-aligned)"
---

- No hardcoded password or secret or credential or key or token
- No shell=True usage in subprocess calls
- No pickle usage
- No hardcoded IP addresses
- No eval() calls
""",
}


def run_init(root: Path, instructions_dir: Path | None = None, *, force: bool = False) -> int:
    """Scaffold default instruction files.

    Args:
        root: Project root directory.
        instructions_dir: Target directory (default: root/.github/instructions/).
        force: Overwrite existing files if True.

    Returns:
        Exit code (0 on success, 1 on error).
    """
    target = instructions_dir or root / ".github" / "instructions"
    target.mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0
    for filename, content in _DEFAULT_INSTRUCTIONS.items():
        dest = target / filename
        if dest.exists() and not force:
            print(f"[guideline-checker] Skipped (already exists): {dest.relative_to(root)}")
            skipped += 1
            continue
        dest.write_text(content, encoding="utf-8")
        print(f"[guideline-checker] Created: {dest.relative_to(root)}")
        created += 1

    print(f"[guideline-checker] Init done — {created} file(s) created, {skipped} skipped.")
    if skipped > 0:
        print("[guideline-checker] Use --force to overwrite existing files.")
    return 0
