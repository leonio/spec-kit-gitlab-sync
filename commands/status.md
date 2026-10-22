---
description: "Compare the feature's tasks and user stories with their GitLab issues without changing anything"
scripts:
  py: scripts/python/gitlab.py status
---

# GitLab status of the feature

## Prerequisites

- The GitLab connection is set up (__SPECKIT_COMMAND_GITLAB-SYNC_INIT__) and `GITLAB_TOKEN` holds a token with the `api` scope.

## User Input

```text
$ARGUMENTS
```

## Steps

1. Run `{SCRIPT}` from the repository root. It only reads; neither GitLab nor the feature files change.

2. If it fails, show `error`. For configuration or token problems, suggest __SPECKIT_COMMAND_GITLAB-SYNC_INIT__.

3. If `feature` is set, start with the feature issue: title, link and GitLab state.

4. Show the tasks as a table (Task, Issue, tasks.md, GitLab, Assignees, Sync) and the user stories as a second table. Explain the `sync` values in words:
   - `ok`: both sides agree
   - `local_change`: changed in tasks.md; sync will update the issue
   - `gitlab_change`: changed in GitLab; sync will update tasks.md (for a story: closed or reopened by hand, which sync leaves alone)
   - `not_pushed`: no issue yet; sync will create one
   - `missing`: the recorded issue no longer exists in the project
   - `out_of_sync` (feature only): the feature issue's state does not match its tasks

5. End with the `summary` counts and the percentage of tasks done, any `orphaned` IDs (issues recorded for stories or tasks that no longer exist locally) and the `warnings`. If anything is not `ok`, suggest __SPECKIT_COMMAND_GITLAB-SYNC_SYNC__.
