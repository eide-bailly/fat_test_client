"""Standalone CLI mirroring `fat deploy gen-parameters`.

Generates a fabric-cicd `parameter.yml` from `fabric.yml` (all environments).

Self-contained: only depends on `_fabric_config` (this directory), `pydantic`, and `pyyaml`.
"""

import argparse
from pathlib import Path
import sys

from _fabric_config import Environment, FabricConfig, resolve_connections, validate_config
from _fabric_config import load_config as _load_config
import yaml

_DEV_ENV_NAME = "dev"


def _source_env(config: FabricConfig) -> Environment | None:
    """Return the canonical dev environment, or None if not present."""
    return next((e for e in config.environments if e.name == _DEV_ENV_NAME), None)


def _target_envs(config: FabricConfig) -> list[Environment]:
    """Return every environment except the canonical dev source environment."""
    return [e for e in config.environments if e.name != _DEV_ENV_NAME]


def _build_workspace_id_entries(config: FabricConfig) -> list[dict]:
    """Build find_replace entries swapping the dev workspace_id / data_workspace_id."""
    dev_env = _source_env(config)
    if dev_env is None:
        return []

    entries: list[dict] = []
    for attr in ("workspace_id", "data_workspace_id"):
        dev_value = getattr(dev_env, attr)
        if not dev_value:
            continue
        replace_value: dict[str, str] = {}
        for env in _target_envs(config):
            target_value = getattr(env, attr)
            if target_value and target_value != dev_value:
                replace_value[env.name] = target_value
        if replace_value:
            entries.append({"find_value": dev_value, "replace_value": replace_value})

    return entries


def _build_item_id_entries(config: FabricConfig) -> list[dict]:
    """Build find_replace entries swapping the dev item GUID for each registered item.

    Covers cross-workspace Lakehouse/Warehouse references and the same-workspace references
    `fabric-cicd`'s `publish_all_items` does not auto-repoint (DataPipeline -> Notebook activity
    references, Notebook -> Lakehouse default-lakehouse metadata). No item_type scoping is
    applied: per skills/deploy-fabric/SKILL.md, the referencing item's type (e.g.
    "DataPipeline") differs from the referenced item's type (e.g. "Notebook"), so a single
    item_type filter cannot cover both sides of the reference. An item registered in dev but
    absent from a target environment's `items` dict is simply skipped for that environment.
    """
    dev_env = _source_env(config)
    if dev_env is None:
        return []

    entries: list[dict] = []
    for item_name, dev_guid in dev_env.items.items():
        if not dev_guid:
            continue
        replace_value: dict[str, str] = {}
        for env in _target_envs(config):
            target_guid = env.items.get(item_name)
            if target_guid and target_guid != dev_guid:
                replace_value[env.name] = target_guid
        if replace_value:
            entries.append({"find_value": dev_guid, "replace_value": replace_value})

    return entries


def _build_connection_entries(config: FabricConfig) -> list[dict]:
    """Build find_replace entries for connections whose resolved GUID differs by env.

    Shared-by-default: a connection registered once in FabricConfig.connections passes
    through unchanged for every environment — no entry is emitted unless a per-environment
    override in `environments[].connections` gives it a genuinely different value from the
    dev environment's resolved value.
    """
    dev_env = _source_env(config)
    if dev_env is None:
        return []

    dev_connections = resolve_connections(config, _DEV_ENV_NAME)

    # Union of every connection name registered anywhere (shared registry + any per-env
    # override), so a connection declared only as a per-env override is still considered.
    all_names: set[str] = set(config.connections.keys())
    for env in config.environments:
        all_names.update(env.connections.keys())

    entries: list[dict] = []
    for conn_name in sorted(all_names):
        dev_guid = dev_connections.get(conn_name)
        if not dev_guid:
            continue
        replace_value: dict[str, str] = {}
        for env in _target_envs(config):
            target_guid = resolve_connections(config, env.name).get(conn_name)
            if target_guid and target_guid != dev_guid:
                replace_value[env.name] = target_guid
        if replace_value:
            entries.append({"find_value": dev_guid, "replace_value": replace_value})

    return entries


