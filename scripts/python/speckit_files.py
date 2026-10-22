"""Reading and writing spec-kit feature files: spec.md, tasks.md and the issue mapping.

Standard library only and compatible with Python 3.9 (see gitlab_client.py).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAPPING_FILE = ".gitlab-mapping.json"


class FeatureError(Exception):
    """The spec-kit project or feature directory could not be resolved."""


# ---------------------------------------------------------------------------
# Project and feature resolution (mirrors spec-kit core's check_prerequisites)
# ---------------------------------------------------------------------------


def _find_specify_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".specify").is_dir():
            return candidate
    return None


def find_project_root() -> Path:
    override = os.environ.get("SPECIFY_INIT_DIR")
    if override:
        root = Path(override).resolve()
        if not (root / ".specify").is_dir():
            raise FeatureError(f"SPECIFY_INIT_DIR is not a spec-kit project: {root}")
        return root
    root = _find_specify_root(Path.cwd()) or _find_specify_root(Path(__file__).parent)
    if root is None:
        raise FeatureError("Not inside a spec-kit project (no .specify/ directory found).")
    return root


def resolve_feature_dir(root: Path, explicit: str | None = None) -> Path:
    """Feature directory from --feature-dir, $SPECIFY_FEATURE_DIRECTORY or .specify/feature.json."""
    raw = explicit or os.environ.get("SPECIFY_FEATURE_DIRECTORY", "")
    if not raw:
        feature_json = root / ".specify" / "feature.json"
        try:
            raw = json.loads(feature_json.read_text(encoding="utf-8")).get("feature_directory", "")
        except (OSError, ValueError, AttributeError):
            raw = ""
    if not raw:
        raise FeatureError(
            "No active feature. Run the spec-kit specify command first, "
            "or pass --feature-dir specs/<feature>."
        )
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    if not path.is_dir():
        raise FeatureError(f"Feature directory not found: {path}")
    return path


# ---------------------------------------------------------------------------
# Markdown scanning helpers
# ---------------------------------------------------------------------------


def _code_lines(lines: list[str]) -> list[bool]:
    """Flag lines inside fenced code blocks or HTML comments (never parsed as structure)."""
    flags: list[bool] = []
    fence: str | None = None
    in_comment = False
    for line in lines:
        stripped = line.strip()
        if fence:
            flags.append(True)
            if stripped.startswith(fence):
                fence = None
            continue
        if in_comment:
            flags.append(True)
            in_comment = "-->" not in stripped
            continue
        fence_match = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence_match:
            fence = fence_match.group(1)
            flags.append(True)
            continue
        if stripped.startswith("<!--") and "-->" not in stripped:
            in_comment = True
            flags.append(True)
            continue
        flags.append(False)
    return flags


# ---------------------------------------------------------------------------
# spec.md
# ---------------------------------------------------------------------------

# spec-kit template: "### User Story 1 - Title (Priority: P1)"
_STORY = re.compile(r"^###\s+User\s+Story\s+(\d+)\s*[-–—:]\s*(.+?)\s*$", re.IGNORECASE)
_PRIORITY = re.compile(r"\s*\(\s*Priority:\s*(P\d+)\s*\)", re.IGNORECASE)


@dataclass
class Story:
    id: str
    title: str
    priority: str | None
    body: str


@dataclass
class Spec:
    title: str | None
    stories: list[Story]
    overview: str


def parse_spec(text: str) -> Spec:
    lines = text.splitlines()
    skip = _code_lines(lines)
    title: str | None = None
    stories: list[Story] = []
    overview: list[str] = []
    current: list[str] | None = None
    for line, is_code in zip(lines, skip):
        stripped = line.strip()
        if not is_code:
            match = _STORY.match(stripped)
            if match:
                if current is not None:
                    stories[-1].body = "\n".join(current).strip()
                heading = match.group(2)
                priority_match = _PRIORITY.search(heading)
                priority = priority_match.group(1).upper() if priority_match else None
                heading = _PRIORITY.sub("", heading).strip()
                stories.append(Story(f"US{int(match.group(1))}", heading, priority, ""))
                current = []
                continue
            if current is not None and (re.match(r"^#{1,3}\s", stripped) or stripped == "---"):
                stories[-1].body = "\n".join(current).strip()
                current = None
                if stripped == "---":
                    continue
            if title is None and re.match(r"^#\s+\S", stripped):
                title = re.sub(r"^#\s+(Feature Specification:\s*)?", "", stripped, flags=re.IGNORECASE).strip()
                if re.fullmatch(r"\[.*\]", title):
                    title = None
                continue
        (current if current is not None else overview).append(line)
    if current is not None:
        stories[-1].body = "\n".join(current).strip()
    unique: dict[str, Story] = {}
    for story in stories:
        unique.setdefault(story.id, story)
    return Spec(title, list(unique.values()), re.sub(r"\n{3,}", "\n\n", "\n".join(overview)).strip())


# ---------------------------------------------------------------------------
# tasks.md
# ---------------------------------------------------------------------------

_TASK = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<id>T\d+)\b(?P<rest>.*)$")
_TAG = re.compile(r"^\s*\[([^\]]+)\]")


@dataclass
class Task:
    id: str
    done: bool
    text: str
    story: str | None = None
    parallel: bool = False


@dataclass
class TaskList:
    tasks: list[Task] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)

    def by_id(self) -> dict[str, Task]:
        return {t.id: t for t in self.tasks}


def parse_tasks(text: str) -> TaskList:
    lines = text.splitlines()
    result = TaskList()
    seen: set[str] = set()
    for line, is_code in zip(lines, _code_lines(lines)):
        match = None if is_code else _TASK.match(line)
        if not match:
            continue
        task = Task(id=match.group("id"), done=match.group("mark") != " ", text="")
        rest = match.group("rest")
        while True:
            tag = _TAG.match(rest)
            if not tag:
                break
            value = tag.group(1).strip()
            if value.upper() == "P":
                task.parallel = True
            elif re.fullmatch(r"US\d+", value, re.IGNORECASE):
                task.story = f"US{int(value[2:])}"
            else:
                break
            rest = rest[tag.end() :]
        task.text = rest.strip()
        if task.id in seen:
            result.duplicates.append(task.id)
            continue
        seen.add(task.id)
        result.tasks.append(task)
    return result


def set_checkboxes(text: str, done: dict[str, bool]) -> tuple[str, list[str]]:
    """Set task checkboxes to the given states. Returns the new text and the changed task IDs."""
    lines = text.splitlines(keepends=True)
    changed: list[str] = []
    seen: set[str] = set()
    for i, (line, is_code) in enumerate(zip(lines, _code_lines(lines))):
        match = None if is_code else _TASK.match(line.rstrip("\r\n"))
        if not match or match.group("id") in seen:
            continue
        task_id = match.group("id")
        seen.add(task_id)
        if task_id not in done or (match.group("mark") != " ") == done[task_id]:
            continue
        mark = "X" if done[task_id] else " "
        lines[i] = line[: match.start("mark")] + mark + line[match.end("mark") :]
        changed.append(task_id)
    return "".join(lines), changed


def read_text(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Mapping: spec-kit IDs -> GitLab issues
# ---------------------------------------------------------------------------


class Mapping:
    """`.gitlab-mapping.json` in the feature directory: which issue belongs to which ID."""

    def __init__(self, feature_dir: Path, data: dict[str, Any]):
        self.path = feature_dir / MAPPING_FILE
        self.data = {
            "project": data.get("project"),
            "feature": data.get("feature"),
            "stories": dict(data.get("stories") or {}),
            "tasks": dict(data.get("tasks") or {}),
        }

    @classmethod
    def load(cls, feature_dir: Path) -> "Mapping":
        path = feature_dir / MAPPING_FILE
        if path.is_file():
            try:
                return cls(feature_dir, json.loads(path.read_text(encoding="utf-8")))
            except ValueError as exc:
                raise FeatureError(f"{path} is not valid JSON: {exc}") from None
        return cls(feature_dir, {})

    @property
    def feature(self) -> dict | None:
        return self.data["feature"]

    @feature.setter
    def feature(self, value: dict | None) -> None:
        self.data["feature"] = value

    @property
    def stories(self) -> dict[str, dict]:
        return self.data["stories"]

    @property
    def tasks(self) -> dict[str, dict]:
        return self.data["tasks"]

    def all_iids(self) -> list[int]:
        refs = list(self.stories.values()) + list(self.tasks.values())
        if self.feature:
            refs.append(self.feature)
        return [ref["iid"] for ref in refs if ref.get("iid") is not None]

    def check_project(self, project: str) -> None:
        mapped = self.data.get("project")
        if mapped and mapped.lower() != project.lower():
            raise FeatureError(
                f"{self.path.name} belongs to GitLab project {mapped!r}, but the config points at "
                f"{project!r}. Fix the config, or remove the mapping file to start over."
            )
        self.data["project"] = project

    def save(self) -> None:
        if not self.path.is_file() and not (self.feature or self.stories or self.tasks):
            return
        stories = dict(sorted(self.stories.items(), key=lambda kv: _id_sort_key(kv[0])))
        tasks = dict(sorted(self.tasks.items(), key=lambda kv: _id_sort_key(kv[0])))
        data = dict(self.data, stories=stories, tasks=tasks)
        write_text(self.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _id_sort_key(value: str) -> tuple[str, int]:
    match = re.match(r"^([A-Za-z]+)(\d+)$", value)
    return (match.group(1), int(match.group(2))) if match else (value, 0)
