"""A glossary defines values. A value parsed as a mechanism is ADR D-0016's failure.

Issue #255: `CLAUDE.md` defines the allowed values of a `runtime:` field as a bullet
list. The loader lifted one entry — "Nothing to run." — out of that list and turned
it into a constraint, then flagged every `asyncio.create_subprocess_exec` call in the
repo against it. No amount of fixing the code can satisfy a constraint derived from a
definition, so a blocking gate leaves the repo red forever or baselines a finding that
is not debt.
"""

from __future__ import annotations

from guideline_checker.loader import _extract_rules

_RUNTIME_GLOSSARY = """
## Container-runtime policy

Every repo carries a `runtime:` field in `repos.yml`:

- `container` — runs as a service. Provides Dockerfile(s) + docker-compose + HEALTHCHECK.
- `exempt:lib` — distributed or imported (library, plugin, pre-commit hook, CLI).
- `exempt:config` — no executable runtime (config, knowledge base, deploy manifests). Nothing to run.
- `exempt:native` — bound to a host OS, device, cloud platform, or editor.
- `pending` — pre-code scaffold; flips to `container` at first code.
"""


def test_an_enum_glossary_yields_no_rules() -> None:
    assert _extract_rules(_RUNTIME_GLOSSARY) == []


def test_prose_after_a_glossary_is_still_read() -> None:
    """Dropping the definitions must not drop the constraints around them."""
    content = _RUNTIME_GLOSSARY + "\n- Never commit a secret to the repository\n"
    assert any("Never commit a secret" in rule for rule in _extract_rules(content))


def test_a_single_dash_bullet_is_still_a_rule() -> None:
    """One definition-shaped line is not a glossary — a glossary is a list."""
    assert _extract_rules("- `print()` — do not call it in production code\n") == [
        "print() — do not call it in production code"
    ]


def test_a_colon_bullet_is_a_rule_not_a_definition() -> None:
    content = "- **Language**: English — all code, comments and docs.\n- **Commits**: Conventional Commits, always.\n"
    assert len(_extract_rules(content)) == 2


def test_two_separate_lists_are_judged_separately() -> None:
    content = _RUNTIME_GLOSSARY + "\n## Rules\n\n- Always pin a dependency version\n- Do not use a bare except\n"
    rules = _extract_rules(content)
    assert len(rules) == 2
    assert all("exempt" not in rule for rule in rules)


def test_an_imperative_anywhere_keeps_the_whole_block() -> None:
    """A list that both defines and demands is still read — dropping it loses a real rule."""
    content = (
        "- `venv/` — forbidden inside a project tree, the interpreter runs in the image.\n"
        "- `node_modules/` — forbidden inside a project tree, deps live in a named volume.\n"
    )
    assert len(_extract_rules(content)) == 2


def test_a_long_leading_term_is_not_a_definition_label() -> None:
    """A definition label is a term. A whole sentence in backticks is not."""
    content = (
        "- `a very long inline code span that is really a sentence and not a term at all` — do this\n"
        "- `another extremely long inline code span standing in for a whole clause here` — do that\n"
    )
    assert len(_extract_rules(content)) == 2


def test_a_numbered_definition_list_is_also_a_glossary() -> None:
    content = "1. `alpha` — the first value\n2. `beta` — the second value\n3. `gamma` — the third value\n"
    assert _extract_rules(content) == []
