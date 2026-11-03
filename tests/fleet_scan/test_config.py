"""Config loading + cross-validation: every problem is aggregated, exit code is non-zero."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from guideline_checker.cli import main
from guideline_checker.fleet.scan.config import load_config, sha256_canonical
from guideline_checker.fleet.scan.errors import ConfigValidationError


def _edit(path: Path, mutate) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _problems(config_dir: Path) -> list[str]:
    with pytest.raises(ConfigValidationError) as info:
        load_config(config_dir)
    return info.value.problems


class TestValidConfig:
    def test_fixture_loads_and_indexes_catalog(self, config) -> None:
        assert len(config.conformance.rules) == 10
        assert config.lookup("CHRYSA/ALPHA") is not None  # case-insensitive
        assert config.lookup("chrysa/unknown") is None
        assert config.warnings == ()

    def test_fingerprints_are_stable_sha256(self, fixture_config_dir: Path) -> None:
        first, second = load_config(fixture_config_dir), load_config(fixture_config_dir)
        assert first.config_fingerprint == second.config_fingerprint
        assert len(first.rules_sha256) == 64

    def test_fingerprint_changes_with_rules_not_with_reporters(self, fixture_config_dir: Path) -> None:
        before = load_config(fixture_config_dir).config_fingerprint
        _edit(fixture_config_dir / "reporters.yaml", lambda d: d["cli"].update(max_rows=3))
        assert load_config(fixture_config_dir).config_fingerprint == before
        _edit(fixture_config_dir / "conformance.yaml", lambda d: d.update(rules_version="test-2"))
        assert load_config(fixture_config_dir).config_fingerprint != before

    def test_fingerprint_never_contains_the_token(self, fixture_config_dir: Path, monkeypatch) -> None:
        before = load_config(fixture_config_dir).config_fingerprint
        monkeypatch.setenv("FLEET_SCAN_TEST_TOKEN", "ghp_" + "z" * 36)
        assert load_config(fixture_config_dir).config_fingerprint == before

    def test_optional_files_absent_are_warnings(self, fixture_config_dir: Path) -> None:
        for name in ("github.yaml", "reporters.yaml", "standards-mapping.yaml", "skills-mapping.yaml"):
            (fixture_config_dir / name).unlink()
        config = load_config(fixture_config_dir)
        assert config.github.api_url == "https://api.github.com"
        assert len(config.warnings) == 4

    def test_canonical_hash_ignores_key_order(self) -> None:
        assert sha256_canonical({"a": 1, "b": 2}) == sha256_canonical({"b": 2, "a": 1})


class TestInvalidConfig:
    def test_missing_dir(self, tmp_path: Path) -> None:
        assert "config dir not found" in _problems(tmp_path / "nope")[0]

    def test_missing_required_files(self, fixture_config_dir: Path) -> None:
        (fixture_config_dir / "conformance.yaml").unlink()
        (fixture_config_dir / "repos.yaml").unlink()
        problems = _problems(fixture_config_dir)
        assert any("conformance.yaml: missing" in p for p in problems)
        assert any("repos.yaml: missing" in p for p in problems)

    def test_unparseable_yaml_and_non_mapping(self, fixture_config_dir: Path) -> None:
        (fixture_config_dir / "github.yaml").write_text("api_url: [unclosed", encoding="utf-8")
        (fixture_config_dir / "reporters.yaml").write_text("- a list\n", encoding="utf-8")
        problems = _problems(fixture_config_dir)
        assert any("github.yaml: cannot be parsed" in p for p in problems)
        assert any("reporters.yaml: top level must be a mapping" in p for p in problems)

    def test_unknown_method_type_is_rejected_with_rule_id(self, fixture_config_dir: Path) -> None:
        _edit(fixture_config_dir / "conformance.yaml", lambda d: d["rules"][0]["method"].update(type="shell_exec"))
        problems = _problems(fixture_config_dir)
        assert any("rules[DOC-001].method" in p and "unknown method type 'shell_exec'" in p for p in problems)

    def test_duplicate_rule_id_and_bad_pattern(self, fixture_config_dir: Path) -> None:
        def mutate(doc):
            doc["rules"][1]["id"] = "DOC-001"
            doc["rules"][2]["id"] = "bad-id"

        _edit(fixture_config_dir / "conformance.yaml", mutate)
        problems = _problems(fixture_config_dir)
        assert any("rules[bad-id].id" in p for p in problems)
        # the duplicate is only detectable once the file validates: fix the pattern, keep the duplicate
        _edit(fixture_config_dir / "conformance.yaml", lambda d: d["rules"][2].update(id="TST-009"))
        assert "conformance.yaml: duplicate rule id DOC-001" in _problems(fixture_config_dir)

    def test_unknown_profile_in_rule_and_negative_chaining(self, fixture_config_dir: Path) -> None:
        def mutate(doc):
            doc["rules"][0]["profiles"] = ["rust"]
            doc["profiles"]["meta"] = {"detect": {"none_of_profiles": ["docs-only", "ghost"]}}
            doc["profiles"]["all"] = {"detect": {"any_file": ["x"]}}

        _edit(fixture_config_dir / "conformance.yaml", mutate)
        problems = _problems(fixture_config_dir)
        assert any("rule DOC-001 references unknown profile 'rust'" in p for p in problems)
        assert any("'docs-only' is itself negative" in p for p in problems)
        assert any("none_of_profiles unknown 'ghost'" in p for p in problems)
        assert any("profile 'all' is implicit" in p for p in problems)

    def test_invalid_method_params(self, fixture_config_dir: Path) -> None:
        def mutate(doc):
            doc["rules"][3]["method"]["params"]["pattern"] = "([unclosed"
            doc["rules"][0]["method"]["params"]["any_of"] = ["../etc/passwd"]
            doc["rules"][7]["method"]["params"]["last_n"] = 500
            doc["rules"][5]["method"]["params"] = {"field": "secret_field", "op": "eq", "value": 1}
            doc["rules"][4]["method"]["params"]["typo"] = 1

        _edit(fixture_config_dir / "conformance.yaml", mutate)
        problems = "\n".join(_problems(fixture_config_dir))
        assert "invalid regular expression" in problems
        assert "must not contain '..'" in problems
        assert "rules[SCM-001]" in problems
        assert "rules[REPO-001]" in problems
        assert "Extra inputs are not permitted" in problems

    def test_file_regex_needs_exactly_one_target_and_in_needs_list(self, fixture_config_dir: Path) -> None:
        def mutate(doc):
            doc["rules"][3]["method"]["params"]["any_of"] = ["a.toml"]
            doc["rules"][5]["method"]["params"] = {"field": "archived", "op": "in", "value": False}

        _edit(fixture_config_dir / "conformance.yaml", mutate)
        problems = "\n".join(_problems(fixture_config_dir))
        assert "exactly one of 'path'" in problems
        assert "op 'in' needs a list value" in problems

    def test_mapping_references_unknown_rule_and_profile(self, fixture_config_dir: Path) -> None:
        _edit(fixture_config_dir / "standards-mapping.yaml", lambda d: d["requirements"][1].update(rules=["SEC-404"]))
        _edit(
            fixture_config_dir / "skills-mapping.yaml",
            lambda d: d["skills"][0].update(rule_ids=[{"id": "TST-404"}], profiles=["cobol"]),
        )
        problems = _problems(fixture_config_dir)
        assert "standards-mapping.yaml: references unknown rule id 'SEC-404'" in problems
        assert "skills-mapping.yaml: references unknown rule id 'TST-404'" in problems
        assert "skills-mapping.yaml: references unknown profile 'cobol'" in problems

    def test_skills_mapping_keys_are_cross_checked(self, fixture_config_dir: Path) -> None:
        _edit(
            fixture_config_dir / "skills-mapping.yaml",
            lambda d: d["skills"][0].update(related_rule_ids=["TST-405"], applies_to_profiles=["fortran"]),
        )
        problems = _problems(fixture_config_dir)
        assert "skills-mapping.yaml: references unknown rule id 'TST-405'" in problems
        assert "skills-mapping.yaml: references unknown profile 'fortran'" in problems

    def test_catalog_consistency(self, fixture_config_dir: Path) -> None:
        def mutate(doc):
            doc["subsets"]["pilot"].append("chrysa/not-in-catalog")
            doc["subsets"]["dupes"] = ["chrysa/alpha", "Chrysa/Alpha"]
            doc["projects"][1]["repos"].append({"full_name": "chrysa/alpha"})
            doc["projects"][3]["repos"][0]["scan_profile"] = ["fortran"]
            doc["projects"][4]["id"] = "alpha"

        _edit(fixture_config_dir / "repos.yaml", mutate)
        problems = "\n".join(_problems(fixture_config_dir))
        assert "subset 'pilot' references chrysa/not-in-catalog, absent from catalog" in problems
        assert "subset 'dupes' contains duplicates" in problems
        assert "repo chrysa/alpha listed twice" in problems
        assert "scan_profile unknown 'fortran'" in problems
        assert "duplicate project id 'alpha'" in problems

    def test_exclusion_requires_reason(self, fixture_config_dir: Path) -> None:
        _edit(fixture_config_dir / "repos.yaml", lambda d: d["projects"][2]["repos"][0].update(exclusion_reason=None))
        problems = _problems(fixture_config_dir)
        assert any("include: false requires a non-empty exclusion_reason" in p for p in problems)

    def test_archived_repo_is_refused_in_catalog(self, fixture_config_dir: Path) -> None:
        _edit(fixture_config_dir / "repos.yaml", lambda d: d["projects"][1]["repos"][0].update(archived=True))
        problems = _problems(fixture_config_dir)
        assert any("chrysa/beta: archived repositories are out of scope" in p for p in problems)

    def test_notion_reporter_cannot_be_enabled_and_api_must_be_https(self, fixture_config_dir: Path) -> None:
        _edit(fixture_config_dir / "reporters.yaml", lambda d: d["notion"].update(enabled=True))
        _edit(fixture_config_dir / "github.yaml", lambda d: d.update(api_url="http://api.github.test"))
        problems = "\n".join(_problems(fixture_config_dir))
        assert "reporters.yaml: notion.enabled" in problems
        assert "api_url must use https://" in problems

    def test_custom_api_host_needs_explicit_opt_in(self, fixture_config_dir: Path) -> None:
        _edit(fixture_config_dir / "github.yaml", lambda d: d.update(allow_custom_api_host=False))
        problems = "\n".join(_problems(fixture_config_dir))
        assert "set allow_custom_api_host: true" in problems

    def test_problems_from_several_files_are_aggregated(self, fixture_config_dir: Path) -> None:
        _edit(fixture_config_dir / "conformance.yaml", lambda d: d["rules"][0].update(severity="urgent"))
        _edit(fixture_config_dir / "repos.yaml", lambda d: d["projects"][0].update(perimeter="mars"))
        problems = _problems(fixture_config_dir)
        assert {p.split(":")[0] for p in problems} >= {"conformance.yaml", "repos.yaml"}


class TestValidateConfigCommand:
    def test_ok_exit_zero(self, fixture_config_dir: Path, capsys) -> None:
        assert main(["fleet-scan", "validate-config", "--config-dir", str(fixture_config_dir)]) == 0
        assert "config OK: 10 rule(s)" in capsys.readouterr().out

    def test_invalid_exit_non_zero(self, fixture_config_dir: Path, capsys) -> None:
        _edit(fixture_config_dir / "conformance.yaml", lambda d: d["rules"][0]["method"].update(type="nope"))
        assert main(["fleet-scan", "validate-config", "--config-dir", str(fixture_config_dir)]) == 2
        assert "unknown method type 'nope'" in capsys.readouterr().err

    def test_warnings_go_to_stderr(self, fixture_config_dir: Path, capsys) -> None:
        (fixture_config_dir / "skills-mapping.yaml").unlink()
        assert main(["fleet-scan", "validate-config", "--config-dir", str(fixture_config_dir)]) == 0
        assert "skills-mapping.yaml: absent" in capsys.readouterr().err


def test_fingerprint_ignores_transport_tuning(fixture_config_dir: Path) -> None:
    before = load_config(fixture_config_dir).config_fingerprint
    _edit(fixture_config_dir / "github.yaml", lambda d: d.update(concurrency=8))
    assert load_config(fixture_config_dir).config_fingerprint == before
    _edit(fixture_config_dir / "github.yaml", lambda d: d.update(content={"max_file_bytes": 10}))
    assert load_config(fixture_config_dir).config_fingerprint != before
