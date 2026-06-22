"""Tests for the named content-scanner registry (``detect.scan``)."""

from __future__ import annotations

import pytest

from guideline_checker.scanners import (
    VALID_SCANS,
    run_scans,
    shannon_entropy,
    unknown_scans,
)

_FAKE_KEY_LINE = 'api_key = "Zx9Qm2Lp7Vt4Rk8Nw1Yb6Hs3DfAa5Cc"'


class TestSecretAssignmentScanner:
    def test_flags_high_entropy_secret_assignment(self) -> None:
        findings = run_scans(["secret-assignment"], _FAKE_KEY_LINE)
        assert len(findings) == 1
        assert findings[0][0] == 1

    def test_skips_low_entropy_placeholder(self) -> None:
        # Dictionary-ish value, >= 12 chars but well below the entropy gate.
        assert run_scans(["secret-assignment"], 'password = "passwordpassword"') == []

    def test_skips_short_value(self) -> None:
        assert run_scans(["secret-assignment"], 'token = "abc12345"') == []

    @pytest.mark.parametrize(
        "line",
        [
            'token = "${VAULT_TOKEN}"',
            'api_key = "$API_KEY"',
            'secret = "<your-secret-here>"',
            'password = "{{ env_password }}"',
        ],
    )
    def test_skips_env_or_template_references(self, line: str) -> None:
        assert run_scans(["secret-assignment"], line) == []

    def test_ignores_non_secret_key(self) -> None:
        # High-entropy value, but the key does not name a secret.
        assert run_scans(["secret-assignment"], 'commit_hash = "4eC39HqLyjWDarjtT1zdp7dc"') == []

    def test_exact_allowed_value_is_skipped(self) -> None:
        line = 'secret = "super-secret-should-not-leak"'
        assert run_scans(["secret-assignment"], line) != []  # flagged without allowlist
        assert run_scans(["secret-assignment"], line, frozenset({"super-secret-should-not-leak"})) == []

    def test_partial_allowlist_entry_does_not_suppress_real_secret(self) -> None:
        # The allowlist matches the WHOLE value, never a substring — otherwise a short
        # entry like "super-secret" would mask every value that merely contains it.
        line = 'secret = "super-secret-should-not-leak"'
        assert run_scans(["secret-assignment"], line, frozenset({"super-secret"})) != []

    def test_reports_each_offending_line(self) -> None:
        content = f"{_FAKE_KEY_LINE}\nx = 1\nclient_secret = 'Zx9Qm2Lp7Vt4Rk8Nw1Yb6Hs3Df'\n"
        findings = run_scans(["secret-assignment"], content)
        assert [lineno for lineno, _ in findings] == [1, 3]


class TestRegistry:
    def test_unknown_scans_lists_unregistered(self) -> None:
        assert unknown_scans(["secret-assignment", "nope"]) == ["nope"]
        assert unknown_scans(["secret-assignment"]) == []

    def test_unknown_scan_name_is_noop(self) -> None:
        assert run_scans(["does-not-exist"], _FAKE_KEY_LINE) == []

    def test_valid_scans_contains_secret_assignment(self) -> None:
        assert "secret-assignment" in VALID_SCANS


class TestShannonEntropy:
    def test_empty_is_zero(self) -> None:
        assert shannon_entropy("") == 0.0

    def test_random_exceeds_word(self) -> None:
        assert shannon_entropy("Zx9Qm2Lp7Vt4Rk8Nw1Yb6Hs3DfAa5Cc") > shannon_entropy("passwordpassword")
