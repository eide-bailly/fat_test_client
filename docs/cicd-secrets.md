# GitHub Repository Secrets Template for Fabric CI/CD (Three-Workflow Model)

This document describes the GitHub repository secrets required by the workflow
exemplars in this component: `pr-validation.yml`, `post-merge.yml`, and the
shared `release.yml` covering every non-dev environment (`stage`, `test`,
`prod` in this exemplar). It is the GitHub analog of the ADO component's
`variable-group-template.md`. See `cicd.md` in this same `docs/` directory for
the workflow model overview.

## Overview

**PR validation workflow** needs dev-environment credentials for one step only
(the `dbt parse` step, which evaluates `profiles.yml`); every other check is a
pure repo-diff validation that never authenticates to Azure or Fabric.

**Post-merge workflow** and the **release workflow** need the target
environment's service principal credentials plus that environment's workspace
ID.

Where the ADO model uses one shared `fabric-cicd-credentials` variable group
plus per-environment `fabric-<env>-vars` groups, the GitHub model uses
**name-prefixed repository secrets** with **one service principal per
environment**:

| Prefix | Environment | Used by |
|---|---|---|
| `DEV_` | dev | Post-merge workflow (git-sync); PR validation workflow (dbt parse step) |
| `STAGE_` | stage | Release workflow (stage runs). **ADAPT-and-delete if your engagement has no stage environment** — most engagements have only `dev` and `prod`. |
| `TEST_` | test | Release workflow (test runs). **ADAPT-and-delete if your engagement has no test environment.** |
| `PROD_` | prod | Release workflow (prod runs) |

For each prefix `<PREFIX>`, create these repository secrets:

| Secret Name | Value | Notes |
|---|---|---|
| `<PREFIX>_SERVICE_PRINCIPAL_TENANT_ID` | `<your-azure-ad-tenant-id>` | Azure AD tenant ID (directory ID). |
| `<PREFIX>_SERVICE_PRINCIPAL_CLIENT_ID` | `<your-sp-client-id>` | Client ID of THIS environment's service principal. |
| `<PREFIX>_DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET` | `<your-sp-client-secret>` | This environment's SP client secret. The `DBT_ENV_SECRET` infix is intentional and must be kept: the workflow maps it onto the unprefixed `DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET` env var, and when dbt runs it scrubs variables with that prefix from its own logs. |
| `<PREFIX>_FABRIC_WORKSPACE_ID` | `<your-workspace-id>` | GUID of this environment's Fabric workspace. For `DEV_`, the same workspace that is Git-connected to the `dev` branch and that native "Branch out to new workspace" branches out from. |

For the `DEV_` prefix only, additionally create (consumed by the PR validation
workflow's `dbt parse` step):

| Secret Name | Value | Notes |
|---|---|---|
| `DEV_DBT_FABRIC_SERVER` | `<dev-warehouse-connection-string>` | Warehouse SQL endpoint mapped onto `DBT_FABRIC_SERVER` for `dbt parse`. Optional if the project has no dbt job. |
| `DEV_DBT_FABRIC_WAREHOUSE` | `<dev-warehouse-name>` | Warehouse name mapped onto `DBT_FABRIC_WAREHOUSE`. Optional if the project has no dbt job. |

**Why per-environment SPNs:** GitHub Environments (and their
environment-scoped secrets and reviewer gates) are deliberately NOT used in
this model — strict parity with the ADO component, where the promotion gate is
a branch policy, not an approval object. Per-environment isolation is instead
achieved by per-environment SPNs behind the name prefixes: the dev SPN has
permissions only on the dev workspace, so even though every repository secret
is technically readable by any workflow run, a leaked or misused dev credential
cannot touch prod. This is naming-convention isolation, not enforcement — the
actual promotion control is branch protection on each environment's branch.

**Placeholder-first setup:** `fat_cicd_apply` creates empty placeholder
repository secrets with these exact names via `gh secret set` and returns
populating the real values as a `pending_actions` entry. A human then sets the
real values (`gh secret set PROD_DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET`
or the GitHub UI: Settings > Secrets and variables > Actions). FAT never reads
or transmits secret values.

## Setting Repository Secrets

### With the GitHub UI

1. In the repository, go to **Settings** > **Secrets and variables** >
   **Actions**.
2. Click **New repository secret** and add each secret from the tables above.

### With the `gh` CLI

```bash
gh secret set PROD_SERVICE_PRINCIPAL_TENANT_ID
gh secret set PROD_SERVICE_PRINCIPAL_CLIENT_ID
gh secret set PROD_DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET
gh secret set PROD_FABRIC_WORKSPACE_ID
```

Each command prompts for the value on stdin (or pipe it:
`gh secret set NAME --body "value"`). Repeat per prefix. Never commit secret
values to the repo and never paste them into workflow YAML — the workflows
reference secrets only as `${{ secrets.<NAME> }}`.

## Service Principal Setup (per environment)

1. **Create one app registration per environment in Azure AD** (e.g.
   `fabric-cicd-dev`, `fabric-cicd-prod`):
   - In the Azure Portal, go to Azure Active Directory > App registrations >
     New registration.
   - Choose "Accounts in this organizational directory only."

2. **Grant Fabric workspace permissions:**
   - In each environment's Fabric workspace, go to Settings > Workspace access.
   - Add THAT environment's service principal as "Member" (or "Admin" if it
     needs to create/modify items, or manage Git connections in the dev
     workspace).

3. **Create a client secret per app registration:**
   - Certificates & secrets > Client secrets > New client secret.
   - Set expiration (90 days, 1 year, or custom). **Note:** secrets expire; set
     a calendar reminder to rotate before expiration, per environment.
   - Copy the secret value immediately (you cannot retrieve it later) and store
     it as `<PREFIX>_DBT_ENV_SECRET_SERVICE_PRINCIPAL_CLIENT_SECRET`.

