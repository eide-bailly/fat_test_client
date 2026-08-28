"""
Template deploy script for publishing Fabric items via fabric-cicd.

Copy this file into a client project and adapt as needed.

Dependencies (add to the client project's requirements):
    fabric-cicd>=1.1.0
    azure-identity>=1.15

Example invocation:
    uv run python scripts/deploy_fabric.py --workspace-id <guid> --env prod
    uv run python scripts/deploy_fabric.py --workspace-id <guid> --env dev --repository fabric
    uv run python scripts/deploy_fabric.py --workspace-id <guid> --env dev --git-sync \
        --expected-commit <pipeline-commit-sha>
    uv run python scripts/deploy_fabric.py --workspace-id <guid> --data-workspace-id <guid> \
        --env prod

"""

import argparse
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
import types
from typing import Any

from _fabric_config import load_config as _load_fabric_config
from _fabric_lro import (
    _FABRIC_API,
    _HTTP_TIMEOUT_SECONDS,
    _LRO_ACTIVE_STATES,
    _LRO_FAILURE_STATES,
    _LRO_POLL_INTERVAL_SECONDS,
    _LRO_SUCCESS_STATES,
    _LRO_TIMEOUT_SECONDS,
    _fabric_headers,
    _get_json,
    _operation_location,
    _operation_url_from_header,
    _parse_retry_after,
    _redact_for_logging,
    _require_success,
    _required_string,
    _response_payload,
    _retry_rate_limited_response,
    _sleep_with_timeout,
)
from azure.identity import ClientSecretCredential
from fabric_cicd import FabricWorkspace, publish_all_items
from fabric_cicd._common._exceptions import PublishError
import requests

# Matches __ITEM_<NAME>__ / __CONNECTION_<NAME>__ / __ENDPOINT_<NAME>__ tokens, plus the two
# fixed, project-wide workspace tokens __WORKSPACE_ID__ / __DATA_WORKSPACE_ID__. <NAME> may
# itself contain literal angle brackets in un-adapted catalog exemplars (e.g.
# `__ITEM_<NOTEBOOK_NAME>__`). Copied verbatim from `_TOKEN_PATTERN` in
# `tool/fat/deploy/bootstrap.py` (rather than imported) because this script is stamped
# verbatim into client repos, which do not have the `fat` package installed.
_UNRESOLVED_TOKEN_PATTERN = re.compile(
    r"__(?:(?:ITEM|CONNECTION|ENDPOINT)_[A-Za-z0-9_<>]+?|WORKSPACE_ID|DATA_WORKSPACE_ID)__"
)


