#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Keep a spec-kit feature's spec.md and tasks.md in sync with GitLab issues.

Commands:
  init            Show or write the GitLab connection settings and check access
  sync            Create missing feature/story/task issues, link them, and
                  reconcile done/open states in both directions
  status          Compare spec-kit and GitLab state without changing anything
  import-feature  Use an existing GitLab issue as the current feature's issue

Every command prints a JSON report on stdout. On failure the report has an
"error" key, the message is repeated on stderr, and the exit code is 1.
Standard library only; compatible with Python 3.9 (see gitlab_client.py).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from gitlab_client import (
    CONFIG_FILE,
    VERSION,
    Config,
    ConfigError,
    GitLabClient,
    GitLabError,
    config_dir,
    load_config,
    resolve_token,
    set_yaml_values,
)
from speckit_files import (
    FeatureError,
    Mapping,
    Spec,
    Story,
    Task,
    TaskList,
    find_project_root,
    parse_spec,
    parse_tasks,
    read_text,
    resolve_feature_dir,
    set_checkboxes,
    write_text,
)

TEMPLATE = Path(__file__).resolve().parents[2] / "gitlab-sync-config.template.yml"


class UsageError(Exception):
    """Invalid arguments or a request that needs the user's confirmation."""


# ---------------------------------------------------------------------------
# Shared setup
# ---------------------------------------------------------------------------


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def connect(root: Path) -> tuple[Config, GitLabClient]:
    config = load_config(root)
    config.require_connection()
    token, _ = resolve_token(config.url)
    if not token:
        raise ConfigError(
            "No GitLab token found. Set GITLAB_TOKEN to a personal access token with the "
            "`api` scope, or log in with `glab auth login`."
        )
    return config, GitLabClient(config.url, config.project, token, config.ca_file)


@dataclass
class Feature:
    root: Path
    dir: Path
    config: Config
    client: GitLabClient
    spec: Spec | None
    tasks: TaskList
    mapping: Mapping
    warnings: list[str]

    @property
    def name(self) -> str:
        return self.dir.name

    @property
    def title(self) -> str:
        return (self.spec.title if self.spec else None) or self.name

    def rel(self, filename: str = "") -> str:
        return _relative(self.dir / filename if filename else self.dir, self.root)

    def story_tasks(self, story_id: str) -> list[Task]:
        return [t for t in self.tasks.tasks if t.story == story_id]

    def story_done(self, story_id: str) -> bool | None:
        """A story is done when all of its tasks are; None when it has no tasks."""
        tasks = self.story_tasks(story_id)
        return all(t.done for t in tasks) if tasks else None

    def all_done(self) -> bool | None:
        return all(t.done for t in self.tasks.tasks) if self.tasks.tasks else None


def load_feature(args: argparse.Namespace) -> Feature:
    root = find_project_root()
    config, client = connect(root)
    feature_dir = resolve_feature_dir(root, args.feature_dir)
    warnings: list[str] = []

    spec_path = feature_dir / "spec.md"
    spec = parse_spec(read_text(spec_path)) if spec_path.is_file() else None
    if spec is None:
        warnings.append("spec.md not found: no feature or user story issues.")
    elif not spec.stories:
        warnings.append("No user stories found in spec.md (expected '### User Story 1 - Title (Priority: P1)').")

    tasks_path = feature_dir / "tasks.md"
    tasks = parse_tasks(read_text(tasks_path)) if tasks_path.is_file() else TaskList()
    if not tasks_path.is_file():
        warnings.append("tasks.md not found: no task issues.")
    if tasks.duplicates:
        warnings.append(f"Duplicate task IDs in tasks.md, first occurrence used: {', '.join(tasks.duplicates)}")

    mapping = Mapping.load(feature_dir)
    mapping.check_project(config.project)

    return Feature(root, feature_dir, config, client, spec, tasks, mapping, warnings)


def issue_labels(config: Config, kind_label: str, priority: str | None) -> list[str]:
    labels = [config.speckit_label, kind_label]
    if config.priority_to_label and priority:
        labels.append(f"priority::{priority[1:]}")
    return [label for label in labels if label]


def _footer(*parts: str) -> str:
    return "\n\n---\n_" + " · ".join(p for p in parts if p) + "_"


def feature_description(feature: Feature) -> str:
    body = feature.spec.overview if feature.spec else ""
    return (body + _footer(f"spec-kit feature `{feature.name}`", f"`{feature.rel('spec.md')}`")).strip()