def _build_schedule_key_value_entries(config: FabricConfig) -> list[dict]:
    """Build a key_value_replace entry flipping `.schedules` `enabled` per environment.

    Reserved override key `schedules_enabled` (bool) drives this. `.schedules` files
    serialize a JSON array of schedule objects with an `enabled` field; `key_value_replace`
    with a JSONPath scoped to `.schedules` flips it per environment without needing a dev
    baseline value.
    """
    replace_value: dict[str, bool] = {}
    for env in _target_envs(config):
        if "schedules_enabled" in env.overrides:
            value = env.overrides["schedules_enabled"]
            replace_value[env.name] = bool(value)

    if not replace_value:
        return []

    return [
        {
            "find_key": "$[*].enabled",
            "replace_value": replace_value,
            "file_path": ".schedules",
        }
    ]


def _build_generic_override_entries(config: FabricConfig) -> list[dict]:
    """Build find_replace entries for non-ID overrides other than `schedules_enabled`.

    `dbt_target` is scoped to `item_type: DataBuildToolJob` (the dbt target name literal is
    contained within that item's Code/dbt files). Any other override key is emitted as an
    unscoped find_replace, swapping the dev environment's value for each target env's value.
    """
    dev_env = _source_env(config)
    if dev_env is None:
        return []

    reserved = {"schedules_enabled"}
    override_names = {k for k in dev_env.overrides if k not in reserved}

    entries: list[dict] = []
    for override_name in sorted(override_names):
        dev_value = dev_env.overrides.get(override_name)
        if dev_value is None or dev_value == "":
            continue
        replace_value: dict[str, str] = {}
        for env in _target_envs(config):
            if override_name not in env.overrides:
                continue
            target_value = env.overrides[override_name]
            if target_value != dev_value:
                replace_value[env.name] = str(target_value)
        if replace_value:
            entry: dict = {"find_value": str(dev_value), "replace_value": replace_value}
            if override_name == "dbt_target":
                entry["item_type"] = "DataBuildToolJob"
            entries.append(entry)

    return entries


def generate_parameters(config: FabricConfig) -> dict:
    """Generate a fabric-cicd parameter.yml dict from all environments in a FabricConfig.

    All entries are keyed on the canonical dev environment's literal value (no tokens).
    Environments are read from `config.environments`; the environment named "dev" is the
    substitution source, every other environment is a substitution target. If no "dev"
    environment is present, GUID/connection/override entries are omitted (there is no
    canonical value to key `find_value` on).
    """
    find_replace: list[dict] = []
    find_replace.extend(_build_workspace_id_entries(config))
    find_replace.extend(_build_item_id_entries(config))
    find_replace.extend(_build_connection_entries(config))
    find_replace.extend(_build_generic_override_entries(config))

    result: dict = {"find_replace": find_replace}

    key_value_replace = _build_schedule_key_value_entries(config)
    if key_value_replace:
        result["key_value_replace"] = key_value_replace

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate fabric-cicd parameter.yml from fabric.yml (all environments)."
    )
    parser.add_argument(
        "--path", "-p", type=Path, default=Path("fabric.yml"), help="Path to fabric.yml"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("fabric/parameter.yml"),
        help="Output file path (default: fabric/parameter.yml)",
    )
    args = parser.parse_args()

    config_path: Path = args.path
    output: Path = args.output

    if not config_path.exists():
        print(f"Error: {config_path} not found.", file=sys.stderr)
        sys.exit(1)

    valid, errors = validate_config(config_path)
    if not valid:
        print(f"Error: {config_path} is invalid:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    config = _load_config(config_path)

    params = generate_parameters(config)

    rendered = yaml.dump(params, default_flow_style=False, sort_keys=False, allow_unicode=True)

    output.write_text(rendered, encoding="utf-8")
    print(f"Wrote parameter.yml to {output}")
    sys.exit(0)


if __name__ == "__main__":
    main()
