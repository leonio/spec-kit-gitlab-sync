---
description: "Use an existing GitLab issue as the feature issue, or start a new feature from it"
scripts:
  py: scripts/python/gitlab.py import-feature
---

# Import a GitLab issue as the feature

## Prerequisites

- The GitLab connection is set up (__SPECKIT_COMMAND_GITLAB-SYNC_INIT__) and `GITLAB_TOKEN` holds a token with the `api` scope.

## User Input

```text
$ARGUMENTS
```

The input names the issue: a number (`232`, `#232`) or an issue URL. If it is missing, ask for it.

## Steps

1. Run `{SCRIPT} <issue>` from the repository root. It fetches the issue and reports it together with the active feature directory (`feature_dir`). It changes nothing.

2. Show the issue: title, state, link and the start of its description.

3. Decide which feature the issue belongs to:
   - If `feature_dir` is null, or the user wants a new feature for this issue, run __SPECKIT_COMMAND_SPECIFY__ with the issue's title and description as the feature description. When the specification is written, continue with step 4.
   - Otherwise ask whether the issue belongs to the active feature (`feature_dir`). If `current_feature_issue` is a different issue, say so: replacing it needs `--replace`.

4. Run `{SCRIPT} <issue> --link`, adding `--replace` only if the user agreed to replace the current feature issue. The issue becomes the feature issue, so sync will not create another one, and story issues that already exist are linked to it (`linked_stories`).

5. Suggest __SPECKIT_COMMAND_GITLAB-SYNC_SYNC__ to create the story and task issues once `spec.md` and `tasks.md` exist.
