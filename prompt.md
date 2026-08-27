Scaffold a scratch client project with cicd="github" and git_integration.provider="github" against ryan-schofield and a disposable test project (created at https://github.com/ryan-schofield/fat_test_client), targeting a scratch Fabric capacity. Drive it through the full initialize-project sequence (skills/initialize-project/SKILL.md, substituting the GitHub-provider tools/paths throughout) end to end:

1. Scaffold and validate
    - Environments: dev, prod
2. Provision workspaces/capacity/SPN grant/items
3. Fabric Git connection: fat_fabric_git_create_pat_connection, then connect + initialize
   against the GitHub repo — this is the first live test of the classic-PAT assumption
4. Onboard sources
    Data sources: one SharePoint CSV source
    - URL: https://ebinsights.sharepoint.com/sites/CAHShared
    - Library: Shared Documents
    - Folder: Strata
    - Filename: benchmarks_dept.csv
    - Connection: Not specified, but an existing SharePoint connection is available in the tenant that may be reusable.
5. Bootstrap, first publish, second-pass resolution, final publish
6. Canonical commit
7. fat_cicd_apply for GitHub: repo adopt/create, placeholder name-prefixed secrets
   (DEV_*/PROD_*), verify the three workflow files, branch prote
8. Populate real secret values (pending action) and trigger pr-validation.yml,
   post-merge.yml, and release.yml for real, confirming each goes green

Confirm each mutating step with me before proceeding (Stepwise-Friendly Constraint). Record any defect as a finding rather than silently working around it — remediate on the spot before merge, per Wave 6's acceptance criteria.

Do not break the boundaries of this local workspace. You can leverage search, fetch, and other external tools as needed, but breaching boundaries to leverage decisions made in other local projects is not permitted.

Keep a running log as you go - a markdown file is fine - that records, for every step:
- what you did and what the tool/skill returned,
- anything that didn't work on the first try, however small, and what you changed to get past it (a hand-edit, a re-read of a skill, a retry with different arguments, a CLI command outside FAT to discover something),
- anything that seemed like a gap, contradiction, or defect in FAT's tools, skills, or documentation, even if you found a way around it.

This log matters as much as the outcome. A run that finishes clean with no entries is less useful than one that finishes clean with five honestly-recorded workarounds. Do not silently route around a problem and move on without writing it down.

On completion, create a bulletpoint list evaluating the end-to-end process and assign a letter-grade to the full process. This information will be used to improve FAT's own skills and documentation.