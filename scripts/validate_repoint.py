"""Standalone CLI mirroring `fat repoint validate` — the PR-time repoint validation gate.

Self-contained: only depends on `_fabric_config` (this directory), `pydantic`, `pyyaml`, and
stdlib. Ported by hand from the Fabric Agentic Toolkit's `fat repoint` package:
- fat/repoint/commands.py (the `validate` command's behavior/args/output)
- fat/repoint/validate.py (run_validation, validate_identity, validate_connections,
  validate_overrides, ValidationResult)
- fat/repoint/identity.py (scan_items, match_items, _read_logical_id, ItemIdentity,
  IdentityBreak, MatchResult)
- fat/repoint/connections.py (scan_connection_references, _walk_json, _looks_like_guid,
  ConnectionReference)
- fat/repoint/overrides.py (check_override_coverage, find_schedule_files,
  find_notebook_fallback_dbt_project_dirs, _non_dev_environments)

Design: with Spike A's find that the branch-out repoint transform's existence and scope
are conditional/unconfirmed, this module implements the plan's explicitly-stated fallback:
a validation-only gate. It never mutates either tree. It checks:

1. Item identity matching between a "before" tree (e.g. `dev`) and an "after" tree (e.g. a
   PR branch), keyed on (directory basename, logicalId) so that a pure move passes and
   only the exceptional renamed/re-identified/duplicated-name cases fail.
2. Connection GUID registration coverage in `fabric.yml`.
3. Override coverage (schedules, dbt target) for declared non-dev environments.
"""

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys

from _fabric_config import (
    Environment,
    FabricConfig,
    load_config,
    resolve_connections,
    validate_config,
)

_DEV_ENV_NAME = "dev"
_PLATFORM_FILE = ".platform"

# ---------------------------------------------------------------------------
# identity.py
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemIdentity:
    """Identity of a single Fabric item directory."""

    relative_path: str  # path relative to the fabric/ root, e.g. "elt/sources/x.DataPipeline"
    logical_id: str | None  # from .platform config.logicalId, if present/parseable