4. **Capture the remaining values** (client ID from the app registration
   overview; tenant ID from Azure Active Directory > Overview; workspace GUID
   from the workspace URL) and store them per the tables above.

5. **Fabric Git-integration connection (dev only):** the dev workspace's Git
   connection to GitHub is backed by a Fabric connection holding a GitHub PAT
   (classic, `repo` scope, from a dedicated GitHub machine user), created by
   `fat_fabric_git_create_pat_connection` — the PAT is sourced server-side from
   the environment and never transits tool arguments or logs. This is separate
   from the SPN credentials above, which are what the workflows themselves use
   for `git/updateFromGit` and `publish_all_items`.

## Auth Scope Notes

- **SPN client secret only.** OIDC workload identity federation is deliberately
  rejected for now (recorded design decision), so there is no `azure/login`
  step and no `id-token: write` permission anywhere in these workflows. If your
  organization later mandates OIDC, replace the `env:` secret mappings with an
  `azure/login` step — the stamped scripts need no changes.
- **github.com only.** GHES/GHEC are out of scope; repository secret APIs and
  Actions availability differ there.

## Validation Checklist

Before running any workflow:

- [ ] `DEV_*` secrets are populated (post-merge workflow + PR validation dbt parse step).
- [ ] `STAGE_*` / `TEST_*` secrets are populated if your engagement has those environments; otherwise the stage/test environments are absent from `fabric.yml` and from the rendered `release.yml`.
- [ ] `PROD_*` secrets are populated (release workflow).
- [ ] Each environment's service principal is added to that environment's Fabric workspace with "Member" or "Admin" role.
- [ ] The dev workspace's Fabric Git connection uses the PAT-holding Fabric connection created by `fat_fabric_git_create_pat_connection`.
- [ ] Branch protection rules on `dev` and every non-dev environment branch require the PR validation workflow as a status check — applied automatically by `fat_cicd_apply` (unlike the ADO path's manual H7/H8 branch-policy steps).
- [ ] Secret names match exactly the names referenced in the workflow `env:` blocks. A typo renders as an empty string at runtime and surfaces as an authentication failure, not a "secret not found" error.

## Troubleshooting

### Workflow step fails: "Missing required environment variables" from deploy_fabric.py

**Cause:** A `<PREFIX>_SERVICE_PRINCIPAL_*` secret is unset or misnamed, so the
mapped env var is empty.

**Solution:**
1. Confirm the exact secret names in Settings > Secrets and variables > Actions
   (names are not case-insensitive).
2. Confirm the release workflow's render-time `case` mapping still matches your
   prefixes — renaming a secret prefix requires re-rendering `release.yml`
   (re-run scaffold), not just renaming the secret.

### Workflow step fails: "Azure authentication failed"

**Cause:** Service principal credentials are incorrect, expired, or the SP does
not have sufficient permissions on the target workspace.

**Solution:**
1. Verify the client ID, client secret (check expiration), and tenant ID.
2. Check that the environment's service principal is added to that
   environment's Fabric workspace with "Member" or "Admin" role.

### Post-merge workflow fails: "PrincipalTypeNotSupported" on `git/updateFromGit`

**Cause:** The workspace contains an item type that does not support SP auth
for Git integration, or the Git connection's backing credential is missing.

**Solution:**
1. Confirm the dev workspace's Git connection uses the PAT-holding Fabric
   connection (created by `fat_fabric_git_create_pat_connection`), and that the
   PAT has not expired.
2. Confirm every item type currently in the dev workspace supports SP auth for
   Git integration (see `plans/archived/spike-c-deploy-fidelity.md` for the
   confirmed-supported list and the GitHub-specific Wave 1 spike findings in
   `plans/add-github-support.md`).

### `parameter.yml` generation fails: "fabric.yml not found"

**Cause:** The `gen_parameters.py` invocation cannot find `fabric.yml`.

**Solution:**
1. Ensure `fabric.yml` exists at the repository root (or update the
   `fabric_config_file` render variable and re-render).
2. Verify the file is committed to the repository.

### Deployment succeeds, but items are not updated

**Cause:** The `parameter.yml` does not contain the expected substitutions, or
fabric-cicd is not finding items to deploy.

**Solution:**
1. Add a diagnostic step to print `parameter.yml` before the deploy step:
   ```bash
   cat fabric/parameter.yml
   ```
2. Check that `fabric.yml` contains the correct item definitions, connection
   registrations, and overrides.
3. Verify that the items exist in the source repository (e.g.,
   `fabric/elt/sources/<source>/`, `fabric/elt/core/`, `fabric/semantic/`).
4. No dry-run mode exists in fabric-cicd; work through the pre-flight checklist
   in the `validate` skill before running
   `python scripts/deploy_fabric.py`.

## Related Documentation

- [fabric-cicd GitHub Repository](https://github.com/microsoft/fabric-cicd)
- [fabric-cicd Parameterization Guide](https://microsoft.github.io/fabric-cicd/1.1.0/how_to/parameterization/)
- [GitHub Actions: Using secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions)
- [GitHub REST API: Actions secrets](https://docs.github.com/en/rest/actions/secrets)
- [GitHub REST API: Branches (branch protection)](https://docs.github.com/en/rest/branches/branch-protection)
- [Azure AD Service Principals](https://learn.microsoft.com/en-us/entra/identity-platform/app-objects-and-service-principal)
- [Automate Git integration with a service principal](https://learn.microsoft.com/en-us/fabric/cicd/git-integration/automate-git-integration-with-service-principal)
