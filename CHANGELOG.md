# Changelog

## 1.0.0

Initial release. Inspired by [ohartwig/spec-kit-gitlab](https://github.com/ohartwig/spec-kit-gitlab).

- Four commands: `init`, `sync`, `status` and `import-feature`.
- `sync` creates and links the feature, user story and task issues and the feature milestone, then syncs done/open state in both directions. Each feature's `.gitlab-mapping.json` records the last synced state, so changes from either side are kept.
- `status` reports what `sync` would do without changing anything; `sync --dry-run` previews a sync.
- Parses spec-kit's story (`### User Story 1 - Title (Priority: P1)`) and task (`- [ ] T001 [P] [US1] …`) formats. Task priority comes from the task's story.
- One standard-library Python script (3.9+), run through spec-kit's `py` script variant on Linux, macOS and Windows. No `glab`, `curl` or `jq`.
- Configuration in `gitlab-sync-config.yml`, `gitlab-sync-config.local.yml` and `SPECKIT_GITLAB_SYNC_*` environment variables, with `gitlab.ca_file` for internal certificates.
- Hooks after `/speckit.tasks` and `/speckit.implement` that offer to run `sync`.
