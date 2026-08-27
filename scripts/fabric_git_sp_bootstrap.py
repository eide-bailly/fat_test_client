"""
One-time bootstrap for the deploy service principal's Fabric Git integration.

Run each subcommand in order, as a human with the deploy SP's credentials
available (see .env / the SERVICE_PRINCIPAL_* env vars used by
scripts/deploy_fabric.py). This replaces the manual "Update from Git" step
documented in docs/cicd.md ("Manual dev synchronization") once complete.

    uv run python scripts/fabric_git_sp_bootstrap.py create-connection \
        --repo-url https://dev.azure.com/<org>/<project>/_git/<repo>
    uv run python scripts/fabric_git_sp_bootstrap.py set-credentials \
        --workspace-id <dev-workspace-guid> --connection-id <id-from-previous-step>
    uv run python scripts/fabric_git_sp_bootstrap.py validate-sync \
        --workspace-id <dev-workspace-guid>
    uv run python scripts/fabric_git_sp_bootstrap.py validate-sync \
        --workspace-id <dev-workspace-guid> --apply

See plans/real-guid-developer-workflow-migration.md, "Resume Procedure", for
the full context and required order of operations.
"""

import argparse
import json
import logging
import os
import sys

from azure.identity import ClientSecretCredential
import requests

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"

_REQUIRED_ENV_VARS = (
    "SERVICE_PRINCIPAL_TENANT_ID",
    "SERVICE_PRINCIPAL_CLIENT_ID",
    "DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET",
)

logger = logging.getLogger(__name__)


def _load_credential() -> ClientSecretCredential:
    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        sys.exit("Missing required environment variables:\n" + "\n".join(f"  {v}" for v in missing))

    return ClientSecretCredential(
        tenant_id=os.environ["SERVICE_PRINCIPAL_TENANT_ID"],
        client_id=os.environ["SERVICE_PRINCIPAL_CLIENT_ID"],
        client_secret=os.environ["DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET"],
    )


def _fabric_headers(credential: ClientSecretCredential) -> dict[str, str]:
    token = credential.get_token(FABRIC_SCOPE).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_connection(args: argparse.Namespace) -> None:
    credential = _load_credential()
    headers = _fabric_headers(credential)
    body = {
        "displayName": args.display_name,
        "connectivityType": "ShareableCloud",
        "connectionDetails": {
            "type": "AzureDevOpsSourceControl",
            "creationMethod": "AzureDevOpsSourceControl.Contents",
            "parameters": [{"dataType": "Text", "name": "url", "value": args.repo_url}],
        },
        "credentialDetails": {
            "credentials": {
                "credentialType": "ServicePrincipal",
                "tenantId": os.environ["SERVICE_PRINCIPAL_TENANT_ID"],
                "servicePrincipalClientId": os.environ["SERVICE_PRINCIPAL_CLIENT_ID"],
                "servicePrincipalSecret": os.environ[
                    "DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET"
                ],
            }
        },
    }
    response = requests.post(f"{FABRIC_API}/connections", headers=headers, json=body, timeout=30)
    response.raise_for_status()
    connection = response.json()
    logger.info("Created connection '%s' (id=%s)", connection["displayName"], connection["id"])
    print(connection["id"])


def set_credentials(args: argparse.Namespace) -> None:
    credential = _load_credential()
    headers = _fabric_headers(credential)
    url = f"{FABRIC_API}/workspaces/{args.workspace_id}/git/myGitCredentials"
    body = {"source": "ConfiguredConnection", "connectionId": args.connection_id}
    response = requests.patch(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    logger.info(
        "Git credentials for workspace %s set to connection %s",
        args.workspace_id,
        args.connection_id,
    )
    print(json.dumps(response.json(), indent=2))


def rename_item(args: argparse.Namespace) -> None:
    credential = _load_credential()
    headers = _fabric_headers(credential)
    url = f"{FABRIC_API}/workspaces/{args.workspace_id}/items/{args.item_id}"
    response = requests.patch(
        url, headers=headers, json={"displayName": args.display_name}, timeout=30
    )
    if not response.ok:
        logger.error("Rename failed (%s): %s", response.status_code, response.text)
    response.raise_for_status()
    logger.info("Renamed item %s to '%s'", args.item_id, args.display_name)


def validate_sync(args: argparse.Namespace) -> None:
    credential = _load_credential()
    headers = _fabric_headers(credential)

    creds_url = f"{FABRIC_API}/workspaces/{args.workspace_id}/git/myGitCredentials"
    creds_response = requests.get(creds_url, headers=headers, timeout=30)
    creds_response.raise_for_status()
    logger.info("Current SP Git credentials: %s", creds_response.json())

    status_response = requests.get(
        f"{FABRIC_API}/workspaces/{args.workspace_id}/git/status", headers=headers, timeout=30
    )
    status_response.raise_for_status()
    status = status_response.json()
    logger.info("Git status as SP:\n%s", json.dumps(status, indent=2))

    workspace_head = status.get("workspaceHead")
    remote_commit_hash = status.get("remoteCommitHash")

    if not remote_commit_hash or workspace_head == remote_commit_hash:
        logger.info("Workspace is already synced to %s. No update needed.", workspace_head)
        return

    if not args.apply:
        logger.info(
            "Workspace head (%s) differs from remote (%s). Re-run with --apply to update.",
            workspace_head,
            remote_commit_hash,
        )
        return

    body = {
        "workspaceHead": workspace_head,
        "remoteCommitHash": remote_commit_hash,
        "options": {"allowOverrideItems": True},
    }
    if args.conflict_resolution:
        body["conflictResolution"] = {
            "conflictResolutionType": "Workspace",
            "conflictResolutionPolicy": args.conflict_resolution,
        }

    update_response = requests.post(
        f"{FABRIC_API}/workspaces/{args.workspace_id}/git/updateFromGit",
        headers=headers,
        json=body,
        timeout=30,
    )
    if not update_response.ok:
        logger.error(
            "updateFromGit failed (%s): %s", update_response.status_code, update_response.text
        )
    update_response.raise_for_status()
    logger.info(
        "Update from Git accepted (operation id=%s).",
        update_response.headers.get("x-ms-operation-id"),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap Fabric Git integration for the deploy service principal."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser(
        "create-connection", help="Create the Fabric AzureDevOpsSourceControl connection."
    )
    create.add_argument("--display-name", default="fabric-ado-git-sp")
    create.add_argument("--repo-url", required=True)
    create.set_defaults(func=create_connection)

    set_creds = subparsers.add_parser(
        "set-credentials", help="Point a workspace's Git credentials at a connection."
    )
    set_creds.add_argument("--workspace-id", required=True)
    set_creds.add_argument("--connection-id", required=True)
    set_creds.set_defaults(func=set_credentials)

    rename = subparsers.add_parser(
        "rename-item", help="Rename a workspace item (e.g. to break a delete/add name collision)."
    )
    rename.add_argument("--workspace-id", required=True)
    rename.add_argument("--item-id", required=True)
    rename.add_argument("--display-name", required=True)
    rename.set_defaults(func=rename_item)

    validate = subparsers.add_parser(
        "validate-sync", help="Check Git status as the SP, and sync if --apply is given."
    )
    validate.add_argument("--workspace-id", required=True)
    validate.add_argument("--apply", action="store_true", help="Call updateFromGit if behind.")
    validate.add_argument(
        "--conflict-resolution",
        choices=["PreferWorkspace", "PreferRemote"],
        default=None,
        help="Required if git/status reports any conflictType=Conflict items.",
    )
    validate.set_defaults(func=validate_sync)

    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
