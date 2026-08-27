# CI/CD Deployment Guide — fat_test_client

## Overview

This project uses GitHub Actions to implement a **three-workflow model** around
native Fabric Git integration and `fabric-cicd`. There is no custom
ID-substitution or tokenization step in this repo's day-to-day CI — the repo's
canonical form is real dev-workspace GUIDs, produced once at bootstrap time (see
"Token Lifecycle" below) and never touched by hand afterward.

| Workflow | File | Trigger | Purpose |
|---|---|---|---|
| PR validation | `.github/workflows/pr-validation.yml` | PR targeting `dev` or any non-dev environment branch | Validation-only gate: ruff, sqlfluff, dbt parse, `fabric.yml` schema validation, repoint coverage, parameter consistency, test suite |
| Post-merge | `.github/workflows/post-merge.yml` | Push to `dev` | Schema/parameter safety-net checks, test suite, then sync the dev workspace to the triggering commit via `git-sync`. Branch and branch-out-workspace cleanup are manual developer steps — this workflow does not perform them. |
| Release | `.github/workflows/release.yml` (one shared file covering every non-dev environment) | Merge into any non-dev environment branch (e.g. `prod`), or manual `workflow_dispatch` naming the environment | Resolve the target environment at runtime, then `gen-parameters` + `fabric-cicd publish_all_items` to that environment only. The gate is the branch protection rule on the environment's branch, not a GitHub Environment approval — there is no `environment:` key in any rendered workflow. A merge into one environment's branch never redeploys another. |

## Branch and Workspace Topology

| Branch | Fabric workspace | Purpose |
|---|---|---|
| `dev` (default branch) | `<project-name>-dev` | Git-connected source of truth; tracks the folder taxonomy below |
| `feature/<dev>-<task>` | `<project-name>-<dev>-<task>` (branch-out workspace) | Disposable, task-scoped. Created via native "Branch out to new workspace" (Select items individually, Preview), never by a script. Deletion of the merged branch and its branch-out workspace is a manual developer step after merge — no workflow in this component performs it. |

Each non-dev environment declares its own release branch in `fabric.yml`
(`environments.<env>.branch`) — `stage`, `test`, and `prod` in this exemplar.
`fabric-cicd` `publish_all_items` deploys the canonical `dev` content directly
to each downstream environment when a merge lands on that environment's
branch; the release workflow's run history in GitHub Actions is the audit
trail of what was deployed when. The branch protection rule on each
environment's branch (required status check: the PR validation workflow) is
the promotion gate — review happens on the PR into that branch, not on a
GitHub Environment approval. GitHub Environments are deliberately not used
anywhere in this model.

### Folder taxonomy (the branch-out unit)

Every branch-out is scoped to one of these top-level folders under `fabric/`:

- `elt/sources/<source>/` — one extract pipeline + ingest notebook per source system. This is the folder a developer typically branches out for a single-source task.
- `elt/core/` — the dbt job (`DataBuildToolJob`) and orchestrator pipelines.
- `semantic/` — semantic models and reports.

Item directory names are the stable identity the repoint gate matches on (directory
name / `.platform` `logicalId`); they change only in exceptional circumstances. If
you need to rename an item directory, expect the PR validation workflow to flag it
as an identity-churn failure and follow its remediation guidance.

## PR Validation Workflow (`pr-validation.yml`)

Runs on every PR targeting `dev` or a non-dev environment branch. Never writes to
the repo, never calls a Fabric workspace for validation, never touches the
developer's branch or branch-out workspace. Checks, in order:

1. **Python lint** — `ruff check .`.
2. **SQL lint** — `sqlfluff lint` (skipped when no `.sqlfluff` config is present).
3. **dbt parse** — `dbt deps` + `dbt parse` (skipped when the scaffolded dbt
   project is absent). `dbt parse` evaluates `profiles.yml`, so the step maps the
   dev environment's SPN credentials and warehouse connection values from the
   `DEV_*` repository secrets; parse does not connect to the warehouse.
