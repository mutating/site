"""Fast build-time and isolated-script coverage for production contracts."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ORIGIN = "https://example.test"
METRIKA_SOURCE = "https://mc.yandex.ru/metrika/tag.js?id=111506392"
pytestmark = pytest.mark.xdist_group(name="production-contracts")


def _run_hugo(
    content: Path,
    destination: Path,
    *,
    environment: str = "production",
) -> None:
    """Build one controlled site and expose complete Hugo diagnostics."""

    source_theme = PROJECT_ROOT / "themes" / "mutating"
    themes = destination.parent / "themes"
    shutil.copytree(
        source_theme,
        themes / "mutating",
        dirs_exist_ok=True,
        ignore=lambda directory, _names: (
            {"static"} if Path(directory) == source_theme else set()
        ),
    )

    result = subprocess.run(
        [
            "hugo",
            "--source",
            str(PROJECT_ROOT),
            "--contentDir",
            str(content),
            "--destination",
            str(destination),
            "--baseURL",
            f"{TEST_ORIGIN}/",
            "--environment",
            environment,
            "--themesDir",
            str(themes),
            "--cleanDestinationDir",
            "--noBuildLock",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _copy_test_content(target: Path) -> None:
    """Copy production content plus the browser-only Markdown fixtures."""

    shutil.copytree(PROJECT_ROOT / "content", target)
    for fixture in (PROJECT_ROOT / "tests" / "fixtures").glob("*-post.md"):
        shutil.copy2(fixture, target / "blog" / fixture.name)
    for fixture in (PROJECT_ROOT / "tests" / "fixtures").glob("*-author.md"):
        shutil.copy2(fixture, target / "authors" / fixture.name)


@pytest.fixture(scope="module")
def built_sites(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build development and production output once for this module."""

    root = tmp_path_factory.mktemp("production-contracts")
    content = root / "content"
    _copy_test_content(content)
    outputs = {
        "development": root / "development",
        "production": root / "production",
    }
    for environment, destination in outputs.items():
        _run_hugo(content, destination, environment=environment)
    return outputs


def _read_output(root: Path, path: str) -> str:
    output = root / path.strip("/") / "index.html"
    if path == "/":
        output = root / "index.html"
    return output.read_text(encoding="utf-8")


def _first(pattern: str, source: str) -> str:
    match = re.search(pattern, source, re.DOTALL)
    assert match is not None, pattern
    return html.unescape(match.group(1).strip())


