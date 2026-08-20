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
MAY_POST_PATH = "/blog/the-mutation-testing-digest-for-may-2026/"
LATEST_DIGEST_PATH = "/blog/the-mutation-testing-digest-for-july-2026/"
JUNE_DIGEST_PATH = "/blog/the-mutation-testing-digest-for-june-2026/"


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
        initial_path=f"/ru{LATEST_DIGEST_PATH}",
        load_interactive_visuals=False,
    )

    expect(page).to_have_url(re.compile(r"/ru/blog/the-mutation-testing-digest-for-july-2026/$"))
    expect(page.locator("html")).to_have_attribute("lang", "ru")
    expect(page.locator(".post__header h1")).to_have_text(
        "Дайджест мутационного тестирования за июль 2026 года."
    )
    expect(page.locator(".post__content > p").first).to_contain_text(
        "Прототип на большой языковой модели"
    )
    expect(page.locator("time")).to_have_attribute("datetime", "2026-08-01")
    expect(page.locator(".post__author")).to_have_attribute(
        "href", "/ru/authors/muta/"
    )
    tag_links = page.locator(".post__tags a")
    expect(tag_links).to_have_count(7)
    assert set(tag_links.evaluate_all(
        "elements => elements.map(element => element.getAttribute('href'))"
    )) == {
        "/ru/tags/testing/",
        "/ru/tags/mutation-testing/",
        "/ru/tags/llm/",
        "/ru/tags/adoption/",
        "/ru/tags/real-world/",
        "/ru/tags/survivor-investigation/",
        "/ru/tags/tools/",
    }


def test_june_source_article_also_has_russian_content(
    driver: DriverFactory,
) -> None:
    """Keep the June source article on its translated resource as well."""

    page = driver(
        stored_language="ru",
        initial_path=f"/ru{JUNE_DIGEST_PATH}",
        load_interactive_visuals=False,
    )

    expect(page).to_have_url(re.compile(r"/ru/blog/the-mutation-testing-digest-for-june-2026/$"))
    expect(page.locator("html")).to_have_attribute("lang", "ru")
    expect(page.locator(".post__header h1")).to_have_text(
        "Дайджест мутационного тестирования за июнь 2026 года."
    )
    expect(page.locator(".post__content > p").first).to_contain_text(
        "Самые интересные июньские новости о мутационном тестировании"
    )
    expect(page.locator("time")).to_have_attribute("datetime", "2026-07-01")
    expect(page.locator(".post__author")).to_have_attribute(
        "href", "/ru/authors/muta/"
    )
    tag_links = page.locator(".post__tags a")
    expect(tag_links).to_have_count(7)
    assert set(tag_links.evaluate_all(
        "elements => elements.map(element => element.getAttribute('href'))"
    )) == {
        "/ru/tags/testing/",
        "/ru/tags/mutation-testing/",
        "/ru/tags/llm/",
        "/ru/tags/adversarial/",
        "/ru/tags/new-tools/",
        "/ru/tags/score-audit/",
        "/ru/tags/evidence/",
    }


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


