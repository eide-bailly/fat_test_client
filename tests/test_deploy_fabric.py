"""Tests for scripts/deploy_fabric.py."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

# scripts/ is not a package; conftest.py puts it on sys.path and stubs the heavy
# third-party imports (fabric_cicd, azure.identity) before this module is collected.
import deploy_fabric
import pytest

WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"
EXPECTED_COMMIT = "a" * 40
OLD_WORKSPACE_HEAD = "b" * 40
OPERATION_URL = "https://api.fabric.microsoft.com/v1/operations/operation-id"
RELATIVE_OPERATION_LOCATION = "/v1/operations/operation-id"


class FakeWorkspace:
    """Stand-in for fabric_cicd.FabricWorkspace that records its kwargs."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _AttributeExposingFakeWorkspace:
    """Stand-in for fabric_cicd.FabricWorkspace that exposes its constructor kwargs as
    real attributes (workspace_id / repository_directory / environment) plus an empty
    `environment_parameter` dict, matching what main()'s proactive sibling-resolution
    path reads directly off the workspace object. Unlike `FakeWorkspace` (whose kwargs
    stay opaque in a `.kwargs` dict), this is needed by tests that drive that resolution
    through `main()` itself rather than by calling `_resolve_sibling_find_replace` /
    `_apply_new_find_replace_entries` directly against a hand-built object.
    """

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = kwargs["workspace_id"]
        self.repository_directory = kwargs["repository_directory"]
        self.environment = kwargs["environment"]
        self.environment_parameter: dict[str, Any] = {}


class FakeResponse:
    """Minimal mocked requests response for Fabric REST API tests."""

    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload if payload is not None else {}
        self.headers = headers if headers is not None else {}

    def json(self) -> Any:
        return self.payload


def _configured_credentials_response() -> FakeResponse:
    return FakeResponse(200, {"source": "ConfiguredConnection"})


def _git_status_response(
    workspace_head: str,
    remote_commit: str = EXPECTED_COMMIT,
    **extra: Any,
) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "workspaceHead": workspace_head,
            "remoteCommitHash": remote_commit,
            **extra,
        },
    )


def _mock_fabric_requests(
    monkeypatch: pytest.MonkeyPatch,
    get_responses: list[FakeResponse],
    post_responses: list[FakeResponse],
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        requests.append({"method": "GET", "url": url, **kwargs})
        assert get_responses, f"Unexpected GET request to {url}"
        return get_responses.pop(0)

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        requests.append({"method": "POST", "url": url, **kwargs})
        assert post_responses, f"Unexpected POST request to {url}"
        return post_responses.pop(0)

    monkeypatch.setattr(deploy_fabric.requests, "get", fake_get)
    monkeypatch.setattr(deploy_fabric.requests, "post", fake_post)
    monkeypatch.setattr(
        deploy_fabric,
        "_fabric_headers",
        lambda credential: {
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        },
    )
    return requests


def _update_request(requests: list[dict[str, Any]]) -> dict[str, Any]:
    return next(request for request in requests if request["method"] == "POST")


def _assert_update_request(request: dict[str, Any]) -> None:
    assert request["method"] == "POST"
    assert request["url"] == (
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/updateFromGit"
    )
    assert request["timeout"] == 30
    assert request["json"] == {
        "workspaceHead": OLD_WORKSPACE_HEAD,
        "remoteCommitHash": EXPECTED_COMMIT,
        "options": {"allowOverrideItems": True},
        "conflictResolution": {
            "conflictResolutionType": "Workspace",
            "conflictResolutionPolicy": "PreferRemote",
        },
    }


def _patch_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_PRINCIPAL_TENANT_ID", "tenant")
    monkeypatch.setenv("SERVICE_PRINCIPAL_CLIENT_ID", "client")
    monkeypatch.setenv("DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(deploy_fabric, "ClientSecretCredential", lambda **kwargs: object())


def test_main_publishes_supplied_repository_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_directory = str(tmp_path / "fabric")
    _patch_credential_env(monkeypatch)
    captured: dict[str, object] = {}

    def fake_publish_all_items(workspace: FakeWorkspace) -> None:
        captured.update(workspace.kwargs)

    monkeypatch.setattr(deploy_fabric, "FabricWorkspace", FakeWorkspace)
    monkeypatch.setattr(deploy_fabric, "publish_all_items", fake_publish_all_items)
    monkeypatch.setattr(deploy_fabric, "_patch_publish_folders", lambda workspace: None)
    monkeypatch.setattr(
        deploy_fabric,
        "_load_fabric_config",
        lambda path: SimpleNamespace(environments=[]),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_fabric.py",
            "--workspace-id",
            "11111111-1111-1111-1111-111111111111",
            "--env",
            "dev",
            "--repository",
            repository_directory,
        ],
    )

    deploy_fabric.main()

    assert captured["repository_directory"] == repository_directory
    assert captured["workspace_id"] == "11111111-1111-1111-1111-111111111111"
    assert captured["environment"] == "dev"
    assert captured["item_type_in_scope"] == ["DataPipeline", "Notebook", "DataBuildToolJob"]


def test_main_configures_info_logging_to_standard_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_credential_env(monkeypatch)
    logging_config: dict[str, object] = {}

    monkeypatch.setattr(
        deploy_fabric.logging,
        "basicConfig",
        lambda **kwargs: logging_config.update(kwargs),
    )
    monkeypatch.setattr(deploy_fabric, "FabricWorkspace", FakeWorkspace)
    monkeypatch.setattr(deploy_fabric, "publish_all_items", lambda workspace: None)
    monkeypatch.setattr(deploy_fabric, "_patch_publish_folders", lambda workspace: None)
    monkeypatch.setattr(
        deploy_fabric,
        "_load_fabric_config",
        lambda path: SimpleNamespace(environments=[]),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_fabric.py",
            "--workspace-id",
            WORKSPACE_ID,
            "--env",
            "dev",
            "--repository",
            str(tmp_path / "fabric"),
        ],
    )

    deploy_fabric.main()

    assert logging_config == {"level": logging.INFO, "stream": sys.stdout, "force": True}