def story_description(feature: Feature, story: Story) -> str:
    return (
        story.body
        + _footer(
            f"spec-kit user story {story.id}",
            f"priority {story.priority}" if story.priority else "",
            f"`{feature.rel('spec.md')}`",
        )
    ).strip()


def task_description(feature: Feature, task: Task) -> str:
    return (
        task.text
        + _footer(
            f"spec-kit task {task.id}",
            f"user story {task.story}" if task.story else "",
            "parallelizable" if task.parallel else "",
            f"`{feature.rel('tasks.md')}`",
        )
    ).strip()


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace, report: dict[str, Any]) -> None:
    root = find_project_root()
    updates = {
        key: value.strip()
        for key, value in (("url", args.url), ("project", args.project), ("ca_file", args.ca_file))
        if value is not None
    }
    if "url" in updates:
        updates["url"] = updates["url"].rstrip("/")
        if not re.match(r"^https?://[^/]+$", updates["url"]):
            raise UsageError(f"--url must look like https://gitlab.example.com, got {args.url!r}")
    if "project" in updates:
        updates["project"] = updates["project"].strip("/")
    if updates:
        path = config_dir(root) / CONFIG_FILE
        if path.is_file():
            text = read_text(path)
        else:
            text = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.is_file() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text(path, set_yaml_values(text, "gitlab", updates))
        report["written"] = _relative(path, root)

    config = load_config(root)
    report["config"] = {
        "url": config.url or None,
        "project": config.project or None,
        "ca_file": config.ca_file or None,
        "sources": config.sources,
        "labels": {
            "speckit_label": config.speckit_label,
            "feature_label": config.feature_label,
            "story_label": config.story_label,
            "task_label": config.task_label,
        },
        "mapping": {
            "feature_to_issue": config.feature_to_issue,
            "feature_to_milestone": config.feature_to_milestone,
            "priority_to_label": config.priority_to_label,
            "link_tasks_to_stories": config.link_tasks_to_stories,
        },
    }
    report["ready"] = False
    missing = [name for name, value in (("url", config.url), ("project", config.project)) if not value]
    if missing:
        report["missing"] = missing
        return

    token, source = resolve_token(config.url)
    report["token"] = {"found": bool(token), "source": source}
    if not token:
        return

    client = GitLabClient(config.url, config.project, token, config.ca_file)
    try:
        report["user"] = client.get_user().get("username")
        project = client.get_project()
    except GitLabError as exc:
        report["connection_error"] = str(exc)
        return
    permissions = project.get("permissions") or {}
    levels = [
        (permissions.get(scope) or {}).get("access_level") for scope in ("project_access", "group_access")
    ]
    access_level = max((level for level in levels if level), default=None)
    report["project"] = {
        "path": project.get("path_with_namespace"),
        "url": project.get("web_url"),
        "issues_enabled": project.get("issues_enabled", True),
        "access_level": access_level,
    }
    problems = []
    if not report["project"]["issues_enabled"]:
        problems.append("Issues are disabled in this project.")
    if access_level is not None and access_level < 15:
        problems.append(
            "The token's user needs at least the Planner or Reporter role to close issues and create milestones."
        )
    report["problems"] = problems
    report["ready"] = not problems


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


def reconcile(local: bool, gitlab: bool, synced: bool | None) -> str:
    """Decide which side's done/open state wins: "ok", "local_change" or "gitlab_change".

    `synced` is the state both sides agreed on at the last sync. The state is
    binary, so when the sides differ exactly one of them changed since then and
    there is no real conflict. Without that record (a feature issue linked by
    import-feature, or a hand-edited mapping) the local files win.
    """
    if local == gitlab:
        return "ok"
    if synced is None:
        return "local_change"
    return "local_change" if gitlab == synced else "gitlab_change"