def test_russian_home_localizes_interface_and_marketing_content(
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
    expect(russian.locator(".main-nav").get_by_role("link", name="Блог")).to_be_visible()
    expect(
        russian.locator(".main-nav").get_by_role("link", name="Контакты")
    ).to_be_visible()
    expect(
        russian.get_by_role("button", name="Переключить цветовую тему")
    ).to_be_visible()
    expect(russian.locator(".mobile-menu > summary")).to_have_text("Меню")
    expect(russian).to_have_title(
        "The Mutating Company — ИИ, который сомневается"
    )
    expect(russian.locator(".hero h1")).to_contain_text("ИИ,")
    expect(russian.locator(".hero h1")).to_contain_text("который сомневается.")
    expect(
        russian.locator(".hero__actions").get_by_role(
            "link", name="Читать наш манифест"
        )
    ).to_have_attribute("href", "#sections")
    expect(
        russian.locator(".hero__actions").get_by_role(
            "link", name="Заглянуть в open-source лабораторию"
        )
    ).to_have_attribute("href", "#open-source")
    expect(russian.locator(".hero__signal")).to_have_attribute(
        "aria-label", "Состояние системы"
    )
    expect(russian.locator(".mascot-card__art")).to_have_attribute(
        "alt", "Весёлый розовый одноглазый мутант с подозрительной ухмылкой"
    )
    expect(russian.locator("#complexity h2")).to_have_text(
        "Энтропия сначала шепчет. Потом выставляет счёт."
    )
    expect(russian.locator("#thesis h2")).to_have_text(
        "Чем больше интеллекта, тем больше нужно сопротивления."
    )
    expect(russian.locator("#containment h2")).to_have_text(
        "Удержать плазму. Направить энергию."
    )
    expect(russian.locator("#what-we-build h2")).to_have_text(
        "Скептический слой для стека агентных систем."
    )
    assert russian.locator(".pillar-card h3").all_inner_texts() == [
        "Верифицируемость",
        "Интерпретируемость",
        "Тестирование на скорости агентов",
    ]
    expect(russian.locator("#scale h2")).to_have_text(
        "Локально, когда нужно. Распределённо, когда всерьёз."
    )
    assert russian.locator(".feedback-node strong").all_inner_texts() == [
        "Продукт лучше",
        "Тесты быстрее",
        "Сигналы точнее",
        "Агенты умнее",
    ]
    expect(russian.locator("#the-deal h2")).to_have_text(
        "Люди и роботы могут сосуществовать."
    )
    expect(russian.locator("#dispatches-heading")).to_have_text(
        "Свежие сигналы из лаборатории."
    )
    expect(russian.locator(".dispatches__status")).to_contain_text(
        "Последние заметки"
    )
    call_to_actions = russian.locator(".dispatch-card__cta")
    expect(call_to_actions).to_have_count(2)
    assert all(
        "Читать заметку" in text for text in call_to_actions.all_inner_texts()
    )
    localized_card = russian.locator(
        f'.dispatch-card[href="/ru{LATEST_DIGEST_PATH}"]'
    )
    expect(localized_card.locator("h3")).to_have_text(
        "Дайджест мутационного тестирования за июль 2026 года"
    )
    expect(localized_card.locator(".dispatch-card__description")).to_have_text(
        "Выжившие мутанты как рабочие задания, результаты под проверкой"
    )
    expect(russian.locator("#contact h2")).to_have_text(
        "Создаёте серьёзную агентную инфраструктуру?"
    )
    expect(russian.locator("#contact .button")).to_have_text(
        re.compile(r"Начать разговор")
    )
    expect(russian.locator(".site-footer__tagline")).to_have_text(
        "ИИ, который сомневается. Для программ, которые не должны подвести."
    )

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
        initial_path=f"/ru{MAY_POST_PATH}",
        load_interactive_visuals=False,
    )

    expect(page.locator(".post__header .section-index")).to_have_text("БЛОГ")
    expect(page.locator(".post__header time")).to_have_text("01 июня 2026")
    expect(page.locator(".post-reading-time > span").first).to_have_text(
        "15 минут чтения"
    )
    expect(page.locator("#post-word-count")).to_have_text("3144 слова")
    expect(page.locator(".post__author")).to_have_text("Автор: Muta")
    expect(page.locator(".author-status__tooltip")).to_contain_text(
        "Muta — автоматизированный автор."
    )
    expect(page.locator(".post__toc-label--desktop")).to_have_text(
        "На этой странице"
    )
    expect(page.locator(".post__toc-label--mobile")).to_have_text("22 раздела")
    expect(page.locator(".post__tags")).to_have_attribute(
        "aria-label", "Темы этой заметки"
    )
    expect(page.locator(".post__nav")).to_have_attribute(
        "aria-label", "Навигация по публикациям"
    )
    expect(page.locator(".post__nav-link--next small")).to_have_text("Следующая")


def test_russian_author_profiles_use_localized_content_and_shared_metadata(
    driver: DriverFactory,
) -> None:
    """Translate author names and biographies without duplicating identity metadata."""

    page = driver(
        stored_language="ru",
        initial_path="/ru/authors/main/",
        load_interactive_visuals=False,
    )

    expect(page.locator(".author-profile h1")).to_have_text("Евгений Блинов")
    expect(page.locator(".author-profile__content")).to_have_text(
        "Генеральный директор."
    )
    expect(page.locator(".author-profile__status")).to_have_attribute(
        "data-author-status", "human"
    )
    expect(page.locator(".author-profile__portrait img")).to_have_attribute(
        "src", "https://pomponchik.org/41.png"
    )
    expect(page.locator(".author-profile__links a")).to_have_attribute(
        "href", "https://pomponchik.org/"
    )
    expect(page).to_have_title("Евгений Блинов — The Mutating Company")

    origin = page.url.split("/ru/", maxsplit=1)[0]
    page.goto(f"{origin}/ru/authors/", wait_until="domcontentloaded")
    main_card = page.locator('.author-directory-card[href="/ru/authors/main/"]')
    expect(main_card.locator("h2")).to_have_text("Евгений Блинов")
    expect(main_card.locator("p")).to_have_text("Генеральный директор.")

    page.goto(f"{origin}/ru/authors/muta/", wait_until="domcontentloaded")
    expect(page.locator(".author-profile h1")).to_have_text("Muta")
    expect(page.locator(".author-profile__content")).to_contain_text(
        "Muta — директор по сомнениям"
    )
    expect(page.locator(".author-profile__content")).to_contain_text(
        "скорость агентов встречается с давлением верификации"
    )
    expect(page.locator(".author-profile__status")).to_have_attribute(
        "data-author-status", "robot"
    )
    expect(page.locator(".author-posts__archive")).to_have_attribute(
        "href", "/ru/authors/muta/posts/"
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
    assert {
        translation.relative_to(translation_root) for translation in translations
    } == {
        Path("main/translation.ru.md"),
        Path("muta/translation.ru.md"),
    }

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
    expected_translation_paths = {
        Path("mt-state-may-2026/translation.ru.md"),
        Path("mt-state-june-2026/translation.ru.md"),
        Path("mt-state-july-2026/translation.ru.md"),
    }
    translation_root = PROJECT_ROOT / "content" / "blog"
    assert {
        translation.relative_to(translation_root)
        for translation in translations
    } == expected_translation_paths

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
