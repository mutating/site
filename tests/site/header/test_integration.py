"""Build the header under deployment and incomplete-translation contracts."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from conftest import PROJECT_ROOT, REPOSITORY_ROOT


class _HeaderParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.languages: set[str] = set()
        self.has_header = False
        self.stylesheets: list[str] = []
        self.feed(source)

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        values = dict(attributes)
        if values.get("data-language-option"):
            self.languages.add(values["data-language-option"])
        if tag == "header" and "tmc-header-shell" in (values.get("class") or "").split():
            self.has_header = True
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.stylesheets.append(values["href"])


def _build(source: Path, destination: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["hugo", "--minify", "--environment", "production", "--panicOnWarning",
         "--noBuildLock", "--destination", str(destination), *arguments],
        cwd=source, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_header_reserves_all_supported_language_codes_on_untranslated_pages(
    tmp_path: Path,
) -> None:
    """A missing long-code translation must not shift the header by 46px."""
    content = tmp_path / "content"
    content.mkdir()
    for name in ("_index.md", "_index.ru.md"):
        shutil.copy2(PROJECT_ROOT / "content" / name, content / name)
    post = content / "blog" / "limited"
    post.mkdir(parents=True)
    (post / "index.md").write_text(
        '+++\ntitle = "Limited translation fixture"\n'
        'slug = "limited"\ndate = "2026-01-01T00:00:00Z"\n'
        '[sites.matrix]\nlanguages = ["en", "ru"]\n+++\nFixture.\n',
    )
    extra = tmp_path / "languages.toml"
    extra.write_text(
        '[languages.zh-hant-tw]\nlabel = "Chinese"\n'
        'locale = "zh-Hant-TW"\nweight = 3\n',
    )
    output = tmp_path / "public"
    _build(PROJECT_ROOT, output, "--config", f"{PROJECT_ROOT / 'hugo.toml'},{extra}",
           "--contentDir", str(content))
    for prefix, available in (("", "ru"), ("ru/", "en")):
        home = (output / prefix / "index.html").read_text()
        article = (output / prefix / "blog/limited/index.html").read_text()
        assert "--tmc-language-switcher-desktop-width:94px" in home
        assert "--tmc-language-switcher-desktop-width:94px" in article
        assert _HeaderParser(article).languages == {available}
    assert not (output / "zh-hant-tw/blog/limited/index.html").exists()


def test_bootstrap_synchronized_sources_build_without_the_original_checkout(
    tmp_path: Path,
) -> None:
    """Build only the playbook's uploaded tree, including pinned npm runtime.

    Copying just Hugo and UI sources missed reboop after the transport migration.
    Execute the declared local preparation, then build with no access to the
    original checkout's node_modules or any frontend toolchain on the server.
    """
    playbook = (REPOSITORY_ROOT / "infra/playbooks/setup-site.yml").read_text()
    task_parts = re.split(r"^    - name: (.+)\n", playbook, flags=re.MULTILINE)
    tasks = dict(zip(task_parts[1::2], task_parts[2::2]))
    variables = dict(re.findall(r'^    (\w+): "([^"]+)"$', task_parts[0], re.MULTILINE))
    variables.update({
        "playbook_dir": str(REPOSITORY_ROOT / "infra/playbooks"),
        "source_staging_path": str(tmp_path / "staging"),
        "source_path": str(tmp_path / "remote"),
        "local_runtime_path": str(tmp_path / "runtime"),
    })

    def render(value: str) -> str:
        while "{{" in value:
            value = re.sub(r"{{\s*(\w+)\s*}}", lambda match: variables[match[1]], value)
        return value

    build_ui = tasks["Build the shared UI locally before source synchronization"]
    assert "delegate_to: localhost" in build_ui
    assert "become: false" in build_ui
    assert "ansible.builtin.command: npm run build:ui" in build_ui
    build_cwd = re.search(r'chdir: "([^"]+)"', build_ui)
    assert build_cwd is not None
    assert Path(render(build_cwd[1])).resolve() == REPOSITORY_ROOT

    runtime_directory = tasks["Create the local Hugo runtime preparation directory"]
    assert "delegate_to: localhost" in runtime_directory
    assert "become: false" in runtime_directory
    assert "state: directory" in runtime_directory
    runtime_path = re.search(r'^        path: "([^"]+)"$', runtime_directory, re.MULTILINE)
    assert runtime_path is not None
    Path(render(runtime_path[1])).mkdir(parents=True)

    manifests = tasks["Copy the root manifests for Hugo runtime preparation"]
    assert "delegate_to: localhost" in manifests
    assert "become: false" in manifests
    manifest_source = re.search(r'^        src: "([^"]+)"$', manifests, re.MULTILINE)
    manifest_target = re.search(r'^        dest: "([^"]+)"$', manifests, re.MULTILINE)
    assert manifest_source is not None and manifest_target is not None
    for item in re.findall(r"^        - ([\w.-]+)$", manifests.split("      loop:", 1)[1], re.MULTILINE):
        variables["item"] = item
        shutil.copy2(render(manifest_source[1]), render(manifest_target[1]))

    runtime_install = tasks["Install the pinned Hugo runtime dependencies locally"]
    assert "delegate_to: localhost" in runtime_install
    assert "become: false" in runtime_install
    install_command = re.search(r"ansible.builtin.command: ([^\n]+)", runtime_install)
    install_cwd = re.search(r'chdir: "([^"]+)"', runtime_install)
    assert install_command is not None and install_cwd is not None
    result = subprocess.run(
        shlex.split(install_command[1]), cwd=render(install_cwd[1]),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    for task in tasks.values():
        if "ansible.posix.synchronize:" not in task:
            continue
        source_match = re.search(r'^        src: "([^"]+)"$', task, re.MULTILINE)
        target_match = re.search(r'^        dest: "([^"]+)"$', task, re.MULTILINE)
        assert source_match is not None and target_match is not None
        items = re.findall(r"^        - (\w+)$", task.split("      loop:", 1)[1], re.MULTILINE) if "      loop:" in task else [""]
        excluded = set(re.findall(r"--exclude=/([^/\n]+)(?:/)?$", task, re.MULTILINE))
        for item in items:
            variables["item"] = item
            source = Path(render(source_match[1])).resolve()
            target = Path(render(target_match[1]))
            shutil.copytree(source, target, dirs_exist_ok=True,
                            ignore=lambda directory, names: excluded.intersection(names) if Path(directory) == source else set())

    # Promotion copies the staged tree unchanged; no monorepo mount is reachable
    # outside this isolated remote directory.
    promotion = tasks["Promote the synchronized checkout into the source directory"]
    assert '"{{ source_staging_path }}/"' in promotion
    assert '"{{ source_path }}/"' in promotion
    remote = Path(variables["source_path"])
    shutil.copytree(Path(variables["source_staging_path"]), remote)
    build_site = tasks["Build the site for the first time"]
    site_cwd = re.search(r'chdir: "([^"]+)"', build_site)
    assert site_cwd is not None
    output = tmp_path / "public"
    _build(Path(render(site_cwd[1])), output, "--baseURL", "https://example.test/")
    assert _HeaderParser((output / "index.html").read_text()).has_header
    assert (output / "ui/index.js").is_file()
    assert (remote / "node_modules/reboop/package.json").is_file()
    assert not (remote / "node_modules/typescript").exists()
    assert not (remote / "node_modules/vite").exists()


def test_all_fingerprinted_header_styles_match_both_immutable_nginx_locations(
    base_url: str,
) -> None:
    """The extracted header CSS must retain the original site's cache policy."""
    with urlopen(base_url, timeout=10) as response:
        styles = _HeaderParser(response.read().decode()).stylesheets
    assert len(styles) >= 3
    assert len([href for href in styles if "/ui/styles/" in href]) == 2
    playbook = (REPOSITORY_ROOT / "infra/playbooks/setup-site.yml").read_text()
    patterns = re.findall(
        r'location ~\* "([^"]+)" \{\s+try_files[^\n]+\s+'
        r'add_header Cache-Control "public, max-age=31536000, immutable"',
        playbook,
    )
    assert len(patterns) == 2, "Both HTTP and HTTPS must cache fingerprinted styles."
    for pattern in patterns:
        for href in styles:
            assert re.fullmatch(pattern, urlsplit(href).path), href
        for stable in ("/", "/ui/styles/header.css", "/apple-touch-icon.png"):
            assert not re.fullmatch(pattern, stable), stable
