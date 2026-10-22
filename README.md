# spec-kit GitLab Sync

Keeps a [spec-kit](https://github.com/github/spec-kit) feature in sync with GitLab issues, on gitlab.com or a self-hosted instance:

- one **milestone** and one **feature issue** per feature
- one issue per **user story** in `spec.md`, linked to the feature issue
- one **Task** issue per task in `tasks.md`, linked to its story
- **two-way status sync**: checking a task in `tasks.md` closes its issue, closing the issue in GitLab checks the task

It talks to the GitLab REST API directly from a small Python script. No `glab`, `curl` or `jq` needed.

> Inspired by [ohartwig/spec-kit-gitlab](https://github.com/ohartwig/spec-kit-gitlab).

## Requirements

- **spec-kit 0.13.2 or later.** spec-kit already brings Python; the script uses only the standard library and works with Python 3.9+, whichever interpreter spec-kit picks (the project's `.venv`, `python3` or `python`).
- A GitLab **personal access token** with the `api` scope, in the `GITLAB_TOKEN` environment variable. If you use the GitLab CLI, a token stored by `glab auth login` is picked up too.

## Installation

```bash
cd /path/to/your/spec-kit-project

# from an archive of this repository (use a release tag to pin a version)
specify extension add gitlab-sync --from https://github.com/leonio/speckit-gitlab-sync/archive/refs/heads/main.zip

# or from a local clone
specify extension add --dev /path/to/speckit-gitlab-sync
```

Then run `/speckit.gitlab-sync.init` in your agent. It asks for the GitLab URL and project path, writes them to `.specify/extensions/gitlab-sync/gitlab-sync-config.yml`, and checks the token and project access.

## Commands

| Command | What it does |
|---|---|
| `/speckit.gitlab-sync.init` | Set the GitLab URL and project, then check the token, project access and permissions. |
| `/speckit.gitlab-sync.sync` | Create and link any missing issues, then sync done/open state in both directions. Safe to run any time. |
| `/speckit.gitlab-sync.status` | Read-only table of every task and story: local state, GitLab state, assignees, and what `sync` would do. |
| `/speckit.gitlab-sync.import-feature 232` | Use an existing GitLab issue as the feature issue, or start a new feature from it via `/speckit.specify`. |

The exact invocation depends on your agent (for example `/speckit-gitlab-sync-sync` for agents that use skills).

Two hooks offer to run `sync` at the right moments, each asking first:

- after `/speckit.tasks`, to create the issues;
- after `/speckit.implement`, to close the issues of completed tasks.

### Typical flow

```
/speckit.gitlab-sync.init                  connect once per project
/speckit.specify → /speckit.plan → /speckit.tasks
                                      hook: sync creates milestone, feature, story and task issues
/speckit.implement                    tasks get checked off in tasks.md
                                      hook: sync closes their issues
/speckit.gitlab-sync.sync                  any time: picks up issues closed or reopened in GitLab
```

Starting from an issue that already exists: `/speckit.gitlab-sync.import-feature 232`.

## How sync decides

For every task, `sync` compares the checkbox in `tasks.md` with the issue's state. Each feature's `.gitlab-mapping.json` records the state both sides agreed on at the last sync. Whichever side changed since then wins:

- `[ ]` → `[X]` in `tasks.md`: the issue is closed (and `[X]` → `[ ]` reopens it);
- an issue closed or reopened in GitLab: the checkbox is updated.

Nothing is ever reverted, so a teammate closing an issue and you checking another task are both kept.

- **Stories** have no checkbox. A story issue is closed when all of its tasks are done and reopened when one is reopened. If someone closes or reopens a story issue by hand, `sync` leaves it alone until its tasks change.
- **The feature issue** is never closed or reopened automatically. When its state doesn't match the tasks, `sync` reports it, and the agent asks before running `sync --sync-feature`.

## What gets created

| Spec-kit | GitLab issue | Labels |
|---|---|---|
| feature directory `001-user-auth` | milestone `001-user-auth`; issue titled after `spec.md`'s heading, with the spec (minus the stories) as description | `spec-kit`, `feature` |
| `### User Story 1 - Sign in (Priority: P1)` | `US1: Sign in`, with the story text as description | `spec-kit`, `user-story`, `priority::1` |
| `- [ ] T012 [P] [US1] Create User model` | `T012: Create User model`, issue type **Task** | `spec-kit`, `task`, `priority::1` (from its story) |

All issues are assigned to the feature's milestone. Labels that don't exist yet are created by GitLab. On GitLab versions without the Task type, tasks are created as regular issues.

The parsers follow spec-kit's templates: stories as `### User Story N - Title (Priority: Pn)`, tasks as `- [ ] T001 [P] [USn] Description`, where `[P]` means *parallelizable*. Lines inside code blocks and HTML comments are ignored.

## Configuration

`.specify/extensions/gitlab-sync/gitlab-sync-config.yml`:

```yaml
gitlab:
  url: "https://gitlab.example.com"
  project: "group/project"      # or group/subgroup/project
  ca_file: ""                   # PEM bundle for a self-signed or internal certificate

labels:
  speckit_label: "spec-kit"
  feature_label: "feature"
  story_label: "user-story"
  task_label: "task"

mapping:
  feature_to_issue: true        # create a parent issue for the feature
  feature_to_milestone: true    # create a milestone per feature
  priority_to_label: true       # P1 -> priority::1
  link_tasks_to_stories: true   # link task issues to their story issue
```

Settings are applied in spec-kit's usual order, later winning:

1. built-in defaults
2. `gitlab-sync-config.yml`
3. `gitlab-sync-config.local.yml` in the same directory (for machine-specific settings; keep it out of version control)
4. environment variables, either in spec-kit's form `SPECKIT_GITLAB_SYNC_<SECTION>_<KEY>` (for example `SPECKIT_GITLAB_SYNC_GITLAB_URL` or `SPECKIT_GITLAB_SYNC_MAPPING_FEATURE_TO_MILESTONE=false`), or the shorter `GITLAB_URL`, `GITLAB_PROJECT` and `GITLAB_CA_FILE`

The token is read only from `GITLAB_TOKEN` (or `glab`), never from a file. HTTPS proxies are honoured through the usual `HTTPS_PROXY` and `NO_PROXY` variables.

## The mapping file

Each feature directory gets a `.gitlab-mapping.json` recording which issue belongs to which story and task, plus the last synced state. Commit it with the feature so that teammates and CI sync against the same issues. If a story or task is removed from `spec.md` or `tasks.md`, its issue is left as it is and `sync` lists it as orphaned.

## Running without an agent

The commands are thin wrappers around one script that prints JSON, so it works in CI too:

```bash
export GITLAB_TOKEN=glpat-...
uv run .specify/extensions/gitlab-sync/scripts/python/gitlab.py sync --dry-run
uv run .specify/extensions/gitlab-sync/scripts/python/gitlab.py status --feature-dir specs/001-user-auth
```

The script declares its Python requirement inline ([PEP 723](https://peps.python.org/pep-0723/)), so `uv run` finds or downloads a suitable interpreter. It has no dependencies, so `python3 …/gitlab.py` works just as well.

Run `gitlab.py --help` or `gitlab.py sync --help` for all options.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No GitLab token found` | Export `GITLAB_TOKEN` with a personal access token that has the `api` scope, or run `glab auth login --hostname <host>`. |
| `HTTP 401` | The token is invalid, expired or lacks the `api` scope. |
| `HTTP 404` | The project path is wrong, or the token's user can't see the project. Check it with `/speckit.gitlab-sync.init`. |
| `CERTIFICATE_VERIFY_FAILED` | The server uses an internal or self-signed certificate. Set `gitlab.ca_file` (or `SSL_CERT_FILE`) to its CA bundle. Python doesn't read the macOS Keychain. |
| Commands show a literal `{SCRIPT}` | spec-kit is older than 0.13.2. Upgrade it, then reinstall the extension. |
| `No active feature` | Run `/speckit.specify` first, or pass `--feature-dir specs/<feature>` when running the script directly. |

## Contributing

Issues and pull requests are welcome. Run the tests before sending changes:

```bash
uv run python -m unittest discover -s tests
```

Add `--python 3.9` after `uv run` to check the oldest supported version.

The tests use only the standard library. They run the script against an in-process fake of the GitLab API, and check the command files against what spec-kit's registration expects. CI runs them on Linux, macOS and Windows, and installs the extension into a fresh spec-kit project.

## License

[MIT](LICENSE) © 2026 leonio
