"""
One-time bootstrap for the deploy service principal's Fabric Git integration on the
GitHub provider path (the analog of scripts/fabric_git_sp_bootstrap.py for Azure
DevOps-backed repos).

GitHub-backed Fabric Git integration requires a ``GitHubSourceControl`` Fabric
connection whose credential is a GitHub personal access token (a classic PAT with
``repo`` scope from a dedicated machine user per client org). Unlike Azure DevOps,
GitHub connections cannot use ``myGitCredentials.source = "Automatic"`` — every
identity (human or service principal) must point its per-workspace Git credentials
at a configured connection.

Run each subcommand in order, as a human with the deploy SP's credentials available
(see .env / the SERVICE_PRINCIPAL_* env vars used by scripts/deploy_fabric.py), and
with the machine user's PAT in the GITHUB_PAT env var (or in .env beside
fabric.yml). The PAT is sent only in the single connection-creation request body —
it is never logged, never printed, and never written to fabric.yml.

    uv run python scripts/fabric_git_github_bootstrap.py create-connection \
        --display-name github-<owner>-<repo>
    uv run python scripts/fabric_git_github_bootstrap.py set-credentials \
        --workspace-id <dev-workspace-guid> --connection-id <id-from-previous-step>
    uv run python scripts/fabric_git_github_bootstrap.py validate-sync \
        --workspace-id <dev-workspace-guid>
    uv run python scripts/fabric_git_github_bootstrap.py validate-sync \
        --workspace-id <dev-workspace-guid> --apply

The connection-creation payload shape below was verified against a live tenant
(Wave 1 spike, plans/add-github-support.md). Three details are load-bearing —
wrong shapes fail with generic 400s:

- the creation method is ``GitHubSourceControl.Contents`` (NOT
  ``GitHubSourceControl.PersonalAccessToken``);
- the credential field is ``key`` (NOT ``token``);
- the optional ``url`` parameter must be omitted entirely (it defaults to
  https://github.com; passing a repo or owner URL fails the server-side test
  connection).

Note: SPN-authored connection creation depends on the tenant switch "Service
principals can create workspaces, connections, and deployment pipelines". If
create-connection fails for that reason on a client tenant, create the connection
manually in the Fabric portal instead and pass its GUID to set-credentials.
"""

import argparse
import json
import logging
import os
from pathlib import Path
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

# Environment variable key holding the GitHub machine user's classic PAT. Matches
# the FAT MCP tool `fat_fabric_git_create_pat_connection`
# (fat.git_integration.pat_connection._PAT_ENV_KEY), so the same .env populates both.
_PAT_ENV_KEY = "GITHUB_PAT"

# Fabric connectionDetails vocabulary for GitHub source-control connections,
# verified via `GET /v1/connections/supportedConnectionTypes` in the Wave 1 spike.
_GITHUB_CONNECTION_TYPE = "GitHubSourceControl"
_GITHUB_CREATION_METHOD = "GitHubSourceControl.Contents"

logger = logging.getLogger(__name__)


def _load_env_values(env_path: Path) -> dict[str, str]:
    """Parse a .env file's simple ``KEY=value`` lines (empty dict if absent)."""
    if not env_path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_github_pat(config_path: Path = Path("fabric.yml")) -> str | None:
    """Resolve the GitHub PAT from the process environment, then .env beside
    *config_path* (the process environment always wins). Returns None when no PAT
    is available. The value is only ever consumed by `_build_connection_body` for
    the single outbound creation call — never logged or returned elsewhere.
    """
    env_file_values = _load_env_values(config_path.resolve().parent / ".env")
    return os.environ.get(_PAT_ENV_KEY) or env_file_values.get(_PAT_ENV_KEY) or None


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
    return {
        "Authorization": f"Bearer {credential.get_token(FABRIC_SCOPE).token}",
        "Content-Type": "application/json",
    }


def _build_connection_body(display_name: str, pat: str) -> dict:
    """Build the ``POST /v1/connections`` body for a GitHub PAT source-control
    connection. The spike-verified shape: creation method
    ``GitHubSourceControl.Contents``, no ``url`` parameter, credentials
    ``{"credentialType": "Key", "key": <pat>}``.
    """
    return {
        "connectivityType": "ShareableCloud",
        "displayName": display_name,
        "connectionDetails": {
            "type": _GITHUB_CONNECTION_TYPE,
            "creationMethod": _GITHUB_CREATION_METHOD,
        },
        "credentialDetails": {
            "singleSignOnType": "None",
            "connectionEncryption": "NotEncrypted",
            "skipTestConnection": False,
            "credentials": {"credentialType": "Key", "key": pat},
        },
    }


def create_connection(args: argparse.Namespace) -> None:
    pat = _resolve_github_pat(Path(args.config))
    if not pat:
        sys.exit(
            f"{_PAT_ENV_KEY} is not set in the process environment or in .env beside "
            f"{args.config} — cannot create the GitHub source-control connection without "
            "it. Populate it with the machine user's classic PAT (`repo` scope) and "
            "retry, or create the connection manually in the Fabric portal and register "
            "its GUID."
        )

    credential = _load_credential()
    headers = _fabric_headers(credential)
    body = _build_connection_body(args.display_name, pat)
    # `pat`/`body` are used only for this one outbound call and are never
    # referenced again — the PAT is never logged or echoed.
    del pat
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap Fabric Git integration for the deploy service principal "
        "on the GitHub provider path."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser(
        "create-connection", help="Create the Fabric GitHubSourceControl connection."
    )
    create.add_argument(
        "--display-name",
        default="github-source-control",
        help="Display name for the connection (convention: github-<owner>-<repo>).",
    )
    create.add_argument(
        "--config",
        default="fabric.yml",
        help="Path to fabric.yml; .env is resolved beside it for the GITHUB_PAT fallback.",
    )
    create.set_defaults(func=create_connection)

    set_creds = subparsers.add_parser(
        "set-credentials", help="Point a workspace's Git credentials at a connection."
    )
    set_creds.add_argument("--workspace-id", required=True)
    set_creds.add_argument("--connection-id", required=True)
    set_creds.set_defaults(func=set_credentials)

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

    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
