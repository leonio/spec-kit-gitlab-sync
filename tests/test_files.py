import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))

from gitlab_client import ConfigError, load_config, parse_simple_yaml, set_yaml_values  # noqa: E402
from speckit_files import Mapping, parse_spec, parse_tasks, set_checkboxes  # noqa: E402

# Shaped like spec-kit's templates/spec-template.md once filled in.
SPEC = """# Feature Specification: User Authentication

**Feature Branch**: `001-user-auth`

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
### User Story 9 - Not a story, inside a comment (Priority: P9)
-->

### User Story 1 - Sign in with email (Priority: P1)

A registered user signs in with email and password.

**Acceptance Scenarios**:

1. **Given** a registered user, **When** they sign in, **Then** they see the dashboard

---

### User Story 2 - Reset password (Priority: P2)

A user resets a forgotten password.
### User Story 3 - Remember me

Stays signed in.

### Edge Cases

- Locked accounts

## Requirements *(mandatory)*

- **FR-001**: System MUST hash passwords
"""

# Shaped like spec-kit's templates/tasks-template.md: [P] = parallel, [USn] = story.
TASKS = """---
description: "Task list"
---

# Tasks: User Authentication

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Create project structure
- [X] T002 [P] Configure linting

## Phase 3: User Story 1 - Sign in (Priority: P1) 🎯 MVP

- [ ] T003 [P] [US1] Create User model in src/models/user.py
- [x] T004 [US1] Implement sign-in endpoint
- [ ] T004 [US1] Duplicate ID
- [ ] TXXX [P] Template placeholder

```text
- [ ] T999 Inside a code fence
```
"""


class SpecParsingTest(unittest.TestCase):
    def test_spec_kit_template_format(self):
        spec = parse_spec(SPEC)
        self.assertEqual(spec.title, "User Authentication")
        self.assertEqual([(s.id, s.title, s.priority) for s in spec.stories], [
            ("US1", "Sign in with email", "P1"),
            ("US2", "Reset password", "P2"),
            ("US3", "Remember me", None),
        ])
        self.assertTrue(spec.stories[0].body.startswith("A registered user"))
        self.assertIn("**Acceptance Scenarios**", spec.stories[0].body)
        self.assertNotIn("---", spec.stories[0].body)
        self.assertEqual(spec.stories[1].body, "A user resets a forgotten password.")
        self.assertEqual(spec.stories[2].body, "Stays signed in.")

    def test_overview_drops_stories_but_keeps_other_sections(self):
        overview = parse_spec(SPEC).overview
        self.assertNotIn("Sign in with email", overview)
        self.assertNotIn("Feature Specification", overview)
        self.assertIn("### Edge Cases", overview)
        self.assertIn("FR-001", overview)

    def test_placeholder_title_is_ignored(self):
        self.assertIsNone(parse_spec("# Feature Specification: [FEATURE NAME]\n").title)


class TaskParsingTest(unittest.TestCase):
    def test_tasks_template_format(self):
        tasks = parse_tasks(TASKS)
        self.assertEqual([t.id for t in tasks.tasks], ["T001", "T002", "T003", "T004"])
        self.assertEqual(tasks.duplicates, ["T004"])
        t001, t002, t003, t004 = tasks.tasks
        self.assertEqual((t001.done, t001.story, t001.parallel), (False, None, False))
        self.assertEqual(t001.text, "Create project structure")
        self.assertEqual((t002.done, t002.parallel), (True, True))
        self.assertEqual((t003.story, t003.parallel), ("US1", True))
        self.assertEqual(t003.text, "Create User model in src/models/user.py")
        self.assertEqual((t004.done, t004.text), (True, "Implement sign-in endpoint"))

    def test_set_checkboxes_preserves_everything_else(self):
        text = "- [ ] T001 One\r\n- [X] T002 Two\r\n  - [ ] T003 [US1] Three\r\n```\n- [ ] T001 fenced\n```\n"
        new, changed = set_checkboxes(text, {"T001": True, "T002": False, "T003": False, "T404": True})
        self.assertEqual(changed, ["T001", "T002"])
        self.assertEqual(new, text.replace("[ ] T001 One", "[X] T001 One").replace("[X] T002", "[ ] T002"))