def _read_logical_id(platform_path: Path) -> str | None:
    """Return the logicalId recorded in a .platform file, or None if absent/unreadable."""
    try:
        data = json.loads(platform_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    config = data.get("config")
    if not isinstance(config, dict):
        return None
    logical_id = config.get("logicalId")
    return logical_id if isinstance(logical_id, str) else None


def scan_items(tree_root: Path) -> dict[str, ItemIdentity]:
    """Scan a fabric/ tree for item directories, keyed by relative path.

    An "item directory" is any directory containing a `.platform` file. Nested item
    directories (an item inside another item's directory, which should not happen in
    practice) are each reported independently.
    """
    items: dict[str, ItemIdentity] = {}
    if not tree_root.exists():
        return items

    for platform_path in sorted(tree_root.rglob(_PLATFORM_FILE)):
        item_dir = platform_path.parent
        relative_path = item_dir.relative_to(tree_root).as_posix()
        logical_id = _read_logical_id(platform_path)
        items[relative_path] = ItemIdentity(relative_path=relative_path, logical_id=logical_id)
    return items


@dataclass(frozen=True)
class IdentityBreak:
    """A single identity-break finding requiring plain-language remediation.

    Only the narrowed rule's exceptional cases produce one: a directory *renamed* (its
    basename changed), a like-named directory whose logicalId changed, or two items
    sharing a basename within a single tree. A directory that merely *moved* to a new
    parent never produces one.
    """

    message: str


@dataclass(frozen=True)
class MatchResult:
    """Classification of item identities across a before/after tree pair.

    Identity is keyed on (basename, logical_id), so the recorded relative paths are
    reporting detail only — a path change alone never moves an item between buckets.
    """

    unchanged: list[str]  # after-tree paths whose basename+logicalId survived (moves included)
    new_items: list[str]  # after-tree paths with no basename or logicalId counterpart in before
    breaks: list[IdentityBreak]  # exceptional renamed/duplicated/re-identified findings


def _basename(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[-1]


def _index_by_basename(items: dict[str, ItemIdentity]) -> dict[str, list[ItemIdentity]]:
    """Group item identities by directory basename, each group sorted by relative path."""
    index: dict[str, list[ItemIdentity]] = {}
    for identity in items.values():
        index.setdefault(_basename(identity.relative_path), []).append(identity)
    for group in index.values():
        group.sort(key=lambda identity: identity.relative_path)
    return index


def match_items(before: dict[str, ItemIdentity], after: dict[str, ItemIdentity]) -> MatchResult:
    """Match item identities between a "before" tree and an "after" tree.

    Matching rule: an identity is the pair (basename, logical_id) — the item directory's
    *name* (which is Fabric's `<displayName>.<type>`) plus the `.platform` logicalId. The
    directory's parent path is deliberately not part of identity: reorganizing the
    repository's folder structure does not change what Fabric considers the item to be, so
    a pure move must not fail the gate.

    Classification:
    - unchanged: basename present in both trees, with equal logicalIds or with either side's
      logicalId missing (unreadable/absent `.platform` -- not enough evidence for a break).
      The parent directory may differ freely.
    - new: basename present only in "after" whose logicalId matches nothing in "before".
      Passes through untouched (it will receive its dev GUIDs on next sync).
    - break: (a) a basename present in both trees whose logicalIds are both present and
      differ -- a like-named item with a different identity; (b) a basename present only in
      "before" whose logicalId reappears in "after" under a *different* basename -- a
      genuine rename; (c) two items sharing a basename within one tree, which means two
      items with the same displayName and type in one workspace and already corrupts the
      name-keyed GUID map deploy_fabric.py builds at publish time.

    A basename that disappears from "after" with its logicalId nowhere else is a clean
    deletion, not an error -- it is simply an item that no longer needs repointing.
    """
    unchanged: list[str] = []
    new_items: list[str] = []
    breaks: list[IdentityBreak] = []

    before_by_name = _index_by_basename(before)
    after_by_name = _index_by_basename(after)

    before_logical_ids = {i.logical_id: i.relative_path for i in before.values() if i.logical_id}
    after_logical_ids = {i.logical_id: i.relative_path for i in after.values() if i.logical_id}

    duplicate_names: set[str] = set()
    for tree_label, index in (("before", before_by_name), ("after", after_by_name)):
        for name, group in sorted(index.items()):
            if len(group) == 1:
                continue
            duplicate_names.add(name)
            paths = ", ".join(f"'{identity.relative_path}'" for identity in group)
            breaks.append(
                IdentityBreak(
                    message=(
                        f"Identity break: {len(group)} items in the {tree_label} tree share the"
                        f" directory name '{name}' ({paths}). A Fabric item is identified by"
                        " its displayName and type within a workspace, so like-named items in"
                        " different folders collide on deploy. Re-establish identity by"
                        " renaming one of them, or treat as a new item if this is intentional."
                    )
                )
            )

    for name in sorted(set(before_by_name) | set(after_by_name)):
        if name in duplicate_names:
            continue
        before_identity = before_by_name[name][0] if name in before_by_name else None
        after_identity = after_by_name[name][0] if name in after_by_name else None

        if before_identity is not None and after_identity is not None:
            same_logical_id = before_identity.logical_id == after_identity.logical_id
            missing_logical_id = (
                before_identity.logical_id is None or after_identity.logical_id is None
            )
            if same_logical_id or missing_logical_id:
                # Identity intact; the parent directory is free to differ.
                unchanged.append(after_identity.relative_path)
                continue
            breaks.append(
                IdentityBreak(
                    message=(
                        f"Identity break for item '{name}': the directory name matches between"
                        " before and after, but the .platform logicalId differs"
                        f" ('{before_identity.relative_path}':"
                        f" {before_identity.logical_id} ->"
                        f" '{after_identity.relative_path}': {after_identity.logical_id})."
                        " Re-establish identity by keeping the original directory name and"
                        " logicalId together, or treat as a new item if this is intentional."
                    )
                )
            )
            continue

        if before_identity is not None:
            logical_id = before_identity.logical_id
            if logical_id and logical_id in after_logical_ids:
                new_path = after_logical_ids[logical_id]
                breaks.append(
                    IdentityBreak(
                        message=(
                            f"Identity break: item at '{before_identity.relative_path}'"
                            f" (logicalId {logical_id}) now appears at '{new_path}' in the after"
                            " tree under a different directory name. Moving an item to a new"
                            " folder is fine; renaming it is not. Re-establish identity by"
                            " restoring the original directory name, or treat as a new item if"
                            " this is intentional."
                        )
                    )
                )
            # Otherwise a clean deletion -- not an error.
            continue

        assert after_identity is not None
        logical_id = after_identity.logical_id
        if logical_id and logical_id in before_logical_ids:
            # The rename case, already reported from the "before" side.
            continue
        new_items.append(after_identity.relative_path)

    return MatchResult(unchanged=sorted(unchanged), new_items=sorted(new_items), breaks=breaks)


# ---------------------------------------------------------------------------
# connections.py
# ---------------------------------------------------------------------------

# Fabric item file extensions worth scanning for connection references. Pipeline content is
# JSON; notebooks are .ipynb (JSON); the generic JSON walk below covers any JSON-based item
# definition without needing an exhaustive file-name list.
_JSON_LIKE_SUFFIXES = {".json", ".ipynb", ".platform"}

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# JSON keys treated as connection-GUID-bearing fields.
_CONNECTION_KEYS = {"connection", "connectionId"}


@dataclass(frozen=True)
class ConnectionReference:
    """A single connection GUID occurrence found in an item definition."""

    guid: str
    file_path: str  # path relative to the scanned tree root


def _looks_like_guid(value: object) -> bool:
    return isinstance(value, str) and bool(_GUID_RE.match(value))


def _walk_json(node: object, file_path: str, found: list[ConnectionReference]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _CONNECTION_KEYS and _looks_like_guid(value):
                found.append(ConnectionReference(guid=value, file_path=file_path))
            else:
                _walk_json(value, file_path, found)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, file_path, found)


def scan_connection_references(tree_root: Path) -> list[ConnectionReference]:
    """Scan every JSON-like item definition file under *tree_root* for connection GUIDs."""
    found: list[ConnectionReference] = []
    if not tree_root.exists():
        return found

    for file_path in sorted(tree_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix not in _JSON_LIKE_SUFFIXES:
            continue
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        relative_path = file_path.relative_to(tree_root).as_posix()
        _walk_json(data, relative_path, found)

    return found


# ---------------------------------------------------------------------------
# overrides.py
# ---------------------------------------------------------------------------


def find_schedule_files(tree_root: Path) -> list[str]:
    """Return relative paths of every `.schedules` file under *tree_root*."""
    if not tree_root.exists():
        return []
    return sorted(
        p.relative_to(tree_root).as_posix() for p in tree_root.rglob(".schedules") if p.is_file()
    )


def find_notebook_fallback_dbt_project_dirs(tree_root: Path) -> list[str]:
    """Return relative paths of dbt project directories NOT nested inside a
    `*.DataBuildToolJob` item directory.

    Per `dbt-framework/exemplars/native-dbt-job-notes.md`, the native Fabric dbt job
    runtime generates its own profile from the item's `connectionSettings` and never
    reads the committed `profiles.yml`'s `target` ("used for local development
    only") -- so a dbt project nested inside a `.DataBuildToolJob` item has no runtime
    dependency on `dbt_target`. A dbt project found anywhere *else* is being run by
    the notebook-fallback tier (`dbt-framework/exemplars/notebook-run-fallback/
    run_dbt.py`), which does invoke `dbt --target <target>` via subprocess against
    that committed `profiles.yml` -- that is the only case where `dbt_target` has a
    real runtime consumer.
    """
    if not tree_root.exists():
        return []
    found: list[str] = []
    for path in tree_root.rglob("dbt_project.yml"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(tree_root).parts
        if any(part.endswith(".DataBuildToolJob") for part in relative_parts):
            continue
        found.append(path.parent.relative_to(tree_root).as_posix())
    return sorted(found)


def _non_dev_environments(config: FabricConfig) -> list[Environment]:
    return [e for e in config.environments if e.name != _DEV_ENV_NAME]


def check_override_coverage(tree_root: Path, config: FabricConfig) -> list[str]:
    """Return plain-language messages for missing override coverage.

    - If any `.schedules` file exists under *tree_root*, every non-dev environment must
      declare `schedules_enabled` in its `overrides` block (the reserved key
      `gen-parameters` uses to flip `.schedules` enablement per environment).
    - If any dbt project directory exists under *tree_root* that is NOT nested inside a
      `.DataBuildToolJob` item directory (i.e. one that will be run by the
      notebook-fallback tier, which invokes `dbt --target <target>` against the
      committed `profiles.yml`), every non-dev environment must declare `dbt_target` in
      its `overrides` block (the reserved key `gen-parameters` scopes to
      `item_type: DataBuildToolJob`).
    """
    messages: list[str] = []
    non_dev = _non_dev_environments(config)
    if not non_dev:
        return messages

    schedule_files = find_schedule_files(tree_root)
    if schedule_files:
        for env in non_dev:
            if "schedules_enabled" not in env.overrides:
                messages.append(
                    f"Environment '{env.name}' is missing the 'schedules_enabled' override,"
                    f" but .schedules file(s) are present in the repo ({schedule_files[0]}"
                    f"{' and others' if len(schedule_files) > 1 else ''}). Add"
                    f" 'schedules_enabled: true|false' under environments[{env.name}]"
                    ".overrides in fabric.yml."
                )

    fallback_dbt_dirs = find_notebook_fallback_dbt_project_dirs(tree_root)
    if fallback_dbt_dirs:
        for env in non_dev:
            if "dbt_target" not in env.overrides:
                messages.append(
                    f"Environment '{env.name}' is missing the 'dbt_target' override, but a"
                    f" notebook-fallback dbt project is present in the repo"
                    f" ({fallback_dbt_dirs[0]}). This tier invokes dbt with --target against"
                    f" the committed profiles.yml (see dbt-framework/exemplars/"
                    f"notebook-run-fallback/run_dbt.py). Add 'dbt_target: <name>' under"
                    f" environments[{env.name}].overrides in fabric.yml."
                )

    return messages


# ---------------------------------------------------------------------------
# validate.py
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Aggregate result of the repoint validation gate."""

    identity_breaks: list[str] = field(default_factory=list)
    unregistered_connections: list[str] = field(default_factory=list)
    override_gaps: list[str] = field(default_factory=list)
    unchanged_count: int = 0
    new_item_count: int = 0

    @property
    def ok(self) -> bool:
        return not (self.identity_breaks or self.unregistered_connections or self.override_gaps)

    @property
    def messages(self) -> list[str]:
        return [*self.identity_breaks, *self.unregistered_connections, *self.override_gaps]


def validate_identity(before: Path, after: Path) -> tuple[list[str], int, int]:
    """Run the item identity match; return (break messages, unchanged count, new count)."""
    before_items = scan_items(before)
    after_items = scan_items(after)
    result = match_items(before_items, after_items)
    return (
        [b.message for b in result.breaks],
        len(result.unchanged),
        len(result.new_items),
    )


def validate_connections(
    after: Path, config: FabricConfig, env_name: str = _DEV_ENV_NAME
) -> list[str]:
    """Return plain-language failure messages for unregistered connection GUIDs.

    Registration is checked against the resolved connection set for *env_name* (defaults to
    "dev", the canonical environment) — shared-by-default, with per-environment overrides
    only where they genuinely differ.
    """
    registered_guids = set(resolve_connections(config, env_name).values())
    references = scan_connection_references(after)

    messages: list[str] = []
    for ref in references:
        if ref.guid not in registered_guids:
            messages.append(
                f"Unregistered connection GUID '{ref.guid}' found in '{ref.file_path}'."
                " Register it in fabric.yml's top-level `connections` block (shared across"
                " environments) or under the relevant environment's `connections` override."
            )
    return messages


def validate_overrides(after: Path, config: FabricConfig) -> list[str]:
    """Return plain-language failure messages for missing override coverage."""
    return check_override_coverage(after, config)


def run_validation(
    before: Path,
    after: Path,
    config: FabricConfig,
    env_name: str = _DEV_ENV_NAME,
) -> ValidationResult:
    """Run all three repoint validation gate checks and aggregate the result."""
    identity_breaks, unchanged_count, new_item_count = validate_identity(before, after)
    unregistered_connections = validate_connections(after, config, env_name)
    override_gaps = validate_overrides(after, config)

    return ValidationResult(
        identity_breaks=identity_breaks,
        unregistered_connections=unregistered_connections,
        override_gaps=override_gaps,
        unchanged_count=unchanged_count,
        new_item_count=new_item_count,
    )


# ---------------------------------------------------------------------------
# commands.py (CLI)
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate branch-out-to-dev item identity, connection, and override coverage."
            " In-memory validation only: never mutates either tree. Fails with"
            " plain-language remediation on identity breaks (renamed/deleted items),"
            " unregistered connection GUIDs, and missing override coverage."
        )
    )
    parser.add_argument(
        "--before", type=Path, required=True, help="Path to the 'before' fabric/ tree."
    )
    parser.add_argument(
        "--after", type=Path, required=True, help="Path to the 'after' fabric/ tree."
    )
    parser.add_argument(
        "--config", "-c", type=Path, default=Path("fabric.yml"), help="Path to fabric.yml."
    )
    parser.add_argument(
        "--env",
        default=_DEV_ENV_NAME,
        help="Environment name to resolve connections against.",
    )
    args = parser.parse_args()

    before: Path = args.before
    after: Path = args.after
    config_path: Path = args.config
    env: str = args.env

    if not after.exists():
        print(f"Error: --after path not found: {after}", file=sys.stderr)
        sys.exit(1)
    if not config_path.exists():
        print(f"Error: --config path not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    valid, errors = validate_config(config_path)
    if not valid:
        print(f"Error: {config_path} is invalid:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    config = load_config(config_path)
    result = run_validation(before, after, config, env_name=env)

    if result.ok:
        print(
            f"Repoint validation passed: {result.unchanged_count} unchanged item(s),"
            f" {result.new_item_count} new item(s), no identity breaks, no unregistered"
            " connections, no override gaps."
        )
        return

    print("Repoint validation failed:", file=sys.stderr)
    for message in result.messages:
        print(f"  - {message}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
