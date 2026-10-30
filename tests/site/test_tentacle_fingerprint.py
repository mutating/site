"""Build-time cache fingerprint coverage for the Tentacles iframe."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPOSITORY_ROOT / "site"
THEME_ROOT = PROJECT_ROOT / "themes" / "mutating"
TENTACLES_SOURCE = THEME_ROOT / "static" / "muta-tentacles.html"
TOKAMAK_SOURCE = THEME_ROOT / "static" / "tokamak-reactor.html"
FINGERPRINT_MARKER = "__MUTA_TENTACLES_FINGERPRINT__"
TOKAMAK_FINGERPRINT_MARKER = "__TOKAMAK_FINGERPRINT__"


class _InteractiveIframeParser(HTMLParser):
    """Extract interactive iframe URLs without depending on HTML formatting."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: dict[str, str] = {}

    def handle_starttag(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attributes)
        classes = (values.get("class") or "").split()
        if tag != "iframe":
            return
        source = (
            values.get("src")
            or values.get("data-deferred-src")
            or values.get("data-prewarm-src")
        )
        if source:
            for class_name in ("contact__tentacles", "tokamak-model__frame"):
                if class_name in classes:
                    self.sources[class_name] = source


def _extract_iframe_query(
    rendered_home: str,
    *,
    class_name: str = "contact__tentacles",
    expected_path: str = "/muta-tentacles.html",
) -> dict[str, list[str]]:
    parser = _InteractiveIframeParser()
    parser.feed(rendered_home)
    assert class_name in parser.sources
    source = urlsplit(parser.sources[class_name])
    assert source.path == expected_path
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
    """Build an isolated site with its locked production JavaScript runtime.

    Copying only the theme and UI misses the site's reboop import. Install
    production dependencies from the real manifests without scripts or
    workspaces, so the build cannot borrow node_modules from the checkout.
    Then compare the generated asset URLs before and after a content-only edit.
    """

    source = tmp_path_factory.mktemp("tentacle-fingerprint") / "site"
    for manifest in ("package.json", "package-lock.json"):
        shutil.copy2(REPOSITORY_ROOT / manifest, source.parent / manifest)
    dependencies = subprocess.run(
        [
            "npm", "ci", "--omit=dev", "--ignore-scripts", "--workspaces=false",
            "--no-audit", "--no-fund",
        ],
        cwd=source.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert dependencies.returncode == 0, dependencies.stdout + dependencies.stderr
    content = source / "content"
    theme = source / "themes" / "mutating"
    # Preserve the monorepo mounts in this otherwise isolated Hugo fixture.
    shared_ui = source.parent / "packages" / "ui"
    for directory in ("dist", "hugo"):
        shutil.copytree(
            REPOSITORY_ROOT / "packages" / "ui" / directory, shared_ui / directory,
        )
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
    isolated_tokamak = theme_static / TOKAMAK_SOURCE.name
    shutil.copy2(TOKAMAK_SOURCE, isolated_tokamak)

    content_before = (content / "_index.md").read_bytes()
    original_bytes = isolated_tentacles.read_bytes()
    original_tokamak_bytes = isolated_tokamak.read_bytes()
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
        "original_tokamak_bytes": original_tokamak_bytes,
        "original_tokamak_output": (
            original_destination / TOKAMAK_SOURCE.name
        ).read_bytes(),
        "mutated_bytes": mutated_bytes,
        "mutated_home": mutated_home,
        "mutated_output": (mutated_destination / TENTACLES_SOURCE.name).read_bytes(),
    }


def test_tentacle_iframe_uses_build_generated_asset_fingerprint(
    tentacle_fingerprint_builds: dict[str, object],
) -> None:
    """The contact partial hashes the served Tentacles file during the build."""

    content_source = (PROJECT_ROOT / "content" / "_index.md").read_text(
        encoding="utf-8"
    )
    partial_source = (
        THEME_ROOT / "layouts" / "partials" / "home" / "contact.html"
    ).read_text(encoding="utf-8")
    original_bytes = tentacle_fingerprint_builds["original_bytes"]
    original_home = tentacle_fingerprint_builds["original_home"]
    assert isinstance(original_bytes, bytes)
    assert isinstance(original_home, str)

    query = _extract_iframe_query(original_home)
    assert FINGERPRINT_MARKER not in content_source
    assert FINGERPRINT_MARKER not in partial_source
    assert 'os.ReadFile "themes/mutating/static/muta-tentacles.html"' in (
        partial_source
    )
    assert "crypto.SHA256" in partial_source
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


def test_tokamak_iframe_uses_build_generated_asset_fingerprint(
    tentacle_fingerprint_builds: dict[str, object],
) -> None:
    """The containment partial hashes the served Tokamak file during the build."""

    content_source = (PROJECT_ROOT / "content" / "_index.md").read_text(
        encoding="utf-8"
    )
    partial_source = (
        THEME_ROOT / "layouts" / "partials" / "home" / "containment.html"
    ).read_text(encoding="utf-8")
    tokamak_bytes = tentacle_fingerprint_builds["original_tokamak_bytes"]
    original_home = tentacle_fingerprint_builds["original_home"]
    assert isinstance(tokamak_bytes, bytes)
    assert isinstance(original_home, str)

    query = _extract_iframe_query(
        original_home,
        class_name="tokamak-model__frame",
        expected_path="/tokamak-reactor.html",
    )
    assert TOKAMAK_FINGERPRINT_MARKER not in content_source
    assert TOKAMAK_FINGERPRINT_MARKER not in partial_source
    assert 'os.ReadFile "themes/mutating/static/tokamak-reactor.html"' in (
        partial_source
    )
    assert "crypto.SHA256" in partial_source
    assert TOKAMAK_FINGERPRINT_MARKER not in original_home
    assert query == {"v": [hashlib.sha256(tokamak_bytes).hexdigest()]}
    assert tentacle_fingerprint_builds["original_tokamak_output"] == tokamak_bytes
