---
applyTo: "**/*.py"
---

# Python coding standards for guideline-checker

## Imports
- Standard library imports first, then third-party, then local — separated by blank lines.
- Use explicit named imports only; avoid star imports.

## Functions
- Maximum function length: 50 lines.
- All public functions must have a docstring.

## Error handling
- Never use bare `except:` — always catch specific exceptions.
- Never suppress exceptions silently.

## Type hints
- All function signatures must include type hints.
- Return types must be annotated.