def test_main_reraises_publish_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_credential_env(monkeypatch)

    def failing_publish_all_items(workspace: FakeWorkspace) -> None:
        raise RuntimeError("fabric-cicd publish failed")

    monkeypatch.setattr(deploy_fabric, "FabricWorkspace", FakeWorkspace)
    monkeypatch.setattr(deploy_fabric, "publish_all_items", failing_publish_all_items)
    monkeypatch.setattr(deploy_fabric, "_patch_publish_folders", lambda workspace: None)
    monkeypatch.setattr(
        deploy_fabric,
        "_load_fabric_config",
        lambda path: SimpleNamespace(environments=[]),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_fabric.py",
            "--workspace-id",
            "11111111-1111-1111-1111-111111111111",
            "--env",
            "dev",
            "--repository",
            str(tmp_path / "fabric"),
        ],
    )

    with pytest.raises(RuntimeError, match="fabric-cicd publish failed"):
        deploy_fabric.main()


def test_main_retries_once_via_two_pass_recovery_on_publish_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the main()/_retry_publish_after_partial_failure wiring itself: a real
    fabric-cicd partial-publish failure raises `PublishError`, not
    `FailedPublishedItemStatusError` (that class is reserved for the unrelated
    unassigned-capacity check) — main() must catch the class fabric-cicd actually
    raises, or the two-pass retry silently never runs. No "dev" environment is
    configured, so proactive resolution is skipped and the reactive retry path
    (`_retry_publish_after_partial_failure(workspace, dev_workspace_id, publish_error)`)
    is exercised, matching main()'s actual call signature.
    """
    _patch_credential_env(monkeypatch)

    publish_error = deploy_fabric.PublishError([("pl_orchestrate_daily", RuntimeError("bad ref"))])

    def failing_publish_all_items(workspace: FakeWorkspace) -> None:
        raise publish_error

    retry_calls: list[tuple[object, object, object]] = []
    monkeypatch.setattr(deploy_fabric, "FabricWorkspace", FakeWorkspace)
    monkeypatch.setattr(deploy_fabric, "publish_all_items", failing_publish_all_items)
    monkeypatch.setattr(deploy_fabric, "_patch_publish_folders", lambda workspace: None)
    monkeypatch.setattr(
        deploy_fabric,
        "_load_fabric_config",
        lambda path: SimpleNamespace(environments=[]),
    )
    monkeypatch.setattr(
        deploy_fabric,
        "_retry_publish_after_partial_failure",
        lambda workspace, dev_workspace_id, publish_error: retry_calls.append(
            (workspace, dev_workspace_id, publish_error)
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_fabric.py",
            "--workspace-id",
            WORKSPACE_ID,
            "--env",
            "prod",
            "--repository",
            str(tmp_path / "fabric"),
        ],
    )

    deploy_fabric.main()

    assert len(retry_calls) == 1
    _workspace, dev_workspace_id, retried_error = retry_calls[0]
    assert dev_workspace_id is None
    assert retried_error is publish_error


def test_patch_publish_folders_resolves_existing_folder_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvokeError(Exception):
        pass

    constants = ModuleType("fabric_cicd.constants")
    constants.INVALID_FOLDER_CHAR_REGEX = r"[<>]"
    constants.INDENT = ""

    check_utils = ModuleType("fabric_cicd._common._check_utils")
    check_utils.check_regex = re.compile
    exceptions = ModuleType("fabric_cicd._common._exceptions")
    exceptions.InvokeError = InvokeError
    logging_module = ModuleType("fabric_cicd._common._logging")
    logging_module.log_header = lambda logger, message: None
    fabric_cicd = ModuleType("fabric_cicd")
    fabric_cicd.constants = constants

    monkeypatch.setitem(sys.modules, "fabric_cicd", fabric_cicd)
    monkeypatch.setitem(sys.modules, "fabric_cicd.constants", constants)
    monkeypatch.setitem(sys.modules, "fabric_cicd._common", ModuleType("fabric_cicd._common"))
    monkeypatch.setitem(sys.modules, "fabric_cicd._common._check_utils", check_utils)
    monkeypatch.setitem(sys.modules, "fabric_cicd._common._exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "fabric_cicd._common._logging", logging_module)

    class Endpoint:
        def invoke(self, **kwargs: object) -> None:
            raise InvokeError("FolderDisplayNameAlreadyInUse")

    class Workspace:
        def __init__(self) -> None:
            self.base_api_url = "https://api.fabric.microsoft.com/v1/workspaces/workspace"
            self.deployed_folders: dict[str, str] = {}
            self.endpoint = Endpoint()
            self.publish_folder_path_exclude_regex = None
            self.publish_folder_path_to_include = None
            self.refreshed = False
            self.repository_folders = {"reports": None}

        def _refresh_deployed_folders(self) -> None:
            self.refreshed = True
            self.deployed_folders["reports"] = "existing-folder-id"

    workspace = Workspace()
    deploy_fabric._patch_publish_folders(workspace)

    workspace._publish_folders()

    assert workspace.refreshed is True
    assert workspace.repository_folders["reports"] == "existing-folder-id"


def test_git_sync_noops_when_workspace_is_already_at_expected_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [_configured_credentials_response(), _git_status_response(EXPECTED_COMMIT)],
        [],
    )

    deploy_fabric._sync_git_workspace(
        WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
    )

    assert [(request["method"], request["url"]) for request in requests] == [
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/myGitCredentials",
        ),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
    ]


def test_main_git_sync_posts_expected_commit_and_checks_final_git_heads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            _git_status_response(EXPECTED_COMMIT),
        ],
        [FakeResponse(200)],
    )
    monkeypatch.setattr(deploy_fabric, "_load_credential", lambda: object())
    monkeypatch.setattr(
        deploy_fabric,
        "publish_all_items",
        lambda workspace: pytest.fail("Git sync must not publish Fabric items."),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_fabric.py",
            "--workspace-id",
            WORKSPACE_ID,
            "--env",
            "dev",
            "--git-sync",
            "--expected-commit",
            EXPECTED_COMMIT,
            "--repository",
            str(tmp_path / "fabric"),
        ],
    )

    deploy_fabric.main()

    _assert_update_request(_update_request(requests))
    assert [(request["method"], request["url"]) for request in requests] == [
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/myGitCredentials",
        ),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
        (
            "POST",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/updateFromGit",
        ),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
    ]


def test_git_sync_waits_for_async_update_retry_after_and_succeeded_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(200, {"status": "Succeeded"}),
            _git_status_response(EXPECTED_COMMIT),
        ],
        [
            FakeResponse(
                202,
                headers={
                    "Location": OPERATION_URL,
                    "Retry-After": "7",
                    "x-ms-operation-id": "operation-id",
                },
            )
        ],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(deploy_fabric.time, "sleep", sleeps.append)

    deploy_fabric._sync_git_workspace(
        WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
    )

    _assert_update_request(_update_request(requests))
    assert sleeps == [7.0]
    assert [(request["method"], request["url"]) for request in requests] == [
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/myGitCredentials",
        ),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
        (
            "POST",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/updateFromGit",
        ),
        ("GET", OPERATION_URL),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
    ]


def test_git_sync_polls_relative_lro_location_on_trusted_fabric_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(200, {"status": "Succeeded"}),
            _git_status_response(EXPECTED_COMMIT),
        ],
        [FakeResponse(202, headers={"Location": RELATIVE_OPERATION_LOCATION})],
    )

    deploy_fabric._sync_git_workspace(
        WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
    )

    assert [(request["method"], request["url"]) for request in requests] == [
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/myGitCredentials",
        ),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
        (
            "POST",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/updateFromGit",
        ),
        ("GET", OPERATION_URL),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
    ]


def test_git_sync_polls_header_operation_id_without_requesting_unsafe_location(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unsafe_location = "https://untrusted.example/v1/operations/operation-id"
    operation_id = "operation-id+retry"
    trusted_operation_url = "https://api.fabric.microsoft.com/v1/operations/operation-id%2Bretry"
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(200, {"status": "Succeeded"}),
            _git_status_response(EXPECTED_COMMIT),
        ],
        [
            FakeResponse(
                202,
                headers={
                    "Location": unsafe_location,
                    "x-ms-operation-id": operation_id,
                },
            )
        ],
    )

    deploy_fabric._sync_git_workspace(
        WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
    )

    assert [request["url"] for request in requests] == [
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/myGitCredentials",
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/updateFromGit",
        trusted_operation_url,
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
    ]
    assert all(request["url"] != unsafe_location for request in requests)


def test_git_sync_treats_undefined_lro_status_as_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(200, {"status": "Undefined"}, {"Retry-After": "2"}),
            FakeResponse(200, {"status": "Succeeded"}),
            _git_status_response(EXPECTED_COMMIT),
        ],
        [FakeResponse(202, headers={"Location": OPERATION_URL})],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(deploy_fabric.time, "sleep", sleeps.append)

    deploy_fabric._sync_git_workspace(
        WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
    )

    assert sleeps == [2.0]
    assert [request["url"] for request in requests].count(OPERATION_URL) == 2


def test_git_sync_retries_update_after_429_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            _git_status_response(EXPECTED_COMMIT),
        ],
        [
            FakeResponse(429, headers={"Retry-After": "3"}),
            FakeResponse(200),
        ],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(deploy_fabric.time, "sleep", sleeps.append)

    deploy_fabric._sync_git_workspace(
        WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
    )

    assert sleeps == [3.0]
    assert len([request for request in requests if request["method"] == "POST"]) == 2


def test_git_sync_retries_lro_poll_after_429_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(429, headers={"Retry-After": "4"}),
            FakeResponse(200, {"status": "Succeeded"}),
            _git_status_response(EXPECTED_COMMIT),
        ],
        [FakeResponse(202, headers={"Location": OPERATION_URL})],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(deploy_fabric.time, "sleep", sleeps.append)

    deploy_fabric._sync_git_workspace(
        WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
    )

    assert sleeps == [4.0]
    assert [request["url"] for request in requests].count(OPERATION_URL) == 2


def test_git_sync_does_not_retry_non_429_lro_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(503, {"error": {"message": "Service unavailable"}}),
        ],
        [FakeResponse(202, headers={"Location": OPERATION_URL})],
    )

    with pytest.raises(RuntimeError, match="Fabric operation operation-id failed with HTTP 503"):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    assert [request["url"] for request in requests].count(OPERATION_URL) == 1


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({}, "did not include a Location header"),
        ({"Location": "not-an-operation-url"}, "malformed operation Location header"),
        ({"Location": "https://api.fabric.microsoft.com/v1/operations/"}, "operation ID"),
    ],
)
def test_git_sync_rejects_missing_or_malformed_lro_location(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    message: str,
    tmp_path: Path,
) -> None:
    _mock_fabric_requests(
        monkeypatch,
        [_configured_credentials_response(), _git_status_response(OLD_WORKSPACE_HEAD)],
        [FakeResponse(202, headers=headers)],
    )

    with pytest.raises(RuntimeError, match=message):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )


def test_git_sync_rejects_off_host_lro_location_before_authenticated_poll(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [_configured_credentials_response(), _git_status_response(OLD_WORKSPACE_HEAD)],
        [
            FakeResponse(
                202,
                headers={"Location": "https://untrusted.example/v1/operations/operation-id"},
            )
        ],
    )

    with pytest.raises(RuntimeError, match="invalid or off-host operation Location header"):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    assert [(request["method"], request["url"]) for request in requests] == [
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/myGitCredentials",
        ),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
        (
            "POST",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/updateFromGit",
        ),
    ]


@pytest.mark.parametrize(
    "location",
    [
        "//api.fabric.microsoft.com/v1/operations/operation-id",
        "https://untrusted.example/v1/operations/operation-id",
        "https://credentials@example.com@api.fabric.microsoft.com/v1/operations/operation-id",
        "/v1/workspaces/workspace-id/git/status",
        "/v1/operations/operation-id?next=/v1/workspaces/workspace-id",
    ],
)
def test_operation_location_rejects_unsafe_variations(location: str) -> None:
    with pytest.raises(RuntimeError):
        deploy_fabric._operation_location(FakeResponse(202, headers={"Location": location}))


@pytest.mark.parametrize(
    ("operation_body", "message"),
    [
        ({}, "did not include a non-empty status"),
        ({"status": []}, "did not include a non-empty status"),
        ([], "JSON response that is not an object"),
    ],
)
def test_git_sync_rejects_missing_or_malformed_lro_status(
    monkeypatch: pytest.MonkeyPatch,
    operation_body: Any,
    message: str,
    tmp_path: Path,
) -> None:
    _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(200, operation_body),
        ],
        [FakeResponse(202, headers={"Location": OPERATION_URL})],
    )

    with pytest.raises(RuntimeError, match=message):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )


def test_git_sync_429_retry_does_not_sleep_past_lro_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [_configured_credentials_response(), _git_status_response(OLD_WORKSPACE_HEAD)],
        [FakeResponse(429, headers={"Retry-After": "11"})],
    )
    monkeypatch.setattr(deploy_fabric, "_LRO_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(deploy_fabric.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        deploy_fabric.time,
        "sleep",
        lambda delay: pytest.fail(f"Should not sleep {delay} seconds past the deadline."),
    )

    with pytest.raises(TimeoutError, match="timed out before retrying Fabric updateFromGit"):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    assert len([request for request in requests if request["method"] == "POST"]) == 1


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["--git-sync", "--expected-commit", EXPECTED_COMMIT, "--env", "prod"],
            "--git-sync may only be used with --env dev",
        ),
        (
            ["--git-sync", "--env", "dev"],
            "--git-sync requires --expected-commit",
        ),
        (
            ["--expected-commit", EXPECTED_COMMIT, "--env", "dev"],
            "--expected-commit may only be used with --git-sync",
        ),
    ],
)
def test_main_rejects_invalid_git_sync_modes(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], message: str
) -> None:
    monkeypatch.setattr(deploy_fabric, "_load_credential", lambda: object())
    monkeypatch.setattr(
        sys,
        "argv",
        ["deploy_fabric.py", "--workspace-id", WORKSPACE_ID, *arguments],
    )

    with pytest.raises(ValueError, match=message):
        deploy_fabric.main()


def test_parse_args_rejects_publish_and_git_sync_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_fabric.py",
            "--workspace-id",
            WORKSPACE_ID,
            "--env",
            "dev",
            "--publish",
            "--git-sync",
        ],
    )

    with pytest.raises(SystemExit):
        deploy_fabric._parse_args()


def test_git_sync_rejects_a_stale_remote_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stale_commit = "c" * 40
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD, remoteCommitHash=stale_commit),
        ],
        [],
    )

    with pytest.raises(RuntimeError, match="does not match the expected pipeline commit"):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    assert [(request["method"], request["url"]) for request in requests] == [
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/myGitCredentials",
        ),
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status",
        ),
    ]


def test_git_sync_rejects_missing_configured_git_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(monkeypatch, [FakeResponse(200, {"source": "None"})], [])

    with pytest.raises(RuntimeError, match="credentials are not configured"):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    assert [(request["method"], request["url"]) for request in requests] == [
        (
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/myGitCredentials",
        )
    ]


def test_git_sync_resolves_detected_conflicts_with_workspace_prefer_remote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(
                OLD_WORKSPACE_HEAD,
                changes=[{"itemName": "dbt_build", "conflictType": "Conflict"}],
            ),
            _git_status_response(EXPECTED_COMMIT),
        ],
        [FakeResponse(200)],
    )

    deploy_fabric._sync_git_workspace(
        WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
    )

    _assert_update_request(_update_request(requests))


def test_git_sync_raises_when_fabric_long_running_operation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(200, {"status": "Failed", "error": {"message": "Fabric failure"}}),
        ],
        [FakeResponse(202, headers={"Location": OPERATION_URL})],
    )

    with pytest.raises(RuntimeError, match="ended in FAILED"):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    _assert_update_request(_update_request(requests))
    assert requests[-1]["url"] == OPERATION_URL


def test_git_sync_rejects_post_update_workspace_head_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unexpected_head = "d" * 40
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            _git_status_response(unexpected_head),
        ],
        [FakeResponse(200)],
    )

    with pytest.raises(RuntimeError, match="workspace head does not match"):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    _assert_update_request(_update_request(requests))
    assert requests[-1]["url"] == (
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status"
    )


def test_git_sync_rejects_post_update_status_missing_remote_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            FakeResponse(200, {"workspaceHead": EXPECTED_COMMIT}),
        ],
        [FakeResponse(200)],
    )

    with pytest.raises(
        RuntimeError,
        match="Fabric post-update Git status did not include a non-empty remoteCommitHash",
    ):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    _assert_update_request(_update_request(requests))
    assert requests[-1]["url"] == (
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status"
    )


def test_git_sync_rejects_post_update_remote_commit_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    newer_remote_commit = "e" * 40
    requests = _mock_fabric_requests(
        monkeypatch,
        [
            _configured_credentials_response(),
            _git_status_response(OLD_WORKSPACE_HEAD),
            _git_status_response(EXPECTED_COMMIT, remoteCommitHash=newer_remote_commit),
        ],
        [FakeResponse(200)],
    )

    with pytest.raises(RuntimeError, match="remote Git commit does not match"):
        deploy_fabric._sync_git_workspace(
            WORKSPACE_ID, EXPECTED_COMMIT, object(), repository_directory=str(tmp_path)
        )

    _assert_update_request(_update_request(requests))
    assert requests[-1]["url"] == (
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/git/status"
    )


def _fake_fab_api_run(responses: dict[str, list[dict[str, Any]]]) -> Any:
    """Return a `subprocess.run` stand-in for 'fab api -X get workspaces/{id}/items'.

    *responses* maps workspace ID -> the list of item dicts that workspace's 'fab api'
    call should report.
    """

    def fake_run(command: list[str], **kwargs: object) -> Any:
        # command == ["fab", "api", "-X", "get", f"workspaces/{workspace_id}/items"]
        workspace_id = command[4].split("/")[1]

        class FakeCompletedProcess:
            returncode = 0
            stdout = json.dumps({"value": responses[workspace_id]})
            stderr = ""

        return FakeCompletedProcess()

    return fake_run


def test_local_item_names_matches_name_dot_item_type_folders(tmp_path: Path) -> None:
    (tmp_path / "pl_orchestrate_daily.DataPipeline").mkdir()
    (tmp_path / "nested" / "pl_refresh_semantic_model.DataPipeline").mkdir(parents=True)
    (tmp_path / "not_an_item_folder").mkdir()

    names = deploy_fabric._local_item_names(str(tmp_path))

    assert names == {"pl_orchestrate_daily", "pl_refresh_semantic_model"}


def test_resolve_item_ids_maps_display_name_to_live_guid_via_fab_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage for the live item-GUID resolution path that replaced the deleted
    `_harvest_live_item_guids` (which paginated a raw Fabric REST 'items' call via
    `requests`). The new path shells out to 'fab api' instead (see
    `_fetch_workspace_items_live`); this exercises that subprocess call plus the
    display-name -> GUID mapping in `_resolve_item_ids`, including the "text"-wrapper
    handling for newer `fab` CLI versions.
    """
    captured_commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> Any:
        captured_commands.append(command)

        class FakeCompletedProcess:
            returncode = 0
            stdout = json.dumps(
                {
                    "text": {
                        "value": [
                            {"displayName": "pl_refresh_semantic_model", "id": "c" * 36},
                            {"displayName": "unrelated_item", "id": "d" * 36},
                        ]
                    }
                }
            )
            stderr = ""

        return FakeCompletedProcess()

    monkeypatch.setattr(deploy_fabric.subprocess, "run", fake_run)

    result = deploy_fabric._resolve_item_ids(WORKSPACE_ID)

    assert result == {
        "pl_refresh_semantic_model": "c" * 36,
        "unrelated_item": "d" * 36,
    }
    assert captured_commands == [["fab", "api", "-X", "get", f"workspaces/{WORKSPACE_ID}/items"]]


