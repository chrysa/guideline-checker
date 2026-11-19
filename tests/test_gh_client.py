from __future__ import annotations

from collections.abc import Sequence

from guideline_checker.fleet.gh_client import GhClient, GhResult


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


class TestGhClientRepoExists:
    def test_true_when_repo_resolves(self) -> None:
        args = "api repos/chrysa/foo --jq .name"
        client = GhClient(runner=_runner({args: GhResult(True, "foo\n", "", 0)}))
        assert client.repo_exists("chrysa", "foo") is True

    def test_false_on_error(self) -> None:
        args = "api repos/chrysa/foo --jq .name"
        client = GhClient(runner=_runner({args: GhResult(False, "", "404", 1)}))
        assert client.repo_exists("chrysa", "foo") is False


class TestGhClientWrites:
    def test_branch_sha(self) -> None:
        args = "api repos/chrysa/foo/git/ref/heads/main --jq .object.sha"
        client = GhClient(runner=_runner({args: GhResult(True, "abc123\n", "", 0)}))
        assert client.branch_sha("chrysa", "foo", "main") == "abc123"

    def test_find_pr_returns_url_when_open(self) -> None:
        args = "pr list --repo chrysa/foo --head chore/distribution-fixes --state open --json url --jq .[0].url"
        client = GhClient(runner=_runner({args: GhResult(True, "https://github.com/chrysa/foo/pull/9\n", "", 0)}))
        assert client.find_pr("chrysa", "foo", "chore/distribution-fixes") == "https://github.com/chrysa/foo/pull/9"

    def test_find_pr_returns_none_when_absent(self) -> None:
        args = "pr list --repo chrysa/foo --head chore/distribution-fixes --state open --json url --jq .[0].url"
        client = GhClient(runner=_runner({args: GhResult(True, "\n", "", 0)}))
        assert client.find_pr("chrysa", "foo", "chore/distribution-fixes") is None


class TestGhClientPutAndAvailable:
    def test_available_reflects_binary_presence(self) -> None:
        # available() probes the real PATH; assert it returns a bool either way.
        assert isinstance(GhClient().available(), bool)

    def test_put_file_includes_sha_when_content_exists(self) -> None:
        seen: list[str] = []

        def runner(args: Sequence[str]) -> GhResult:
            joined = " ".join(args)
            seen.append(joined)
            if joined.endswith("--jq .sha"):
                return GhResult(True, "existing-sha\n", "", 0)  # file already exists
            return GhResult(True, "", "", 0)  # the PUT

        client = GhClient(runner=runner)
        assert client.put_file("chrysa", "foo", "LICENSE", "MIT\n", "msg", "branch") is True
        put_call = next(c for c in seen if "--method PUT" in c)
        assert "sha=existing-sha" in put_call