def _extract_language_bootstrap(source: str) -> str:
    match = re.search(
        r"<script>\s*(\(function \(\) \{.*?var currentLanguage.*?\}\)\(\);)\s*</script>",
        source,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def _run_node(source: str) -> dict[str, object]:
    result = subprocess.run(
        ["node", "-e", source],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _run_language_bootstrap(
    bootstrap: str,
    *,
    stored: object = None,
    languages: object = None,
    language: object = "en-US",
    storage_throws: bool = False,
    search: str = "",
    fragment: str = "",
    path: str = "/",
) -> dict[str, object]:
    harness = f"""
let redirectedTo = null;
let replacedUrl = null;
const storageWrites = [];
globalThis.localStorage = {{
  getItem() {{
    if ({json.dumps(storage_throws)}) throw new Error("storage blocked");
    return {json.dumps(stored)};
  }},
  setItem(key, value) {{
    if ({json.dumps(storage_throws)}) throw new Error("storage blocked");
    storageWrites.push([key, value]);
  }},
}};
Object.defineProperty(globalThis, "navigator", {{
  configurable: true,
  value: {{ languages: {json.dumps(languages)}, language: {json.dumps(language)} }},
}});
globalThis.document = {{ documentElement: {{ dataset: {{}} }} }};
globalThis.window = {{
  history: {{
    state: null,
    replaceState(_state, _title, url) {{ replacedUrl = url; }},
  }},
  location: {{
    origin: {json.dumps(TEST_ORIGIN)},
    pathname: {json.dumps(path)},
    search: {json.dumps(search)},
    hash: {json.dumps(fragment)},
    replace(url) {{ redirectedTo = url; }},
  }},
}};
{bootstrap}
console.log(JSON.stringify({{
  redirectedTo,
  replacedUrl,
  storageWrites,
  language: document.documentElement.dataset.language,
}}));
"""
    return _run_node(harness)


def _extract_metrika_script(source: str) -> str:
    match = re.search(
        r"<script[^>]*>(\s*\(function \(\) \{\s*function loadMetrika\(\).*?)</script>",
        source,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def _run_metrika_harness(
    source: str,
    *,
    ready_state: str,
    has_idle_callback: bool,
    existing_script: bool = False,
) -> dict[str, object]:
    harness = f"""
const sourceUrl = {json.dumps(METRIKA_SOURCE)};
const inserted = [];
const idleCalls = [];
const timerCalls = [];
const listeners = [];
const scripts = {"[{src: sourceUrl}]" if existing_script else "[]"};
const anchor = {{ parentNode: {{ insertBefore(node) {{ inserted.push(node); scripts.push(node); }} }} }};
globalThis.window = globalThis;
globalThis.location = {{ href: "https://example.test/page/" }};
globalThis.document = {{
  readyState: {json.dumps(ready_state)},
  referrer: "https://referrer.test/",
  scripts,
  createElement(tag) {{ return {{ tagName: tag }}; }},
  getElementsByTagName() {{ return [anchor]; }},
}};
window.addEventListener = (type, callback, options) => listeners.push({{type, callback, options}});
window.setTimeout = (callback, delay) => {{ timerCalls.push({{callback, delay}}); return 1; }};
if ({json.dumps(has_idle_callback)}) {{
  window.requestIdleCallback = (callback, options) => {{ idleCalls.push({{callback, options}}); return 1; }};
}}
{source}
const beforeLoad = {{idle: idleCalls.length, timers: timerCalls.length}};
for (const listener of listeners.filter((item) => item.type === "load")) listener.callback();
for (const call of idleCalls) call.callback();
for (const call of timerCalls) call.callback();
const queuedYmCalls = typeof window.ym === "function"
  ? (window.ym.a || []).map((args) => Array.from(args))
  : [];
console.log(JSON.stringify({{
  beforeLoad,
  listeners: listeners.map((item) => ({{type: item.type, once: item.options?.once === true}})),
  idleTimeouts: idleCalls.map((item) => item.options?.timeout),
  timerDelays: timerCalls.map((item) => item.delay),
  inserted: inserted.map((item) => ({{src: item.src, async: item.async}})),
  queuedYmCalls,
}}));
"""
    return _run_node(harness)


def test_home_emits_the_pantograph_mount_consumed_by_runtime(
    built_sites: dict[str, Path],
) -> None:
    """The built home exposes the exact Pantograph mount required by site.js."""

    home = _read_output(built_sites["production"], "/")

    assert home.count('class="pantograph-visual"') == 1
    assert home.count('class="pantograph-visual__object"') == 1
    assert 'class="distribution__visual-object"' not in home
    assert re.search(
        r'<div class="pantograph-visual" role="img" '
        r'aria-label="An animated pantograph coordinating local and distributed '
        r'verification work"><div class="pantograph-visual__object" '
        r'aria-hidden="true"></div></div>',
        home,
    )


def test_home_emits_feedback_nodes_in_the_intended_cycle_order(
    built_sites: dict[str, Path],
) -> None:
    """The feedback diagram keeps each numbered node's intended process label."""

    home = _read_output(built_sites["production"], "/")
    labels = re.findall(
        r'<div class="feedback-node feedback-node--(?:top|right|bottom|left)">'
        r'<span>\d+</span><strong>([^<]+)</strong></div>',
        home,
    )

    assert labels == [
        "Better product",
        "Faster tests",
        "Sharper signals",
        "Smarter agents",
    ]


def test_metrika_markup_is_emitted_only_in_production_build(
    built_sites: dict[str, Path],
) -> None:
    """Production contains one Metrika instance and development contains none."""

    production = _read_output(built_sites["production"], "/")
    development = _read_output(built_sites["development"], "/")

    assert production.count("function scheduleMetrika()") == 1
    assert production.count(METRIKA_SOURCE) == 1
    assert production.count("https://mc.yandex.ru/watch/111506392") == 1
    assert "Yandex.Metrika" not in development
    assert "mc.yandex.ru" not in development


@pytest.mark.parametrize(
    ("ready_state", "has_idle"),
    [
        ("complete", True),
        ("complete", False),
        ("loading", True),
        ("loading", False),
    ],
    ids=["complete-idle", "complete-timeout", "load-idle", "load-timeout"],
)
def test_metrika_scheduler_uses_idle_or_timeout_after_document_readiness(
    built_sites: dict[str, Path],
    ready_state: str,
    has_idle: bool,
) -> None:
    """Metrika waits for load, then prefers idle scheduling over a zero timeout."""

    source = _extract_metrika_script(_read_output(built_sites["production"], "/"))
    result = _run_metrika_harness(
        source,
        ready_state=ready_state,
        has_idle_callback=has_idle,
    )

    if ready_state == "loading":
        assert result["beforeLoad"] == {"idle": 0, "timers": 0}
        assert result["listeners"] == [{"type": "load", "once": True}]
    else:
        assert result["listeners"] == []
        assert result["beforeLoad"] == {
            "idle": int(has_idle),
            "timers": int(not has_idle),
        }
    assert result["idleTimeouts"] == ([2000] if has_idle else [])
    assert result["timerDelays"] == ([] if has_idle else [0])
    assert result["inserted"] == [{"src": METRIKA_SOURCE, "async": 1}]
    assert result["queuedYmCalls"] == [
        [
            111506392,
            "init",
            {
                "ssr": True,
                "webvisor": True,
                "clickmap": True,
                "ecommerce": "dataLayer",
                "referrer": "https://referrer.test/",
                "url": "https://example.test/page/",
                "accurateTrackBounce": True,
                "trackLinks": True,
            },
        ]
    ]


def test_metrika_loader_reuses_an_existing_external_script(
    built_sites: dict[str, Path],
) -> None:
    """A repeated Metrika bootstrap reuses its matching external script."""

    source = _extract_metrika_script(_read_output(built_sites["production"], "/"))
    result = _run_metrika_harness(
        source,
        ready_state="complete",
        has_idle_callback=True,
        existing_script=True,
    )

    assert result["inserted"] == []
    assert result["queuedYmCalls"][0][:2] == [111506392, "init"]
    assert len(result["queuedYmCalls"]) == 1


def test_language_bootstrap_normalizes_preferences_and_survives_unavailable_state(
    built_sites: dict[str, Path],
) -> None:
    """Language bootstrap normalizes locales and tolerates unavailable browser state."""

    english = _extract_language_bootstrap(_read_output(built_sites["production"], "/"))
    russian = _extract_language_bootstrap(
        _read_output(built_sites["production"], "/ru/")
    )
    cases = [
        (
            english,
            {"stored": "  RU_ru  ", "languages": ["en-US"]},
            f"{TEST_ORIGIN}/ru/",
        ),
        (
            english,
            {"stored": None, "languages": ["zz-ZZ", "ru-KZ"]},
            f"{TEST_ORIGIN}/ru/",
        ),
        (english, {"stored": "invalid", "languages": ["ru-RU"]}, f"{TEST_ORIGIN}/ru/"),
        (
            english,
            {"storage_throws": True, "languages": ["ru-RU"]},
            f"{TEST_ORIGIN}/ru/",
        ),
        (english, {"languages": [], "language": "ru_RU"}, f"{TEST_ORIGIN}/ru/"),
        (english, {"languages": None, "language": "en-US"}, None),
        (russian, {"stored": None, "languages": ["en-US"]}, None),
    ]

    for bootstrap, arguments, expected_redirect in cases:
        result = _run_language_bootstrap(bootstrap, **arguments)
        assert result["redirectedTo"] == expected_redirect, arguments
        assert result["language"] in {"en", "ru"}


def test_language_redirect_preserves_query_and_fragment_exactly(
    built_sites: dict[str, Path],
) -> None:
    """Automatic locale redirects preserve the original query and fragment."""

    bootstrap = _extract_language_bootstrap(
        _read_output(built_sites["production"], "/")
    )
    result = _run_language_bootstrap(
        bootstrap,
        stored=None,
        languages=["ru-RU"],
        search="?preview=1&utm_source=test",
        fragment="#section",
    )

    assert result == {
        "redirectedTo": (f"{TEST_ORIGIN}/ru/?preview=1&utm_source=test#section"),
        "replacedUrl": None,
        "storageWrites": [],
        "language": "en",
    }


def test_manual_language_choice_overrides_stale_storage_and_cleans_its_marker(
    built_sites: dict[str, Path],
) -> None:
    """A manual RU marker beats stale EN storage and is removed from the URL."""

    bootstrap = _extract_language_bootstrap(
        _read_output(built_sites["production"], "/ru/")
    )
    result = _run_language_bootstrap(
        bootstrap,
        stored="en",
        search="?preview=1&tmc-language-choice=ru",
        fragment="#section",
        path="/ru/",
        storage_throws=True,
    )

    assert result == {
        "redirectedTo": None,
        "replacedUrl": "/ru/?preview=1#section",
        "storageWrites": [],
        "language": "ru",
    }


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "/",
            {
                "title": "The Mutating Company — AI systems that doubt",
                "description": (
                    "Deterministic verification, interpretability, and testing "
                    "infrastructure for the AI agents writing tomorrow’s software."
                ),
                "type": "website",
            },
        ),
        (
            "/blog/the-mutation-testing-digest-for-may-2026/",
            {
                "title": (
                    "The mutation-testing digest for May 2026 — The Mutating Company"
                ),
                "description": (
                    "AI Tests Get a Lie Detector, PIT Learns “Equivalent,” "
                    "and Zig Gets a Preview"
                ),
                "type": "article",
            },
        ),
        (
            "/ru/blog/the-mutation-testing-digest-for-may-2026/",
            {
                "title": (
                    "Дайджест мутационного тестирования за май 2026 года — "
                    "The Mutating Company"
                ),
                "description": (
                    "ИИ-тесты получают детектор лжи, PIT узнаёт об "
                    "эквивалентности, а Zig — предварительную версию"
                ),
                "type": "article",
            },
        ),
        (
            "/ru/blog/markdown-feature-fixture/",
            {
                "title": "Markdown feature fixture — The Mutating Company",
                "description": (
                    "Controlled Markdown content used only by the browser test build."
                ),
                "type": "article",
            },
        ),
    ],
    ids=["home", "english-article", "russian-article", "russian-fallback"],
)
def test_production_head_metadata_matches_each_page_locale(
    built_sites: dict[str, Path],
    path: str,
    expected: dict[str, str],
) -> None:
    """Production title, description and social metadata match the page locale."""

    source = _read_output(built_sites["production"], path)
    title = _first(r"<title>(.*?)</title>", source)
    description = _first(r'<meta name="description" content="([^"]*)">', source)
    og_title = _first(r'<meta property="og:title" content="([^"]*)">', source)
    og_description = _first(
        r'<meta property="og:description" content="([^"]*)">', source
    )
    og_type = _first(r'<meta property="og:type" content="([^"]*)">', source)

    assert title == expected["title"]
    assert description == expected["description"]
    assert og_title == title
    assert og_description == description
    assert og_type == expected["type"]
    assert _first(r'<meta property="og:url" content="([^"]*)">', source) == (
        f"{TEST_ORIGIN}{path}"
    )
    assert _first(r'<meta property="og:image" content="([^"]*)">', source) == (
        f"{TEST_ORIGIN}/og.png"
    )
    assert source.count('<meta name="twitter:card" content="summary_large_image">') == 1


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/ru/",
        "/blog/the-mutation-testing-digest-for-may-2026/",
        "/ru/blog/the-mutation-testing-digest-for-may-2026/",
        "/ru/blog/markdown-feature-fixture/",
    ],
)
def test_production_head_exposes_canonical_and_complete_language_alternates(
    built_sites: dict[str, Path],
    path: str,
) -> None:
    """Production pages expose one canonical plus EN, RU and x-default alternates."""

    source = _read_output(built_sites["production"], path)
    canonical = re.findall(r'<link rel="canonical" href="([^"]+)">', source)
    alternates = re.findall(
        r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">',
        source,
    )

    assert canonical == [f"{TEST_ORIGIN}{path}"]
    assert [language for language, _url in alternates] == ["en", "ru", "x-default"]
    assert len({language for language, _url in alternates}) == 3
    assert all(url.startswith(f"{TEST_ORIGIN}/") for _language, url in alternates)
    alternate_map = dict(alternates)
    assert alternate_map["x-default"] == alternate_map["en"]


