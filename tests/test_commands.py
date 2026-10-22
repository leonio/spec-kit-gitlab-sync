import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gitlab  # noqa: E402
from fake_gitlab import FakeGitLab  # noqa: E402

SPEC = """# Feature Specification: User Authentication

**Input**: User description: "Let users sign in"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sign in with email (Priority: P1)

A registered user signs in. Ünïcödé is fine.

---

### User Story 2 - Reset password (Priority: P2)

A user resets a forgotten password.

---

### Edge Cases

- Locked accounts
"""

TASKS = """# Tasks: User Authentication

## Phase 1: Setup

- [ ] T001 Create project structure
- [X] T002 [P] Configure linting

## Phase 3: User Story 1 - Sign in with email (Priority: P1)

- [ ] T003 [P] [US1] Create User model in src/models/user.py
- [X] T004 [US1] Implement sign-in endpoint

## Phase 4: User Story 2 - Reset password (Priority: P2)

- [ ] T005 [US2] Implement reset flow
"""

FEATURE = "specs/001-user-auth"
NO_ACTIONS = {"create": 0, "link": 0, "close": 0, "reopen": 0, "check": 0, "uncheck": 0}


class CommandTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitLab()
        self.addCleanup(self.fake.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / ".specify").mkdir()
        (self.root / ".specify" / "feature.json").write_text(json.dumps({"feature_directory": FEATURE}))
        self.feature_dir = self.root / FEATURE
        self.feature_dir.mkdir(parents=True)
        self.write("spec.md", SPEC)
        self.write("tasks.md", TASKS)
        env = {
            "SPECIFY_INIT_DIR": str(self.root),
            "GITLAB_URL": self.fake.url,
            "GITLAB_PROJECT": "group/project",
            "GITLAB_TOKEN": "secret",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in ("SPECIFY_FEATURE_DIRECTORY", "GITLAB_CA_FILE"):
            os.environ.pop(var, None)

    def write(self, name, text):
        (self.feature_dir / name).write_bytes(text.encode("utf-8"))  # no newline translation on Windows

    def read(self, name):
        return (self.feature_dir / name).read_text(encoding="utf-8")

    def mapping(self):
        return json.loads(self.read(".gitlab-mapping.json"))

    def run_cli(self, *argv, expect=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gitlab.main(list(argv))
        self.assertEqual(code, expect, f"stdout={out.getvalue()} stderr={err.getvalue()}")
        report = json.loads(out.getvalue())
        self.assertTrue(out.getvalue().isascii())
        return report

    def actions(self, report, action):
        return [(a["kind"], a["id"]) for a in report["actions"] if a["action"] == action]


class SyncTest(CommandTestCase):
    def test_first_sync_creates_links_and_closes_done_tasks(self):
        report = self.run_cli("sync")
        self.assertEqual(self.actions(report, "create"), [
            ("milestone", "001-user-auth"),
            ("feature", "001-user-auth"),
            ("story", "US1"), ("story", "US2"),
            ("task", "T001"), ("task", "T002"), ("task", "T003"), ("task", "T004"), ("task", "T005"),
        ])
        self.assertEqual(self.actions(report, "link"), [
            ("story", "US1"), ("story", "US2"), ("task", "T003"), ("task", "T004"), ("task", "T005"),
        ])
        self.assertEqual(self.actions(report, "close"), [("task", "T002"), ("task", "T004")])
        self.assertEqual(report["summary"], dict(NO_ACTIONS, create=9, link=5, close=2))
        self.assertEqual(report["feature"]["iid"], 1)
        self.assertFalse(report["feature"]["out_of_sync"])

        issues = self.fake.issues
        feature, us1, us2, t3 = issues[1], issues[2], issues[3], issues[6]
        self.assertEqual(feature["title"], "User Authentication")
        self.assertEqual(feature["labels"], ["spec-kit", "feature"])
        self.assertIn("Edge Cases", feature["description"])
        self.assertNotIn("Reset password", feature["description"])
        self.assertEqual(us1["title"], "US1: Sign in with email")
        self.assertEqual(us1["labels"], ["spec-kit", "user-story", "priority::1"])
        self.assertIn("Ünïcödé", us1["description"])
        self.assertEqual(us2["labels"], ["spec-kit", "user-story", "priority::2"])
        self.assertEqual(t3["title"], "T003: Create User model in src/models/user.py")
        self.assertEqual((t3["issue_type"], t3["labels"]), ("task", ["spec-kit", "task", "priority::1"]))
        self.assertEqual(issues[4]["labels"], ["spec-kit", "task"])  # T001 has no story, so no priority
        self.assertTrue(all(i["milestone"]["title"] == "001-user-auth" for i in issues.values()))
        self.assertEqual(self.fake.links, {frozenset(p) for p in [(2, 1), (3, 1), (6, 2), (7, 2), (8, 3)]})
        states = [i["state"] for i in issues.values()]
        self.assertEqual(states, ["opened"] * 4 + ["closed", "opened", "closed", "opened"])

        mapping = self.mapping()
        self.assertEqual(mapping["project"], "group/project")
        self.assertEqual(mapping["feature"], {"iid": 1, "url": issues[1]["web_url"]})
        self.assertEqual(mapping["stories"]["US1"], {"iid": 2, "url": us1["web_url"], "done": False, "linked_to": 1})
        self.assertEqual(mapping["tasks"]["T003"], {"iid": 6, "url": t3["web_url"], "done": False, "linked_to": 2})
        self.assertTrue(mapping["tasks"]["T004"]["done"])
        self.assertNotIn("linked_to", mapping["tasks"]["T001"])

    def test_second_sync_only_reads(self):
        self.run_cli("sync")
        writes = len(self.fake.writes())
        report = self.run_cli("sync")
        self.assertEqual(report["actions"], [])
        self.assertEqual(len(self.fake.writes()), writes)

    def test_story_closes_when_all_tasks_done_and_reopens(self):
        self.run_cli("sync")
        self.write("tasks.md", TASKS.replace("- [ ] T003", "- [x] T003"))
        report = self.run_cli("sync")
        self.assertEqual(self.actions(report, "close"), [("task", "T003"), ("story", "US1")])
        self.write("tasks.md", TASKS.replace("- [X] T004", "- [ ] T004").replace("- [ ] T003", "- [x] T003"))
        report = self.run_cli("sync")
        self.assertEqual(self.actions(report, "reopen"), [("task", "T004"), ("story", "US1")])

    def test_feature_closes_only_with_flag(self):
        self.run_cli("sync")
        self.write("tasks.md", TASKS.replace("- [ ]", "- [X]"))
        report = self.run_cli("sync")
        self.assertEqual(report["feature"]["state"], "opened")
        self.assertTrue(report["feature"]["all_tasks_done"])
        self.assertTrue(report["feature"]["out_of_sync"])
        report = self.run_cli("sync", "--sync-feature")
        self.assertEqual(self.actions(report, "close"), [("feature", "001-user-auth")])
        self.assertEqual(self.fake.issues[1]["state"], "closed")
        self.assertFalse(report["feature"]["out_of_sync"])

    def test_dry_run_sends_no_writes(self):
        report = self.run_cli("sync", "--dry-run")
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["summary"], dict(NO_ACTIONS, create=9, link=5, close=2))
        self.assertEqual(self.fake.writes(), [])
        self.assertFalse((self.feature_dir / ".gitlab-mapping.json").exists())

    def test_pagination(self):
        self.fake.max_per_page = 2
        self.run_cli("sync")
        self.fake.issues[4]["state"] = "closed"
        report = self.run_cli("status")
        self.assertEqual(report["summary"]["closed_in_gitlab"], 3)

    def test_tasks_fall_back_to_issues_when_task_type_is_rejected(self):
        self.fake.reject_task_type = True
        report = self.run_cli("sync")
        self.assertEqual(self.fake.issues[4]["issue_type"], "issue")
        self.assertEqual(len(self.fake.issues), 8)
        self.assertTrue(any("issue_type=task" in w for w in report["warnings"]))

    def test_config_switches(self):
        ext = self.root / ".specify" / "extensions" / "gitlab-sync"
        ext.mkdir(parents=True)
        (ext / "gitlab-sync-config.yml").write_text(
            "mapping:\n  feature_to_issue: false\n  feature_to_milestone: false\n"
            "  priority_to_label: false\n  link_tasks_to_stories: false\n"
        )
        report = self.run_cli("sync")
        kinds = {kind for kind, _ in self.actions(report, "create")}
        self.assertEqual(kinds, {"story", "task"})
        self.assertEqual(self.actions(report, "link"), [])
        self.assertEqual(self.fake.issues[1]["labels"], ["spec-kit", "user-story"])
        self.assertIsNone(self.fake.issues[1]["milestone"])

class TwoWayTest(CommandTestCase):
    def test_gitlab_changes_update_tasks_md(self):
        self.write("tasks.md", TASKS.replace("\n", "\r\n"))
        self.run_cli("sync")
        self.fake.issues[4]["state"] = "closed"  # T001 closed by a teammate
        self.fake.issues[5]["state"] = "opened"  # T002 reopened by a teammate
        report = self.run_cli("sync", "--dry-run")
        self.assertEqual(self.actions(report, "check"), [("task", "T001")])
        self.assertEqual(self.actions(report, "uncheck"), [("task", "T002")])
        self.assertIn("- [ ] T001", self.read("tasks.md"))
        writes = len(self.fake.writes())
        report = self.run_cli("sync")
        self.assertEqual(report["summary"], dict(NO_ACTIONS, check=1, uncheck=1))
        self.assertEqual(len(self.fake.writes()), writes, "GitLab changes must not be pushed back")
        text = (self.feature_dir / "tasks.md").read_bytes().decode()
        self.assertIn("- [X] T001 Create project structure\r\n", text)
        self.assertIn("- [ ] T002 [P] Configure linting\r\n", text)
        self.assertEqual(self.run_cli("sync")["actions"], [])

    def test_both_directions_in_one_run(self):
        self.run_cli("sync")
        self.fake.issues[4]["state"] = "closed"  # T001 done in GitLab
        self.write("tasks.md", TASKS.replace("- [ ] T005", "- [X] T005"))  # T005 done locally
        report = self.run_cli("sync")
        self.assertEqual(self.actions(report, "check"), [("task", "T001")])
        self.assertEqual(self.actions(report, "close"), [("task", "T005"), ("story", "US2")])
        self.assertIn("- [X] T001", self.read("tasks.md"))
        self.assertEqual(self.fake.issues[8]["state"], "closed")

    def test_story_closed_by_hand_is_left_alone(self):
        self.run_cli("sync")
        self.fake.issues[3]["state"] = "closed"  # US2 closed by hand, its task still open
        report = self.run_cli("sync")
        self.assertEqual(report["actions"], [])
        self.assertEqual(self.fake.issues[3]["state"], "closed")
        story = {s["id"]: s for s in self.run_cli("status")["stories"]}["US2"]
        self.assertEqual(story["sync"], "gitlab_change")

class StatusTest(CommandTestCase):
    def test_status_reports_differences(self):
        self.run_cli("sync")
        self.fake.issues[4]["state"] = "closed"  # T001 closed in GitLab
        self.fake.issues[6]["assignees"] = [{"username": "alice"}]
        self.write("tasks.md", TASKS.replace("- [ ] T003", "- [X] T003") + "- [ ] T006 [US2] New task\n")
        writes = len(self.fake.writes())
        report = self.run_cli("status")
        self.assertEqual(len(self.fake.writes()), writes)
        rows = {r["id"]: r for r in report["tasks"]}
        self.assertEqual(rows["T001"]["sync"], "gitlab_change")
        self.assertEqual((rows["T003"]["sync"], rows["T003"]["assignees"]), ("local_change", ["alice"]))
        self.assertEqual(rows["T006"]["sync"], "not_pushed")
        self.assertEqual(rows["T002"]["sync"], "ok")
        self.assertEqual(report["feature"]["gitlab"], "opened")
        self.assertEqual([s["sync"] for s in report["stories"]], ["local_change", "ok"])
        self.assertEqual(report["summary"], {
            "tasks": 6, "done_locally": 3, "closed_in_gitlab": 3, "not_pushed": 1,
            "local_changes": 1, "gitlab_changes": 1,
        })


class ImportFeatureTest(CommandTestCase):
    def test_import_then_sync_links_to_existing_issue(self):
        epic = self.fake.add_issue("Users can sign in")
        report = self.run_cli("import-feature", "#1")
        self.assertEqual((report["issue"]["title"], report["linked"]), ("Users can sign in", False))
        self.assertEqual(report["feature_dir"], FEATURE)
        report = self.run_cli("import-feature", epic["web_url"], "--link")
        self.assertTrue(report["linked"])
        report = self.run_cli("sync")
        self.assertNotIn(("feature", "001-user-auth"), self.actions(report, "create"))
        self.assertIn(frozenset((2, 1)), self.fake.links)

    def test_import_links_existing_stories_and_refuses_silent_replace(self):
        self.run_cli("sync")
        other = self.fake.add_issue("Another epic")
        self.run_cli("import-feature", str(other["iid"]), "--link", expect=1)
        report = self.run_cli("import-feature", str(other["iid"]), "--link", "--replace")
        self.assertEqual(report["linked_stories"], ["US1", "US2"])
        self.assertEqual(self.mapping()["feature"]["iid"], other["iid"])
        self.assertIn(frozenset((2, other["iid"])), self.fake.links)

    def test_import_without_active_feature(self):
        self.fake.add_issue("Idea")
        (self.root / ".specify" / "feature.json").unlink()
        report = self.run_cli("import-feature", "1")
        self.assertIsNone(report["feature_dir"])
        self.assertEqual(report["issue"]["title"], "Idea")

    def test_issue_reference_formats(self):
        # gitlab.com now reports issue URLs as /-/work_items/<iid>.
        for ref in ("232", "#232", "https://g.example/group/project/-/issues/232",
                    "https://g.example/group/project/-/work_items/232?show=x"):
            self.assertEqual(gitlab.parse_issue_ref(ref, "Group/Project"), 232, ref)

    def test_rejects_issue_url_from_another_project(self):
        report = self.run_cli("import-feature", f"{self.fake.url}/other/proj/-/issues/1", expect=1)
        self.assertIn("other/proj", report["error"])


class InitAndErrorsTest(CommandTestCase):
    def test_init_writes_config_and_checks_access(self):
        del os.environ["GITLAB_URL"], os.environ["GITLAB_PROJECT"]
        report = self.run_cli("init")
        self.assertEqual(report["missing"], ["url", "project"])
        self.assertFalse(report["ready"])
        report = self.run_cli("init", "--url", self.fake.url + "/", "--project", "/group/project/")
        self.assertTrue(report["ready"], report)
        self.assertEqual(report["user"], "tester")
        self.assertEqual(report["project"]["access_level"], 30)
        config = (self.root / ".specify" / "extensions" / "gitlab-sync" / "gitlab-sync-config.yml").read_text()
        self.assertIn(f'url: "{self.fake.url}"', config)
        self.assertIn('project: "group/project"', config)
        self.assertIn("feature_to_issue: true", config)  # written from the template

    def test_missing_token(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            del os.environ["GITLAB_TOKEN"]
            report = self.run_cli("sync", expect=1)
        self.assertIn("GITLAB_TOKEN", report["error"])

    def test_bad_token(self):
        os.environ["GITLAB_TOKEN"] = "wrong"
        report = self.run_cli("sync", expect=1)
        self.assertIn("HTTP 401", report["error"])
        self.assertIn("api` scope", report["error"])

    def test_mapping_from_another_project(self):
        self.run_cli("sync")
        os.environ["GITLAB_PROJECT"] = "group/other"
        report = self.run_cli("status", expect=1)
        self.assertIn("group/project", report["error"])

    def test_partial_failure_keeps_mapping(self):
        original = self.fake.route

        def fail_on_t003(method, parts, query, body):
            if method == "POST" and parts[-1] == "issues" and body.get("title", "").startswith("T003"):
                return 500, {"message": "boom"}, None
            return original(method, parts, query, body)

        self.fake.route = fail_on_t003
        report = self.run_cli("sync", expect=1)
        self.assertIn("HTTP 500", report["error"])
        self.assertEqual(sorted(self.mapping()["tasks"]), ["T001", "T002"])
        self.fake.route = original
        report = self.run_cli("sync")
        self.assertEqual(self.actions(report, "create"), [("task", "T003"), ("task", "T004"), ("task", "T005")])
        self.assertEqual(len(self.fake.issues), 8)


if __name__ == "__main__":
    unittest.main()
