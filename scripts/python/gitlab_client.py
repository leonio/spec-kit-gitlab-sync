"""Configuration loading and a minimal GitLab REST client.

Standard library only: spec-kit runs extension scripts with whichever
interpreter it finds (the project's .venv, python3 or python), so no
third-party package is guaranteed to be importable. Keep this file
compatible with Python 3.9.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERSION = "1.0.0"

EXTENSION_DIR = Path(".specify") / "extensions" / "gitlab-sync"
CONFIG_FILE = "gitlab-sync-config.yml"
LOCAL_CONFIG_FILE = "gitlab-sync-config.local.yml"

DEFAULTS: dict[str, dict[str, Any]] = {
    "gitlab": {"url": "", "project": "", "ca_file": ""},
    "labels": {
        "speckit_label": "spec-kit",
        "feature_label": "feature",
        "story_label": "user-story",
        "task_label": "task",
    },
    "mapping": {
        "feature_to_issue": True,
        "feature_to_milestone": True,
        "priority_to_label": True,
        "link_tasks_to_stories": True,
    },
}

# Short aliases; every setting can also be set as SPECKIT_GITLAB_SYNC_<SECTION>_<KEY>
# (spec-kit's convention for extension config), e.g. SPECKIT_GITLAB_SYNC_LABELS_TASK_LABEL.
ENV_ALIASES = {
    ("gitlab", "url"): "GITLAB_URL",
    ("gitlab", "project"): "GITLAB_PROJECT",
    ("gitlab", "ca_file"): "GITLAB_CA_FILE",
}


class ConfigError(Exception):
    """The extension configuration is missing or invalid."""


class GitLabError(Exception):
    """A GitLab API request failed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Minimal YAML subset (nested mappings of scalars) for gitlab-sync-config.yml
# ---------------------------------------------------------------------------

_KEY_LINE = re.compile(r"^( *)([A-Za-z0-9_.-]+):(?:[ \t]+(.*))?$")