def test_fetch_workspace_items_live_raises_item_resolution_error_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(
        deploy_fabric.subprocess, "run", lambda command, **kwargs: FakeCompletedProcess()
    )

    with pytest.raises(deploy_fabric._ItemResolutionError, match="exited with code 1"):
        deploy_fabric._fetch_workspace_items_live(WORKSPACE_ID)


def test_retry_publish_after_partial_failure_adds_find_replace_and_republishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dev_guid = "0adedd39-3e73-44b4-82fb-730abda0acf1"
    prod_guid = "c3b3942d-200c-4729-90fb-6ad530c67054"
    dev_workspace_id = "22222222-2222-2222-2222-222222222222"

    repo_dir = tmp_path / "fabric"
    (repo_dir / "pl_refresh_semantic_model.DataPipeline").mkdir(parents=True)

    workspace = FakeWorkspace(workspace_id=WORKSPACE_ID)
    workspace.workspace_id = WORKSPACE_ID
    workspace.repository_directory = str(repo_dir)
    workspace.environment = "prod"
    workspace.environment_parameter = {}

    monkeypatch.setattr(
        deploy_fabric.subprocess,
        "run",
        _fake_fab_api_run(
            {
                dev_workspace_id: [{"displayName": "pl_refresh_semantic_model", "id": dev_guid}],
                WORKSPACE_ID: [{"displayName": "pl_refresh_semantic_model", "id": prod_guid}],
            }
        ),
    )

    published: list[object] = []
    sleeps: list[float] = []
    monkeypatch.setattr(deploy_fabric, "publish_all_items", lambda ws: published.append(ws))
    monkeypatch.setattr(deploy_fabric.time, "sleep", lambda seconds: sleeps.append(seconds))

    publish_error = deploy_fabric.PublishError([("pl_orchestrate_daily", RuntimeError("bad ref"))])
    deploy_fabric._retry_publish_after_partial_failure(workspace, dev_workspace_id, publish_error)

    assert sleeps == [deploy_fabric._PUBLISH_RETRY_DELAY_SECONDS]
    assert published == [workspace]
    assert workspace.environment_parameter["find_replace"] == [
        {"find_value": dev_guid, "replace_value": {"prod": prod_guid}}
    ]


