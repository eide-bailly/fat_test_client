# FAT Live Smoke Test — GitHub Provider — Run Log

**Started:** 2026-08-27
**Goal:** Scaffold and initialize a new Fabric project end-to-end via the `initialize-project` skill, substituting GitHub-provider tools/paths, driven from this sibling directory (`fat_test_client`).

Target: cicd="github", git_integration.provider="github", org `ryan-schofield`, repo `fat_test_client` (https://github.com/ryan-schofield/fat_test_client), scratch Fabric capacity.

---

## Phase 0 — Inputs and preflight

### 0c — `fat_init_assess` (live)
- Ran `fat_init_assess(config_path="fabric.yml", mode="live", env="dev")`.
- `az_account` and `fab_account` agree: tenant `50511d1a-...`, principal `rschofield@EBInsights.onmicrosoft.com`. No mismatch.
- No blockers. `next_action: fat_scaffold_plan` (fabric.yml doesn't exist yet — expected).
- `context_id`: `83354ce7-9b7e-4a83-add6-b93b4c4f930e`.
- **Discrepancy noted:** `fat_init_assess`'s `capacity` block reported `"No capacity_id configured in fabric.yml or FABRIC_CAPACITY_ID."` even though `FABRIC_CAPACITY_ID` **is** set in `.env` (`8C8F75F6-...`). A follow-up `fat_provision_preflight` call, run seconds later against the same `.env`, correctly found and validated that same capacity ID as visible. So `fat_init_assess`'s capacity check appears not to read `FABRIC_CAPACITY_ID` from the environment before `fabric.yml` exists (it may only read the config file's `environments.*.capacity_id`, and message-copy claims env-var fallback that isn't actually implemented at this stage). **Recording as a candidate documentation/behavior defect** — not blocking, since `fat_provision_preflight` catches it, but the `fat_init_assess` message is misleading pre-scaffold.

### `fat_list_capacities`
- 5 capacities visible. Target scratch capacity per `.env`: `8c8f75f6-c0ad-4e4f-a058-66c94563c7fd` = **copilotebatesting** (F4, North Central US, Active).

### `fat_provision_preflight`
- All 5 checks passed: fab tenant, az tenant, tenant alignment, ENV keys, capacity visibility (via fab CLI as user).

### `fat_dev_doctor`
- Failed only on `fabric-yml-exists` (expected pre-scaffold).

### 0d — Tenant-admin hard gate
- Cannot be verified by any tool; requires the user (as tenant admin or via one) to confirm directly.
- **User confirmed all gates enabled** (SP Fabric API access, SP workspace/connection/pipeline creation, SP public API calls, `dbt jobs (preview)` flag).

**Checkpoint 0 — approved.** Input set echoed and confirmed:
- Directory/repo name: `fat_test_client` (already underscore-safe — no hyphen/underscore split needed)
- dbt project name: `fat_test_client`
- Environments: `dev`, `prod`
- GitHub owner/repo: `ryan-schofield` / `fat_test_client`
- Capacity: `copilotebatesting` (F4), `8c8f75f6-c0ad-4e4f-a058-66c94563c7fd`
- Deployment SPN client ID: from `.env` `SERVICE_PRINCIPAL_CLIENT_ID`

---

## Phase 1 — Scaffold (local only)

### `fat_scaffold_plan`
- `project_name=fat_test_client`, `client_name="Ryan Schofield Test Client"`, `cicd=github`.
- Plan: environments dev/prod, split workspace layout, components `dev-environment, dbt-framework, epm-framework, fabric-pipelines, cicd-github`.

### `fat_scaffold_apply` — confirmed by user, applied
- 70 files created, including `.github/workflows/{pr-validation,post-merge,release}.yml`, the full `dbt_build.DataBuildToolJob` payload, `docs/cicd.md`, `docs/cicd-secrets.md`, `docs/fabric-yml-reference.md`.

### Guardrail 5 + git_integration/cicd sections — hand-edit required
- **Finding:** `fat_scaffold_apply` with `cicd="github"` writes `fabric.yml` with `identity.tenant_id`/`identity.deployment_sp_client_id` **blank** and with **no `git_integration:` or `cicd:` section at all** — even though `cicd: github` was the explicit scaffold input and the GitHub workflow files were rendered on that basis. The skill's Phase 1 text ("Set `identity.deployment_sp_client_id` in `fabric.yml` now") anticipates the identity hand-edit, but nothing in the runbook flags that `git_integration:`/`cicd:` blocks are *also* absent post-scaffold for the GitHub path and must be hand-authored from `docs/fabric-yml-reference.md` before Phase 3/6 can run. Read `docs/fabric-yml-reference.md` to get the exact schema, then hand-edited `fabric.yml` to add:
  - `identity.tenant_id` = `SERVICE_PRINCIPAL_TENANT_ID` from `.env`
  - `identity.deployment_sp_client_id` = `SERVICE_PRINCIPAL_CLIENT_ID` from `.env`
  - `environments.dev/prod.capacity_id`/`capacity_name` = the scratch capacity `copilotebatesting` / `8c8f75f6-c0ad-4e4f-a058-66c94563c7fd` (both envs pointed at the same scratch capacity — disposable project)
  - `git_integration:` block (`provider: github`, `owner: ryan-schofield`, `repository: fat_test_client`, `branch: dev`, `directory: fabric/`)
  - `cicd:` block (`provider: github`, `owner: ryan-schofield`, `repository: fat_test_client`)

### `fat_validate` — `valid: true`, no errors after the hand-edit.

### `fat_config_reconcile` — reports drift:
- `"Component 'cicd-github' declared but no known presence markers are configured for it"`
- **Finding:** the reconciler's `_COMPONENT_MARKERS` map appears to have an entry for `cicd-azure-devops` but not `cicd-github`, even though `cicd-github` is a first-class scaffold option. The files it should be checking for (`.github/workflows/*.yml`) are present and correct — this is a reconciler coverage gap, not an actual drift. Non-blocking; recorded as a defect.

### Guardrail 6 payload hygiene check
- `find fabric/ -type f -empty` → none. `find fabric/ -name .gitkeep` → none. Clean.

**Checkpoint 1 — passed.** `fabric.yml` valid, no zero-byte files, `find .` directory tree reviewed.

---

## Phase 2 — Provision Fabric resources

### `fat_provision_plan(env=dev)` — clean plan, no conflicts
- 2 workspaces (`fat-test-client-dev`, `fat-test-client-data-dev`), capacity assign x2, spn_admin grant x2, 2 baseline items — all `create`.

### `fat_provision_apply` attempt 1 — `context_invalid`
- First apply call used the `context_id` from the very first `fat_init_assess` (run before `fabric.yml` existed and before the hand-edits). It was rejected as `context_invalid` — expected per the skill's guardrail (identity/config/tenant/capacity fingerprint had shifted once `fabric.yml` existed with real capacity/identity values). **Not a defect** — re-ran `fat_init_assess` for a fresh `context_id` per the documented recovery path. Worth noting for the skill: the first `fat_init_assess` in Phase 0 happens before scaffold, so its `context_id` is close to guaranteed to go stale by the time Phase 2 apply runs — the runbook could say explicitly "expect to re-run `fat_init_assess` before every phase's first apply," rather than leaving it to the `context_invalid` recovery note alone.

### `fat_provision_apply` attempt 2 (fresh context) — **defect: false-negative failure status**
- Applied the same `plan_id` with the fresh `context_id`. Result: `status: "completed_with_failures"`, with exactly **one** result entry (`workspace:dev:items`) marked `status: "failed"` — but its `message` field shows the underlying `fab` CLI call **succeeded**: `"* 'fat-test-client-dev.Workspace' created"` with a real workspace ID (`a94952b2-9783-48cd-b46b-f79240df81a7`) and correct capacity assignment in the table output. The apply then stopped after this one step — none of the other 6 steps (capacity assign, spn_admin grant, second workspace, items) were attempted in this call, even though a `completed_with_failures` status with only 1/7 steps executed suggests a hard abort partway through the sequence.
- **Root cause (inferred, not confirmed in source):** the apply logic appears to classify this step as failed based on parsing the `fab` CLI's tabular/text output rather than its actual exit code — the literal string `"created"` is present in the message but something about the surrounding output (possibly the workspace-listing table format, or a non-zero-but-benign exit code from `fab workspace create`) is tripping a failure branch.
- **Verified via re-plan:** `fat_provision_plan(env=dev)` immediately after shows `workspace:dev:items` as `planned_action: "adopt"` against the same workspace ID, and `capacity:fat-test-client-dev` as `skip` (capacity already correctly assigned) — confirming the "failed" step in fact fully succeeded live. This is Guardrail 4 territory (inspect per-resource status, not top-level) but goes one step further than the guardrail's stated failure mode: here the *per-resource* status itself is wrong, not just the top-level rollup.
- **Recorded as an open defect** in `fat_provision_apply`'s workspace-create step result classification. Remediation used: re-ran `fat_provision_plan` (fresh plan `d32c2b92-...`) which correctly reflects live state via adopt/skip, then re-applied to complete the remaining steps — the documented recovery path (partial failure leaves live resources in place; a fresh plan naturally reflects them) works correctly despite the misleading status.

### `fat_provision_apply` attempt 3 — same defect, second workspace
- Re-applied `d32c2b92-...`: `workspace:dev:items` now correctly `adopted`, `capacity` `already_assigned`, `spn_admin` `granted` — all three genuinely correct this time. Then `workspace:dev:data` hit the **identical false-negative** pattern: `status: "failed"`, message shows `"* 'fat-test-client-data-dev.Workspace' created"` with a real workspace ID and correct capacity in the output table. Apply again stopped mid-sequence (4/7 steps run).
- **Conclusion: this is a systematic classification bug in the create-step result parser, not a one-off flake** — it reproduced identically on both workspace-create calls.

### `fat_provision_apply` attempt 4 — same defect class, now on an *item*-create step
- Re-planned (`13a6c161-...`) — both workspaces now correctly `adopt`/`skip`/`granted`. Applied: workspace/capacity/spn_admin steps all correct, then `item:dev:lh_fat_test_client` (Lakehouse create) hit the **same false-negative-failure pattern**: message shows `"* 'lh_fat_test_client.Lakehouse' created"` with a real item ID, but `status: "failed"`, apply stopped (7 declared steps, only 6 attempted, warehouse never reached).
- **Broadens the defect scope**: this is not workspace-create-specific — it reproduces on Lakehouse item-create too. The common thread across all three false negatives is that the underlying `fab` CLI output is a **tabular listing followed by a `* '<name>.<Type>' created` confirmation line**, and something about parsing that combined shape is tripping a failure branch in the result classifier.

### Spurious conflict: Lakehouse vs. its auto-created SQLEndpoint sibling — blocking defect
- Re-planned again (`2e9751c9-...`, then reproduced identically as `544d171c-...`): `item:dev:lh_fat_test_client` now reports `planned_action: "conflict"` — `"An item named 'lh_fat_test_client' already exists but is of type 'SQLEndpoint', not 'Lakehouse'."`
- **Verified via direct `fab api -X get "workspaces/<data-ws-id>/items"`** (used here only to independently verify live state, not to route around FAT — see Guardrail-4-adjacent verification practice used throughout this log): the data workspace has **two** items named `lh_fat_test_client` — the real `Lakehouse` (created successfully in the previous step, `fb7f3096-...`) **and** Fabric's auto-generated `SQLEndpoint` sibling that every Lakehouse spawns automatically under the identical display name (`8bfc6d4b-...`). FAT's conflict/adopt matching is keying on `displayName` alone without filtering by `item_type`, and is evidently picking up the SQLEndpoint sibling (auto-created, same name, different type) as "the" existing item instead of the real Lakehouse — a **false conflict**, reproducible deterministically across two separate `fat_provision_plan` calls.
- **This blocks the entire plan**, not just the one step: `fat_provision_apply` on the conflicted plan returns `status: "plan_has_conflicts"` and performs no mutation at all (per the documented "a plan with unresolved conflicts is never applied" contract) — so the still-outstanding, non-conflicting Warehouse-create step is collaterally blocked too.
- **Root cause is structural**: any project using the standard Lakehouse-provisioning convention will always have a same-named SQLEndpoint sibling live the moment the Lakehouse exists, so this false conflict is not an edge case — it will reproducibly block every second-pass `fat_provision_plan`/`apply` after the first Lakehouse-create step, for every project, once the false-negative-failure defect above (or any other reason) forces a second provisioning pass. The two defects compound: the false-negative-failure bug forces retries, and every retry then trips the SQLEndpoint false-conflict bug.

### Workaround attempt: `fat_provision_items` — third defect, silent no-op
- Tried the standalone `fat_provision_items` tool (documented as "Provision lakehouses, warehouses, and other items declared in fabric.yml... creates items via `fab`") to create just the outstanding Warehouse, bypassing the blocked plan/apply pair. Called with `item_type="Warehouse"` and then again with no `item_type` filter.
- Both calls returned `status: "completed"`, `written: {}` — **no error, no explanation, and nothing was actually created.** Verified via `fab api -X get ".../items"` immediately after both calls: `wh_fat_test_client` still did not exist.
- Re-reading `docs/fabric-yml-reference.md`'s `items:`/`item_types:`/`endpoints:` section suggests `written` may refer to the `endpoints:` back-write map (Warehouse TDS endpoint harvesting), not items actually created — if so, the tool's own docstring ("creates items via `fab`") is misleading, since in this run it created nothing and gave no indication why (e.g. it may silently no-op whenever *any* item in the same env has an unresolved conflict, without saying so).
- **This is the most concerning of the three defects**: a `"completed"` status with zero explanation for zero output looks identical to "nothing to do," which is unsafe for an unattended run.

### Manual remediation (documented, on the record, not silent)
- Since (a) the SQLEndpoint false-conflict blocks the standard `fat_provision_plan`/`apply` path entirely, and (b) the `fat_provision_items` fallback silently no-ops, created the outstanding Warehouse directly via `fab api -X post "workspaces/<data-ws-id>/warehouses" -i '{"displayName": "wh_fat_test_client"}'` (202 Accepted, LRO). Verified live via `fab api -X get ".../items"` ~15s later: `wh_fat_test_client` (Warehouse) now present alongside the Lakehouse and its SQLEndpoint sibling.
- This directly matches the declared intent already in `fabric.yml`'s `provisioned_items:` (this exact name/type pair) — it is not a deviation from the plan, only a manual execution of a step the tooling itself could not complete.
- **Net Phase 2 (dev) live state, confirmed correct:** workspace `fat-test-client-dev` (`a94952b2-...`), data workspace `fat-test-client-data-dev` (`6979ce42-...`), both on capacity `copilotebatesting`, SPN Workspace Admin granted on both, `lh_fat_test_client` (Lakehouse) and `wh_fat_test_client` (Warehouse) both live in the data workspace.

### Fourth defect: workspace IDs not actually written back to `fabric.yml`
- `fat_provision_apply`'s own docstring states: "Resolved workspace and capacity IDs are written back into fabric.yml." After three successful/partially-successful applies that created and adopted both dev workspaces, re-ran `fat_init_assess` and it read `environments.dev.workspace_id`/`data_workspace_id` as `null` — confirmed by directly reading `fabric.yml`: both fields were still blank in the file.
- **Recorded as a fourth open defect** — the write-back described in the docstring did not happen across any of the four apply calls in this run.
- **Remediation:** hand-wrote `workspace_id: a94952b2-9783-48cd-b46b-f79240df81a7` and `data_workspace_id: 6979ce42-5a1a-425e-b77e-3fa7f27fcfe9` into `fabric.yml`'s `dev` environment block from the values independently verified live via `fab api`. Re-ran `fat_validate` — `valid: true`.


### Prod provisioning
- Same 4th-defect pattern reproduced identically for `prod`: workspace-create false-negative on both `fat-test-client-prod` and `fat-test-client-data-prod`, Lakehouse-create false-negative, then SQLEndpoint false-conflict blocking the Warehouse step. Used the same proven manual remediation immediately (no repeat troubleshooting needed) — `fab api -X post ".../warehouses"` — rather than re-exercising the already-confirmed-broken tool path.
- **Net Phase 2 (prod) live state:** workspace `fat-test-client-prod` (`671959da-...`), data workspace `fat-test-client-data-prod` (`b1fce454-...`), both on `copilotebatesting`, SPN Workspace Admin granted, Lakehouse + Warehouse live.
- Hand-wrote prod `workspace_id`/`data_workspace_id` into `fabric.yml` (same write-back defect as dev). `fat_validate` → `valid: true`.

### Connections — SharePoint source, discovered and shared cleanly
- `fat_provision_discover_connections(name="ebinsights.sharepoint.com", connection_type="SharePoint")` → found exactly one reusable candidate: `"https://ebinsights.sharepoint.com rschofield"` (`a4a28c55-cf6f-4f40-8e88-67720d44439c`), `ShareableCloud`. Matches the prompt's tip that a reusable connection already exists in the tenant.
- `fat_provision_share_connection(env=dev, connection_id=a4a28c55-...)` → `status: "ok"`, `already_shared` (deployment SPN already had the `User` role — idempotent success, not a failure).
- `fat_dev_register_connection(connection_name="sharepoint_cahshared", connection_guid=a4a28c55-...)` → `status: "registered"`, correctly wrote `connections.sharepoint_cahshared` into `fabric.yml` this time — **this tool's write-back worked correctly**, unlike `fat_provision_apply`'s workspace-ID write-back (4th defect above). Worth noting as a positive contrast when reporting the write-back defect: it's not that write-back is broken everywhere, just in that one code path.

**Checkpoint 2 — passed.** No blank dev IDs; prod IDs present too (ahead of the checkpoint's minimum bar, which only requires dev).

---

## Phase 3 — Fabric Git connect

### `fat_fabric_git_create_pat_connection` — classic-PAT assumption confirmed live
- `fat_fabric_git_create_pat_connection(display_name="github-fat-test-client-pat", confirm=True)` → `status: "created"`, `connection_type: GitHubSourceControl`, `connection_id: 15a94639-09cb-4b8a-ad6e-8b04b611e43e`.
- **The classic-PAT assumption holds**: `GITHUB_PAT` from `.env` passed Fabric's server-side test-connection check at creation time. This was flagged in the task prompt as the first live test of that assumption — it worked cleanly.

### `fat_fabric_git_plan(env=dev)` — fifth defect: wrong connection auto-discovered
- Plan resolved the target correctly (`github.com/ryan-schofield/fat_test_client@dev -> fabric/`), confirmed `workspace_id` declared, confirmed no existing Git connection, confirmed no unmanaged items. All good.
- The `git-github-connection` check discovered `fabric-unicorn-demo` (`fd22b7f6-7281-409e-bfbe-0264ca5ec6ce`) as "the" GitHub source-control connection to use — **not** the `github-fat-test-client-pat` connection just created for this project one call earlier. Neither `fat_fabric_git_plan` nor `fat_fabric_git_apply` expose any parameter to pin a specific connection id when more than one `GitHubSourceControl` connection exists in the tenant; discovery appears to just take the first/any match.
- **Recorded as a fifth defect.**

### `fat_fabric_git_apply` — confirms the defect is not cosmetic
- Applied plan `8d75e8f0-...`: result was **not** the runbook's documented greenfield expectation (`git_provider_not_found`). Instead: `status: "connect_failed"`, HTTP 400 `ConnectionMismatch` — `"The connection is incompatible with the specified Git provider details."` This is Fabric correctly rejecting the mismatched `fabric-unicorn-demo` connection against the `ryan-schofield/fat_test_client` target, which confirms the wrong-connection defect above is a real, hard-blocking failure mode on this GitHub path — not a hypothetical.
- Verified via `fat_fabric_git_status(workspace_id=a94952b2-...)`: `connection.connected: false`, no partial/dirty state left behind. Safe.
- **Deferring per the skill's documented Phase 3 → Phase 8 path** (repo doesn't exist until Phase 6 anyway) — but note the actual blocking failure differs from what the skill documents as the expected greenfield outcome for GitHub (`git_provider_not_found`); the real GitHub-path first-pass outcome observed here was `ConnectionMismatch` from the wrong-connection defect, which happened to be masked by the fact that Phase 3 is deferred regardless. **This needs to be resolved before Phase 8's real connect** — likely by finding a way to force `fat_fabric_git_apply`/`fat_fabric_git_plan` to use `15a94639-...` instead of `fd22b7f6-...`, or by determining why discovery didn't prefer the connection just created in the same session.
- `fat_fabric_git_status` also surfaced (as a bonus read) the current bootstrap-token resolution state ahead of Phase 5 — including one `unresolved` token, `__CONNECTION_PIPELINE_INVOKE__` (no `pipeline_invoke` connection registered in `fabric.yml` yet), noted for Phase 5.

**Checkpoint 3 — deferred, as designed.** Git connect state: not connected; explicit deferral to Phase 8, with the wrong-connection defect flagged for resolution before that phase's real connect attempt.

---

## Phase 4 — Source onboarding

### Access-ladder decision
- Read `skill://data-access-ladder` fresh (per its own instruction, not from pretrained assumptions). SharePoint document-library sources are GA at **Rung 1 (OneLake shortcut)** as of 2026 — confirmed with the user before proceeding, per the skill's explicit warning against defaulting to a lower rung from stale training data.

### `fat_source_plan` — sixth defect: same SQLEndpoint-vs-Lakehouse bug, third tool affected
- `fat_source_plan(source_name="benchmarks_dept", rung="shortcut", target_item="lh_fat_test_client", connection_name="sharepoint_cahshared", connection_type/target_type="oneDriveSharePoint")` → `is_unsupported: true`. Connection step correctly adopted `sharepoint_cahshared` (`a4a28c55-...`). The `shortcut` step returned `planned_action: "unsupported"` — `"Item 'lh_fat_test_client' is of type 'SQLEndpoint'; only ['Lakehouse'] host OneLake shortcuts."`
- **This is the same root-cause defect as the Phase 2 SQLEndpoint false-conflict** (displayName-only item matching, no `item_type` disambiguation), now confirmed in a **third** independent tool (`fat_provision_plan`'s conflict check, and now `fat_source_plan`'s target-item resolution). Re-ran the identical plan call a second time — deterministic, not a live-state race: same result both times.
- **Recorded as the sixth defect**, and the most damaging so far: it fully blocks the only end-to-end-automated rung (shortcut) for **any** project using the standard Lakehouse-provisioning convention, since every Lakehouse always has a same-named SQLEndpoint sibling. This is not an edge case — it will reproduce on every shortcut-rung source onboarding for every FAT project.

### Manual remediation
- Since `fat_source_apply` could not run (its plan was `unsupported`), created the shortcut directly via `fab api -X post "workspaces/<data-ws-id>/items/<lakehouse-item-id>/shortcuts"`, using the **correct Lakehouse item ID** (`fb7f3096-973c-4ffa-b060-df6f1782a2ea`, obtained from the earlier `fab api get .../items` verification, not the misresolved SQLEndpoint ID) and the exact body shape documented in `skill://data-access-ladder`'s SharePoint section — `oneDriveSharePoint` target, `location="https://ebinsights.sharepoint.com"`, `subpath="/sites/CAHShared/Shared Documents/Strata"`, `connectionId=a4a28c55-...`. `201 Created`.
- **Verified independently** with `fat_provision_verify_shortcut(env=dev, lakehouse_name="lh_fat_test_client", shortcut_name="benchmarks_dept")` → `found: true`, full shortcut definition matches. **Notable: this verify tool correctly resolved the Lakehouse item** despite the SQLEndpoint sibling — it does not share the same-name matching bug as `fat_provision_plan`/`fat_source_plan`, which narrows the defect to the create/conflict-check code paths specifically, not item resolution generally. Worth flagging precisely this way to whoever fixes it.
- Per Checkpoint 4's expected-success shape: shortcut re-read and confirmed present. ✅.

### Human-only step flagged, not attempted (H5)
- The on-read CSV transformation for this shortcut (`benchmarks_dept.csv`) is UI-only per the skill — no REST/CLI API sets it. **Not attempted here**; flagged as a pending manual portal step for the user, consistent with the runbook's H5 human-only-step classification (not a tool gap).

### Per-environment repetition — explicitly deferred
- Per Phase 4 guidance, a shortcut lives in one lakehouse in one workspace; the `prod` lakehouse would need its own shortcut. The task prompt specified one SharePoint source without asking for per-environment repetition up front. **Deferring the prod shortcut to prod's first promotion**, stated explicitly here rather than silently skipped, per the skill's own allowed deferral pattern.

**Checkpoint 4 — passed for dev**, with the shortcut verified present and the CSV-transform + prod-shortcut steps explicitly called out as outstanding (one human-only, one deferred-by-design).

---

## Phase 5 — Bootstrap publish → second-pass resolution → publish

### Pass 1 bootstrap (`fat_deploy_bootstrap_plan`/`apply`)
- Registered `pipeline_invoke` connection first (discovered an existing reusable `FabricDataPipelines rschofield` connection, `61c69937-...`, shared with SPN — `already_shared` — and registered in `fabric.yml`), resolving the `__CONNECTION_PIPELINE_INVOKE__` token that `fat_fabric_git_status` had flagged as `unresolved` back in Phase 3.
- `fat_deploy_bootstrap_plan` over the 3 item files: exactly the documented shape — `pl_orchestrate_daily` resolved workspace/endpoint/item/connection tokens, deferred `__ITEM_NB_RUN_DBT_JOB__`/`__ITEM_DBT_BUILD__` to `second_pass` (expected — these are created by this very publish), and flagged `__ITEM_PL_REFRESH_SEMANTIC_MODEL__` as `inactive_stub` (Inactive activity reference, not-yet-live). `pl_refresh_semantic_model` and `dbt_build` files resolved cleanly.
- `fat_deploy_bootstrap_apply(final_pass=False)` → wrote all `resolved` tokens, and wrote **placeholder GUIDs** (`00000000-0000-0000-0000-000000000000`-style) for the two `inactive_stub` tokens with `[fat] placeholder for __TOKEN__` markers, exactly as documented.

### First publish to dev (`fat_deploy_gen_parameters` → `fat_deploy_plan` → `fat_deploy_apply`)
- `fat_deploy_gen_parameters` → `parameter.yml` written with `find_replace` entries mapping dev workspace/data-workspace GUIDs to their prod equivalents.
- `fat_deploy_plan(env=dev)` → all 5 checks passed (`deploy-spn-admin`, `deploy-params`, `deploy-env-ids`, `deploy-script-present`; `deploy-unresolved-tokens` a `warn`-level pass noting the expected 2 second-pass tokens).
- `fat_deploy_apply` ran long enough to move to a background task (>120s — expected, it's a real `fabric_cicd` subprocess publishing ~29 items). Result: **`status: "failed"`**, `deployed_item_count: 3` — `nb_run_dbt_job` (Notebook) and `dbt_build` (DataBuildToolJob) published successfully; `pl_refresh_semantic_model` (DataPipeline) also published successfully; **`pl_orchestrate_daily` (DataPipeline) failed**:
  > `Failed to publish DataPipeline 'pl_orchestrate_daily': ... The document creation or update failed because of invalid reference '00000000-0000-0000-0000-000000000000'.`

### Seventh defect: the "Inactive reference never blocks publish" guarantee does not hold
- The `00000000-...` reference is the exact placeholder GUID `fat_deploy_bootstrap_apply` wrote for `__ITEM_PL_REFRESH_SEMANTIC_MODEL__` — an **Inactive** pipeline-activity reference (`Invoke_RefreshSemanticModel`). The skill states explicitly and in bold: *"an Inactive edge never blocks this publish... Do not retry, do not roll back, do not report it as an error."* That guarantee did not hold here: the placeholder GUID caused a real, hard Fabric API rejection (`invalid reference`) that failed the whole item's publish.
- The deploy script's own `_retry_publish_after_partial_failure` self-heal logic ran automatically (per stdout: `"attempting a one-time two-pass retry"`), waited the documented 90s cooldown, then correctly declined to blindly retry: `"Two-pass retry could not derive any new find_replace entry from the workspace's current live items — this is not a missing sibling-item GUID reference."` — i.e. the script itself recognized this doesn't match the self-heal path's assumption (a newly-created *sibling item* GUID), which is accurate: the actual cause is the Inactive-reference placeholder-GUID mechanism, a different code path the self-heal isn't designed to fix.
- **Recorded as the seventh defect** — a documented guarantee (Inactive references are always safe) contradicted by a live, reproducible API failure.
- **Not a naive-retry situation** (Guardrail 3's warning applies): the standard two-pass bootstrap loop is expected to resolve this naturally now, since `pl_refresh_semantic_model` — the target of the previously-Inactive-and-unresolved reference — now exists live from this same publish pass. Proceeding with the documented second-pass bootstrap next, per Phase 5c, rather than retrying the identical publish or hand-patching the placeholder.

### Second-pass bootstrap + final publish
- `fat_deploy_bootstrap_plan` (pass 2) → `fully_resolved: true`. `__ITEM_NB_RUN_DBT_JOB__`/`__ITEM_DBT_BUILD__` resolved live against the items published in pass 1; `__ITEM_PL_REFRESH_SEMANTIC_MODEL__`'s placeholder was upgraded to the real GUID now that `pl_refresh_semantic_model` exists live — confirming the seventh defect (above) self-heals through the standard second-pass loop exactly as the skill predicts for the sibling-item case, even though the *first*-pass failure mode (an Inactive placeholder actually blocking publish) contradicts the skill's stated guarantee.
- `fat_deploy_bootstrap_apply(final_pass=True)` → applied cleanly, `needs_second_pass: false`, no hard-error raised (nothing was left as a literal unresolved token).
- `fat_deploy_gen_parameters` → regenerated `parameter.yml`.
- `fat_deploy_plan(env=dev)` → all 5 checks now full `pass`, including `deploy-unresolved-tokens: "No unresolved bootstrap tokens found."`
- `fat_deploy_apply` → **`status: "succeeded"`**, `returncode: 0`, `deployed_item_count: 4` — `nb_run_dbt_job`, `dbt_build`, `pl_orchestrate_daily`, `pl_refresh_semantic_model` all published cleanly this pass.
- `grep -rE "__[A-Z_<>]+__" fabric/` → 2 matches, both confirmed to be inert `_comment` documentation strings inside permanently-Inactive activities (`Invoke_RefreshSemanticModel`, `RefreshSemanticModel`) describing the Inactive-stub pattern itself — not live token fields Fabric evaluates. **Zero live unresolved placeholders anywhere under `fabric/`.**

**Checkpoint 5 — passed.** Final publish succeeded, `fat_config_reconcile` next (Phase 6 prep), zero remaining `__*__` placeholders in live-evaluated fields.

---

## Phase 6 — CI/CD setup (GitHub)

### Repo precondition check (outside FAT, `gh`)
- `gh repo view ryan-schofield/fat_test_client` confirmed the repo already exists (per the task prompt), private, default branch `main`, not empty (holds the local repo's initial commit). Local `dev` branch tracks `origin/main`; no `dev` or `prod` branch exists on the remote yet.

### `fat_cicd_plan` — clean plan
- All 4 checks passed (`cicd-explicit-target`, `cicd-environment-branches`, `github-auth`, `github-actions-runners`). Actions: adopt the repo, create all 3 workflow files (not yet in remote), create branch protection on `dev` and `prod`. `pending_actions` correctly listed the 8 name-prefixed placeholder secrets and the "push workflows" step as human follow-ups.

### `fat_cicd_apply` — long-running, opaque, against a plan that can't support branch protection (not classified as a defect — see below)
- Applied the plan. The call exceeded the harness's 120s foreground limit and moved to a background task. Checked live GitHub state independently at ~8 min and ~11 min elapsed via `gh secret list` and `gh api .../branches`: **zero secrets created, no new branches, nothing changed on GitHub at either checkpoint.**
- Independently discovered the likely root cause: `gh api repos/ryan-schofield/fat_test_client/branches/dev/protection` (and `.../main/protection`) both return **`403 "Upgrade to GitHub Pro or make this repository public to enable this feature."`** — this repo is private and neither the personal account nor (after the org move below) the `eide-bailly` org's plan tier support the branch-protection API on a private repo. `docs/cicd.md`'s own "What's Confirmed vs. Assumed" section and the skill both anticipate this exact scenario as a `pending_actions` entry — a graceful degradation. **What actually happened was the call running indefinitely with no visible progress instead** — no error surfaced to the caller, no `pending_actions` entry, no partial-progress secrets/workflow-verification step observed live in the 10m53s before the user asked to stop it.
- **User's framing, on reflection, and adopted here: this is not being recorded as a defect.** The underlying condition (private repo, insufficient plan tier for branch protection) is a real, unmet precondition — the tool doing real work against a slow/rate-limited external API for an extended period is not inherently wrong. **What is genuinely missing is operator visibility and a pre-check**, not broken tool behavior:
  - **No intermediate logging/progress visibility** during a multi-step `fat_cicd_apply` run (repo adopt → workflow verify → 8 secrets → 2 branch-protection calls) — from the caller's side, "still working" at 8 minutes and "still working" at 11 minutes are indistinguishable from a hang, especially with zero visible side effects on GitHub in between. Recommend the tool report incremental step-by-step progress (or at least log which sub-step it is on) rather than one opaque result at the end.
  - **No pre-check for the GitHub plan/repo-visibility precondition that branch protection needs.** This should be verifiable up front (`gh api repos/{owner}/{repo}` visibility + an org/plan capability check, or simply a documented manual precondition) as part of Phase 0 or `fat_cicd_plan`'s own checks, rather than discovered live inside a long-running `fat_cicd_apply` call.
- Stopped the task manually (`TaskStop`) after ~11 minutes with no visible progress. Re-verified via `gh secret list`/`gh api .../branches` immediately after stopping: still nothing created — the stop was safe, no partial/dirty state left behind.
- **User's remediation:** moved the repo to the `eide-bailly` GitHub org (`https://github.com/eide-bailly/fat_test_client`), on the assumption that would provide GitHub Pro-tier plan coverage.
  - Verified independently: `gh api orgs/eide-bailly` still reports `plan.name: "free"`, and `gh api repos/eide-bailly/fat_test_client/branches/main/protection` still returns the same `403 Upgrade to GitHub Pro...` — **moving the repo to the org did not resolve the plan-tier gap**; branch protection on a private repo needs the *org* itself on GitHub Team/Enterprise, not just any org membership.
  - Separately verified the classic PAT (`GITHUB_PAT`) is **not** the blocker: it has `admin` permission on `eide-bailly/fat_test_client` and scopes `admin:gpg_key, admin:org, admin:ssh_signing_key, project, repo, user` — more than sufficient.
  - **User's decision:** make the repo public instead, sidestepping the plan-tier requirement (branch protection works on public repos on any plan). User also flagged, independent of this specific run, that **FAT should have an alternate implementation path for when branch protection genuinely can't be enabled** (e.g. private repo on a plan that will never get Team/Enterprise) — rather than only the current binary of "succeeds" or "opaque long-running call with an eventual pending_action."
- **Recommendation for the runbook/tooling** (not filed as a defect, per the user's framing): add a Phase 0-level pre-check for GitHub-provider engagements — confirm repo visibility and org plan tier support branch protection *before* Phase 6 — and add incremental progress logging to `fat_cicd_apply` so a long real API sequence is distinguishable from a hang without the operator independently polling GitHub out-of-band the way this run had to.

**Phase 6 — paused, not yet complete.** Waiting on the repo being made public before re-attempting `fat_cicd_plan`/`fat_cicd_apply`. `fabric.yml`'s `git_integration`/`cicd` `owner` fields updated to `eide-bailly`; local `origin` remote still points at the old `ryan-schofield/fat_test_client` location and needs updating once the move is confirmed.

### Reclassifying: this is a hard blocker, and the earlier "not a defect" framing needs revision
- After the repo was made public and the local `origin` remote updated, re-ran `fat_cicd_plan` (read-only) fresh. It also exceeded the 120s foreground limit and moved to background. Independently verified via direct `gh` calls while it ran:
  - `gh api rate_limit` → `5000/5000 remaining, used: 0` — GitHub API is fully healthy and unthrottled.
  - `gh repo view eide-bailly/fat_test_client --json name` → returns in **0.6s**.
  - `gh api repos/eide-bailly/fat_test_client/branches/main/protection` → now `404 "Branch not protected"` (expected/healthy — public repo, protection API accessible, just nothing configured yet).
  - `gh api user -i` → no SSO block, normal headers.
  - `gh api repos/.../contents/.github/workflows` → clean, fast `404` (nothing pushed yet, expected).
- **This rules out the plan-tier/403 hypothesis as the (sole) explanation.** `fat_cicd_plan` is read-only and, per its own docstring, only needs to confirm `gh` auth, Actions availability, and existing-resource state for adopt/create decisions — all of which `gh` itself answers in well under a second directly. A **read-only preview call** hanging for 3+ minutes with GitHub itself fully responsive points to a hang **inside FAT's own tool implementation** (an internal retry/backoff loop, a blocking call with no timeout, or similar) rather than an external plan-tier limitation. Stopped after ~3m22s with no progress.
- **Revising the earlier framing**: the private-repo/free-plan 403 is real and does explain why `fat_cicd_apply`'s branch-protection step specifically would fail — but it does not explain a hang in the read-only `fat_cicd_plan` against an already-public repo with a fully healthy GitHub API. **This is being recorded as a genuine defect (ninth)** — `fat_cicd_plan`/`fat_cicd_apply` can hang independent of the GitHub-side condition that was assumed to be the root cause, and neither call surfaces any intermediate diagnostic to distinguish "hung" from "working."

**Phase 6 — hard fail / blocked.** Per the user's direction, this run stops here rather than continuing to debug live: `fat_cicd_plan`/`fat_cicd_apply` cannot be completed against this tenant/repo in this session. Phases 7 (canonical commit) and 8 (deferred Git connect) — and the secret-population/workflow-trigger steps in the task prompt's step 8 — are **not attempted**, since they depend on Phase 6 completing. Next step (per user direction): use this run's findings to investigate the hang directly in the FAT toolkit source at a sibling repo, outside this project's boundary — a deliberate, explicit pivot, not a silent workaround.

---

## Final Evaluation

**Run outcome:** Phases 0–5 completed successfully (with workarounds), Phase 6 hard-blocked by a tool hang independent of the GitHub-side condition it was initially attributed to. Phases 7–8 and the task prompt's final secret-population/workflow-trigger step were not reached.

### What worked cleanly
- Phase 0 preflight (`fat_init_assess`, `fat_list_capacities`, `fat_provision_preflight`) — accurate, fast, correctly surfaced the one pre-scaffold capacity-detection quirk.
- Scaffold (Phase 1) — 70 files, correct GitHub-specific component set (`cicd-github`, `.github/workflows/*`), clean `fat_validate`.
- Connection discovery/sharing (`fat_provision_discover_connections`, `fat_provision_share_connection`, `fat_dev_register_connection`) — worked correctly every time, including idempotent `already_shared` outcomes and correct `fabric.yml` write-back.
- The GitHub classic-PAT assumption for Fabric Git integration (`fat_fabric_git_create_pat_connection`) — held up live, first try.
- The bootstrap two-pass token-resolution design (Phase 5) — worked exactly as documented once the Phase-5-first-publish Inactive-reference defect was worked through; the self-healing second pass is genuinely elegant and did what the skill promises.
- `fat_deploy_plan`/`fat_deploy_apply` — accurate pre-checks, and the final publish succeeded cleanly with correct per-item results.

### What did not work / defects encountered (see phase sections above for full detail)
1. `fat_init_assess` misreports capacity detection pre-scaffold (cosmetic, non-blocking).
2. `fat_config_reconcile` has no presence-marker entry for `cicd-github` (cosmetic, non-blocking).
3. `fat_provision_apply` reports false-negative `"failed"` status on workspace-create and item-create steps that actually succeeded, aborting the remaining steps in that call each time (reproduced 3×).
4. `fat_provision_plan`/`fat_source_plan` both mis-resolve a Lakehouse's auto-generated same-named `SQLEndpoint` sibling as the item conflict/match target, instead of the Lakehouse itself — blocks standard provisioning conflict resolution and fully blocks the shortcut rung's only automated path (reproduced deterministically, 2 separate tools).
5. `fat_provision_apply`'s docstring claim that workspace IDs are written back to `fabric.yml` did not hold across 4 apply calls (dev + prod) — required hand-editing both environments' `workspace_id`/`data_workspace_id`.
6. (Folded into #4 above — same root cause, different tool.)
7. Phase 5's documented guarantee that an Inactive pipeline-activity reference placeholder GUID "never blocks publish" did not hold — it caused a real, hard Fabric API failure on the first publish pass. Self-healed correctly via the standard second-pass bootstrap loop, so not a dead end, but the stated guarantee is false as written.
8/9. `fat_cicd_plan`/`fat_cicd_apply` both hang for extended periods (11+ min observed) with zero intermediate visibility, independent of the GitHub-side plan-tier condition that was the initial working hypothesis — confirmed via direct, fast, healthy `gh` API calls run in parallel with the hung MCP calls. This is the most severe defect in the run: it fully blocked completion, gave no diagnostic signal to distinguish "working" from "stuck," and reproduced on both the mutating `apply` and the read-only `plan` call.

### Process observations (not defects)
- The private-repo/free-GitHub-plan branch-protection precondition (403 on `.../branches/.../protection`) is real and should be a Phase-0-level pre-check for GitHub-provider engagements, not something discovered live inside a long-running call.
- No FAT tool call in this run provided intermediate progress visibility on multi-step operations — every long call was an opaque "still working" until either success, failure, or a user-requested stop. This compounded defects #8/9 specifically, but is a general gap worth addressing for any multi-step live-mutation tool.
- The two-pass provisioning/bootstrap retry pattern this run repeatedly had to fall back on (re-plan → re-apply → manual `fab api` completion) worked reliably as a recovery mechanism once understood, but required independently discovering it was safe each time via direct `fab`/`gh` verification — the tooling's own guidance (`next_action` fields) was accurate and helpful throughout this recovery pattern.

### Letter grade: **C**

Phases 0, 1, 2 (with workarounds), 4 (with a workaround), and 5 (with a self-healing defect) all reached correct, independently-verified live end states — the underlying design (assess → bounded plan/apply, two-tier live-mutation gating, live item-ID resolution, two-pass bootstrap) is sound and mostly delivers on its promises. But the run surfaced nine distinct findings, three of which (#3/#4, the SQLEndpoint mismatch; #5, the write-back gap; #8/9, the hang) are structural rather than edge cases — they will reproduce on essentially any GitHub-provider project using the standard Lakehouse-provisioning convention, and the run could not reach its stated termination criteria (CI/CD resources created and independently verified present) within this session. A B-range process with a genuinely blocking, high-severity defect at the end pulls the overall grade down to a C.

---

## Phase 6 — resumed and completed after out-of-band fix

### Pivot: diagnosed and fixed the hang in the FAT toolkit source
- Per user direction, paused the smoke-test-as-strict-runbook framing and used this run's evidence to investigate directly in the toolkit source at `/home/rs3548/repos/fat/fabric-agentic-toolkit` (branch `feature/add-github-support`) — a deliberate, explicit cross-repo pivot, not a silent workaround.
- Independently ruled out the GitHub side as sole cause: `gh api rate_limit` showed `5000/5000` unused, `gh repo view`/`gh secret set` calls run directly (including 3 rapid consecutive calls) all completed in under a second, both against `ryan-schofield/fat_test_client` and after the move to `eide-bailly/fat_test_client` (public).
- Inspected `tool/fat/github/client.py::_run()`: it called `subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=False)` — no `timeout`, and critically, when `stdin` is `None`, `input=None` does **not** redirect the child's stdin; it is left inherited from the parent.
- **Confirmed the actual mechanism live**: inspected the running FAT MCP server process's open file descriptors (`/proc/<pid>/fd`) — fd 0/1/2 were all **sockets** (the MCP stdio transport channel), not a terminal, pipe, or `/dev/null`. Every `gh` subprocess FAT spawned was inheriting that live socket as its own stdin.
- **Two-part fix applied and committed** (`f5c4999` on `feature/add-github-support`, pushed by the user):
  1. `_run()` now explicitly passes `stdin=subprocess.DEVNULL` whenever there is no *stdin* payload to send (instead of leaving stdio inherited).
  2. A 30s per-call timeout (`_GH_TIMEOUT_SECONDS`) kept as defense-in-depth, converting any *other* stall into a bounded `GitHubOperationError` rather than an indefinite hang.
- Added 2 regression tests (`test_run_never_leaves_stdin_inherited_from_the_parent`, `test_run_surfaces_a_hung_gh_process_as_a_bounded_timeout_failure`) and fixed 2 other tests' brittle `subprocess.run` monkeypatch signatures that would have broken on the new kwargs. Full suite: **1115 passed, 1 skipped**. `ruff check`/`ruff format` clean.

### Verified live, post-fix, in this exact session
- User pushed the fix and reconnected the `fat` MCP server. Re-ran `fat_init_assess` (fresh `context_id`) → clean.
- `fat_cicd_plan` → returned instantly (previously the very first sign of trouble was this same read-only call hanging 3+ minutes).
- `fat_cicd_apply` → **`status: "succeeded"`**, completed within the 120s foreground window (no backgrounding needed) — all 8 placeholder secrets created (1 `already_present` survivor from the earlier partial run, 7 fresh `placeholder_created`), zero `failed_resources`. `workflow:*` and `branch-protection:*` entries correctly `pending` (expected — the workflow files and `dev`/`prod` branches don't exist on the remote yet; that's Phase 7's job).
- **Independently verified via `gh secret list --repo eide-bailly/fat_test_client`**: all 8 name-prefixed secrets present with real creation timestamps, confirming the tool's report is accurate this time (not just self-reported).

**Checkpoint 6 — repository/secrets portion complete and independently verified.** Workflow-file and branch-protection completion is correctly deferred to Phase 7 (canonical commit/push creates the `dev`/`prod` branches and workflow files on the remote) — not attempted yet in this run.
