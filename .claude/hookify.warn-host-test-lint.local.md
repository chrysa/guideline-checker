---
name: warn-host-test-lint
enabled: true
event: bash
action: warn
conditions:
  - field: command
    operator: regex_match
    pattern: ^\s*(pytest|ruff|mypy|tsc|black|isort)\b
---

⚠️ **Host test/lint invocation detected.**
chrysa execution rule: tests/lint/build run via **Docker or pre-commit ONLY** —
never directly on the host. Use `pre-commit run …`, `make …`, or a `docker compose run …`
wrapper instead.
