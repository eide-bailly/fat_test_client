"""Self-contained fabric.yml config core (ported from the Fabric Agentic Toolkit).

Ports the Pydantic v2 schema, environment-inheritance resolution, and config
loading/validation used by the FAT `fat config` commands, without depending on
the `fat` package. Kept in sync by hand with:
- fat/config/schema.py
- fat/config/loader.py (validate_config / load_config / resolve_environment_inheritance)
"""

from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError
import yaml

# Reserved override keys with special gen-parameters handling.
# - "schedules_enabled": bool — flips the `enabled` field in .schedules files for the
#   environment via a key_value_replace entry.
# - "dbt_target": str — dbt target name variance, emitted as a find_replace entry scoped
#   to item_type "DataBuildToolJob".
# Any other override key is emitted as an unscoped find_replace (dev value -> env value).
OverrideValue = str | bool | int | float

_ENVIRONMENT_MAP_FIELDS = ("items", "item_types", "connections", "endpoints", "overrides")


class Environment(BaseModel):
    name: str
    # Optional parent environment. The loader resolves inherited values before
    # constructing this model so all consumers receive an effective environment.
    extends: str | None = None
    workspace_id: str | None = None  # items workspace (source-controlled Fabric items)
    workspace_name: str | None = None
    data_workspace_id: str | None = None  # data workspace (lakehouses/warehouses); optional split
    data_workspace_name: str | None = None
    items: dict[str, str] = Field(default_factory=dict)  # item_name -> item_id
    # item_types is free-form — valid Fabric item type strings include:
    # "Warehouse", "Lakehouse", "DataPipeline", "Notebook", "DataBuildToolJob"
    item_types: dict[str, str] = Field(
        default_factory=dict
    )  # item_name -> Fabric item type (e.g. "Warehouse", "Lakehouse")
    # connection keys are snake_case purpose names (e.g. pipeline_invoke, batchmaster_sql),
    # not display names. pipeline_invoke is the user-scoped Fabric connection required by
    # InvokeFabricPipeline activities.
    #
    # Shared-by-default semantics: a connection registered once in FabricConfig.connections
    # applies to every environment. This per-environment dict is for *overrides only* —
    # declare a connection name here only when this environment's GUID genuinely differs
    # from the shared registration. Presence of a key here (even an empty string) is
    # treated as an explicit override; absence means "use the shared value".
    connections: dict[str, str] = Field(default_factory=dict)  # connection_name -> connection_guid
    endpoints: dict[str, str] = Field(default_factory=dict)  # endpoint_name -> TDS FQDN
    capacity_id: str | None = None  # Fabric capacity GUID
    capacity_name: str | None = None  # Fabric capacity display name (for fab mkdir)
    # Non-ID environment variances, e.g.:
    #   overrides:
    #     schedules_enabled: false
    #     dbt_target: prod
    # Compiled by gen-parameters into scoped find_replace / key_value_replace entries.
    overrides: dict[str, OverrideValue] = Field(default_factory=dict)


# ComponentRef.name is intentionally free-form (no enum) — catalog component names
# such as "epm-framework", "dbt-framework", etc. are validated by convention only.
class ComponentRef(BaseModel):
    name: str  # catalog component name e.g. "dbt-framework"
    enabled: bool = True


class GitConfig(BaseModel):
    """Repository Git settings used by the developer workflow."""

    default_branch: str = "main"


class FabricConfig(BaseModel):
    name: str  # project name
    client: str | None = None
    platform: str = "fabric"
    orchestrator: str = "fabric-pipelines"  # e.g. "fabric-pipelines", "airflow"
    git: GitConfig = Field(default_factory=GitConfig)
    environments: list[Environment] = []
    components: list[ComponentRef] = []  # components meant to be present
    # Shared-by-default connection registry: connection_name -> connection_guid. Applies to
    # every environment unless overridden in that environment's `connections` block. Every
    # connection GUID appearing under `fabric/` must be registered here (or per-environment).
    connections: dict[str, str] = Field(default_factory=dict)


