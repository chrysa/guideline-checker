---
applyTo: "**/*.py"
description: "Python production code guidelines"
---

# Python guidelines

- No print() calls in production code
- No bare except clauses
- No eval() or exec() calls
- No wildcard imports (no import *)
- No TODO or FIXME comments
- No hardcoded secrets or credentials (no hardcoded password, api_key, secret)
- No global statement
- No type: ignore comments
- No assert statements outside tests
