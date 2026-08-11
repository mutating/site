"""Multilingual routing, fallback, and canonical metadata coverage."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from playwright.sync_api import expect

from conftest import DriverFactory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAY_POST_PATH = "/blog/the-mutation-testing-digest-for-may-2026/"
TEST_DIGEST_PATH = "/blog/test-didegest-june-2026/"


def test_russian_article_uses_translated_content_and_canonical_metadata(
    driver: DriverFactory,
) -> None:
    """Localize article copy while retaining the original author and tags."""

    page = driver(
        stored_language="ru",
        initial_path=f"/ru{MAY_POST_PATH}",
        load_interactive_visuals=False,
    )

    expect(page.locator("html")).to_have_attribute("lang", "ru")
    expect(page.locator(".post__header h1")).to_have_text(
        "Дайджест мутационного тестирования за май 2026 года."
    )
    expect(page.locator(".post__content > p").first).to_contain_text(
        "В мае мутационное тестирование"
    )
    expect(page.locator(".post__author")).to_have_attribute(
        "href", "/ru/authors/muta/"
    )
    tag_links = page.locator(".post__tags a")
    expect(tag_links).to_have_count(8)
    assert all(
        href.startswith("/ru/tags/")
        for href in tag_links.evaluate_all(
            "elements => elements.map(element => element.getAttribute('href'))"
        )
    )


def test_missing_russian_article_resource_falls_back_under_ru_url(
    driver: DriverFactory,
) -> None:
    """Render canonical English copy at the mirrored Russian URL when absent."""

    page = driver(
        stored_language="ru",
        initial_path="/ru/blog/markdown-feature-fixture/",
        load_interactive_visuals=False,
    )

    expect(page).to_have_url(re.compile(r"/ru/blog/markdown-feature-fixture/$"))
    expect(page.locator("html")).to_have_attribute("lang", "ru")
    expect(page.locator(".post__header h1")).to_have_text(
        "Markdown feature fixture."
    )
    expect(page.locator(".post__content > p").first).to_have_text(
        "This article is generated only inside the temporary test site."
    )


def test_latest_source_article_also_has_russian_content(
    driver: DriverFactory,
) -> None:
    """Do not leave the newest source article on its English fallback."""

    page = driver(
        stored_language="ru",
        initial_path=f"/ru{TEST_DIGEST_PATH}",
        load_interactive_visuals=False,
    )

    expect(page.locator(".post__header h1")).to_have_text(
        "Тестовый дайджест за июнь 2026 года."
    )
    expect(page.locator(".post__content > p").first).to_contain_text(
        "В июне 2026 года мутационное тестирование"
    )


def test_multilingual_collections_never_repeat_an_article(
    driver: DriverFactory,
) -> None:
    """Keep blog, tag, and author archive hrefs unique in the Russian site."""

    page = driver(
        stored_language="ru",
        initial_path="/ru/blog/",
        load_interactive_visuals=False,
    )
    origin = page.url.split("/ru/", maxsplit=1)[0]

    for path in (
        "/ru/blog/",
        "/ru/tags/testing/",
        "/ru/authors/muta/posts/",
    ):
        page.goto(f"{origin}{path}", wait_until="domcontentloaded")
        hrefs = page.locator(".blog-card__link").evaluate_all(
            "elements => elements.map(element => element.getAttribute('href'))"
        )
        assert hrefs
        assert len(hrefs) == len(set(hrefs)), path


def test_home_copy_is_identical_in_both_languages(
    driver: DriverFactory,
) -> None:
    """Keep landing-page copy unchanged outside the language control."""

    english = driver(
        stored_language="en",
        load_interactive_visuals=False,
    )
    russian = driver(
        stored_language="ru",
        initial_path="/ru/",
        load_interactive_visuals=False,
    )

    copy_without_language_control = """
      body => {
        const copy = body.cloneNode(true);
        copy.querySelectorAll("[data-language-switcher]").forEach(
          (switcher) => switcher.remove(),
        );
        return copy.innerText;
      }
    """
    assert english.locator("body").evaluate(
        copy_without_language_control
    ) == russian.locator("body").evaluate(copy_without_language_control)


def test_translation_resources_do_not_duplicate_canonical_front_matter() -> None:
    """Store dates, slugs, authors, and taxonomies only on canonical posts."""

    translations = sorted(
        (PROJECT_ROOT / "content" / "blog").glob(
            "*/translation.ru.md"
        )
    )
    assert len(translations) == 4

    for translation in translations:
        front_matter = translation.read_text(encoding="utf-8").split("---", 2)[1]
        assert re.search(r"^title:", front_matter, re.MULTILINE)
        assert re.search(r"^description:", front_matter, re.MULTILINE)
        for canonical_key in ("author", "date", "slug", "tags"):
            assert re.search(
                rf"^{canonical_key}:", front_matter, re.MULTILINE
            ) is None


def test_new_language_is_driven_entirely_by_hugo_configuration(
    tmp_path: Path,
) -> None:
    """Add a third language without changing templates or browser scripts."""

    extra_config = tmp_path / "extra-language.toml"
    extra_config.write_text(
        """