class ConfigTest(unittest.TestCase):
    def test_parse_simple_yaml(self):
        data = parse_simple_yaml(
            'gitlab:\n  url: "https://gitlab.example.com"  # comment\n  project: group/sub/proj\n'
            "  ca_file:\nlabels:\n  story_label: 'it''s'\nmapping:\n  feature_to_issue: false\n"
            "  link_tasks_to_stories: yes # inline\n"
        )
        self.assertEqual(
            data["gitlab"], {"url": "https://gitlab.example.com", "project": "group/sub/proj", "ca_file": {}}
        )
        self.assertEqual(data["labels"], {"story_label": "it's"})
        self.assertEqual(data["mapping"], {"feature_to_issue": False, "link_tasks_to_stories": True})

    def test_parse_simple_yaml_rejects_lists(self):
        with self.assertRaises(ConfigError):
            parse_simple_yaml("labels:\n  - one\n")

    def test_set_yaml_values_keeps_comments(self):
        text = "gitlab:\n  # URL of your GitLab\n  url: \"${GITLAB_URL}\"  # keep me\n\nlabels:\n  task_label: task\n"
        updated = set_yaml_values(text, "gitlab", {"url": "https://g.example", "project": "a/b"})
        self.assertEqual(
            updated,
            "gitlab:\n  # URL of your GitLab\n  url: \"https://g.example\"  # keep me\n  project: \"a/b\"\n\n"
            "labels:\n  task_label: task\n",
        )
        self.assertEqual(set_yaml_values("", "gitlab", {"url": "u"}), "gitlab:\n  url: \"u\"\n")
        self.assertEqual(
            set_yaml_values("gitlab:\n  url: u\n\n# Labels\nlabels: {}\n", "gitlab", {"project": "p"}),
            "gitlab:\n  url: u\n  project: \"p\"\n\n# Labels\nlabels: {}\n",
        )

    def test_config_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ext = root / ".specify" / "extensions" / "gitlab-sync"
            ext.mkdir(parents=True)
            (ext / "gitlab-sync-config.yml").write_text(
                'gitlab:\n  url: ""\n  project: "group/project"\nmapping:\n  feature_to_issue: false\n'
            )
            (ext / "gitlab-sync-config.local.yml").write_text("labels:\n  task_label: todo\n")
            config = load_config(root)
            self.assertEqual((config.url, config.project, config.task_label), ("", "group/project", "todo"))
            self.assertFalse(config.feature_to_issue)
            self.assertTrue(config.feature_to_milestone)
            (ext / "gitlab-sync-config.yml").write_text("mapping:\n  feature_to_issue: maybe\n")
            with self.assertRaises(ConfigError):
                load_config(root)

    def test_environment_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".specify" / "extensions" / "gitlab-sync").mkdir(parents=True)
            env = {
                "GITLAB_URL": "https://alias.example",
                "SPECKIT_GITLAB_SYNC_GITLAB_URL": "https://speckit.example/",  # spec-kit convention wins
                "SPECKIT_GITLAB_SYNC_LABELS_TASK_LABEL": "yes",  # strings are taken literally
                "SPECKIT_GITLAB_SYNC_MAPPING_FEATURE_TO_MILESTONE": "false",
            }
            with mock.patch.dict(os.environ, env):
                config = load_config(root)
            self.assertEqual((config.url, config.task_label), ("https://speckit.example", "yes"))
            self.assertFalse(config.feature_to_milestone)
            self.assertIn("$SPECKIT_GITLAB_SYNC_LABELS_TASK_LABEL", config.sources)
            with mock.patch.dict(os.environ, {"SPECKIT_GITLAB_SYNC_MAPPING_PRIORITY_TO_LABEL": "sometimes"}):
                with self.assertRaises(ConfigError):
                    load_config(root)


class MappingTest(unittest.TestCase):
    def test_empty_mapping_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            Mapping.load(Path(tmp)).save()
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
