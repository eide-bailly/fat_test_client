"""Tests for scripts/fabric_git_github_bootstrap.py.

The connection-creation payload shape is load-bearing (Wave 1 spike,
plans/add-github-support.md): the creation method must be
``GitHubSourceControl.Contents``, the credential field must be ``key`` (not
``token``), and the optional ``url`` parameter must be omitted. These tests pin
that shape and the PAT secret boundary (sourced from GITHUB_PAT / .env, never
logged or echoed).
"""

from __future__ import annotations

from pathlib import Path

# scripts/ is not a package; conftest.py puts it on sys.path and stubs the heavy
# third-party imports (azure.identity, requests) before this module is collected.
import fabric_git_github_bootstrap as bootstrap
import pytest

PAT = "ghp_test-token-value"
DISPLAY_NAME = "github-client-org-client-repo"


class TestBuildConnectionBody:
    def test_matches_spike_verified_shape(self) -> None:
        body = bootstrap._build_connection_body(DISPLAY_NAME, PAT)

        assert body == {
            "connectivityType": "ShareableCloud",
            "displayName": DISPLAY_NAME,
            "connectionDetails": {
                "type": "GitHubSourceControl",
                "creationMethod": "GitHubSourceControl.Contents",
            },
            "credentialDetails": {
                "singleSignOnType": "None",
                "connectionEncryption": "NotEncrypted",
                "skipTestConnection": False,
                "credentials": {"credentialType": "Key", "key": PAT},
            },
        }

    def test_omits_url_parameter(self) -> None:
        body = bootstrap._build_connection_body(DISPLAY_NAME, PAT)
        assert "parameters" not in body["connectionDetails"]

    def test_credential_field_is_key_not_token(self) -> None:
        body = bootstrap._build_connection_body(DISPLAY_NAME, PAT)
        credentials = body["credentialDetails"]["credentials"]
        assert credentials["credentialType"] == "Key"
        assert "key" in credentials
        assert "token" not in credentials


class TestResolveGithubPat:
    def test_environment_wins_over_dotenv(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GITHUB_PAT", "ghp_env-token")
        (tmp_path / ".env").write_text("GITHUB_PAT=ghp_dotenv-token\n", encoding="utf-8")
        assert bootstrap._resolve_github_pat(tmp_path / "fabric.yml") == "ghp_env-token"

    def test_dotenv_parses_quotes_and_comments(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        (tmp_path / ".env").write_text(
            '# comment\n\nOTHER=value\nGITHUB_PAT="ghp_quoted-token"\n', encoding="utf-8"
        )
        assert bootstrap._resolve_github_pat(tmp_path / "fabric.yml") == "ghp_quoted-token"

    def test_missing_everywhere_returns_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        assert bootstrap._resolve_github_pat(tmp_path / "fabric.yml") is None


class TestCreateConnection:
    """Drives create_connection with stubbed credential/HTTP layers (conftest's
    azure.identity stub plus a monkeypatched requests.post)."""

    def _stub_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERVICE_PRINCIPAL_TENANT_ID", "tenant")
        monkeypatch.setenv("SERVICE_PRINCIPAL_CLIENT_ID", "client")
        monkeypatch.setenv("DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET", "secret")
        monkeypatch.setattr(bootstrap, "_fabric_headers", lambda credential: {})

    def test_exits_with_plain_language_error_when_pat_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        args = bootstrap._parse_args(
            [
                "create-connection",
                "--display-name",
                DISPLAY_NAME,
                "--config",
                str(tmp_path / "fabric.yml"),
            ]
        )

        with pytest.raises(SystemExit) as excinfo:
            args.func(args)

        message = str(excinfo.value)
        assert "GITHUB_PAT" in message
        assert "manual" in message  # documents the portal fallback

    def test_posts_spike_verified_body_and_prints_only_connection_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        self._stub_auth(monkeypatch)
        monkeypatch.setenv("GITHUB_PAT", PAT)
        captured: dict = {}

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"id": "connection-guid", "displayName": DISPLAY_NAME}

        def fake_post(url: str, **kwargs: object) -> _Response:
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return _Response()

        monkeypatch.setattr(bootstrap.requests, "post", fake_post)

        args = bootstrap._parse_args(
            [
                "create-connection",
                "--display-name",
                DISPLAY_NAME,
                "--config",
                str(tmp_path / "fabric.yml"),
            ]
        )
        args.func(args)

        assert captured["url"] == f"{bootstrap.FABRIC_API}/connections"
        assert captured["json"]["credentialDetails"]["credentials"] == {
            "credentialType": "Key",
            "key": PAT,
        }
        # The PAT is never echoed to stdout/stderr; only the connection id is printed.
        out, err = capsys.readouterr()
        assert PAT not in out
        assert PAT not in err
        assert "connection-guid" in out


class TestParseArgs:
    def test_subcommand_required(self) -> None:
        with pytest.raises(SystemExit):
            bootstrap._parse_args([])

    def test_validate_sync_defaults(self) -> None:
        args = bootstrap._parse_args(["validate-sync", "--workspace-id", "guid"])
        assert args.apply is False
        assert args.conflict_resolution is None

    def test_create_connection_default_config_path(self) -> None:
        args = bootstrap._parse_args(["create-connection"])
        assert args.config == "fabric.yml"
