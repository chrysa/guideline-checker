---
name: dependency-audit
description: 'Use when auditing guideline-checker dependencies for CVEs — tree-sitter/tree-sitter-typescript/tree-sitter-javascript grammars and the optional web extra (fastapi, ldap3, PyJWT). Not for adding new dependencies.'
---

# Dependency audit — CVE pass on native and security-sensitive deps

## When to invoke
Auto-invoke when: reviewing `pyproject.toml` dependency bumps, doing a periodic security
pass, or before a release involving the `web` extra.

## Why this repo needs it

- `tree-sitter` / `tree-sitter-typescript` / `tree-sitter-javascript` are native-code
  grammars parsing untrusted repository content — a parser CVE is a real attack surface.
- The optional `web` extra (`fastapi`, `ldap3`, `PyJWT`) adds auth-adjacent dependencies
  (LDAP bind, JWT verification) that see security advisories more often than typical
  pure-Python libs.
- `pip-audit` is already a declared dev dependency (`pyproject.toml`) but nothing runs it
  as a recurring check.

## Workflow

```bash
pip-audit --strict
pip-audit --extra web --strict   # covers fastapi/ldap3/PyJWT when the web extra is used
```

- Triage every finding: pin bump available → bump and re-run tests; no fix available →
  document the accepted risk with the CVE id and rationale.
- Re-run after any `pyproject.toml` dependency version change, not just on a schedule.
