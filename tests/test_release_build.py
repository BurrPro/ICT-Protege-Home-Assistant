"""Release packaging tests."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / ".github" / "scripts" / "prepare_release.py"
SPEC = importlib.util.spec_from_file_location("prepare_release", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseBuildTests(unittest.TestCase):
    def test_archive_matches_manifest_and_excludes_generated_files(self):
        with tempfile.TemporaryDirectory() as output_dir:
            result = release.prepare_release(REPOSITORY, output_dir)
            self.assertEqual(result["tag"], f"v{result['version']}")
            with ZipFile(result["archive"]) as archive:
                names = archive.namelist()
                self.assertIn(
                    "custom_components/ict_automation/manifest.json", names
                )
                self.assertTrue(
                    all(
                        "__pycache__" not in name and not name.endswith(".pyc")
                        for name in names
                    )
                )

    def test_missing_changelog_section_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no release notes"):
            release.changelog_notes("# Changelog\n", "9.9.9")