def _write_controlled_author_content(
    root: Path,
    posts: list[tuple[str, list[str]]],
) -> Path:
    content = root / "content"
    _copy_test_content(content)
    (content / "authors" / "coverage-author.md").write_text(
        "---\ntitle: Coverage Author\nrobot: false\n---\n\nCoverage profile.\n",
        encoding="utf-8",
    )
    for index, (slug, tags) in enumerate(posts, start=1):
        rendered_tags = "\n".join(f"  - {tag}" for tag in tags)
        (content / "blog" / f"coverage-{index:02d}.md").write_text(
            (
                "---\n"
                f"title: Coverage post {index}\n"
                f"date: 1999-01-{index:02d}T00:00:00Z\n"
                f"slug: {slug}\n"
                "description: Controlled tag aggregation.\n"
                "author: coverage-author\n"
                f"tags:\n{rendered_tags}\n"
                "---\n\nBody.\n"
            ),
            encoding="utf-8",
        )
    return content


def _author_tags(source: str) -> list[tuple[str, int, str, str]]:
    block_match = re.search(
        r'(<aside class="author-profile__tags".*?</aside>)',
        source,
        re.DOTALL,
    )
    assert block_match is not None
    block = html.unescape(block_match.group(1))
    matches = re.findall(
        r'<a[^>]+class="signal-tag signal-tag--compact"[^>]+href="([^"]+)"[^>]+aria-label="([^"]+)"[^>]*>.*?'
        r'<span class="signal-tag__label">([^<]+)</span>.*?'
        r'<small class="signal-tag__count"[^>]*>(\d+)</small>',
        block,
        re.DOTALL,
    )
    return [
        (label.strip(), int(count), href, aria) for href, aria, label, count in matches
    ]


