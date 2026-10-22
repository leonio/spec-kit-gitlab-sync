"""An in-process fake of the GitLab REST API endpoints the extension uses.

Behaviour follows the GitLab API docs: offset pagination with X-Next-Page,
`iids[]` filtering, labels created on the fly, `state_event` close/reopen,
and HTTP 409 when issues are already linked.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeGitLab:
    def __init__(self, project: str = "group/project", token: str = "secret", max_per_page: int = 100):
        self.token = token
        self.max_per_page = max_per_page
        self.reject_task_type = False
        self.issues: dict[int, dict] = {}
        self.milestones: list[dict] = []
        self.links: set[frozenset[int]] = set()
        self.requests: list[tuple[str, str]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.project = {
            "id": 42,
            "path_with_namespace": project,
            "web_url": f"{self.url}/{project}",
            "issues_enabled": True,
            "permissions": {"project_access": {"access_level": 30}, "group_access": None},
        }
        self._thread = threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- helpers for tests -------------------------------------------------------

    def add_issue(self, title: str, state: str = "opened", issue_type: str = "issue") -> dict:
        iid = len(self.issues) + 1
        issue = {
            "id": 1000 + iid,
            "iid": iid,
            "project_id": self.project["id"],
            "title": title,
            "description": "",
            "labels": [],
            "state": state,
            "issue_type": issue_type,
            "milestone": None,
            "assignees": [],
            "web_url": f"{self.project['web_url']}/-/issues/{iid}",
        }
        self.issues[iid] = issue
        return issue

    def writes(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if r[0] != "GET"]

    # -- request handling --------------------------------------------------------

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _send(self, status: int, payload, headers: dict | None = None) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method: str) -> None:
                split = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(split.query, keep_blank_values=True)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else {}
                fake.requests.append((method, split.path))
                if self.headers.get("PRIVATE-TOKEN") != fake.token:
                    return self._send(401, {"message": "401 Unauthorized"})
                parts = [urllib.parse.unquote(p) for p in split.path.split("/")[3:]]  # after /api/v4/
                status, payload, headers = fake.route(method, parts, query, body)
                self._send(status, payload, headers)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

        return Handler

    def _page(self, items: list, query: dict):
        per_page = min(int(query.get("per_page", ["20"])[0]), self.max_per_page)
        page = int(query.get("page", ["1"])[0])
        chunk = items[(page - 1) * per_page : page * per_page]
        more = page * per_page < len(items)
        return 200, chunk, {"X-Page": str(page), "X-Next-Page": str(page + 1) if more else ""}

    def route(self, method: str, parts: list[str], query: dict, body: dict):
        not_found = (404, {"message": "404 Not Found"}, None)
        if parts == ["user"] and method == "GET":
            return 200, {"id": 1, "username": "tester"}, None
        if len(parts) < 2 or parts[0] != "projects":
            return not_found
        if parts[1] not in (self.project["path_with_namespace"], str(self.project["id"])):
            return 404, {"message": "404 Project Not Found"}, None
        rest = parts[2:]

        if rest == [] and method == "GET":
            return 200, self.project, None

        if rest == ["milestones"]:
            if method == "GET":
                title = query.get("title", [None])[0]
                return self._page([m for m in self.milestones if title in (None, m["title"])], query)
            if method == "POST":
                n = len(self.milestones) + 1
                milestone = {
                    "id": 500 + n,
                    "iid": n,
                    "title": body["title"],
                    "description": body.get("description", ""),
                    "web_url": f"{self.project['web_url']}/-/milestones/{n}",
                }
                self.milestones.append(milestone)
                return 201, milestone, None

        if rest == ["issues"]:
            if method == "GET":
                wanted = {int(i) for i in query.get("iids[]", [])}
                issues = [i for iid, i in sorted(self.issues.items()) if not wanted or iid in wanted]
                return self._page(issues, query)
            if method == "POST":
                if not body.get("title"):
                    return 400, {"error": "title is missing"}, None
                issue_type = body.get("issue_type", "issue")
                if issue_type not in ("issue", "incident", "test_case", "task") or (
                    issue_type == "task" and self.reject_task_type
                ):
                    return 400, {"error": "issue_type does not have a valid value"}, None
                milestone = None
                if "milestone_id" in body:
                    milestone = next((m for m in self.milestones if m["id"] == body["milestone_id"]), None)
                    if milestone is None:
                        return 400, {"message": {"milestone": ["is invalid"]}}, None
                issue = self.add_issue(body["title"], issue_type=issue_type)
                issue.update(
                    description=body.get("description", ""),
                    labels=[label for label in body.get("labels", "").split(",") if label],
                    milestone=milestone,
                )
                return 201, issue, None

        if len(rest) >= 2 and rest[0] == "issues" and rest[1].isdigit():
            issue = self.issues.get(int(rest[1]))
            if issue is None:
                return not_found
            if rest[2:] == [] and method == "GET":
                return 200, issue, None
            if rest[2:] == [] and method == "PUT":
                event = body.get("state_event")
                if event == "close":
                    issue["state"] = "closed"
                elif event == "reopen":
                    issue["state"] = "opened"
                return 200, issue, None
            if rest[2:] == ["links"] and method == "POST":
                if body.get("target_project_id") not in (self.project["id"], self.project["path_with_namespace"]):
                    return 404, {"message": "404 Project Not Found"}, None
                target = int(body["target_issue_iid"])
                if target not in self.issues:
                    return not_found
                pair = frozenset((issue["iid"], target))
                if pair in self.links:
                    return 409, {"message": "Issue(s) already assigned"}, None
                self.links.add(pair)
                link = {"source_issue": issue, "target_issue": self.issues[target], "link_type": "relates_to"}
                return 201, link, None
        return not_found