def test_retry_publish_after_partial_failure_reraises_when_nothing_new_resolvable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When no new find_replace entry can be derived, the original PublishError is
    re-raised as-is (with its per-item detail intact) — `FailedPublishedItemStatusError`
    no longer exists in `deploy_fabric.py`.
    """
    dev_workspace_id = "22222222-2222-2222-2222-222222222222"

    repo_dir = tmp_path / "fabric"
    repo_dir.mkdir()  # no local item folders -> nothing to resolve regardless of live items

    workspace = FakeWorkspace(workspace_id=WORKSPACE_ID)
    workspace.workspace_id = WORKSPACE_ID
    workspace.repository_directory = str(repo_dir)
    workspace.environment = "prod"
    workspace.environment_parameter = {}

    monkeypatch.setattr(
        deploy_fabric.subprocess,
        "run",
        _fake_fab_api_run({dev_workspace_id: [], WORKSPACE_ID: []}),
    )
    monkeypatch.setattr(
        deploy_fabric,
        "publish_all_items",
        lambda ws: pytest.fail("publish_all_items should not be retried"),
    )
    monkeypatch.setattr(deploy_fabric.time, "sleep", lambda seconds: None)

    publish_error = deploy_fabric.PublishError([("pl_orchestrate_daily", RuntimeError("bad ref"))])

    with pytest.raises(deploy_fabric.PublishError) as exc_info:
        deploy_fabric._retry_publish_after_partial_failure(
            workspace, dev_workspace_id, publish_error
        )

    assert exc_info.value is publish_error


def test_main_resolves_sibling_find_replace_for_split_data_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A split-layout project (--data-workspace-id given, distinct from --workspace-id)
    must resolve sibling GUIDs against *both* the items workspace and the data
    workspace, then merge both sets of find_replace entries onto the same publish
    (issue #3). Each pair independently resolves a dev-vs-target literal-GUID entry:
    an items-workspace DataPipeline and a data-workspace Lakehouse.
    """
    dev_items_workspace_id = "22222222-2222-2222-2222-222222222222"
    dev_data_workspace_id = "44444444-4444-4444-4444-444444444444"
    target_data_workspace_id = "55555555-5555-5555-5555-555555555555"

    dev_items_guid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    target_items_guid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    dev_data_guid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    target_data_guid = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    repository_directory = str(tmp_path / "fabric")
    (Path(repository_directory) / "pl_refresh_semantic_model.DataPipeline").mkdir(parents=True)
    (Path(repository_directory) / "lh_bronze.Lakehouse").mkdir(parents=True)

    _patch_credential_env(monkeypatch)
    monkeypatch.setattr(deploy_fabric, "FabricWorkspace", _AttributeExposingFakeWorkspace)
    monkeypatch.setattr(deploy_fabric, "_patch_publish_folders", lambda workspace: None)
    monkeypatch.setattr(
        deploy_fabric,
        "_load_fabric_config",
        lambda path: SimpleNamespace(
            environments=[
                SimpleNamespace(
                    name="dev",
                    workspace_id=dev_items_workspace_id,
                    data_workspace_id=dev_data_workspace_id,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        deploy_fabric.subprocess,
        "run",
        _fake_fab_api_run(
            {
                dev_items_workspace_id: [
                    {"displayName": "pl_refresh_semantic_model", "id": dev_items_guid}
                ],
                WORKSPACE_ID: [
                    {"displayName": "pl_refresh_semantic_model", "id": target_items_guid}
                ],
                dev_data_workspace_id: [{"displayName": "lh_bronze", "id": dev_data_guid}],
                target_data_workspace_id: [{"displayName": "lh_bronze", "id": target_data_guid}],
            }
        ),
    )
    published: list[Any] = []
    monkeypatch.setattr(
        deploy_fabric, "publish_all_items", lambda workspace: published.append(workspace)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_fabric.py",
            "--workspace-id",
            WORKSPACE_ID,
            "--data-workspace-id",
            target_data_workspace_id,
            "--env",
            "prod",
            "--repository",
            repository_directory,
        ],
    )

    deploy_fabric.main()

    assert len(published) == 1
    assert published[0].environment_parameter["find_replace"] == [
        {"find_value": dev_items_guid, "replace_value": {"prod": target_items_guid}},
        {"find_value": dev_data_guid, "replace_value": {"prod": target_data_guid}},
    ]


def test_main_skips_data_workspace_resolution_when_data_workspace_id_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting --data-workspace-id (the single-combined-workspace layout) must be a
    strict no-op for the data-workspace resolution path: no extra 'fab api' calls, and
    an unchanged find_replace list relative to items-only resolution.
    """
    dev_items_workspace_id = "22222222-2222-2222-2222-222222222222"
    dev_items_guid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    target_items_guid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    repository_directory = str(tmp_path / "fabric")
    (Path(repository_directory) / "pl_refresh_semantic_model.DataPipeline").mkdir(parents=True)

    _patch_credential_env(monkeypatch)
    monkeypatch.setattr(deploy_fabric, "FabricWorkspace", _AttributeExposingFakeWorkspace)
    monkeypatch.setattr(deploy_fabric, "_patch_publish_folders", lambda workspace: None)
    monkeypatch.setattr(
        deploy_fabric,
        "_load_fabric_config",
        lambda path: SimpleNamespace(
            environments=[
                SimpleNamespace(
                    name="dev",
                    workspace_id=dev_items_workspace_id,
                    data_workspace_id="66666666-6666-6666-6666-666666666666",
                )
            ]
        ),
    )
    fab_api_commands: list[list[str]] = []
    fake_run = _fake_fab_api_run(
        {
            dev_items_workspace_id: [
                {"displayName": "pl_refresh_semantic_model", "id": dev_items_guid}
            ],
            WORKSPACE_ID: [{"displayName": "pl_refresh_semantic_model", "id": target_items_guid}],
        }
    )

    def recording_run(command: list[str], **kwargs: object) -> Any:
        fab_api_commands.append(command)
        return fake_run(command, **kwargs)

    monkeypatch.setattr(deploy_fabric.subprocess, "run", recording_run)
    published: list[Any] = []
    monkeypatch.setattr(
        deploy_fabric, "publish_all_items", lambda workspace: published.append(workspace)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_fabric.py",
            "--workspace-id",
            WORKSPACE_ID,
            "--env",
            "prod",
            "--repository",
            repository_directory,
        ],
    )

    deploy_fabric.main()

    assert len(published) == 1
    assert published[0].environment_parameter["find_replace"] == [
        {"find_value": dev_items_guid, "replace_value": {"prod": target_items_guid}}
    ]
    assert len(fab_api_commands) == 2
    resolved_workspace_ids = {command[4].split("/")[1] for command in fab_api_commands}
    assert resolved_workspace_ids == {dev_items_workspace_id, WORKSPACE_ID}