def cmd_sync(args: argparse.Namespace, report: dict[str, Any]) -> None:
    feature = load_feature(args)
    config, client, mapping, dry = feature.config, feature.client, feature.mapping, args.dry_run
    actions: list[dict[str, Any]] = []
    report.update(
        feature_dir=feature.rel(), project=config.project, dry_run=dry, actions=actions, warnings=feature.warnings
    )

    def record(action: str, kind: str, ref_id: str, **details: Any) -> None:
        actions.append({"action": action, "kind": kind, "id": ref_id, **details})

    def save() -> None:
        if not dry:
            mapping.save()

    # 1. Create what is missing: milestone, feature, story and task issues.
    milestone_id = None
    if config.feature_to_milestone:
        milestone = client.find_milestone(feature.name)
        if milestone is None:
            if not dry:
                milestone = client.create_milestone(feature.name, f"spec-kit feature `{feature.rel()}`")
            record("create", "milestone", feature.name, url=(milestone or {}).get("web_url"))
        milestone_id = milestone["id"] if milestone else None

    task_type: str | None = "task"

    def create(kind: str, ref_id: str, title: str, description: str, labels: list[str], issue_type=None) -> dict:
        # In a dry run nothing is sent; the placeholder (iid None) lets the
        # linking and state logic below report what would happen.
        nonlocal task_type
        ref: dict[str, Any] = {"iid": None, "url": None}
        if not dry:
            try:
                issue = client.create_issue(title, description, labels, issue_type, milestone_id)
            except GitLabError as exc:
                # GitLab versions without the Task work item type reject issue_type=task.
                if not (issue_type and exc.status == 400 and "issue_type" in str(exc)):
                    raise
                feature.warnings.append(f"GitLab rejected issue_type={issue_type}; tasks are created as issues.")
                task_type = None
                issue = client.create_issue(title, description, labels, None, milestone_id)
            ref = {"iid": issue["iid"], "url": issue["web_url"]}
        record("create", kind, ref_id, title=title, **ref)
        if kind != "feature":
            ref["done"] = False  # sync record: new issues start open
        return ref

    if config.feature_to_issue and not mapping.feature and feature.spec is not None:
        mapping.feature = create(
            "feature",
            feature.name,
            feature.title,
            feature_description(feature),
            [config.speckit_label, config.feature_label],
        )
        save()

    stories = feature.spec.stories if feature.spec else []
    for story in stories:
        if story.id not in mapping.stories:
            mapping.stories[story.id] = create(
                "story",
                story.id,
                f"{story.id}: {story.title}",
                story_description(feature, story),
                issue_labels(config, config.story_label, story.priority),
            )
            save()

    story_priority = {story.id: story.priority for story in stories}
    for task in feature.tasks.tasks:
        if task.id not in mapping.tasks:
            mapping.tasks[task.id] = create(
                "task",
                task.id,
                f"{task.id}: {task.text}",
                task_description(feature, task),
                issue_labels(config, config.task_label, story_priority.get(task.story or "")),
                issue_type=task_type,
            )
            save()

    # 2. Link stories to the feature issue and tasks to their story.
    def link(kind: str, ref_id: str, ref: dict, parent_kind: str, parent_id: str, parent: dict) -> None:
        if parent["iid"] is not None and ref.get("linked_to") == parent["iid"]:
            return
        if not dry:
            client.link_issues(ref["iid"], parent["iid"])
            ref["linked_to"] = parent["iid"]
            save()
        record("link", kind, ref_id, iid=ref["iid"], to=f"{parent_kind} {parent_id}", to_iid=parent["iid"])

    if mapping.feature:
        for story_id, ref in mapping.stories.items():
            link("story", story_id, ref, "feature", feature.name, mapping.feature)
    if config.link_tasks_to_stories:
        for task in feature.tasks.tasks:
            story_ref = mapping.stories.get(task.story or "")
            if story_ref:
                link("task", task.id, mapping.tasks[task.id], "story", task.story or "", story_ref)

    # 3. Reconcile done/open: whichever side changed since the last sync wins.
    remote = client.get_issues(mapping.all_iids())

    def gitlab_done(kind: str, ref_id: str, ref: dict) -> bool | None:
        if ref["iid"] is None:
            return False  # created in this dry run
        issue = remote.get(ref["iid"])
        if issue is None:
            feature.warnings.append(
                f"{kind} {ref_id}: issue #{ref['iid']} not found in {config.project} (deleted or moved?)."
            )
            return None
        return issue["state"] == "closed"

    def set_gitlab(kind: str, ref_id: str, ref: dict, done: bool) -> None:
        if not dry:
            client.set_issue_state(ref["iid"], "close" if done else "reopen")
        record("close" if done else "reopen", kind, ref_id, iid=ref["iid"], url=ref.get("url"))

    checkboxes: dict[str, bool] = {}
    for task in feature.tasks.tasks:
        ref = mapping.tasks[task.id]
        remote_done = gitlab_done("task", task.id, ref)
        if remote_done is None:
            continue
        decision = reconcile(task.done, remote_done, ref.get("done"))
        if decision == "local_change":
            set_gitlab("task", task.id, ref, task.done)
        elif decision == "gitlab_change":
            checkboxes[task.id] = task.done = remote_done
            record("check" if remote_done else "uncheck", "task", task.id, iid=ref["iid"])
        ref["done"] = task.done
    if checkboxes and not dry:
        tasks_path = feature.dir / "tasks.md"
        write_text(tasks_path, set_checkboxes(read_text(tasks_path), checkboxes)[0])

    for story in stories:
        # Stories have no checkbox: they are done when all their tasks are.
        # A manual close/reopen in GitLab is left alone until the tasks change.
        ref, done = mapping.stories[story.id], feature.story_done(story.id)
        remote_done = gitlab_done("story", story.id, ref) if done is not None else None
        if done is None or remote_done is None:
            continue
        decision = reconcile(done, remote_done, ref.get("done"))
        if decision == "local_change":
            set_gitlab("story", story.id, ref, done)
        if decision != "gitlab_change":
            ref["done"] = done

    if mapping.feature:
        remote_done = gitlab_done("feature", feature.name, mapping.feature)
        all_done = feature.all_done()
        out_of_sync = remote_done is not None and all_done is not None and remote_done != all_done
        if out_of_sync and args.sync_feature:
            set_gitlab("feature", feature.name, mapping.feature, bool(all_done))
            remote_done, out_of_sync = all_done, False
        report["feature"] = {
            "iid": mapping.feature["iid"],
            "url": mapping.feature.get("url"),
            "state": None if remote_done is None else ("closed" if remote_done else "opened"),
            "all_tasks_done": all_done,
            "out_of_sync": out_of_sync,
        }

    known = {story.id for story in stories} | {task.id for task in feature.tasks.tasks}
    orphaned = sorted(i for i in list(mapping.stories) + list(mapping.tasks) if i not in known)
    if orphaned:
        feature.warnings.append(
            f"Mapped but no longer in spec.md/tasks.md (left untouched in GitLab): {', '.join(orphaned)}"
        )
    report["summary"] = {
        name: sum(1 for a in actions if a["action"] == name)
        for name in ("create", "link", "close", "reopen", "check", "uncheck")
    }
    save()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace, report: dict[str, Any]) -> None:
    feature = load_feature(args)
    mapping = feature.mapping
    remote = feature.client.get_issues(mapping.all_iids())

    def row(ref_id: str, title: str, ref: dict | None, done: bool | None) -> dict[str, Any]:
        issue = remote.get(ref["iid"]) if ref else None
        state = issue["state"] if issue else None
        if ref is None:
            sync = "not_pushed"
        elif issue is None:
            sync = "missing"
        elif done is None:
            sync = "ok"
        else:
            sync = reconcile(done, state == "closed", ref.get("done"))
        return {
            "id": ref_id,
            "title": title,
            "iid": ref["iid"] if ref else None,
            "url": (issue or {}).get("web_url") or (ref or {}).get("url"),
            "local": None if done is None else ("done" if done else "open"),
            "gitlab": state,
            "assignees": [a.get("username") for a in (issue or {}).get("assignees") or []],
            "sync": sync,
        }

    stories = feature.spec.stories if feature.spec else []
    tasks = feature.tasks.tasks
    task_rows = [row(t.id, t.text, mapping.tasks.get(t.id), t.done) for t in tasks]
    feature_row = None
    if mapping.feature:
        feature_row = row(feature.name, feature.title, mapping.feature, feature.all_done())
        if feature_row["sync"] == "local_change":
            feature_row["sync"] = "out_of_sync"
    known = {s.id for s in stories} | {t.id for t in tasks}
    report.update(
        feature_dir=feature.rel(),
        project=feature.config.project,
        feature=feature_row,
        stories=[row(s.id, s.title, mapping.stories.get(s.id), feature.story_done(s.id)) for s in stories],
        tasks=task_rows,
        orphaned=sorted(i for i in list(mapping.stories) + list(mapping.tasks) if i not in known),
        summary={
            "tasks": len(tasks),
            "done_locally": sum(1 for t in tasks if t.done),
            "closed_in_gitlab": sum(1 for r in task_rows if r["gitlab"] == "closed"),
            "not_pushed": sum(1 for r in task_rows if r["sync"] == "not_pushed"),
            "local_changes": sum(1 for r in task_rows if r["sync"] == "local_change"),
            "gitlab_changes": sum(1 for r in task_rows if r["sync"] == "gitlab_change"),
        },
        warnings=feature.warnings,
    )