4. **Fabric schema validation** — `scripts/validate_fabric.py` confirms
   `fabric.yml` parses and conforms to the project schema.
5. **Repoint coverage** — `scripts/validate_repoint.py` confirms every
   cross-workspace item reference in the diff still resolves to a stable identity
   present in `dev`. Fails plain-language, naming the item, only on the
   exceptional case of a renamed/deleted item identity.
6. **Parameter consistency** — `scripts/gen_parameters.py` regenerates
   `parameter.yml` from `fabric.yml` and diffs it against the checked-in
   `fabric/parameter.yml`.
7. **Test suite** — `uv run pytest` (no-op if no tests ship with the scaffolded
   scripts).

## Post-Merge Workflow (`post-merge.yml`)

Runs on every push to `dev` (i.e., immediately after a PR merges). Steps:

1. **Validate `fabric.yml` schema** (safety net after merge).
2. **Confirm `fabric/parameter.yml` consistency** against `fabric.yml`.
3. **Run the test suite** (no-op if no tests ship with the scaffolded scripts).
4. **Synchronize the dev workspace to the triggering commit** via `scripts/deploy_fabric.py
   --git-sync`, under the dev environment's service-principal identity (mapped from
   the `DEV_*` repository secrets), so the dev workspace's Git connection picks up
   the merged commit.

Branch and branch-out-workspace cleanup are **manual developer steps** after a PR
merges. Delete the merged `feature/*` branch and its branch-out workspace yourself
once you've confirmed the sync above succeeded; this workflow does not automate
either step, and no cleanup-script stub is scaffolded for it.

## Release Workflow (`release.yml`)

One shared workflow covering every non-dev environment declared in `fabric.yml`
(typically just `prod`; `stage`/`test` are ADAPT-and-delete if your engagement has
no such environments). Triggered by a merge into any non-dev environment's branch
(`fabric.yml.environments.<env>.branch`) or by `workflow_dispatch` with an
`environment` choice input. Each run:

1. **Resolves the target environment** from the triggering ref (or the dispatch
   input) against `fabric.yml.environments[].branch`, read fresh from the
   checked-out repo on every run — never baked in at render time.
2. `scripts/gen_parameters.py --path fabric.yml --output fabric/parameter.yml`
   compiles `fabric.yml` into a single `parameter.yml` covering every declared
   environment.
3. **Runs the test suite** (no-op if no tests ship with the scaffolded scripts).
4. `scripts/deploy_fabric.py` deploys the canonical `fabric/` tree to the resolved
   environment's workspace only, under that environment's OWN service principal.
   The deploy step's `env:` block surfaces every environment's name-prefixed
   secrets and the shell selects the resolved environment's set by prefix (GitHub
   Actions cannot index the `secrets` context by a computed name). The job is a
   plain job — there is no `environment:` key and GitHub creates nothing. The gate
   is the branch protection rule configured on the environment's branch; there is
   no cross-environment chaining, so a merge into one environment's branch never
   redeploys another.

## Token Lifecycle (bootstrap vs. post-bootstrap)

Tokens exist **only** in catalog exemplars (this toolkit's `catalog/`), as
bootstrap-time placeholders (e.g. `__DATA_WORKSPACE_ID__`-style tokens in a
scaffold template). They are resolved exactly **once**, at scaffold or migration
time, via a tokenized `fabric-cicd` publish into the dev workspace — after which
the dev workspace is Git-connected and a commit-all makes the real-GUID
serialization canonical. Tokens **never re-enter this repository** after that
point.

The first-pass, dev-targeted resolution of `__ITEM_*__`/`__CONNECTION_*__`/
`__ENDPOINT_*__`/`__WORKSPACE_ID__`/`__DATA_WORKSPACE_ID__` placeholders is
automated by `fat_deploy_bootstrap_plan`/`fat_deploy_bootstrap_apply`, which run
**before** `fat_deploy_gen_parameters` in the deploy sequence:

1. `fat_deploy_bootstrap_plan` scans the rendered item JSON for bootstrap tokens
   and resolves each one against `fabric.yml`'s already-known item/connection/
   endpoint values for the target environment (normally `dev`).
2. `fat_deploy_bootstrap_apply` writes the resolved values into the rendered
   files. Some tokens cannot resolve on the first pass — an item referencing
   another not-yet-created item's GUID, or the genuinely circular case of an
   item referencing its own not-yet-created GUID — and are left as-is,
   reported back for a documented **two-pass publish**: publish what resolved,
   then re-run `fat_deploy_bootstrap_plan`/`apply` for the second pass before
   publishing again. The newly created items' GUIDs are resolved live during
   deployment via `parameter.yml`'s `find_replace` mechanism (see
   `plans/live-identity-resolution.md`).
