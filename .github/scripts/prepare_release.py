"""Validate release metadata and build the Home Assistant component archive."""

import argparse
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")


def changelog_notes(changelog, version):
    """Return the body under the changelog heading for version."""
    match = re.search(
        rf"^## {re.escape(version)}(?:\s+-[^\n]*)?\s*\n(.*?)(?=^## |\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    if match is None or not match.group(1).strip():
        raise ValueError(f"CHANGELOG.md has no release notes for {version}")
    return match.group(1).strip() + "\n"


def prepare_release(repository, output_dir):
    """Validate metadata, build the ZIP, and return its release properties."""
    repository = Path(repository).resolve()
    component = repository / "custom_components" / "ict_automation"
    manifest = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest.get("version", ""))
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("manifest.json must contain a valid version")

    notes = changelog_notes(
        (repository / "CHANGELOG.md").read_text(encoding="utf-8"), version
    )
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"ict_automation-{version}.zip"
    notes_file = output_dir / f"release-notes-{version}.md"

    with ZipFile(archive, "w", ZIP_DEFLATED) as release_zip:
        for source in sorted(component.rglob("*")):
            if (
                not source.is_file()
                or "__pycache__" in source.parts
                or source.suffix == ".pyc"
            ):
                continue
            relative = source.relative_to(repository)
            release_zip.write(source, relative.as_posix())

    notes_file.write_text(notes, encoding="utf-8", newline="\n")
    return {
        "version": version,
        "tag": f"v{version}",
        "archive": archive.as_posix(),
        "notes": notes_file.as_posix(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    result = prepare_release(args.repository, args.output_dir)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8", newline="\n") as output:
            for key, value in result.items():
                output.write(f"{key}={value}\n")
    print(f"Built {result['archive']} for {result['tag']}")


if __name__ == "__main__":
    main()