def _comment_start(line: str) -> int:
    """Index where a trailing `# comment` starts, ignoring `#` inside quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return i
    return -1


def _strip_comment(line: str) -> str:
    idx = _comment_start(line)
    return line if idx < 0 else line[:idx]


def _scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    lowered = value.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("", "null", "~"):
        return None
    if value == "{}":
        return {}
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_simple_yaml(text: str, source: str = "<config>") -> dict[str, Any]:
    """Parse nested `key: value` mappings, the only YAML the config uses."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError(f"{source}:{lineno}: tabs are not allowed for indentation")
        match = _KEY_LINE.match(_strip_comment(raw).rstrip())
        if not match:
            raise ConfigError(f"{source}:{lineno}: unsupported YAML: {raw.strip()}")
        indent, key, value = len(match.group(1)), match.group(2), match.group(3)
        while indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value is None or not value.strip():
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _scalar(value)
    return root


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_yaml_values(text: str, section: str, values: dict[str, str]) -> str:
    """Set `section.key` scalars in a config file, keeping comments and layout."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if re.match(rf"^{re.escape(section)}:\s*(#.*)?$", line)),
        None,
    )
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{section}:")
        start = len(lines) - 1
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] == " "):
        end += 1
    for key, value in values.items():
        new_line = f"  {key}: {_quote(value)}"
        for i in range(start + 1, end):
            if re.match(rf"^\s+{re.escape(key)}:(\s|$)", lines[i]):
                comment = _comment_start(lines[i])
                if comment > 0:
                    new_line += "  " + lines[i][comment:]
                lines[i] = new_line
                break
        else:
            last = max((i for i in range(start + 1, end) if lines[i].strip()), default=start)
            lines.insert(last + 1, new_line)
            end += 1
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class Config:
    url: str
    project: str
    ca_file: str
    speckit_label: str
    feature_label: str
    story_label: str
    task_label: str
    feature_to_issue: bool
    feature_to_milestone: bool
    priority_to_label: bool
    link_tasks_to_stories: bool
    sources: list[str]

    def require_connection(self) -> None:
        if not self.url or not self.project:
            missing = " and ".join(
                name for name, value in (("url", self.url), ("project", self.project)) if not value
            )
            raise ConfigError(
                f"GitLab {missing} not configured. Run the speckit.gitlab-sync.init command, "
                "or set GITLAB_URL and GITLAB_PROJECT."
            )


def config_dir(root: Path) -> Path:
    return root / EXTENSION_DIR


def _coerce(value: Any, default: Any, where: str) -> Any:
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{where} must be true or false, got {value!r}")
        return value
    return str(value)


def load_config(root: Path) -> Config:
    merged = copy.deepcopy(DEFAULTS)
    sources: list[str] = []
    for name in (CONFIG_FILE, LOCAL_CONFIG_FILE):
        path = config_dir(root) / name
        if not path.is_file():
            continue
        data = parse_simple_yaml(path.read_text(encoding="utf-8"), str(path))
        sources.append(str(path.relative_to(root)))
        for section, defaults in DEFAULTS.items():
            values = data.get(section)
            if not isinstance(values, dict):
                continue
            for key, default in defaults.items():
                value = values.get(key)
                if value is None or value == {}:
                    continue
                merged[section][key] = _coerce(value, default, f"{path}: {section}.{key}")
    for section, defaults in DEFAULTS.items():
        for key, default in defaults.items():
            for var in (ENV_ALIASES.get((section, key)), f"SPECKIT_GITLAB_SYNC_{section}_{key}".upper()):
                raw = os.environ.get(var) if var else None
                if raw:
                    value = _scalar(raw) if isinstance(default, bool) else raw
                    merged[section][key] = _coerce(value, default, f"${var}")
                    sources.append(f"${var}")

    url = merged["gitlab"]["url"].strip().rstrip("/")
    if url and not re.match(r"^https?://[^/]+", url):
        raise ConfigError(f"GitLab url must start with https:// or http://, got {url!r}")
    return Config(
        url=url,
        project=merged["gitlab"]["project"].strip().strip("/"),
        ca_file=merged["gitlab"]["ca_file"].strip(),
        sources=sources,
        **merged["labels"],
        **merged["mapping"],
    )


def resolve_token(url: str) -> tuple[str | None, str | None]:
    """Find an API token: $GITLAB_TOKEN first, then a token stored by `glab auth login`."""
    if os.environ.get("GITLAB_TOKEN"):
        return os.environ["GITLAB_TOKEN"], "GITLAB_TOKEN"
    glab = shutil.which("glab")
    host = urllib.parse.urlparse(url).netloc
    if glab and host:
        try:
            result = subprocess.run(
                [glab, "config", "get", "token", "--host", host],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None, None
        token = result.stdout.strip()
        if result.returncode == 0 and token:
            return token, "glab"
    return None, None


# ---------------------------------------------------------------------------
# REST client
# ---------------------------------------------------------------------------


def _error_hint(status: int) -> str:
    return {
        401: " Check that GITLAB_TOKEN is valid and has the `api` scope.",
        403: " The token's user lacks permission for this action in the project.",
        404: " Check the project path and that the token's user can access it.",
    }.get(status, "")


class GitLabClient:
    def __init__(self, url: str, project: str, token: str, ca_file: str = "", timeout: float = 30):
        self.api = url.rstrip("/") + "/api/v4"
        self.project = project
        self.token = token
        self.timeout = timeout
        self._project_ref = "projects/" + urllib.parse.quote(project, safe="")
        self._project_id: int | None = None
        try:
            context = ssl.create_default_context(cafile=ca_file or None)
        except (OSError, ssl.SSLError) as exc:
            raise ConfigError(f"Cannot load CA file {ca_file!r}: {exc}") from None
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        url = f"{self.api}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("PRIVATE-TOKEN", self.token)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"spec-kit-gitlab/{VERSION}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                headers = resp.headers
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(detail)
                detail = payload.get("message") or payload.get("error") or detail
            except (ValueError, AttributeError):
                pass
            if not isinstance(detail, str):
                detail = json.dumps(detail)
            raise GitLabError(
                f"{method} {path} failed with HTTP {exc.code}: {detail.strip()[:500]}.{_error_hint(exc.code)}",
                status=exc.code,
            ) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            hint = ""
            if isinstance(reason, ssl.SSLCertVerificationError):
                hint = " Set gitlab.ca_file in the config (or SSL_CERT_FILE) to your CA bundle."
            raise GitLabError(f"Cannot reach {self.api}: {reason}.{hint}") from None
        except (TimeoutError, OSError) as exc:
            raise GitLabError(f"Cannot reach {self.api}: {exc}") from None
        return (json.loads(raw) if raw else None), headers

    def get(self, path: str, params: dict | None = None):
        return self.request("GET", path, params=params)[0]

    def get_all(self, path: str, params: dict | None = None) -> list:
        params = dict(params or {}, per_page=100)
        page: str | None = "1"
        items: list = []
        while page:
            params["page"] = page
            data, headers = self.request("GET", path, params=params)
            items.extend(data or [])
            page = headers.get("X-Next-Page") or None
        return items

    # -- project-scoped helpers ------------------------------------------------

    def get_user(self) -> dict:
        return self.get("user")

    def get_project(self) -> dict:
        project = self.get(self._project_ref)
        self._project_id = project["id"]
        return project

    @property
    def project_id(self) -> int:
        if self._project_id is None:
            self.get_project()
        return self._project_id  # type: ignore[return-value]

    def find_milestone(self, title: str) -> dict | None:
        milestones = self.get_all(f"{self._project_ref}/milestones", {"title": title})
        return next((m for m in milestones if m.get("title") == title), None)

    def create_milestone(self, title: str, description: str) -> dict:
        return self.request(
            "POST", f"{self._project_ref}/milestones", body={"title": title, "description": description}
        )[0]

    def create_issue(
        self,
        title: str,
        description: str,
        labels: list[str],
        issue_type: str | None = None,
        milestone_id: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {"title": title[:255], "description": description, "labels": ",".join(labels)}
        if issue_type:
            body["issue_type"] = issue_type
        if milestone_id:
            body["milestone_id"] = milestone_id
        return self.request("POST", f"{self._project_ref}/issues", body=body)[0]

    def get_issue(self, iid: int) -> dict:
        return self.get(f"{self._project_ref}/issues/{iid}")

    def get_issues(self, iids: list[int]) -> dict[int, dict]:
        """Fetch many issues by iid; missing (deleted or moved) issues are absent."""
        found: dict[int, dict] = {}
        unique = sorted(set(iids))
        for i in range(0, len(unique), 100):
            chunk = unique[i : i + 100]
            for issue in self.get_all(f"{self._project_ref}/issues", {"iids[]": chunk, "state": "all"}):
                found[issue["iid"]] = issue
        return found

    def set_issue_state(self, iid: int, state_event: str) -> dict:
        return self.request(
            "PUT", f"{self._project_ref}/issues/{iid}", body={"state_event": state_event}
        )[0]

    def link_issues(self, iid: int, target_iid: int) -> bool:
        """Create a `relates_to` link. Returns False if the issues were already linked."""
        try:
            self.request(
                "POST",
                f"{self._project_ref}/issues/{iid}/links",
                body={"target_project_id": self.project_id, "target_issue_iid": target_iid},
            )
        except GitLabError as exc:
            if exc.status == 409:
                return False
            raise
        return True
