---
description: "Sync the current feature with GitLab: create and link missing feature, story and task issues, then sync done/open state in both directions"
scripts:
  py: scripts/python/gitlab.py sync
---

# Sync the feature with GitLab

## Prerequisites

- The GitLab connection is set up (__SPECKIT_COMMAND_GITLAB-SYNC_INIT__) and `GITLAB_TOKEN` holds a token with the `api` scope.
- The active feature has a `spec.md` and, for task issues, a `tasks.md`.

## User Input

```text
$ARGUMENTS
```

If the user asks for a preview or dry run, add `--dry-run` to the commands below.

## What sync does

It is always safe to run again: issues recorded in the feature's `.gitlab-mapping.json` are never created twice.

1. Creates what is missing: a milestone named after the feature directory, a feature issue, one issue per user story in `spec.md`, and one Task issue per task in `tasks.md`.
2. Links each story issue to the feature issue and each task issue to its story.
3. Syncs each task's done/open state in whichever direction changed since the last sync: checking or unchecking it in `tasks.md` closes or reopens the issue, and closing or reopening the issue in GitLab checks or unchecks the task.
4. Closes a story issue when all of its tasks are done, and reopens it when one is reopened.

`.specify/extensions/gitlab-sync/gitlab-sync-config.yml` switches the feature issue, milestone, priority labels and task links on or off.

## Steps

1. Run `{SCRIPT}` from the repository root. It prints a JSON report.

2. If it fails, show `error`. For a missing URL, project or token, or HTTP 401 or 404, suggest __SPECKIT_COMMAND_GITLAB-SYNC_INIT__. If it stopped partway, the issues created so far are recorded and running it again continues from there.

3. Summarise the report briefly:
   - issues created, grouped by kind, with their URLs (`actions` with `"action": "create"`)
   - issues closed or reopened, and tasks checked or unchecked in `tasks.md`
   - how many links were created (not each one)
   - every entry in `warnings`

4. If `feature.out_of_sync` is true, the feature issue is open although every task is done, or closed although some are not. Ask whether to update it, and if so run `{SCRIPT} --sync-feature`.
