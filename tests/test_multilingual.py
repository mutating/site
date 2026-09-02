"""Multilingual routing, fallback, and canonical metadata coverage."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

from playwright.sync_api import expect

from conftest import DriverFactory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCALIZED_POST_PATH = "/blog/localized-post-fixture/"


def test_russian_article_uses_translated_content_and_canonical_metadata(
    driver: DriverFactory,
) -> None:
    """Localize article copy while retaining the original author and tags."""

    page = driver(
        stored_language="ru",
        initial_path=f"/ru{LOCALIZED_POST_PATH}",
        load_interactive_visuals=False,
    )

    expect(page.locator("html")).to_have_attribute("lang", "ru")
    expect(page.locator(".post__header h1")).to_have_text(
        "Контролируемый локализованный сигнал."
    )
    expect(page.locator(".post__content > p").first).to_have_text(
        "Эта переведённая статья существует только во временном "
        "тестовом сайте."
    )
    expect(page.locator(".post__author")).to_have_attribute(
        "href", "/ru/authors/test-robot-author/"
    )
    tag_links = page.locator(".post__tags a")
    expect(tag_links).to_have_count(5)
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
        "/ru/authors/test-author/posts/",
    ):
        page.goto(f"{origin}{path}", wait_until="domcontentloaded")
        hrefs = page.locator(".blog-card__link").evaluate_all(
            "elements => elements.map(element => element.getAttribute('href'))"
        )
        assert hrefs
        assert len(hrefs) == len(set(hrefs)), path


def test_russian_home_localizes_without_editorial_copy_expectations(
    driver: DriverFactory,
) -> None:
    """Render one shared home structure with complete language-specific copy."""

    english = driver(
        stored_language="en",
        load_interactive_visuals=False,
    )
    russian = driver(
        stored_language="ru",
        initial_path="/ru/",
        load_interactive_visuals=False,
    )

    expect(english.locator(".main-nav .source-link")).to_have_text("Sources")
    expect(russian.locator(".main-nav .source-link")).to_have_text("Исходники")
    expect(russian.locator(".main-nav")).to_have_attribute(
        "aria-label", "Основная навигация"
    )
    expect(
        russian.locator(".main-nav").get_by_role("link", name="Блог")
    ).to_be_visible()
    expect(
        russian.locator(".main-nav").get_by_role("link", name="Контакты")
    ).to_be_visible()
    expect(
        russian.get_by_role("button", name="Переключить цветовую тему")
    ).to_be_visible()
    expect(russian.locator(".mobile-menu > summary")).to_have_text("Меню")
    assert english.title().strip()
    assert russian.title().strip()
    assert english.title() != russian.title()

    english_actions = english.locator(".hero__actions a")
    russian_actions = russian.locator(".hero__actions a")
    expect(english_actions).to_have_count(2)
    expect(russian_actions).to_have_count(2)
    assert english_actions.evaluate_all(
        "links => links.map(link => link.getAttribute('href'))"
    ) == ["#sections", "#open-source"]
    assert russian_actions.evaluate_all(
        "links => links.map(link => link.getAttribute('href'))"
    ) == ["#sections", "#open-source"]
    assert english_actions.all_inner_texts() != russian_actions.all_inner_texts()

    expect(russian.locator(".hero__signal")).to_have_attribute(
        "aria-label", "Состояние системы"
    )
    english_alt = english.locator(".mascot-card__art").get_attribute("alt")
    russian_alt = russian.locator(".mascot-card__art").get_attribute("alt")
    assert english_alt and russian_alt and english_alt != russian_alt

    for selector in (
        ".hero h1",
        "#complexity h2",
        "#thesis h2",
        "#containment h2",
        "#what-we-build h2",
        ".pillar-card h3",
        "#scale h2",
        ".feedback-node strong",
        "#the-deal h2",
        "#dispatches-heading",
        ".dispatches__status",
        "#contact h2",
        "#contact .button",
        ".site-footer__tagline",
    ):
        english_texts = [
            text.strip() for text in english.locator(selector).all_inner_texts()
        ]
        russian_texts = [
            text.strip() for text in russian.locator(selector).all_inner_texts()
        ]
        assert english_texts and all(english_texts), selector
        assert russian_texts and all(russian_texts), selector
        assert len(english_texts) == len(russian_texts), selector
        assert english_texts != russian_texts, selector

    english_cards = english.locator(".dispatch-card")
    russian_cards = russian.locator(".dispatch-card")
    assert 1 <= english_cards.count() <= 2
    expect(russian_cards).to_have_count(english_cards.count())
    for index in range(english_cards.count()):
        english_card = english_cards.nth(index)
        russian_card = russian_cards.nth(index)
        english_href = english_card.get_attribute("href")
        russian_href = russian_card.get_attribute("href")
        assert english_href and russian_href == f"/ru{english_href}"
        for selector in ("h3", ".dispatch-card__description", ".dispatch-card__cta"):
            assert english_card.locator(selector).inner_text().strip()
            assert russian_card.locator(selector).inner_text().strip()

    home_structure = """
      sections => sections.map(section => ({
        id: section.id,
        classes: section.className,
      }))
    """
    assert english.locator("main > section").evaluate_all(
        home_structure
    ) == russian.locator("main > section").evaluate_all(home_structure)


def test_home_translation_files_keep_structural_html_in_templates() -> None:
    """Store home copy in content files while keeping its markup in the theme."""

    for path in (
        PROJECT_ROOT / "content" / "_index.md",
        PROJECT_ROOT / "content" / "_index.ru.md",
    ):
        source = path.read_text(encoding="utf-8")
        assert "<section" not in source
        assert "<iframe" not in source
        assert "<canvas" not in source


def test_russian_collection_headers_use_localized_copy(
    driver: DriverFactory,
) -> None:
    """Localize collection chrome without fixing any editorial wording."""

    english = driver(
        stored_language="en",
        load_interactive_visuals=False,
    )
    russian = driver(
        stored_language="ru",
        initial_path="/ru/",
        load_interactive_visuals=False,
    )
    origin = english.url.rstrip("/")
    cases = (
        (
            "/blog/",
            "/ru/blog/",
            (
                ".blog-index__header > .section-index",
                ".blog-index__header h1",
                ".blog-index__intro",
            ),
        ),
        (
            "/tags/testing/",
            "/ru/tags/testing/",
            (
                ".blog-index__header > .section-index",
                ".blog-index__header h1",
            ),
        ),
        (
            "/authors/",
            "/ru/authors/",
            (
                ".authors-index__header > .section-index",
                ".authors-index__header h1",
            ),
        ),
    )
    for english_path, russian_path, selectors in cases:
        english.goto(f"{origin}{english_path}", wait_until="domcontentloaded")
        english_texts = {
            selector: english.locator(selector).inner_text().strip()
            for selector in selectors
        }
        russian.goto(f"{origin}{russian_path}", wait_until="domcontentloaded")
        localized_differences = 0
        for selector in selectors:
            russian_text = russian.locator(selector).inner_text().strip()
            assert english_texts[selector]
            assert russian_text
            localized_differences += russian_text != english_texts[selector]
        assert localized_differences, (english_path, english_texts)


def test_collection_translation_resources_only_contain_localized_fields() -> None:
    """Keep routing and archive identity metadata in canonical section files."""

    translations = sorted(
        translation
        for collection in ("blog", "authors", "author-posts")
        for translation in (PROJECT_ROOT / "content" / collection).glob(
            "**/_index.ru.md"
        )
    )

    for translation in translations:
        front_matter = translation.read_text(encoding="utf-8").split("---", 2)[1]
        assert re.search(r"^title:", front_matter, re.MULTILINE)
        assert re.search(r"^description:", front_matter, re.MULTILINE)
        for canonical_key in ("author", "type", "bodyClass"):
            assert re.search(
                rf"^{canonical_key}:", front_matter, re.MULTILINE
            ) is None


def test_russian_home_hero_fits_narrow_viewports(
    driver: DriverFactory,
) -> None:
    """Keep the longer non-wrapping Russian hero line inside the page shell."""

    page = driver(
        stored_language="ru",
        initial_path="/ru/",
        viewport=(390, 844),
        load_interactive_visuals=False,
    )

    for width, height in ((390, 844), (280, 700)):
        page.set_viewport_size({"width": width, "height": height})
        bounds = page.locator(".hero h1 em").evaluate(
            """
            element => {
              const rect = element.getBoundingClientRect();
              return {
                left: rect.left,
                right: rect.right,
                viewportWidth: document.documentElement.clientWidth,
              };
            }
            """
        )
        assert bounds["left"] >= 0
        assert bounds["right"] <= bounds["viewportWidth"]


def test_russian_article_localizes_shared_interface(
    driver: DriverFactory,
) -> None:
    """Use the active Hugo language for chrome, dates, plurals, and accessibility."""

    page = driver(
        stored_language="ru",
        initial_path=f"/ru{LOCALIZED_POST_PATH}",
        load_interactive_visuals=False,
    )

    expect(page.locator(".post__header .section-index")).to_have_text("БЛОГ")
    expect(page.locator(".post__header time")).to_have_text("01 января 2002")
    expect(page.locator(".post-reading-time > span").first).to_have_text(
        re.compile(r"^\d+ (?:минута|минуты|минут) чтения$")
    )
    expect(page.locator("#post-word-count")).to_have_text(
        re.compile(r"^\d+ (?:слово|слова|слов)$")
    )
    expect(page.locator(".post__author")).to_have_text(
        "Автор: Controlled Robot Author"
    )
    expect(page.locator(".author-status__tooltip")).to_contain_text(
        "Controlled Robot Author — автоматизированный автор."
    )
    expect(page.locator(".post__toc-label--desktop")).to_have_text(
        "На этой странице"
    )
    expect(page.locator(".post__toc-label--mobile")).to_have_text("2 раздела")
    expect(page.locator(".post__tags")).to_have_attribute(
        "aria-label", "Темы этой заметки"
    )
    expect(page.locator(".post__nav")).to_have_attribute(
        "aria-label", "Навигация по публикациям"
    )
    expect(page.locator(".post__nav-link--next small")).to_have_text(
        "Следующая"
    )


def test_russian_author_profiles_use_localized_content_and_shared_metadata(
    driver: DriverFactory,
) -> None:
    """Translate author names and biographies without duplicating identity metadata."""

    page = driver(
        stored_language="ru",
        initial_path="/ru/authors/localized-author/",
        load_interactive_visuals=False,
    )

    expect(page.locator(".author-profile h1")).to_have_text(
        "Контролируемый локализованный автор"
    )
    expect(page.locator(".author-profile__content")).to_have_text(
        "Переведённый профиль только для временного тестового "
        "сайта."
    )
    expect(page.locator(".author-profile__status")).to_have_attribute(
        "data-author-status", "human"
    )
    expect(page.locator(".author-profile__portrait img")).to_have_attribute(
        "src", "/muta-v2-transparent-v10.webp"
    )
    expect(page.locator(".author-profile__links a")).to_have_attribute(
        "href", "https://example.com/localized-author"
    )
    expect(page).to_have_title(
        "Контролируемый локализованный автор — "
        "The Mutating Company"
    )

    origin = page.url.split("/ru/", maxsplit=1)[0]
    page.goto(f"{origin}/ru/authors/", wait_until="domcontentloaded")
    localized_card = page.locator(
        '.author-directory-card[href="/ru/authors/localized-author/"]'
    )
    expect(localized_card.locator("h2")).to_have_text(
        "Контролируемый локализованный автор"
    )
    expect(localized_card.locator("p")).to_have_text(
        "Переведённый профиль только для временного тестового "
        "сайта."
    )


def test_missing_russian_author_resource_falls_back_to_canonical_profile(
    driver: DriverFactory,
) -> None:
    """Keep fixture and future author profiles usable before translation arrives."""

    page = driver(
        stored_language="ru",
        initial_path="/ru/authors/test-author/",
        load_interactive_visuals=False,
    )

    expect(page.locator(".author-profile h1")).to_have_text("Controlled Human")
    expect(page.locator(".author-profile__content h2")).to_have_text(
        "Working method"
    )
    expect(page.locator(".author-profile__status")).to_have_attribute(
        "data-author-status", "human"
    )


def test_author_translation_resources_only_contain_localized_fields() -> None:
    """Keep identity, images, and destinations canonical across profile languages."""

    translation_root = PROJECT_ROOT / "content" / "authors"
    translations = sorted(translation_root.glob("*/translation.ru.md"))

    for translation in translations:
        front_matter = translation.read_text(encoding="utf-8").split("---", 2)[1]
        assert re.search(r"^title:", front_matter, re.MULTILINE)
        for canonical_key in ("robot", "photo", "photo2x", "links"):
            assert re.search(
                rf"^{canonical_key}:", front_matter, re.MULTILINE
            ) is None


def test_interface_translation_tables_define_the_same_message_keys() -> None:
    """Require every supported language to cover the complete interface catalog."""

    translation_root = PROJECT_ROOT / "i18n"
    english = tomllib.loads(
        (translation_root / "en.toml").read_text(encoding="utf-8")
    )
    russian = tomllib.loads(
        (translation_root / "ru.toml").read_text(encoding="utf-8")
    )

    assert english.keys() == russian.keys()


def test_translation_resources_do_not_duplicate_canonical_front_matter() -> None:
    """Store dates, slugs, authors, and taxonomies only on canonical posts."""

    translations = sorted(
        (PROJECT_ROOT / "content" / "blog").glob(
            "*/translation.ru.md"
        )
    )

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
