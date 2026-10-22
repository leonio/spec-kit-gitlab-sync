"""Checks on the extension layout that spec-kit relies on when registering commands."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "missing frontmatter"
    return match.group(1)


class ExtensionLayoutTest(unittest.TestCase):
    def setUp(self):
        self.manifest = (ROOT / "extension.yml").read_text(encoding="utf-8")
        self.commands = re.findall(r'- name: "(speckit\.gitlab-sync\.[a-z-]+)"\n\s+file: "([^"]+)"', self.manifest)

    def test_every_command_file_exists_and_every_file_is_registered(self):
        self.assertTrue(self.commands)
        registered = {file for _, file in self.commands}
        on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "commands").glob("*.md")}
        self.assertEqual(registered, on_disk)

    def test_hooks_point_at_registered_commands(self):
        names = {name for name, _ in self.commands}
        hooked = set(re.findall(r'command: "([^"]+)"', self.manifest))
        self.assertTrue(hooked)
        self.assertLessEqual(hooked, names)

    def test_command_scripts_use_variants_spec_kit_understands(self):
        # spec-kit only accepts sh/ps/py keys under `scripts:` and only replaces a
        # bare {SCRIPT}; anything else fails registration or reaches the agent verbatim.
        for _, file in self.commands:
            text = (ROOT / file).read_text(encoding="utf-8")
            scripts = re.search(r"^scripts:\n((?:  .*\n?)+)", frontmatter(text) + "\n", re.MULTILINE)
            self.assertIsNotNone(scripts, file)
            entries = dict(re.findall(r"^  (\w+): (.+)$", scripts.group(1), re.MULTILINE))
            self.assertEqual(set(entries), {"py"}, file)
            script = entries["py"].split()[0]
            self.assertTrue((ROOT / script).is_file(), f"{file}: {script} not found")
            self.assertIn("{SCRIPT}", text, file)
            self.assertNotRegex(text, r"\{SCRIPT:", file)

    def test_config_template_matches_script_defaults(self):
        import sys

        sys.path.insert(0, str(ROOT / "scripts" / "python"))
        from gitlab_client import DEFAULTS, parse_simple_yaml

        template = parse_simple_yaml((ROOT / "gitlab-sync-config.template.yml").read_text(encoding="utf-8"))
        for section, values in DEFAULTS.items():
            self.assertEqual(set(template[section]), set(values), section)
            for key, default in values.items():
                if isinstance(default, bool) or default:
                    self.assertEqual(template[section][key], default, f"{section}.{key}")


if __name__ == "__main__":
    unittest.main()
