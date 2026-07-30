"""Tests for the extended rule engine v0.3 — new pattern families."""

from __future__ import annotations

from pathlib import Path

from guideline_checker.checker import (
    DISABLE_COMMENT,
    _build_checks,
    _check_length_rules,
    _python_strict_checks,
    _security_checks,
    _typescript_checks,
    run_checks,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_project(tmp_path: Path, filename: str, content: str, rule: str) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    inst_dir = root / ".github" / "instructions"
    inst_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix or ".py"
    (inst_dir / "rules.instructions.md").write_text(
        f"---\napplyTo: '**/*{ext}'\ndescription: 'test'\n---\n- {rule}\n",
        encoding="utf-8",
    )
    (root / filename).write_text(content, encoding="utf-8")
    return root, inst_dir


# ─── TypeScript checks ────────────────────────────────────────────────────────


class TestTypeScriptChecks:
    def test_detects_colon_any(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.ts", "const x: any = 1;\n", "No any type annotations")
        results = run_checks(root=root, instructions_dir=inst)
        assert any(": any" in v.line_content for r in results for v in r.violations)

    def test_detects_as_any(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.ts", "const x = y as any;\n", "No any type annotations")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("as any" in v.line_content for r in results for v in r.violations)

    def test_detects_ts_ignore(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.ts", "// @ts-ignore\nconst x = 1;\n", "No @ts-ignore comments")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("@ts-ignore" in v.line_content for r in results for v in r.violations)

    def test_detects_ts_nocheck(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.ts", "// @ts-nocheck\n", "No @ts-nocheck directives")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("@ts-nocheck" in v.line_content for r in results for v in r.violations)

    def test_detects_console_warn(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.ts", "console.warn('x');\n", "No console.warn calls")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("console.warn" in v.line_content for r in results for v in r.violations)

    def test_detects_inline_style(self, tmp_path: Path) -> None:
        root, inst = _make_project(
            tmp_path, "app.tsx", "<div style={{ color: 'red' }}>x</div>\n", "No inline styles in JSX"
        )
        results = run_checks(root=root, instructions_dir=inst)
        assert any("style={{" in v.line_content for r in results for v in r.violations)

    def test_typescript_checks_no_match(self) -> None:
        checks = _typescript_checks("no print calls")
        assert checks == []

    def test_avoid_any_variant(self) -> None:
        checks = _typescript_checks("avoid any usage")
        assert any(c.pattern == ": any" for c in checks)


# ─── Python strict checks ─────────────────────────────────────────────────────


class TestPythonStrictChecks:
    def test_detects_global_statement(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "def f():\n    global x\n    x = 1\n", "No global statement")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("global " in v.line_content for r in results for v in r.violations)

    def test_detects_type_ignore(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "x = foo()  # type: ignore\n", "No type: ignore comments")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("type: ignore" in v.line_content for r in results for v in r.violations)

    def test_detects_type_ignore_nospace(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "x = foo()  # type:ignore[misc]\n", "No type: ignore comments")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("type:ignore" in v.line_content for r in results for v in r.violations)

    def test_detects_mutable_default_list(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "def f(items=[]): pass\n", "No mutable default arguments")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("=[]" in v.line_content for r in results for v in r.violations)

    def test_detects_mutable_default_dict(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "def f(cfg={}): pass\n", "No mutable default arguments")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("={}" in v.line_content for r in results for v in r.violations)

    def test_python_strict_no_match(self) -> None:
        checks = _python_strict_checks("no print calls")
        assert checks == []


# ─── Security checks ──────────────────────────────────────────────────────────


class TestSecurityChecks:
    def test_detects_hardcoded_http(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", 'BASE_URL = "http://internal.server"\n', "No hardcoded URLs")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("http://" in v.line_content for r in results for v in r.violations)

    def test_detects_hardcoded_localhost(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", 'HOST = "127.0.0.1"\n', "No hardcoded IP addresses")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("127.0.0.1" in v.line_content for r in results for v in r.violations)

    def test_detects_shell_true(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", 'subprocess.run("ls", shell=True)\n', "No shell=True usage")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("shell=True" in v.line_content for r in results for v in r.violations)

    def test_detects_pickle_import(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "import pickle\n", "No pickle usage")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("import pickle" in v.line_content for r in results for v in r.violations)

    def test_detects_pickle_load(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "data = pickle.load(f)\n", "No pickle usage")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("pickle.load" in v.line_content for r in results for v in r.violations)

    def test_security_checks_no_match(self) -> None:
        checks = _security_checks("no eval calls")
        assert checks == []

    def test_detects_zero_zero_ip(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", 'HOST = "0.0.0.0"\n', "No hardcoded IP addresses")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("0.0.0.0" in v.line_content for r in results for v in r.violations)

    def test_detects_https_url(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", 'URL = "https://api.example.com"\n', "No hardcoded URLs")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("https://" in v.line_content for r in results for v in r.violations)


# ─── Length rules ─────────────────────────────────────────────────────────────


class TestLengthRules:
    def test_detects_file_too_long(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "\n".join(["x = 1"] * 600), "Maximum file length: 500")
        results = run_checks(root=root, instructions_dir=inst)
        assert len(results[0].violations) == 1
        assert "600" in results[0].violations[0].line_content

    def test_no_violation_when_within_limit(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "\n".join(["x = 1"] * 100), "Maximum file length: 500")
        results = run_checks(root=root, instructions_dir=inst)
        assert len(results[0].violations) == 0

    def test_max_n_lines_per_file_variant(self, tmp_path: Path) -> None:
        lines = [str(Path(__file__).parent / "tmp.py")]
        file_path = Path(lines[0]) if False else Path(__file__)
        file_lines = ["x = 1"] * 600
        violations = _check_length_rules(file_path, file_lines, "max 500 lines per file")
        assert len(violations) == 1

    def test_no_length_rule_returns_empty(self) -> None:
        violations = _check_length_rules(Path("app.py"), ["x = 1"], "no bare except")
        assert violations == []


# ─── Inline disable ───────────────────────────────────────────────────────────


class TestInlineDisable:
    def test_skips_suppressed_line(self, tmp_path: Path) -> None:
        root, inst = _make_project(
            tmp_path,
            "app.py",
            f'print("debug")  # {DISABLE_COMMENT}\n',
            "No print() calls",
        )
        results = run_checks(root=root, instructions_dir=inst)
        assert all(len(r.violations) == 0 for r in results)

    def test_non_suppressed_line_still_detected(self, tmp_path: Path) -> None:
        root, inst = _make_project(
            tmp_path,
            "app.py",
            f'print("ok")  # {DISABLE_COMMENT}\nprint("bad")\n',
            "No print() calls",
        )
        results = run_checks(root=root, instructions_dir=inst)
        violations = [v for r in results for v in r.violations]
        assert len(violations) == 1
        assert "bad" in violations[0].line_content


# ─── build_checks aggregation ─────────────────────────────────────────────────


class TestBuildChecksAggregation:
    def test_build_checks_returns_list(self) -> None:
        checks = _build_checks("no print calls and no eval and no any")
        assert isinstance(checks, (list, tuple))
        patterns = [c.pattern for c in checks]
        assert "print(" in patterns
        assert "eval(" in patterns

    def test_build_checks_empty_for_unknown_rule(self) -> None:
        checks = _build_checks("use proper naming conventions")
        assert len(checks) == 0


class TestPhraseWordBoundaries:
    """Short phrases must not bleed into longer words (`no exec` vs `no executable`)."""

    def test_no_executable_runtime_does_not_arm_exec_detector(self) -> None:
        prose = "exempt:config — no executable runtime (config, knowledge base). nothing to run."
        assert _build_checks(prose) == ()

    def test_no_evaluation_does_not_arm_eval_detector(self) -> None:
        assert _build_checks("no evaluation of user input happens here") == ()

    def test_real_exec_rule_still_arms_the_detector(self) -> None:
        patterns = [check.pattern for check in _build_checks("no exec calls in application code")]
        assert "exec(" in patterns

    def test_real_eval_rule_still_arms_the_detector(self) -> None:
        patterns = [check.pattern for check in _build_checks("no eval on runtime data")]
        assert "eval(" in patterns
