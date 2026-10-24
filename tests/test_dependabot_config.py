"""The public repo keeps Dependabot on, for the ecosystems it really uses.

The config is read with a few line patterns rather than a YAML library: PyYAML
is not a project dependency, and the file is plain block YAML. The patterns
only need to find each update entry's ecosystem and directory.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github" / "dependabot.yml"

# Ecosystem -> file globs that prove a manifest lives in the directory.
MANIFESTS = {
    "pip": ("pyproject.toml", "setup.py", "requirements*.txt", "*-requirements.txt"),
    "github-actions": ("*.yml", "*.yaml"),
}


def _entries() -> list[tuple[str, str]]:
    text = CONFIG.read_text(encoding="utf-8")
    assert "\t" not in text, "YAML forbids tab indentation"
    assert re.search(r"^version:\s*2\s*$", text, re.M), "version: 2 is required"
    assert re.search(r"^updates:\s*$", text, re.M), "top-level updates: is required"
    ecosystems = re.findall(
        r'^\s*-\s*package-ecosystem:\s*["\']?([\w-]+)["\']?\s*$', text, re.M
    )
    directories = re.findall(
        r'^\s+directory:\s*["\']?([^"\'\s]+)["\']?\s*$', text, re.M
    )
    assert ecosystems, "no update entries"
    assert len(ecosystems) == len(directories), "each entry needs a directory"
    return list(zip(ecosystems, directories, strict=True))


def test_dependabot_config_exists() -> None:
    assert CONFIG.is_file(), ".github/dependabot.yml is missing"


def test_every_entry_has_a_manifest() -> None:
    for ecosystem, directory in _entries():
        assert ecosystem in MANIFESTS, f"unexpected ecosystem {ecosystem!r}"
        base = ROOT / directory.lstrip("/")
        if ecosystem == "github-actions":
            base = base / ".github" / "workflows" if directory == "/" else base
        found = [p for pattern in MANIFESTS[ecosystem] for p in base.glob(pattern)]
        assert found, f"{ecosystem} entry at {directory!r} has no manifest"


def test_every_ecosystem_in_the_repo_is_covered() -> None:
    covered = {ecosystem for ecosystem, _ in _entries()}
    assert covered == {"pip", "github-actions"}


def test_schedule_and_limit_are_set() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    count = len(_entries())
    assert len(re.findall(r"^\s+interval:\s*weekly\s*$", text, re.M)) == count
    limits = [int(n) for n in re.findall(r"open-pull-requests-limit:\s*(\d+)", text)]
    assert len(limits) == count and all(1 <= n <= 5 for n in limits)
