"""Release hygiene: version numbers and metadata stay consistent."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

from custom_components.rct_bin_collections.const import DOMAIN, VERSION

ROOT = Path(__file__).parent.parent
MANIFEST = json.loads(
    (ROOT / "custom_components" / DOMAIN / "manifest.json").read_text(encoding="utf-8")
)


def test_versions_match() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert MANIFEST["version"] == VERSION == pyproject["project"]["version"]


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION)


def test_changelog_has_entry_for_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{VERSION}]" in changelog