# ---------------------------------------------------------------------------
# import-feature
# ---------------------------------------------------------------------------

_ISSUE_URL = re.compile(r"^https?://[^/]+/(?P<project>.+?)/-/(?:issues|work_items)/(?P<iid>\d+)(?:[/?#].*)?$")


def parse_issue_ref(value: str, project: str) -> int:
    value = value.strip()
    match = re.fullmatch(r"#?(\d+)", value)
    if match:
        return int(match.group(1))
    match = _ISSUE_URL.match(value)
    if not match:
        raise UsageError(f"Expected an issue number (232, #232) or issue URL, got {value!r}")
    if match.group("project").lower() != project.lower():
        raise UsageError(f"{value} is in project {match.group('project')!r}, but the config uses {project!r}.")
    return int(match.group("iid"))


def cmd_import_feature(args: argparse.Namespace, report: dict[str, Any]) -> None:
    root = find_project_root()
    config, client = connect(root)
    iid = parse_issue_ref(args.issue, config.project)
    issue = client.get_issue(iid)
    report["issue"] = {
        "iid": iid,
        "title": issue.get("title"),
        "state": issue.get("state"),
        "url": issue.get("web_url"),
        "labels": issue.get("labels", []),
        "description": issue.get("description") or "",
    }
    report["linked"] = False
    try:
        feature_dir = resolve_feature_dir(root, args.feature_dir)
    except FeatureError:
        report["feature_dir"] = None
        return
    report["feature_dir"] = _relative(feature_dir, root)
    mapping = Mapping.load(feature_dir)
    mapping.check_project(config.project)
    report["current_feature_issue"] = mapping.feature
    if not args.link:
        return
    if mapping.feature and mapping.feature["iid"] != iid and not args.replace:
        raise UsageError(
            f"{report['feature_dir']} is already linked to feature issue #{mapping.feature['iid']}. "
            f"Pass --replace to use #{iid} instead."
        )
    if not mapping.feature or mapping.feature["iid"] != iid:
        mapping.feature = {"iid": iid, "url": issue.get("web_url")}
    linked = []
    for story_id, ref in mapping.stories.items():
        if ref.get("linked_to") != iid:
            client.link_issues(ref["iid"], iid)
            ref["linked_to"] = iid
            linked.append(story_id)
    mapping.save()
    report.update(linked=True, linked_stories=linked)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitlab.py", description="Sync a spec-kit feature with GitLab issues. Prints JSON."
    )
    parser.add_argument("--version", action="version", version=f"spec-kit-gitlab {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, handler: Callable, help_text: str, feature: bool = True) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        if feature:
            sub.add_argument(
                "--feature-dir",
                help="Feature directory (default: $SPECIFY_FEATURE_DIRECTORY or .specify/feature.json)",
            )
        return sub

    init = add("init", cmd_init, "Show or write the GitLab settings and check access.", feature=False)
    init.add_argument("--url", help="GitLab URL, e.g. https://gitlab.example.com")
    init.add_argument("--project", help="Project path, e.g. group/project")
    init.add_argument("--ca-file", help="PEM CA bundle for a self-signed or internal certificate")

    sync = add("sync", cmd_sync, "Create and link missing issues, then reconcile done/open states both ways.")
    sync.add_argument("--dry-run", action="store_true", help="Report what would change without changing it")
    sync.add_argument(
        "--sync-feature", action="store_true", help="Also close/reopen the feature issue to match the tasks"
    )

    add("status", cmd_status, "Compare spec-kit and GitLab state without changing anything.")

    imp = add("import-feature", cmd_import_feature, "Use an existing GitLab issue as the feature issue.")
    imp.add_argument("issue", help="Issue number (232, #232) or issue URL")
    imp.add_argument("--link", action="store_true", help="Record the issue as the feature issue and link stories")
    imp.add_argument("--replace", action="store_true", help="With --link: replace a different feature issue")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="backslashreplace")
    args = build_parser().parse_args(argv)
    report: dict[str, Any] = {"command": args.command}
    try:
        args.handler(args, report)
    except (ConfigError, FeatureError, GitLabError, UsageError) as exc:
        report["error"] = str(exc)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
