---
description: "Connect the project to GitLab: set the server URL and project, then check the token and project access"
scripts:
  py: scripts/python/gitlab.py init
---

# Set up the GitLab connection

## User Input

```text
$ARGUMENTS
```

The input may contain a GitLab URL, a project path, or both. Use them instead of asking.

## Steps

1. Run `{SCRIPT}` from the repository root. It changes nothing: it prints the current settings (`config`) and, if a URL and project are set, checks the token and project access.

2. If `config.url` or `config.project` is missing, or the user wants to change them, ask only for what is missing:
   - **GitLab URL**, e.g. `https://gitlab.example.com` (no path, no trailing slash)
   - **Project path**, e.g. `group/project` or `group/subgroup/project`

   Save them with `{SCRIPT} --url <url> --project <path>`, passing only the values that change. Add `--ca-file <path-to-pem>` if the server uses a self-signed or internal certificate. The command checks the connection again after saving.

3. Act on the result:
   - `token.found` is false: ask the user to create a personal access token with the `api` scope (GitLab → Preferences → Access tokens) and set it in their shell as `GITLAB_TOKEN`, or to run `glab auth login --hostname <host>` if they use the GitLab CLI. Never write the token to a file in the repository.
   - `connection_error`: show it. A certificate error means `--ca-file` is needed; HTTP 401 means the token is invalid or lacks the `api` scope; HTTP 404 means the project path is wrong or not visible to the token's user.
   - `problems`: show each one.
   - `ready` is true: confirm the connection with the user name and project URL.

4. Mention that labels and options (feature issue, milestone, priority labels, task links) are in `.specify/extensions/gitlab-sync/gitlab-sync-config.yml`, and that machine-specific overrides go in `gitlab-sync-config.local.yml` next to it. Once the feature has a `spec.md` and `tasks.md`, the next step is __SPECKIT_COMMAND_GITLAB-SYNC_SYNC__.