[languages.pt-br]
label = "Português"
locale = "pt-BR"
weight = 3
""".strip(),
        encoding="utf-8",
    )
    destination = tmp_path / "public"
    cache_directory = tmp_path / "cache"
    build = subprocess.run(
        [
            "hugo",
            "--config",
            f"{PROJECT_ROOT / 'hugo.toml'},{extra_config}",
            "--destination",
            str(destination),
            "--cacheDir",
            str(cache_directory),
            "--noBuildLock",
            "--panicOnWarning",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    english_html = (destination / "index.html").read_text(encoding="utf-8")
    portuguese_html = (destination / "pt-br" / "index.html").read_text(
        encoding="utf-8"
    )
    option_pattern = r'data-language-option="([^"]+)"'

    assert set(re.findall(option_pattern, english_html)) == {"ru", "pt-br"}
    assert set(re.findall(option_pattern, portuguese_html)) == {"en", "ru"}
    assert 'var supportedLanguages = ["en","ru","pt-br"]' in english_html
    assert "--language-switcher-desktop-width: 59px" in english_html

    bootstrap_match = re.search(
        r"<script>\s*(\(function \(\) \{.*?\}\)\(\);)\s*</script>",
        english_html,
        re.DOTALL,
    )
    assert bootstrap_match is not None
    bootstrap = bootstrap_match.group(1)

    def resolve_initial_language(
        *,
        stored_language: str | None,
        device_languages: list[str],
    ) -> dict[str, object]:
        harness = f"""
let redirectedTo = null;
globalThis.localStorage = {{
  getItem: () => {json.dumps(stored_language)},
}};
Object.defineProperty(globalThis, "navigator", {{
  value: {{
    languages: {json.dumps(device_languages)},
    language: {json.dumps(device_languages[0])},
  }},
}});
globalThis.document = {{ documentElement: {{ dataset: {{}} }} }};
globalThis.window = {{
  location: {{
    origin: "https://example.test",
    search: "",
    hash: "",
    replace: (url) => {{ redirectedTo = url; }},
  }},
}};
{bootstrap}
console.log(JSON.stringify({{
  redirectedTo,
  language: document.documentElement.dataset.language,
}}));
"""
        result = subprocess.run(
            ["node", "-e", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    assert resolve_initial_language(
        stored_language=None,
        device_languages=["pt-BR", "en-US"],
    ) == {
        "redirectedTo": "https://example.test/pt-br/",
        "language": "en",
    }
    assert resolve_initial_language(
        stored_language="pt-br",
        device_languages=["en-US"],
    ) == {
        "redirectedTo": "https://example.test/pt-br/",
        "language": "en",
    }

    browser_script = (
        PROJECT_ROOT / "themes" / "mutating" / "assets" / "js" / "site.js"
    ).read_text(encoding="utf-8")
    assert 'language !== "en"' not in browser_script
    assert 'language !== "ru"' not in browser_script