3. Only once bootstrap resolution is complete (no tokens left) does
   `fat_deploy_gen_parameters` run, compiling `fabric.yml`'s now fully-resolved
   dev values into `parameter.yml` `find_replace`/`key_value_replace` entries
   for every other environment — it does not itself scan for `__*__` token
   strings.

Everything described in this document — the PR gate, the post-merge automation,
and the release workflow — operates on that **post-bootstrap, real-GUID canonical
form**. If you ever see a `__TOKEN__`-style placeholder in a diff against `dev`,
that is a bug: bootstrap-only content has leaked into the canonical branch, and the
PR validation workflow should be extended to catch it explicitly if your
engagement hits this case.

## parameter.yml Maintenance

`parameter.yml` is generated (not hand-edited) from `fabric.yml` and is
regenerated by the release workflow on every run — it is not committed to the
repo. Regenerate locally for troubleshooting with:

```bash
uv run python scripts/gen_parameters.py --path fabric.yml --output parameter.yml
```

## Data Plane (lakehouses and warehouses)

Lakehouses and warehouses are **never source-controlled** and are not deployed by
any of the three workflows above — they are pre-provisioned infrastructure whose
structure is defined by code (pipelines, dbt). See `docs/onboarding.md` (or
equivalent project doc) for the personal-warehouse-per-developer pattern.

## What's Confirmed vs. Assumed

SP auth for Git integration operations (the dev-workspace `git-sync` step in the
post-merge workflow) is expected to work for the item types in scope for this
project (DataPipeline, Notebook, DataBuildToolJob, SemanticModel, Report) but is
unverified against a live tenant for a GitHub-backed connection — verify
per-tenant before treating it as unattended. Deploy fidelity (same-workspace
auto-repoint scope, schedule serialization, and residue left behind in
`known_lakehouses`-style metadata after repointing) remains the one open area to
watch for; treat any deploy that touches those cases as needing manual
verification until you've confirmed it on your own tenant.

Branch and branch-out-workspace cleanup are **manual developer responsibilities** —
no automation for either exists in this component, and none is planned. Delete the
merged `feature/*` branch and its branch-out workspace yourself after confirming
the post-merge sync succeeded.

## GitHub Setup Checklist

See `docs/cicd-secrets.md` for the full repository-secret setup. Before the first
run of any workflow:

- [ ] `DEV_*` and `PROD_*` repository secrets populated (plus `STAGE_*`/`TEST_*` if your engagement has those environments) — `fat_cicd_apply` sets empty placeholders; real values are populated by a human via `gh secret set` or the GitHub UI
- [ ] Each environment's SP granted Member/Admin on its Fabric workspace
- [ ] Each non-dev environment has its own branch in `fabric.yml` (`environments.<env>.branch`) with a branch protection rule (required status check: the PR validation workflow) — applied automatically by `fat_cicd_apply`, unlike the ADO path's manual H7/H8 steps. This is the promotion gate, not a GitHub Environment approval
- [ ] Branch protection on `dev` requires the PR validation workflow to pass and restricts direct pushes (merges arrive via PRs from `feature/*`)
- [ ] The dev workspace's Fabric Git connection is backed by the PAT-holding Fabric connection created by `fat_fabric_git_create_pat_connection`
- [ ] SP auth for `git/updateFromGit` against a GitHub-backed connection is confirmed on this tenant