def resolve_connections(config: FabricConfig, env_name: str) -> dict[str, str]:
    """Resolve the effective connection_name -> guid mapping for *env_name*.

    Shared-by-default: starts from the top-level `FabricConfig.connections` registry, then
    applies any per-environment overrides declared in that environment's `connections` block.
    A key present in the environment's `connections` dict (even with an empty-string value)
    always wins over the shared value.
    """
    merged = dict(config.connections)
    env = next((e for e in config.environments if e.name == env_name), None)
    if env is not None:
        merged.update(env.connections)
    return merged


class ConfigResolutionError(ValueError):
    """Raised when an environment inheritance graph cannot be resolved."""


def resolve_environment_inheritance(raw: Any) -> Any:
    """Resolve ``environments[].extends`` into effective environment mappings.

    Scalar fields inherit from the parent unless the child declares a value.
    Resource maps merge shallowly so a child can register only its deltas.
    """
    if not isinstance(raw, dict):
        return raw

    raw_environments = raw.get("environments")
    if not isinstance(raw_environments, list):
        return raw

    named_environments: dict[str, dict[str, Any]] = {}
    for index, environment in enumerate(raw_environments):
        if not isinstance(environment, dict):
            continue
        name = environment.get("name")
        if not isinstance(name, str):
            continue
        if name in named_environments:
            raise ConfigResolutionError(f"Duplicate environment name '{name}' at index {index}.")
        named_environments[name] = environment

    resolved: dict[str, dict[str, Any]] = {}
    resolving: list[str] = []

    def resolve(name: str) -> dict[str, Any]:
        if name in resolved:
            return resolved[name]
        if name in resolving:
            cycle = " -> ".join([*resolving, name])
            raise ConfigResolutionError(f"Environment inheritance cycle: {cycle}.")

        environment = named_environments[name]
        parent_name = environment.get("extends")
        resolving.append(name)
        if parent_name is None:
            merged: dict[str, Any] = {}
        elif not isinstance(parent_name, str):
            raise ConfigResolutionError(
                f"Environment '{name}' has a non-string extends value: {parent_name!r}."
            )
        elif parent_name not in named_environments:
            raise ConfigResolutionError(
                f"Environment '{name}' extends unknown environment '{parent_name}'."
            )
        else:
            merged = deepcopy(resolve(parent_name))

        for field in _ENVIRONMENT_MAP_FIELDS:
            if field not in environment:
                continue
            child_value = environment[field]
            parent_value = merged.get(field, {})
            if isinstance(parent_value, dict) and isinstance(child_value, dict):
                merged[field] = {**parent_value, **deepcopy(child_value)}
            else:
                merged[field] = deepcopy(child_value)

        for field, value in environment.items():
            if field not in _ENVIRONMENT_MAP_FIELDS:
                merged[field] = deepcopy(value)

        resolving.pop()
        resolved[name] = merged
        return merged

    resolved_environments = [
        resolve(environment["name"])
        if isinstance(environment, dict) and environment.get("name") in named_environments
        else environment
        for environment in raw_environments
    ]
    return {**raw, "environments": resolved_environments}


def load_config(path: Path) -> FabricConfig:
    """Load and validate a fabric.yml file, returning a FabricConfig."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return FabricConfig.model_validate(resolve_environment_inheritance(raw))


def validate_config(path: Path) -> tuple[bool, list[str]]:
    """Validate a fabric.yml file. Returns (valid, errors)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return False, [f"YAML parse error: {exc}"]

    try:
        FabricConfig.model_validate(resolve_environment_inheritance(raw))
        return True, []
    except (ConfigResolutionError, ValidationError) as exc:
        if isinstance(exc, ConfigResolutionError):
            return False, [str(exc)]
        errors = [f"{e['loc']}: {e['msg']}" for e in exc.errors()]
        return False, errors
