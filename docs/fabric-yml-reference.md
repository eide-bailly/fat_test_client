# `fabric.yml` Schema Reference

This document is the authoritative field-by-field reference for `fabric.yml`, sourced directly from the pydantic models in `tool/fat/config/schema.py` and the loading/inheritance logic in `tool/fat/config/loader.py`. If this document and the code ever disagree, the code wins — file an issue or fix the doc.

`fabric.yml` is the project's **manifest of intent**: project identity, environment topology, workspace and platform resource IDs, and CI/CD and Fabric Git integration targets. It holds workspace-level and platform IDs, never secrets. Item-level identity (lakehouse GUIDs, warehouse GUIDs, item types, and endpoint FQDNs) are **resolved live from the Fabric API** at the point of use, not stored in this file. The matching secret for `identity.deployment_sp_client_id` lives in `.env` beside `fabric.yml`, never in this file.

## Loading and validation

- Loader: `tool/fat/config/loader.py::load_config` — `yaml.safe_load` → `resolve_environment_inheritance` (resolves `extends` on `environments`) → `FabricConfig.model_validate`.
- `fabric.yml` (or `.fat/config.yml`) is optional — FAT infers project shape from repo context when absent. Config is an accelerant, not a hard dependency.
- All pydantic fields on `FabricConfig` and its nested sections are `Optional`, so a syntactically valid YAML file with no sections at all still validates. Individual tools (e.g. `fat_cicd_plan`) run their own stricter runtime checks on top of the pydantic validation — see [`cicd:`](#cicd) below.

## Top-level structure

`FabricConfig` (`tool/fat/config/schema.py:112-132`):

| Key | Type | Required | Default |
|---|---|---|---|
| `config_version` | `int` | optional | `1` |
| `name` | `str` | **required** | — |
| `client` | `str \| None` | optional | `None` |
| `platform` | `str` | optional | `"fabric"` |
| `orchestrator` | `str` | optional | `"fabric-pipelines"` (e.g. `"airflow"`) |
| `git` | `GitConfig` | optional | `{}` (see below) |
| `identity` | `IdentityConfig \| None` | optional | `None` |
| `environments` | `list[Environment]` | optional | `[]` |
| `components` | `list[ComponentRef]` | optional | `[]` |
| `connections` | `dict[str, str]` | optional | `{}` |
| `provisioned_items` | `list[ProvisionedItem]` | optional | `[]` |
| `sources` | `list[SourceDeclaration]` | optional | `[]` |
| `git_integration` | `GitIntegrationConfig \| None` | optional | `None` |
| `cicd` | `CicdConfig \| None` | optional | `None` |

Each section is detailed below.

## `git:`

`GitConfig` (`schema.py:55-58`) — repo Git settings for the developer workflow. Distinct from `git_integration:` (Fabric's Git connection) and `cicd:` (the selected CI/CD provider target, currently Azure DevOps or GitHub).

| Field | Type | Default |
|---|---|---|
| `default_branch` | `str` | `"main"` |

```yaml
git:
  default_branch: main
```

## `identity:`

`IdentityConfig` (`schema.py:61-69`) — durable, non-secret identity intent for the project. Only IDs live here; the matching secret (the deployment SP client secret) lives in `.env` beside `fabric.yml`, never in this file.

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_id` | `str \| None` | optional | Azure AD tenant GUID. |
| `deployment_sp_client_id` | `str \| None` | optional | Client (application) ID of the deployment service principal. The corresponding secret goes in `.env`, never here. |

These are the only two fields on `IdentityConfig` — there is no `deployment_sp_client_secret` or similar field on this model by design.

```yaml
identity:
  tenant_id: 00000000-0000-0000-0000-000000000000
  deployment_sp_client_id: 11111111-1111-1111-1111-111111111111
```

Corresponding `.env` entry (never in `fabric.yml`):

```
DEPLOYMENT_SP_CLIENT_SECRET=<secret value, from Key Vault or a local .env>
```

## `cicd:`

`CicdConfig` (`schema.py`) — the CI/CD target for this project. The provider is explicit and defaults to `ado` for backward compatibility; the target may also be GitHub-backed when `provider: github` is selected.

| Field | Type | Default | Notes |
|---|---|---|---|
| `provider` | `Literal["ado", "github"]` | `"ado"` | Selects the CI/CD shape. |
| `organization` | `str \| None` | `None` | Required when `provider: ado`; forbidden when `provider: github`. |
| `project` | `str \| None` | `None` | Required when `provider: ado`; forbidden when `provider: github`. |
| `owner` | `str \| None` | `None` | Required when `provider: github`; forbidden when `provider: ado`. |
| `repository` | `str \| None` | `None` | Required for either provider. |
| `dev_branch` | `str` | `"dev"` | **Deprecated and ignored.** Use `Environment.branch` instead. |
| `prod_branch` | `str` | `"prod"` | **Deprecated and ignored.** Use `Environment.branch` instead. |

```yaml
cicd:
  provider: ado
  organization: https://dev.azure.com/acme
  project: analytics-platform
  repository: analytics-platform
```

```yaml
cicd:
  provider: github
  owner: acme
  repository: analytics-platform
```

Provider validation is enforced at the schema layer:

- `provider: ado` requires `organization`, `project`, and `repository`.
- `provider: github` requires `owner` and `repository` and forbids `project` and `organization`.
- When both `cicd:` and `git_integration:` are present, `cicd.provider` and `git_integration.provider` must match or validation fails with a plain-language message.

The runtime gate is `_check_explicit_target` in `tool/fat/cicd/plan.py` (invoked by `fat_cicd_plan`), which requires a fully populated target for the selected provider. If any required target fields are missing, `fat_cicd_plan` fails the `cicd-explicit-target` check and returns an empty `plan_id`, with a message pointing back to this file.

### `dev_branch` and `prod_branch` deprecation

The `cicd.dev_branch` and `cicd.prod_branch` fields are deprecated and ignored — nothing reads them anymore. **`Environment.branch` is now authoritative** for the Git branch that deploys each environment. Projects scaffolded with prior versions that declare `dev_branch` and `prod_branch` in the `cicd:` block may continue to work (those fields no longer cause any issues), but they are vestigial.

**For projects without `Environment.branch` declared:** If a project's environments lack the `branch` field, `fat_cicd_plan` will fail with a validation error message naming the field and directing you to add it. Old projects are not automatically migrated — you must explicitly add `branch: dev` (or the appropriate branch name) to each environment in `fabric.yml`.

### What `fat_cicd_plan` / `fat_cicd_apply` cover

- `fat_cicd_plan` (`tool/fat/cicd/plan.py::build_cicd_plan`) plans adopt/create/conflict actions for the selected provider's repository and CI model: Azure DevOps repository + three pipelines (`ci-pr`, `ci-post-merge`, and the single shared `ci-release` covering every declared non-dev environment), or GitHub repository + the three GitHub Actions workflows (`pr-validation.yml`, `post-merge.yml`, and `release.yml`). **There is no provider-specific `environment:` object anywhere in this model** (`tool/fat/cicd/plan.py:30`) — no `prod` provider environment is planned or created.
- `fat_cicd_apply` (`tool/fat/cicd/apply.py::apply_cicd_plan`) creates/adopts that repository and those provider-specific runtime objects, plus the empty secret placeholders required by the selected platform. For Azure DevOps this is the empty `isSecret` pipeline variable placeholder (`DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET`); for GitHub this is the name-prefixed repository secrets (`DEV_*`, `STAGE_*`, `TEST_*`, `PROD_*`) and the branch-protection gate. It never creates a provider-specific environment object.
- Neither tool creates branch policies or GitHub Environments, and neither performs any Git action (branch creation, pushes, etc.) — FAT never performs Git actions on the caller's behalf.

### Provider-specific pending-action model

`fat_cicd_apply` appends human-readable follow-up items to the plan's `pending_actions: list[str]` for traceability in the apply outcome and any surfaced runbook output. These are provider-specific:

- **Azure DevOps** — `H7` and `H8` remain the manual follow-up steps for branch-policy creation on `dev_branch` and `prod_branch`, because FAT does not create branch policies on behalf of the caller. `H7` requires the `ci-pr` pipeline to pass before merge; `H8` requires creating the `prod_branch` first and then matching the same policy on that branch.
- **GitHub** — `fat_cicd_apply` automates branch-protection creation via `gh api`; the remaining pending actions are the repository-secrets population step and any explicit repo admin follow-up required by the client. GitHub Environments are not created by FAT; the provider-level isolation is the name-prefixed repo-secret convention (`DEV_*`, `STAGE_*`, `TEST_*`, `PROD_*`).

A third, unlabeled pending item may also appear in the same list for any provider: a reminder to populate the empty placeholder secret values manually. `fat_cicd_apply` only creates the empty placeholders and never reads or transmits the deployment SPN client secret. A "no approver configured" warning may also be folded into `pending_actions` when applicable.

Consumers of `fat_cicd_apply`'s output should surface every entry in `pending_actions` to the human operator as manual follow-up steps; none of them are automated by design.

## `git_integration:`

`GitIntegrationConfig` (`schema.py`) — the Fabric Git connection target for this project (distinct from `cicd:`, which targets the CI/CD platform, and from `git:`, which is the developer's local repo default branch).

| Field | Type | Default | Notes |
|---|---|---|---|
| `provider` | `Literal["ado", "github"]` | `"ado"` | Selects the Git connection shape. |
| `organization` | `str \| None` | `None` | Required when `provider: ado`; forbidden when `provider: github`. |
| `project` | `str \| None` | `None` | Required when `provider: ado`; forbidden when `provider: github`. |
| `owner` | `str \| None` | `None` | Required when `provider: github`; forbidden when `provider: ado`. |
| `repository` | `str \| None` | `None` | Required for either provider. |
| `branch` | `str \| None` | `None` | |
| `directory` | `str` | `"fabric/"` | |

```yaml
git_integration:
  provider: ado
  organization: https://dev.azure.com/acme
  project: analytics-platform
  repository: analytics-platform
  branch: dev
  directory: fabric/
```

```yaml
git_integration:
  provider: github
  owner: acme
  repository: analytics-platform
  branch: main
  directory: fabric/
```

Provider validation is enforced at the schema layer:

- `provider: ado` requires `organization`, `project`, and `repository`.
- `provider: github` requires `owner` and `repository` and forbids `project` and `organization`.
- When both `cicd:` and `git_integration:` are present, the two `provider` values must match.

Values here are defaults only — any MCP tool call may pass explicit target arguments that take precedence over this section. Resolution happens in `tool/fat/git_integration/plan.py::_resolve_target`.

### Tool split

Fabric Git integration is split across four MCP tools (`tool/fat/mcp/server.py`), backed by `tool/fat/git_integration/plan.py` and `tool/fat/git_integration/apply.py`:

- **`fat_fabric_git_plan`** — read-only preview. Resolves the org/project/repo/branch/directory target from `git_integration:` or explicit arguments, confirms the target environment's `workspace_id`, and fetches the live Fabric Git connection/status state. No writes.
- **`fat_fabric_git_apply`** — connects the workspace to Git **and** calls `initializeConnection` (with `PreferRemote`) in one step.
- **`fat_fabric_git_connect`** — connect-only counterpart to `fat_fabric_git_apply`. Stops after `workspaces/{id}/git/connect` and never calls `initializeConnection`. Use this when the workspace's items must be created/populated before Fabric's Git initialization runs (e.g. the runbook's deferred-connect phase, where `initializeConnection` with `PreferRemote` is unsafe post-publish).
- **`fat_fabric_git_initialize`** — standalone `initializeConnection` wrapper for use after `fat_fabric_git_connect`. Refuses `PreferRemote` unless every item declared under the configured Git-synced directory (`directory`, default `fabric/`) as a `<name>.<ItemType>` folder genuinely exists live in the target workspace — see `_has_resolved_live_items`/`_declared_item_names` in `tool/fat/git_integration/apply.py`. Item folders anywhere else in the repository are ignored by this guardrail. `PreferWorkspace` is exempt from this guardrail.
- **`fat_fabric_git_status`** — read-only status check, independent of any plan.

Use `fat_fabric_git_connect` + `fat_fabric_git_initialize` (rather than the combined `fat_fabric_git_apply`) whenever workspace items are not yet fully provisioned at connect time.

## `environments:`

`Environment` (`schema.py:12-45`) — one entry per deployment target (e.g. `dev`, a per-developer `dev-<name>`, `prod`).

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | `str` | **required** | Environment identifier. |
| `extends` | `str \| None` | `None` | Name of another environment in this file to inherit from (see below). |
| `workspace_id` | `str \| None` | `None` | Fabric workspace GUID. |
| `workspace_name` | `str \| None` | `None` | |
| `data_workspace_id` | `str \| None` | `None` | GUID of a separate data workspace, if the project splits compute/data workspaces. |
| `data_workspace_name` | `str \| None` | `None` | |
| `branch` | `str \| None` | `None` | Git branch whose merges deploy this environment (e.g. `dev`, `prod`). Authoritative source for environment/branch mapping. Replaces deprecated `cicd.dev_branch` / `prod_branch`. |
| `connections` | `dict[str, str]` | `{}` | `connection_name -> guid`; overrides the top-level `connections:` registry for this environment only. |
| `capacity_id` | `str \| None` | `None` | Fabric capacity GUID. |
| `capacity_name` | `str \| None` | `None` | |
| `overrides` | `dict[str, str \| bool \| int \| float]` | `{}` | Free-form per-environment scalar overrides (e.g. `dbt_target`, `schedules_enabled`). |

The fields above are the complete, typed `tool/fat/config/schema.py::Environment` model.

### `items` / `item_types` / `endpoints` — raw pass-through keys, not typed model fields

`fabric.yml` may also carry `items:`, `item_types:`, and `endpoints:` maps under an environment entry (same `item_name -> value` shape as the table above would suggest), but these are **not** fields on `tool/fat/config/schema.py::Environment` — the main package's typed loader silently ignores them on load (Pydantic v2's default `extra="ignore"` behavior), consistent with this project's live-identity-resolution design (item-level identity is resolved from the Fabric API at point of use, not modeled in the main package's config; see `plans/live-identity-resolution.md`).

As of `config_version: 5`, **no FAT tool writes a per-env `items:` or `item_types:` GUID map anymore** — declared provisioning intent lives in the top-level [`provisioned_items:`](#provisioned_items) list, and item GUIDs are resolved live by name via `fat.deploy.identity`. Only `endpoints:` is still written back (Warehouse TDS endpoints harvested by `fat_provision_items` and `fat dev provision`, via direct ruamel-YAML surgery on the raw file, bypassing the typed model entirely). Legacy `items:`/`item_types:` maps left over from older files are stripped in place by `fat_project_upgrade_apply` (config_version 4 migration) — there is deliberately no conversion path from them to `provisioned_items`, since `fabric.yml` is a FAT-native convention and pre-FAT onboarding goes through `fat_config_reconcile` inference.

The CI-runtime scripts may still read these keys: `catalog/components/fabric-scripts/scripts/_fabric_config.py` — the self-contained config schema `gen_parameters.py`/`deploy_fabric.py`/`validate_repoint.py` use (see [Execution contexts](../AGENTS.md#execution-contexts); it has no dependency on the main `fat` package and is hand-kept in sync with it) — declares `items`, `item_types`, and `endpoints` as real typed fields on its own `Environment` model. `gen-parameters` reads `environments.<env>.items` to compile a `find_replace` entry per registered item, covering cross-environment Lakehouse/Warehouse GUID references that the live sibling-resolution mechanisms in `deploy_fabric.py` can't reach (see `skills/deploy-fabric/SKILL.md`'s item-GUID `find_replace` row for the full contract).

In short: if you're working through the main `fat` MCP tools, treat `items`/`item_types`/`endpoints` as opaque to that layer. If you're authoring or debugging the CI-runtime scripts (or `fabric.yml` fields they consume), they are real, typed, and documented in `skills/deploy-fabric/SKILL.md` and `skills/provision-fabric/SKILL.md`.

### `extends` inheritance

Resolved by `tool/fat/config/loader.py::resolve_environment_inheritance` before pydantic validation:

- Map fields — `connections`, `overrides` — merge shallowly, child keys winning over parent keys.
- Scalar fields (`workspace_id`, `workspace_name`, `data_workspace_id`, `data_workspace_name`, `capacity_id`, `capacity_name`, `branch`) are overridden wholesale only if the child environment explicitly declares them.
- Cycles, duplicate environment names, and a non-string `extends` value all raise `ConfigResolutionError` at load time.

```yaml
environments:
  - name: dev
    workspace_id: 44444444-4444-4444-4444-444444444444
    workspace_name: acme-analytics-dev
    branch: dev
    capacity_id: 66666666-6666-6666-6666-666666666666
    overrides:
      dbt_target: dev
      schedules_enabled: false

  - name: dev-rschofield
    extends: dev
    overrides:
      dbt_target: personal
```

## `components:`

`ComponentRef` (`schema.py:50-52`) — declares which catalog components this project uses.

| Field | Type | Default |
|---|---|---|
| `name` | `str` | **required** |
| `enabled` | `bool` | `True` |

`name` is a free-form catalog identifier (see `_COMPONENT_MARKERS` in `tool/fat/config/loader.py`), typically one of: `dbt-framework`, `epm-framework`, `fabric-pipelines`, `cicd-azure-devops`, `dev-environment`.

```yaml
components:
  - name: dbt-framework
    enabled: true
  - name: cicd-azure-devops
    enabled: true
```

## `connections:`

Top-level `dict[str, str]` (`schema.py:129`) — a shared-by-default registry of `connection_name -> connection_guid`, resolved per environment by `resolve_connections(config, env_name)` (`schema.py:135-147`). An environment's own `connections:` map merges on top of this shared registry — child values win even when the override is an empty string.

```yaml
connections:
  pipeline_invoke: 22222222-2222-2222-2222-222222222222
  batchmaster_sql: 33333333-3333-3333-3333-333333333333
```

## `provisioned_items:`

`ProvisionedItem` (`schema.py`) — declares the Lakehouse/Warehouse items FAT pre-provisions directly (`fat_provision_plan`/`fat_provision_apply`/`fat_provision_items`). Introduced at `config_version: 5`, replacing the old convention of discovering `<item_name>.<ItemType>/` stub folders under `fabric/`: the set of items FAT pre-provisions is declared intent here, and everything else under `fabric/` (pipelines, notebooks, dbt jobs) is managed by fabric-cicd and never pre-provisioned.

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | `str` | **required** | Item name, e.g. `lh_acme_analytics`. |
| `type` | `str` | **required** | Restricted to `"Lakehouse"` or `"Warehouse"` — anything else fails validation. |

The declaration is **uniform across all environments** — it is top-level, not per-environment, by design. Personal (`extends:`-based) developer environments never get distinct items: they inherit the base environment's data workspace, where the declared items already exist and are adopted rather than duplicated. Item GUIDs are still never stored in this file; they are resolved live by name at the point of use (see `plans/live-identity-resolution.md`). Fresh scaffolds emit the `lh_`/`wh_` + dbt-safe-project-name convention (`lh_acme_analytics`, `wh_acme_analytics`); `fabric.yml` itself is the escape hatch for deviations — there is no scaffold flag for this.

```yaml
provisioned_items:
  - name: lh_acme_analytics
    type: Lakehouse
  - name: wh_acme_analytics
    type: Warehouse
```

## `sources:`

`SourceDeclaration` (`schema.py:72-85`) — declares upstream data sources and where each sits on the access ladder.

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | `str` | **required** | |
| `type` | `str` | **required** | e.g. `sql_server`. |
| `ladder_rung` | `str \| None` | `None` | e.g. `"shortcut"`, `"mirroring"`, `"copy_activity"`. |
| `target_item` | `str \| None` | `None` | Name of the Fabric item this source lands in. |
| `connection_ref` | `str \| None` | `None` | Key into `connections:`. |

```yaml
sources:
  - name: batchmaster
    type: sql_server
    ladder_rung: mirroring
    target_item: batchmaster_mirror
    connection_ref: batchmaster_sql
```

## Complete worked example

```yaml
config_version: 5
name: acme-analytics
client: Acme Corp
platform: fabric
orchestrator: fabric-pipelines

provisioned_items:
  - name: lh_acme_analytics
    type: Lakehouse
  - name: wh_acme_analytics
    type: Warehouse

git:
  default_branch: main

identity:
  tenant_id: 00000000-0000-0000-0000-000000000000
  deployment_sp_client_id: 11111111-1111-1111-1111-111111111111

components:
  - name: dbt-framework
    enabled: true
  - name: epm-framework
    enabled: true
  - name: fabric-pipelines
    enabled: true
  - name: cicd-azure-devops
    enabled: true
  - name: dev-environment
    enabled: true

connections:
  pipeline_invoke: 22222222-2222-2222-2222-222222222222
  batchmaster_sql: 33333333-3333-3333-3333-333333333333

sources:
  - name: batchmaster
    type: sql_server
    ladder_rung: mirroring
    target_item: batchmaster_mirror
    connection_ref: batchmaster_sql

git_integration:
  organization: https://dev.azure.com/acme
  project: analytics-platform
  repository: analytics-platform
  branch: dev
  directory: fabric/

cicd:
  organization: https://dev.azure.com/acme
  project: analytics-platform
  repository: analytics-platform

environments:
  - name: dev
    workspace_id: 44444444-4444-4444-4444-444444444444
    workspace_name: acme-analytics-dev
    branch: dev
    data_workspace_id: 55555555-5555-5555-5555-555555555555
    data_workspace_name: acme-analytics-data-dev
    capacity_id: 66666666-6666-6666-6666-666666666666
    capacity_name: acme-fabric-f64
    overrides:
      schedules_enabled: false
      dbt_target: dev

  - name: dev-rschofield
    extends: dev
    overrides:
      dbt_target: personal

  - name: prod
    workspace_id: 88888888-8888-8888-8888-888888888888
    workspace_name: acme-analytics-prod
    branch: prod
    capacity_id: 66666666-6666-6666-6666-666666666666
    overrides:
      schedules_enabled: true
      dbt_target: prod
```

Corresponding `.env` (never committed, never referenced from `fabric.yml`):

```
DEPLOYMENT_SP_CLIENT_SECRET=<secret value, from Key Vault or a local .env>
```