def test_author_frequent_tags_merge_case_rank_by_count_and_limit_to_five(
    tmp_path: Path,
) -> None:
    """Frequent tags merge case, rank by count and expose only the top five."""

    posts = [
        ("one", ["Testing", "alpha", "beta", "gamma", "delta", "epsilon"]),
        ("two", ["testing", "alpha", "beta", "gamma", "delta"]),
        ("three", ["TESTING", "alpha", "beta", "gamma"]),
        ("four", ["Testing", "alpha", "beta"]),
        ("five", ["testing", "alpha"]),
        ("six", ["Testing"]),
    ]
    content = _write_controlled_author_content(tmp_path, posts)
    output = tmp_path / "public"
    _run_hugo(content, output)
    tags = _author_tags(_read_output(output, "/authors/coverage-author/"))

    assert [(label.lower(), count) for label, count, _href, _aria in tags] == [
        ("testing", 6),
        ("alpha", 5),
        ("beta", 4),
        ("gamma", 3),
        ("delta", 2),
    ]
    testing = tags[0]
    assert testing[0].lower() == "testing"
    assert testing[2] == "/tags/testing/"
    assert testing[3].lower() == "#testing, 6 authored signals"
    assert all(label != "epsilon" for label, _count, _href, _aria in tags)


def test_author_frequent_tag_ties_are_deterministic_across_content_order(
    tmp_path: Path,
) -> None:
    """Equal-frequency tags retain one deterministic order across input order."""

    posts = [
        ("one", ["gamma", "alpha"]),
        ("two", ["beta", "delta"]),
    ]
    ordered_results = []
    for name, ordered_posts in (("forward", posts), ("reverse", posts[::-1])):
        root = tmp_path / name
        content = _write_controlled_author_content(root, ordered_posts)
        output = root / "public"
        _run_hugo(content, output)
        ordered_results.append(
            _author_tags(_read_output(output, "/authors/coverage-author/"))
        )

    assert ordered_results[0] == ordered_results[1]
    assert sorted(
        (label.lower(), count) for label, count, _href, _aria in ordered_results[0]
    ) == [
        ("alpha", 1),
        ("beta", 1),
        ("delta", 1),
        ("gamma", 1),
    ]
