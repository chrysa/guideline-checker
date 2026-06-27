from __future__ import annotations

from collections.abc import Sequence

from guideline_checker.gh_client import GhClient, GhResult


def _runner(responses: dict[str, GhResult]):
    """Fake runner keyed by the joined argument string."""

    def run(args: Sequence[str]) -> GhResult:
        return responses[" ".join(args)]

    return run


class TestGhClientReadFile:
    def test_returns_raw_text_on_success(self) -> None:
        args = "api -H Accept: application/vnd.github.raw repos/chrysa/foo/contents/LICENSE?ref=main"
        client = GhClient(runner=_runner({args: GhResult(True, "MIT License\n", "", 0)}))
        assert client.read_file("chrysa", "foo", "LICENSE", "main") == "MIT License\n"

    def test_returns_none_on_404(self) -> None:
        args = "api -H Accept: application/vnd.github.raw repos/chrysa/foo/contents/LICENSE?ref=main"
        client = GhClient(runner=_runner({args: GhResult(False, "", "gh: Not Found (HTTP 404)", 1)}))
        assert client.read_file("chrysa", "foo", "LICENSE", "main") is None


class TestGhClientDefaultBranch:
    def test_reads_default_branch(self) -> None:
        args = "api repos/chrysa/foo --jq .default_branch"
        client = GhClient(runner=_runner({args: GhResult(True, "develop\n", "", 0)}))
        assert client.default_branch("chrysa", "foo") == "develop"