def _strip_private_keys(obj: object) -> object:
    """Return a deep copy of *obj* with every dict key that starts with `_` (and
    everything nested beneath it) removed.

    A `_`-prefixed JSON key (e.g. `_comment`) is a non-functional annotation, never
    live payload content, and may legitimately reference a token name in prose.
    Copied verbatim from `tool/fat/deploy/bootstrap.py` for the same reason the token
    pattern above is: this script is stamped verbatim into client repos.
    """
    if isinstance(obj, dict):
        return {k: _strip_private_keys(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_private_keys(v) for v in obj]
    return obj


def _scannable_text(text: str) -> str:
    """Return *text* with the content of every `_`-prefixed JSON key removed, for
    token-discovery purposes only — never for writing back to disk.

    Falls back to *text* unchanged if it is not valid JSON.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text
    return json.dumps(_strip_private_keys(data))


_REQUIRED_ENV_VARS = (
    "SERVICE_PRINCIPAL_TENANT_ID",
    "SERVICE_PRINCIPAL_CLIENT_ID",
    "DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET",
)

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish all Fabric items in a repository directory to a workspace."
    )
    parser.add_argument("--workspace-id", required=True, help="Target Fabric workspace GUID.")
    parser.add_argument(
        "--data-workspace-id",
        default=None,
        help=(
            "Target Fabric data workspace GUID (lakehouses/warehouses), for projects that "
            "split items and data into separate workspaces. Omit for a single combined "
            "workspace layout."
        ),
    )
    parser.add_argument(
        "--env", required=True, help="Deployment environment name (e.g. prod, dev)."
    )
    parser.add_argument(
        "--repository",
        default="fabric",
        help="Subdirectory of the repo that contains the Fabric items (default: fabric).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--publish",
        action="store_true",
        help="Explicitly use the default fabric-cicd publish mode.",
    )
    mode.add_argument(
        "--git-sync",
        action="store_true",
        help="Synchronize the dev workspace from its configured Fabric Git source.",
    )
    parser.add_argument(
        "--expected-commit",
        help=(
            "Full commit SHA that Fabric must report before synchronizing (the source "
            "commit of the CI/CD pipeline run, from whichever Git provider backs the repo)."
        ),
    )
    return parser.parse_args()


def _load_credential() -> ClientSecretCredential:
    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        sys.exit("Missing required environment variables:\n" + "\n".join(f"  {v}" for v in missing))

    return ClientSecretCredential(
        tenant_id=os.environ["SERVICE_PRINCIPAL_TENANT_ID"],
        client_id=os.environ["SERVICE_PRINCIPAL_CLIENT_ID"],
        client_secret=os.environ["DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET"],
    )


def _log_changed_items(status: dict[str, Any]) -> None:
    """Log Git status item names without assuming one Fabric status schema version."""

    def item_name(item: dict[str, Any]) -> str | None:
        metadata = item.get("itemMetadata")
        metadata_name = (
            metadata.get("displayName") or metadata.get("name")
            if isinstance(metadata, dict)
            else None
        )
        name = (
            item.get("displayName")
            or item.get("itemName")
            or item.get("name")
            or metadata_name
            or item.get("itemId")
            or item.get("logicalId")
            or item.get("objectId")
        )
        return str(name) if name else None

    changes = status.get("changes")
    if isinstance(changes, list):
        change_items = [item for item in changes if isinstance(item, dict)]
        changed_names = [name for item in change_items if (name := item_name(item))]
        conflict_names = [
            name
            for item in change_items
            if item.get("conflictType") == "Conflict" and (name := item_name(item))
        ]
        if changed_names:
            logger.info("Changed Git items: %s", ", ".join(changed_names))
        if conflict_names:
            logger.info("Conflicting Git items: %s", ", ".join(conflict_names))

    conflicts = status.get("conflicts")
    if isinstance(conflicts, list):
        conflict_names = [
            name for item in conflicts if isinstance(item, dict) and (name := item_name(item))
        ]
        if conflict_names:
            logger.info("Conflicting Git items: %s", ", ".join(conflict_names))


def _wait_for_git_update(
    response: requests.Response, headers: dict[str, str], deadline: float
) -> None:
    """Wait for an accepted updateFromGit operation to reach Succeeded."""
    if response.status_code == 200:
        operation_id = response.headers.get("x-ms-operation-id")
        if operation_id:
            logger.info("Fabric Git update completed immediately (operation ID: %s).", operation_id)
        else:
            logger.info("Fabric Git update completed immediately.")
        return
    if response.status_code != 202:
        raise RuntimeError(
            f"Fabric updateFromGit returned unexpected success status {response.status_code}."
        )

    operation_from_header = _operation_url_from_header(response)
    operation_url, operation_id = (
        operation_from_header
        if operation_from_header is not None
        else _operation_location(response)
    )
    logger.info("Fabric Git update accepted (operation ID: %s).", operation_id)

    initial_delay = _parse_retry_after(response.headers.get("Retry-After"))
    if initial_delay is not None:
        _sleep_with_timeout(initial_delay, deadline, f"polling operation {operation_id}")

    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Fabric Git update operation {operation_id} did not finish within "
                f"{_LRO_TIMEOUT_SECONDS} seconds."
            )
        operation_response = requests.get(
            operation_url, headers=headers, timeout=_HTTP_TIMEOUT_SECONDS
        )
        if _retry_rate_limited_response(
            operation_response, deadline, f"Fabric operation {operation_id}"
        ):
            continue
        _require_success(operation_response, f"Fabric operation {operation_id}")
        operation = _response_payload(operation_response, f"Fabric operation {operation_id}")
        operation_status = _required_string(
            operation, "status", f"Fabric operation {operation_id}"
        ).upper()
        if operation_status in _LRO_SUCCESS_STATES:
            logger.info("Fabric Git update operation %s succeeded.", operation_id)
            return
        if operation_status in _LRO_FAILURE_STATES:
            logger.error(
                "Fabric Git update operation %s %s: %s",
                operation_id,
                operation_status.lower(),
                json.dumps(_redact_for_logging(operation), sort_keys=True),
            )
            raise RuntimeError(
                f"Fabric Git update operation {operation_id} ended in {operation_status}."
            )
        if operation_status not in _LRO_ACTIVE_STATES:
            raise RuntimeError(
                f"Fabric Git update operation {operation_id} returned unexpected status "
                f"{operation_status}."
            )

        delay = _parse_retry_after(operation_response.headers.get("Retry-After"))
        _sleep_with_timeout(
            _LRO_POLL_INTERVAL_SECONDS if delay is None else delay,
            deadline,
            f"polling operation {operation_id}",
        )


def _find_unresolved_bootstrap_tokens(repository_directory: str) -> dict[str, list[str]]:
    """Scan a repository directory for unresolved `__TOKEN__`-style bootstrap placeholders.

    By the post-merge, git-sync phase every catalog exemplar's bootstrap tokens should
    already have been resolved (see `tool/fat/deploy/bootstrap.py`'s module docstring for
    the token lifecycle). A lingering token here means init-time token resolution never
    completed, and `fabric-cicd` would otherwise publish it as a literal, unresolvable
    string. Returns a mapping of offending file path -> sorted unique tokens found in it.
    """
    offenders: dict[str, list[str]] = {}
    root = Path(repository_directory)
    if not root.exists():
        return offenders
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        matches = sorted(set(_UNRESOLVED_TOKEN_PATTERN.findall(_scannable_text(text))))
        if matches:
            offenders[str(path)] = matches
    return offenders


def _sync_git_workspace(
    workspace_id: str,
    expected_commit: str,
    credential: ClientSecretCredential,
    repository_directory: str,
) -> None:
    """Synchronize a configured dev workspace to one exact remote Git commit."""
    offenders = _find_unresolved_bootstrap_tokens(repository_directory)
    if offenders:
        offending_files = "\n".join(
            f"  {file}: {', '.join(tokens)}" for file, tokens in offenders.items()
        )
        raise RuntimeError(
            "Unresolved bootstrap tokens found in repository directory "
            f"'{repository_directory}' — init token resolution never completed:\n"
            f"{offending_files}"
        )

    headers = _fabric_headers(credential)
    workspace_url = f"{_FABRIC_API}/workspaces/{workspace_id}"
    credentials = _get_json(
        f"{workspace_url}/git/myGitCredentials",
        headers,
        "Fabric Git credentials lookup",
    )
    if credentials.get("source") != "ConfiguredConnection":
        raise RuntimeError(
            "Fabric Git credentials are not configured for this service principal "
            "(expected source ConfiguredConnection)."
        )

    status = _get_json(f"{workspace_url}/git/status", headers, "Fabric Git status lookup")
    remote_commit = _required_string(status, "remoteCommitHash", "Fabric Git status")
    workspace_head = _required_string(status, "workspaceHead", "Fabric Git status")
    _log_changed_items(status)
    logger.info(
        "Fabric Git status: workspace head=%s, remote commit=%s, expected commit=%s.",
        workspace_head,
        remote_commit,
        expected_commit,
    )
    if remote_commit != expected_commit:
        raise RuntimeError(
            "Fabric remote Git commit does not match the expected pipeline commit "
            f"(expected {expected_commit}, got {remote_commit})."
        )
    if workspace_head == remote_commit:
        logger.info("Workspace is already synchronized to expected commit %s.", expected_commit)
        return

    body = {
        "workspaceHead": workspace_head,
        "remoteCommitHash": remote_commit,
        "options": {"allowOverrideItems": True},
        "conflictResolution": {
            "conflictResolutionType": "Workspace",
            "conflictResolutionPolicy": "PreferRemote",
        },
    }
    deadline = time.monotonic() + _LRO_TIMEOUT_SECONDS
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Fabric Git update did not start within {_LRO_TIMEOUT_SECONDS} seconds."
            )
        update_response = requests.post(
            f"{workspace_url}/git/updateFromGit",
            headers=headers,
            json=body,
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if not _retry_rate_limited_response(update_response, deadline, "Fabric updateFromGit"):
            break
    _require_success(update_response, "Fabric updateFromGit")
    _wait_for_git_update(update_response, headers, deadline)

    final_status = _get_json(
        f"{workspace_url}/git/status", headers, "Fabric post-update Git status"
    )
    final_workspace_head = _required_string(
        final_status, "workspaceHead", "Fabric post-update Git status"
    )
    final_remote_commit = _required_string(
        final_status, "remoteCommitHash", "Fabric post-update Git status"
    )
    _log_changed_items(final_status)
    logger.info(
        "Fabric post-update Git status: workspace head=%s, remote commit=%s, expected commit=%s.",
        final_workspace_head,
        final_remote_commit,
        expected_commit,
    )
    if final_workspace_head != expected_commit:
        raise RuntimeError(
            "Fabric workspace head does not match the expected pipeline commit after Git update "
            f"(expected {expected_commit}, got {final_workspace_head})."
        )
    if final_remote_commit != expected_commit:
        raise RuntimeError(
            "Fabric remote Git commit does not match the expected pipeline commit after Git update "
            f"(expected {expected_commit}, got {final_remote_commit})."
        )


def _patch_publish_folders(workspace: FabricWorkspace) -> None:
    """
    Monkey-patch _publish_folders to handle HTTP 409 (FolderDisplayNameAlreadyInUse).

    fabric-cicd 1.1.0 crashes when a subfolder already exists in the workspace
    (e.g. from a previous partial run). This patch catches the 409 and falls back
    to scanning the workspace API to retrieve the existing folder's ID, so the
    local hierarchy is kept consistent.

    Remove this patch when fabric-cicd handles 409 on folder creation natively.
    """
    import re

    from fabric_cicd import constants
    from fabric_cicd._common._check_utils import check_regex
    from fabric_cicd._common._exceptions import InvokeError
    from fabric_cicd._common._logging import log_header

    _outer_logger = logger

    def patched_publish_folders(self: FabricWorkspace) -> None:
        _fabric_logger = logging.getLogger("fabric_cicd.fabric_workspace")
        sorted_folders = sorted(self.repository_folders.keys(), key=lambda path: path.count("/"))
        log_header(_fabric_logger, "Publishing Workspace Folders")
        _fabric_logger.info("Publishing Workspace Folders")

        for folder_path in sorted_folders:
            if self.publish_folder_path_exclude_regex:
                regex_pattern = check_regex(self.publish_folder_path_exclude_regex)
                if regex_pattern.search(folder_path):
                    _fabric_logger.info("Skipping folder '%s' — excluded by regex.", folder_path)
                    continue
                ancestor_path = folder_path
                ancestor_excluded = False
                while "/" in ancestor_path and ancestor_path != "":
                    ancestor_path = ancestor_path.rsplit("/", 1)[0]
                    if ancestor_path and regex_pattern.search(ancestor_path):
                        ancestor_excluded = True
                        break
                if ancestor_excluded:
                    _fabric_logger.info(
                        "Skipping folder '%s' — ancestor excluded by regex.", folder_path
                    )
                    continue

            if self.publish_folder_path_to_include:
                is_included = folder_path in self.publish_folder_path_to_include
                is_ancestor_of_included = any(
                    included.startswith(folder_path + "/")
                    for included in self.publish_folder_path_to_include
                )
                if not is_included and not is_ancestor_of_included:
                    _fabric_logger.info("Skipping folder '%s' — not in include list.", folder_path)
                    continue

            if folder_path in self.deployed_folders:
                self.repository_folders[folder_path] = self.deployed_folders[folder_path]
                continue

            folder_name = folder_path.split("/")[-1]
            folder_parent_path = "/".join(folder_path.split("/")[:-1])
            folder_parent_id = self.repository_folders.get(folder_parent_path, None)

            if re.search(constants.INVALID_FOLDER_CHAR_REGEX, folder_name):
                msg = f"Folder name '{folder_name}' contains invalid characters."
                raise Exception(msg)  # noqa: TRY002

            request_body: dict = {"displayName": folder_name}
            if folder_parent_id:
                request_body["parentFolderId"] = folder_parent_id

            request_url = f"{self.base_api_url}/folders"
            try:
                response = self.endpoint.invoke(method="POST", url=request_url, body=request_body)
                self.repository_folders[folder_path] = response["body"]["id"]
            except InvokeError as exc:
                if "FolderDisplayNameAlreadyInUse" in str(exc):
                    _outer_logger.warning(
                        "Folder '%s' already exists — refreshing and resolving ID.", folder_path
                    )
                    self._refresh_deployed_folders()
                    if folder_path in self.deployed_folders:
                        self.repository_folders[folder_path] = self.deployed_folders[folder_path]
                    else:
                        raise RuntimeError(
                            f"Folder '{folder_path}' reported as already existing "
                            "but not found after refresh."
                        ) from exc
                else:
                    raise

        _fabric_logger.info(f"{constants.INDENT}Published")

    workspace._publish_folders = types.MethodType(patched_publish_folders, workspace)


_ITEM_FOLDER_PATTERN = re.compile(r"^(?P<name>.+)\.(?P<type>[A-Za-z]+)$")


def _local_item_names(repository_directory: str) -> set[str]:
    """Return every item name declared as a `<name>.<ItemType>` folder anywhere
    under *repository_directory* (searched recursively; items may live nested,
    e.g. under `fabric/elt/core/`).
    """
    names: set[str] = set()
    root = Path(repository_directory)
    if not root.exists():
        return names
    for path in root.rglob("*"):
        if path.is_dir():
            match = _ITEM_FOLDER_PATTERN.match(path.name)
            if match:
                names.add(match.group("name"))
    return names


class _ItemResolutionError(RuntimeError):
    """Raised when a live item-listing lookup cannot resolve item GUIDs.

    Ported from `tool/fat/deploy/identity.py`'s `ItemResolutionError`. This script
    cannot import that module (no `fat` package in client repos — see AGENTS.md's
    self-contained-script convention), so identity.py's live-resolution intent is
    duplicated here rather than imported. Never falls back to a placeholder or
    stale value.
    """


def _fetch_workspace_items_live(
    workspace_id: str, credential: ClientSecretCredential
) -> list[dict[str, Any]]:
    """Call the Fabric REST API to list items in *workspace_id* and return the parsed list.

    Live API call — not a cache read.

    Uses the same `_get_json`/`_fabric_headers`/`_FABRIC_API` REST helpers (from
    `_fabric_lro.py`) every other Fabric API call in this script already uses,
    rather than shelling out to the `fab` CLI as `tool/fat/deploy/identity.py`
    does in the interactive agent-driven flow (where the developer's own `fab auth
    login` session is what authenticates it). That approach was ported here
    verbatim in an earlier version and is wrong for this script's context: an
    unattended CI run has no interactive `fab` session, the scaffolded CI
    workflows never install the Fabric CLI at all, and this script already
    authenticates every *other* Fabric API call via `credential` directly.
    Confirmed live: a `release.yml` run failed with
    `FileNotFoundError: [Errno 2] No such file or directory: 'fab'` the moment a
    non-dev environment's publish needed sibling-item GUID resolution (dev-only
    deploys never hit this path, which is why post-merge.yml's git-sync had
    already succeeded against the same repo). Raises `_ItemResolutionError` on
    error.
    """
    context = f"listing items for workspace {workspace_id}"
    try:
        payload = _get_json(
            f"{_FABRIC_API}/workspaces/{workspace_id}/items", _fabric_headers(credential), context
        )
    except RuntimeError as exc:
        raise _ItemResolutionError(str(exc)) from exc

    try:
        return list(payload["value"])
    except KeyError as exc:
        raise _ItemResolutionError(f"{context}: response had no 'value' field") from exc


def _resolve_item_ids(workspace_id: str, credential: ClientSecretCredential) -> dict[str, str]:
    """Resolve every item in *workspace_id* to its live GUID, name -> GUID.

    Live API call (one items-list call) — not a cache read. Ported from
    `tool/fat/deploy/identity.py::resolve_item_ids`.
    """
    items = _fetch_workspace_items_live(workspace_id, credential)
    return {item["displayName"]: item["id"] for item in items}


def _resolve_sibling_find_replace(
    workspace: FabricWorkspace,
    dev_workspace_id: str | None,
    credential: ClientSecretCredential,
) -> list[dict[str, Any]]:
    """Resolve find_replace entries for sibling items referenced by dev's literal GUID.

    Generalizes the two-pass bootstrap strategy documented in
    `tool/fat/deploy/bootstrap.py` (dev's first tokenized publish) to any
    environment's publish: an item whose content references a sibling item by a
    literal GUID baked in during dev's bootstrap (rather than a `find_replace`-covered
    token) needs that GUID find_replace'd to this workspace's live GUID for the same
    item — but only once that sibling item actually exists live in this workspace.
    Both the dev-side literal GUIDs and this workspace's live GUIDs are resolved live
    via the Fabric REST API (see `_resolve_item_ids`) rather than read from
    `fabric.yml`, since per-item GUID maps are no longer authored/generated there.

    Returns an empty list (not an error) for names with no live match in this
    workspace yet — a genuinely brand-new item that has never been created here
    still can't be resolved this way and is left to
    `_retry_publish_after_partial_failure`'s reactive path.

    Confirmed: fabric-cicd's `find_replace` rewrites raw file content generically —
    it is not filtered by file type or nesting depth (`Item.collect_item_files`
    walks every file under an item's directory with no extension filter, and
    `File.type` classification is content-sniffed via `filetype.guess`, not
    extension-based) — so this also reaches a `DataBuildToolJob`'s nested
    `Code/dbt/seeds/*.csv` content, not just DataPipeline/Notebook JSON. See
    `tests/deploy/test_find_replace_csv_seed.py::
    test_find_replace_rewrites_literal_guid_in_nested_dbt_seed_csv`.
    """
    if not dev_workspace_id or workspace.workspace_id == dev_workspace_id:
        return []

    names = _local_item_names(workspace.repository_directory)
    dev_item_ids = _resolve_item_ids(dev_workspace_id, credential)
    target_item_ids = _resolve_item_ids(workspace.workspace_id, credential)

    return [
        {"find_value": dev_guid, "replace_value": {workspace.environment: live_guid}}
        for name in names & dev_item_ids.keys()
        if (dev_guid := dev_item_ids[name])
        and (live_guid := target_item_ids.get(name))
        and live_guid != dev_guid
    ]


def _apply_new_find_replace_entries(
    workspace: FabricWorkspace, entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add *entries* not already covered by `workspace`'s find_replace list.

    Returns the subset of *entries* actually added (i.e. not already covered).
    """
    find_replace = workspace.environment_parameter.setdefault("find_replace", [])
    already_covered = {entry.get("find_value") for entry in find_replace}
    new_entries = [entry for entry in entries if entry["find_value"] not in already_covered]
    for entry in new_entries:
        logger.info(
            "Resolving dev literal %s -> %s for environment %s.",
            entry["find_value"],
            entry["replace_value"][workspace.environment],
            workspace.environment,
        )
    find_replace.extend(new_entries)
    return new_entries


# Empirically required (see live-fire test notes, 2026-08-05): Fabric enforces a
# genuine backend cooldown after a *failed* item-creation attempt before it will
# accept a retry with the same displayName — confirmed reproducible on a workspace
# that had never seen any prior attempt, so it is not a name-reuse penalty carried
# over from repeated failures. A retry issued only seconds after the failure
# reliably fails again with "Requested '<name>' is not available yet and is
# expected to become available in the upcoming minutes."; 90 seconds reliably
# succeeded in testing. This is conservative, not a documented Fabric SLA.
_PUBLISH_RETRY_DELAY_SECONDS = 90


def _retry_publish_after_partial_failure(
    workspace: FabricWorkspace,
    dev_workspace_id: str | None,
    publish_error: PublishError,
    credential: ClientSecretCredential,
) -> None:
    """One-time recovery for a genuinely brand-new item with nothing live to harvest yet.

    Item-GUID resolution now normally runs proactively before the first publish
    attempt (see `_resolve_sibling_find_replace`, called from `main()`), so this
    reactive path is only still needed for the one case proactive resolution can't
    cover: a sibling item that did not exist live in this workspace when the run
    started, but gets created earlier in the same publish pass that then fails on
    a later item referencing it by dev's literal GUID. Once the sibling exists,
    re-resolving its live ID and adding the missing find_replace entry (dev's
    literal GUID -> this workspace's real GUID) lets a second pass succeed with no
    manual intervention.

    If no new find_replace entry can be derived, *publish_error* is not a missing-
    GUID-reference case at all — it's a genuinely unrelated publish failure — so
    it is re-raised as-is rather than masked behind a generic error, preserving its
    per-item detail for CI logs.

    Waits `_PUBLISH_RETRY_DELAY_SECONDS` before retrying — see that constant's
    comment for why an immediate retry reliably fails regardless of this
    function's find_replace fix being correct.
    """
    logger.info(
        "Waiting %s seconds for Fabric's post-failure item-creation cooldown to "
        "clear before retrying.",
        _PUBLISH_RETRY_DELAY_SECONDS,
    )
    time.sleep(_PUBLISH_RETRY_DELAY_SECONDS)

    new_entries = _apply_new_find_replace_entries(
        workspace, _resolve_sibling_find_replace(workspace, dev_workspace_id, credential)
    )
    if not new_entries:
        logger.error(
            "Two-pass retry could not derive any new find_replace entry from the "
            "workspace's current live items — this is not a missing sibling-item "
            "GUID reference. Original publish failure: %s",
            publish_error,
        )
        raise publish_error

    publish_all_items(workspace)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)

    args = _parse_args()
    credential = _load_credential()
    if args.git_sync:
        if args.env.lower() != "dev":
            raise ValueError("--git-sync may only be used with --env dev.")
        if not args.expected_commit or not args.expected_commit.strip():
            raise ValueError("--git-sync requires --expected-commit.")
        _sync_git_workspace(
            args.workspace_id, args.expected_commit.strip(), credential, args.repository
        )
        return
    if args.expected_commit:
        raise ValueError("--expected-commit may only be used with --git-sync.")

    workspace = FabricWorkspace(
        workspace_id=args.workspace_id,
        repository_directory=args.repository,
        environment=args.env,
        token_credential=credential,
        item_type_in_scope=["DataPipeline", "Notebook", "DataBuildToolJob"],
    )
    _patch_publish_folders(workspace)

    logger.info(f"Workspace ID: {args.workspace_id}")
    if args.data_workspace_id:
        logger.info(f"Data workspace ID: {args.data_workspace_id}")
    logger.info(f"Environment: {args.env}")
    logger.info(f"Repository path: {args.repository}")

    config = _load_fabric_config(Path("fabric.yml"))
    dev_env = next((e for e in config.environments if e.name == "dev"), None)
    dev_workspace_id = dev_env.workspace_id if dev_env is not None else None
    if not dev_workspace_id:
        logger.warning(
            "No 'dev' environment (or no workspace_id on it) found in fabric.yml; "
            "skipping proactive item-GUID resolution. Sibling-item GUID references "
            "will only be resolved reactively, on a first-publish failure."
        )
    else:
        _apply_new_find_replace_entries(
            workspace, _resolve_sibling_find_replace(workspace, dev_workspace_id, credential)
        )

    # Split-layout projects (see --data-workspace-id) keep lakehouses/warehouses in a
    # second workspace, separate from the items workspace above. Item names still only
    # come from _local_item_names' repository-directory scan (that folder-scan
    # restriction is a separate, already-tracked issue — not addressed here), but the
    # live dev/target GUID resolution needs to run a second time against the data
    # workspace's own dev and target GUIDs. A lightweight namespace stands in for the
    # FabricWorkspace here because _resolve_sibling_find_replace only reads
    # workspace_id / repository_directory / environment off its `workspace` argument.
    if args.data_workspace_id:
        dev_data_workspace_id = dev_env.data_workspace_id if dev_env is not None else None
        if not dev_data_workspace_id:
            logger.warning(
                "--data-workspace-id was supplied but no 'dev' environment (or no "
                "data_workspace_id on it) was found in fabric.yml; skipping proactive "
                "item-GUID resolution for the data workspace."
            )
        else:
            data_workspace = types.SimpleNamespace(
                workspace_id=args.data_workspace_id,
                repository_directory=args.repository,
                environment=args.env,
            )
            _apply_new_find_replace_entries(
                workspace,
                _resolve_sibling_find_replace(data_workspace, dev_data_workspace_id, credential),
            )

    try:
        publish_all_items(workspace)
    except PublishError as exc:
        logger.warning(
            "Initial publish reported failed item(s); attempting a one-time "
            "two-pass retry (see _retry_publish_after_partial_failure)."
        )
        _retry_publish_after_partial_failure(workspace, dev_workspace_id, exc, credential)


if __name__ == "__main__":
    main()
