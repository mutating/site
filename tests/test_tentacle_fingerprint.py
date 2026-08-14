"""Build-time cache fingerprint coverage for the Tentacles iframe."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEME_ROOT = PROJECT_ROOT / "themes" / "mutating"
TENTACLES_SOURCE = THEME_ROOT / "static" / "muta-tentacles.html"
FINGERPRINT_MARKER = "__MUTA_TENTACLES_FINGERPRINT__"


class _ContactIframeParser(HTMLParser):
    """Extract the contact iframe URL without depending on HTML formatting."""

    def __init__(self) -> None:
        super().__init__()
        self.source: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attributes)
        classes = (values.get("class") or "").split()
        if tag == "iframe" and "contact__tentacles" in classes:
            self.source = values.get("src")


def _extract_iframe_query(rendered_home: str) -> dict[str, list[str]]:
    parser = _ContactIframeParser()
    parser.feed(rendered_home)
    assert parser.source is not None
    source = urlsplit(parser.source)
    assert source.path == "/muta-tentacles.html"
    return parse_qs(source.query, keep_blank_values=True)


def _build_site(source: Path, destination: Path) -> str:
    result = subprocess.run(
        [
            "hugo",
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--cacheDir",
            str(source / "cache"),
            "--cleanDestinationDir",
            "--noBuildLock",
        ],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (destination / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tentacle_fingerprint_builds(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, object]:
    """Build an isolated site before and after a content-only Tentacles edit."""

    source = tmp_path_factory.mktemp("tentacle-fingerprint") / "site"
    content = source / "content"
    theme = source / "themes" / "mutating"
    content.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "hugo.toml", source / "hugo.toml")
    shutil.copy2(PROJECT_ROOT / "content" / "_index.md", content / "_index.md")
    shutil.copytree(
        THEME_ROOT,
        theme,
        ignore=lambda directory, _names: (
            {"static"} if Path(directory) == THEME_ROOT else set()
        ),
    )
    theme_static = theme / "static"
    theme_static.mkdir()
    isolated_tentacles = theme_static / TENTACLES_SOURCE.name
    shutil.copy2(TENTACLES_SOURCE, isolated_tentacles)

    content_before = (content / "_index.md").read_bytes()
    original_bytes = isolated_tentacles.read_bytes()
    original_destination = source / "public-original"
    original_home = _build_site(source, original_destination)

    mutated_bytes = original_bytes + b"\n<!-- fingerprint regression probe -->\n"
    isolated_tentacles.write_bytes(mutated_bytes)
    mutated_destination = source / "public-mutated"
    mutated_home = _build_site(source, mutated_destination)

    return {
        "content_unchanged": content_before == (content / "_index.md").read_bytes(),
        "original_bytes": original_bytes,
        "original_home": original_home,
        "original_output": (original_destination / TENTACLES_SOURCE.name).read_bytes(),
        "mutated_bytes": mutated_bytes,
        "mutated_home": mutated_home,
        "mutated_output": (mutated_destination / TENTACLES_SOURCE.name).read_bytes(),
    }


def test_tentacle_iframe_uses_build_generated_asset_fingerprint(
    tentacle_fingerprint_builds: dict[str, object],
) -> None:
    """Hugo replaces the marker with the served Tentacles file's full SHA-256."""

    content_source = (PROJECT_ROOT / "content" / "_index.md").read_text(
        encoding="utf-8"
    )
    original_bytes = tentacle_fingerprint_builds["original_bytes"]
    original_home = tentacle_fingerprint_builds["original_home"]
    assert isinstance(original_bytes, bytes)
    assert isinstance(original_home, str)

    query = _extract_iframe_query(original_home)
    assert content_source.count(FINGERPRINT_MARKER) == 1
    assert FINGERPRINT_MARKER not in original_home
    assert query == {
        "v": [hashlib.sha256(original_bytes).hexdigest()],
        "embedded": ["1"],
    }
    assert tentacle_fingerprint_builds["original_output"] == original_bytes


def test_tentacle_iframe_fingerprint_changes_when_asset_content_changes(
    tentacle_fingerprint_builds: dict[str, object],
) -> None:
    """A content-only Tentacles edit changes the next build URL automatically."""

    original_home = tentacle_fingerprint_builds["original_home"]
    mutated_home = tentacle_fingerprint_builds["mutated_home"]
    mutated_bytes = tentacle_fingerprint_builds["mutated_bytes"]
    assert isinstance(original_home, str)
    assert isinstance(mutated_home, str)
    assert isinstance(mutated_bytes, bytes)

    original_query = _extract_iframe_query(original_home)
    mutated_query = _extract_iframe_query(mutated_home)
    assert tentacle_fingerprint_builds["content_unchanged"] is True
    assert mutated_query["v"] != original_query["v"]
    assert mutated_query["v"] == [hashlib.sha256(mutated_bytes).hexdigest()]
    assert mutated_query["embedded"] == ["1"]
    assert tentacle_fingerprint_builds["mutated_output"] == mutated_bytes
