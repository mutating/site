"""Browser coverage for the Hugo blog and Markdown article templates."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import DriverFactory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUGO_CONFIG = tomllib.loads((PROJECT_ROOT / "hugo.toml").read_text())
PAGER_SIZE = int(HUGO_CONFIG["pagination"]["pagerSize"])
MARKDOWN_FIXTURE_PATH = "/blog/markdown-feature-fixture/"
UNTAGGED_FIXTURE_PATH = "/blog/unauthored-fixture/"
NEWS_FIXTURE_PATH = "/news/controlled-release-announcement/"
CONTROLLED_TAG_PATH = "/tags/controlled-pagination/"
CLIPBOARD_INIT_SCRIPT = """
window.__copiedCode = null;
Object.defineProperty(navigator, "clipboard", {
  configurable: true,
  value: {
    writeText: async (text) => {
      window.__copiedCode = text;
    },
  },
});
"""
HEADING_CHAIN_GEOMETRY_SCRIPT = """
heading => {
  const anchor = heading.querySelector(".post__heading-anchor");
  const chain = heading.querySelector(".post__heading-chain");
  const textRange = document.createRange();
  textRange.selectNodeContents(anchor.firstChild);

  const lines = Array.from(textRange.getClientRects());
  const lastLine = lines.at(-1);
  const chainRect = chain.getBoundingClientRect();
  const contentRect = heading.closest(".post__content").getBoundingClientRect();
  const fontSize = Number.parseFloat(getComputedStyle(heading).fontSize);

  return {
    lineCount: lines.length,
    fontSize,
    horizontalGap: chainRect.left - lastLine.right,
    verticalDelta:
      chainRect.top + chainRect.height / 2
      - (lastLine.top + lastLine.height / 2),
    chainRight: chainRect.right,
    contentRight: contentRect.right,
  };
}
"""


def test_blog_index_uses_shared_chrome(driver: DriverFactory) -> None:
    """Keep the blog index inside the site's shared navigation and theme chrome."""

    page = driver(initial_path="/blog/")

    expect(page.locator(".site-header")).to_be_visible()
    expect(page.locator(".site-footer")).to_be_visible()
    expect(page.get_by_role("button", name="Toggle color theme")).to_be_visible()
    expect(page.locator(".blog-index__header > .section-index")).to_have_text(
        "BLOG"
    )
    expect(
        page.locator(".blog-index__header > a.section-index")
    ).to_have_count(0)
    assert 1 <= page.locator(".blog-card").count() <= PAGER_SIZE
    expect(page.locator(".blog-card__cta")).to_have_count(0)
    expect(page.get_by_text("Read field note", exact=True)).to_have_count(0)


def test_browser_assets_use_content_fingerprinted_urls(
    driver: DriverFactory,
) -> None:
    """Force browsers to fetch changed copy-button CSS and JavaScript bundles."""

    page = driver(load_interactive_visuals=False)
    stylesheet = page.locator('link[rel="stylesheet"]')
    script = page.locator('script[src*="/js/site."]')

    expect(stylesheet).to_have_attribute(
        "href",
        re.compile(r"/css/site(?:\.min)?\.[0-9a-f]{64}\.css$"),
    )
    expect(script).to_have_attribute(
        "src",
        re.compile(r"/js/site(?:\.min)?\.[0-9a-f]{64}\.js$"),
    )
    expect(stylesheet).to_have_attribute("integrity", re.compile(r"^sha256-"))
    expect(script).to_have_attribute("integrity", re.compile(r"^sha256-"))


def test_shared_blog_links_open_the_hugo_index(driver: DriverFactory) -> None:
    """Route both shared Blog links to the Hugo index instead of a home anchor."""

    page = driver(viewport=(1440, 900), load_interactive_visuals=False)
    expect(page.locator('.main-nav a[href="/blog/"]')).to_have_count(1)
    expect(page.locator('.site-footer a[href="/blog/"]')).to_have_count(1)
    page.locator('.main-nav a[href="/blog/"]').click()

    expect(page).to_have_url(re.compile(r"/blog/$"))
    expect(page.locator(".blog-index h1")).to_be_visible()


def test_shared_news_links_open_the_news_index(driver: DriverFactory) -> None:
    """Expose News in shared navigation and keep its cards in that section."""

    page = driver(viewport=(1440, 900), load_interactive_visuals=False)
    expect(page.locator('.main-nav a[href="/news/"]')).to_have_count(1)
    expect(page.locator('.site-footer a[href="/news/"]')).to_have_count(1)
    page.locator('.main-nav a[href="/news/"]').click()

    expect(page).to_have_url(re.compile(r"/news/$"))
    expect(page.locator(".blog-index__header > .section-index")).to_have_text(
        "NEWS"
    )
    expect(page.locator(".blog-card__link")).to_have_attribute(
        "href", NEWS_FIXTURE_PATH
    )


def test_introducing_posts_live_only_in_news() -> None:
    """Keep product announcements out of Blog while preserving old redirects."""

    blog = PROJECT_ROOT / "content" / "blog"
    news = PROJECT_ROOT / "content" / "news"
    blog_announcements = sorted(blog.glob("introducing-*"))
    news_announcements = sorted(news.glob("introducing-*"))

    assert blog_announcements == []
    assert len(news_announcements) == 11
    for announcement in news_announcements:
        front_matter = (announcement / "index.md").read_text()
        assert f'aliases: ["/blog/{announcement.name}/"]' in front_matter


def test_blog_cards_open_individual_posts(driver: DriverFactory) -> None:
    """Open a card while keeping list-only title punctuation out of the article."""

    page = driver(initial_path="/blog/")
    first_title = page.locator(".blog-card h2").first.inner_text()
    page.locator(".blog-card__link").first.click()

    expect(page.locator(".post__header .section-index")).to_have_text("BLOG")
    article_title = page.locator(".post__header h1").inner_text()
    expected_list_title = (
        article_title if article_title.endswith(".") else f"{article_title}."
    )
    assert first_title == expected_list_title
    expect(page).to_have_url(re.compile(r"/blog/[^/]+/$"))


def test_project_release_post_template_places_logo_in_heading() -> None:
    """Keep optional release artwork decorative, square, and next to the title."""

    template = (
        PROJECT_ROOT / "themes/mutating/layouts/news/single.html"
    ).read_text()
    stylesheet = (
        PROJECT_ROOT / "themes/mutating/assets/css/site.css"
    ).read_text()
    assert 'with .Params.logo' in template
    assert 'post__title-row--with-logo' in template
    assert '.Resources.GetMatch .' in template
    assert 'class="post__project-logo" aria-hidden="true"' in template
    assert 'alt=""' in template
    assert ".post__project-logo {" in stylesheet
    assert "aspect-ratio: 1;" in stylesheet


def test_skelet_release_uses_the_full_color_mark_without_its_wordmark() -> None:
    """Keep the canonical iridescent artwork instead of the old monochrome logo."""

    logo = (
        PROJECT_ROOT
        / "content"
        / "news"
        / "introducing-skelet"
        / "logo-mark.svg"
    ).read_text()
    colors = {
        tuple(int(color[index : index + 2], 16) for index in (0, 2, 4))
        for color in re.findall(r'fill="#([0-9a-fA-F]{6})"', logo)
    }

    assert 'aria-label="SKELET"' not in logo
    assert len(colors) > 100
    assert any(max(color) - min(color) > 40 for color in colors)


def test_blog_and_tag_pages_share_one_post_list_partial() -> None:
    """Keep separate collection templates while sharing all list markup."""

    blog_template = (
        PROJECT_ROOT / "themes/mutating/layouts/blog/list.html"
    ).read_text()
    news_template = (
        PROJECT_ROOT / "themes/mutating/layouts/news/list.html"
    ).read_text()
    tag_template = (
        PROJECT_ROOT / "themes/mutating/layouts/_default/term.html"
    ).read_text()
    shared_partial = (
        PROJECT_ROOT
        / "themes/mutating/layouts/partials/post-list.html"
    ).read_text()

    assert 'partial "post-list.html"' in blog_template
    assert 'partial "post-list.html"' in news_template
    assert 'partial "post-list.html"' in tag_template
    assert "blog-card" not in blog_template
    assert "blog-card" not in news_template
    assert "blog-card" not in tag_template
    assert 'class="blog-card"' in shared_partial
    assert 'class="blog-pagination"' in shared_partial
    assert 'data-has-pagination="{{ $hasPagination }}"' in shared_partial


def test_tag_component_is_shared_by_posts_authors_and_term_pages() -> None:
    """Render one tag primitive at copy, compact, and display sizes."""

    layouts = PROJECT_ROOT / "themes" / "mutating" / "layouts"
    tag_partial = (layouts / "partials" / "tag.html").read_text()
    post_template = (layouts / "blog" / "single.html").read_text()
    author_template = (layouts / "authors" / "single.html").read_text()
    term_template = (layouts / "_default" / "term.html").read_text()

    assert 'partial "tag.html"' in post_template
    assert '"size" "copy"' in post_template
    assert ".Params.tags" in post_template
    assert ".LinkTitle" not in post_template
    assert 'partial "tag.html"' in author_template
    assert '"size" "compact"' in author_template
    assert "range first 5 $tagItems" in author_template
    assert '"size" "display"' in term_template
    assert '"label" .Data.Term' in term_template
    assert "signal-tag--%s" in tag_partial
    assert "signal-tag__bracket" in tag_partial
    assert "signal-tag__label" in tag_partial
    assert "signal-tag__count" in tag_partial


@pytest.mark.parametrize(
    ("viewport", "page_overflows", "border_width"),
    [
        ((390, 600), "true", "0px"),
        ((1440, 1600), "false", "1px"),
    ],
    ids=["scrolling-page", "short-page"],
)
def test_unpaginated_list_keeps_tail_line_only_to_fill_a_short_page(
    driver: DriverFactory,
    viewport: tuple[int, int],
    page_overflows: str,
    border_width: str,
) -> None:
    """Drop the redundant final rule unless it helps fill the viewport."""

    page = driver(
        viewport=viewport,
        load_interactive_visuals=False,
        initial_path="/tags/verification/",
    )
    index = page.locator('.blog-index[data-has-pagination="false"]')
    expect(index).to_be_visible()
    expect(page.locator(".blog-pagination")).to_have_count(0)
    expect(index).to_have_attribute("data-page-overflows", page_overflows)
    expect(page.locator(".blog-card:last-child")).to_have_css(
        "border-bottom-width",
        border_width,
    )


def test_unpaginated_blog_tail_recomputes_after_live_resize(
    driver: DriverFactory,
) -> None:
    """Recompute the final blog rule when a mounted page starts or stops scrolling."""

    page = driver(
        viewport=(390, 600),
        load_interactive_visuals=False,
        initial_path="/tags/verification/",
    )
    index = page.locator('.blog-index[data-has-pagination="false"]')
    last_card = page.locator(".blog-card:last-child")
    expect(index).to_have_attribute("data-page-overflows", "true")
    expect(last_card).to_have_css("border-bottom-width", "0px")

    page.set_viewport_size({"width": 1440, "height": 1600})
    expect(index).to_have_attribute("data-page-overflows", "false")
    expect(last_card).to_have_css("border-bottom-width", "1px")

    page.set_viewport_size({"width": 390, "height": 600})
    expect(index).to_have_attribute("data-page-overflows", "true")
    expect(last_card).to_have_css("border-bottom-width", "0px")


def test_unpaginated_blog_tail_recomputes_when_fonts_finish_loading(
    driver: DriverFactory,
) -> None:
    """Re-measure the final blog rule after the held font-ready promise resolves."""

    page = driver(
        viewport=(1440, 1600),
        load_interactive_visuals=False,
        initial_path="/tags/verification/",
        init_scripts=(
            """
            (() => {
              let release;
              const heldReady = new Promise((resolve) => { release = resolve; });
              Object.defineProperty(document.fonts, "ready", {
                configurable: true,
                get: () => heldReady,
              });
              window.__releaseHeldFontsReady = release;
            })();
            """,
        ),
    )
    index = page.locator('.blog-index[data-has-pagination="false"]')
    expect(index).to_have_attribute("data-page-overflows", "false")
    page.evaluate(
        """
        () => {
          const spacer = document.createElement("div");
          spacer.id = "font-metric-spacer";
          spacer.style.height = "1800px";
          document.body.append(spacer);
        }
        """
    )
    expect(index).to_have_attribute("data-page-overflows", "false")

    page.evaluate("window.__releaseHeldFontsReady()")
    expect(index).to_have_attribute("data-page-overflows", "true")
    expect(page.locator(".blog-card:last-child")).to_have_css(
        "border-bottom-width", "0px"
    )


def test_post_renders_full_markdown_features(driver: DriverFactory) -> None:
    """Render every supported Markdown structure from the controlled test post."""

    page = driver(initial_path=MARKDOWN_FIXTURE_PATH)

    article = page.locator(".post__content")
    expect(article.locator("h2")).to_have_count(3)
    expect(article.locator("ol")).to_have_count(1)
    expect(article.locator("blockquote")).to_have_count(1)
    expect(article.locator("table")).to_have_count(1)
    expect(article.locator("pre code")).to_have_count(2)


@pytest.mark.parametrize(
    "viewport",
    [(1280, 720), (390, 844)],
    ids=["desktop", "phone"],
)
def test_post_word_count_moves_into_the_reading_time_tooltip(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep only reading time visible and disclose words on hover or focus."""

    page = driver(
        viewport=viewport,
        load_interactive_visuals=False,
        initial_path=MARKDOWN_FIXTURE_PATH,
    )
    metadata = page.locator(".post__meta")
    reading_time = metadata.locator(".post-reading-time")
    visible_reading_time = reading_time.locator(":scope > span").first
    tooltip = reading_time.locator(".post-reading-time__tooltip")

    expect(reading_time).to_have_attribute("tabindex", "0")
    expect(reading_time).to_have_attribute(
        "aria-describedby",
        "post-word-count",
    )
    expect(visible_reading_time).to_have_text(re.compile(r"^\d+ min read$"))
    expect(metadata.locator(":scope > span:not(.post-reading-time)")).to_have_count(0)
    expect(tooltip).to_have_text(re.compile(r"^\d+ words$"))
    expect(tooltip).to_be_hidden()

    reading_time.hover()
    expect(tooltip).to_be_visible()
    presentation = tooltip.evaluate(
        """
        (element) => {
          const authorTooltip = document.querySelector(
            ".post__meta .author-status__tooltip",
          );
          const read = (tooltip) => {
            const style = getComputedStyle(tooltip);
            const rect = tooltip.getBoundingClientRect();
            return {
              background: style.backgroundColor,
              borderColor: style.borderColor,
              borderRadius: style.borderRadius,
              backdropFilter: style.backdropFilter,
              boxShadow: style.boxShadow,
              fontFamily: style.fontFamily,
              fontSize: style.fontSize,
              width: rect.width,
              height: rect.height,
              clientWidth: tooltip.clientWidth,
              scrollWidth: tooltip.scrollWidth,
              left: rect.left,
              right: rect.right,
            };
          };
          return {
            reading: read(element),
            author: read(authorTooltip),
            viewportWidth: document.documentElement.clientWidth,
          };
        }
        """
    )
    for property_name in (
        "background",
        "borderColor",
        "borderRadius",
        "backdropFilter",
        "boxShadow",
        "fontFamily",
        "fontSize",
    ):
        assert presentation["reading"][property_name] == (
            presentation["author"][property_name]
        )
    assert presentation["reading"]["left"] >= 0
    assert presentation["reading"]["right"] <= presentation["viewportWidth"]
    assert presentation["reading"]["width"] < presentation["author"]["width"]
    assert presentation["reading"]["width"] <= (
        presentation["reading"]["scrollWidth"] + 3
    )

    tooltip.evaluate(
        """
        element => {
          element.textContent =
            "1234567890123456789012345678901234567890123456789012345678901234567890 words";
        }
        """
    )
    long_value = tooltip.evaluate(
        """
        element => {
          const rect = element.getBoundingClientRect();
          return {
            width: rect.width,
            height: rect.height,
            clientWidth: element.clientWidth,
            scrollWidth: element.scrollWidth,
            left: rect.left,
            right: rect.right,
            viewportWidth: document.documentElement.clientWidth,
          };
        }
        """
    )
    assert long_value["width"] <= 340
    assert long_value["height"] > presentation["reading"]["height"]
    assert long_value["scrollWidth"] <= long_value["clientWidth"]
    assert long_value["left"] >= 0
    assert long_value["right"] <= long_value["viewportWidth"]

    page.mouse.move(viewport[0] - 1, viewport[1] - 1)
    expect(tooltip).to_be_hidden()
    reading_time.evaluate("element => element.focus()")
    expect(tooltip).to_be_visible()


def test_post_heading_links_stay_visually_quiet_until_hover(
    driver: DriverFactory,
) -> None:
    """Keep headings unchanged at rest, then reveal their underline and chain."""

    page = driver(
        load_interactive_visuals=False,
        reduced_motion="reduce",
        initial_path=MARKDOWN_FIXTURE_PATH,
    )

    headings = page.locator(".post__content h2")
    links = headings.locator(":scope > .post__heading-anchor")
    expect(links).to_have_count(3)

    first_heading = headings.first
    first_link = links.first
    heading_box_before = first_heading.bounding_box()
    assert heading_box_before is not None
    expect(first_link).to_have_attribute("href", "#ordered-evidence")
    expect(first_link).to_have_css("text-decoration-line", "none")
    chain = first_link.locator(".post__heading-chain")
    expect(chain).to_have_attribute("aria-hidden", "true")
    expect(chain).to_have_text("🔗")
    expect(chain).to_have_css("opacity", "0")

    first_link.hover()
    expect(first_link).to_have_css("text-decoration-line", "underline")
    page.wait_for_function(
        "element => getComputedStyle(element).opacity === '1'",
        arg=chain.element_handle(),
    )
    heading_box_after = first_heading.bounding_box()
    assert heading_box_after is not None
    assert heading_box_after["width"] == heading_box_before["width"]
    assert heading_box_after["height"] == heading_box_before["height"]

    geometry = first_heading.evaluate(HEADING_CHAIN_GEOMETRY_SCRIPT)
    assert geometry["lineCount"] == 1
    assert geometry["horizontalGap"] >= geometry["fontSize"] * 0.27
    assert abs(geometry["verticalDelta"]) <= geometry["fontSize"] * 0.12


@pytest.mark.parametrize("viewport", [(1600, 1000), (900, 900), (390, 844)])
def test_multiline_post_heading_chain_follows_the_last_line(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep the chain beside the final line across desktop and mobile widths."""

    page = driver(
        viewport=viewport,
        load_interactive_visuals=False,
        reduced_motion="reduce",
        initial_path="/blog/localized-post-fixture/",
    )

    heading = page.locator(
        "#a-deliberately-extended-controlled-heading-that-wraps-across-"
        "every-supported-article-viewport"
    )
    link = heading.locator(":scope > .post__heading-anchor")
    chain = link.locator(".post__heading-chain")
    link.hover()
    expect(chain).to_have_css("opacity", "1")

    geometry = heading.evaluate(HEADING_CHAIN_GEOMETRY_SCRIPT)
    assert geometry["lineCount"] >= 2
    assert geometry["horizontalGap"] >= geometry["fontSize"] * 0.27
    assert abs(geometry["verticalDelta"]) <= geometry["fontSize"] * 0.12
    assert geometry["chainRight"] <= geometry["contentRight"] + 0.5


def test_post_heading_link_navigates_to_its_fragment(
    driver: DriverFactory,
) -> None:
    """Update the URL hash when a reader clicks a Markdown heading."""

    page = driver(
        load_interactive_visuals=False,
        initial_path=MARKDOWN_FIXTURE_PATH,
    )

    target = page.locator("#structured-signals")
    link = target.locator(":scope > .post__heading-anchor")
    expect(link).to_have_attribute("href", "#structured-signals")
    link.click()

    expect(page).to_have_url(re.compile(r"#structured-signals$"))
    assert page.evaluate("window.location.hash") == "#structured-signals"
    assert target.get_attribute("id") == "structured-signals"


def test_code_copy_button_appears_only_on_block_hover(
    driver: DriverFactory,
) -> None:
    """Reveal the code-copy control on hover and hide it again afterward."""

    page = driver(
        load_interactive_visuals=False,
        initial_path=MARKDOWN_FIXTURE_PATH,
    )

    blocks = page.locator(".post__content .code-block")
    block = blocks.first
    button = block.get_by_role("button", name="Copy code to clipboard")
    expect(blocks).to_have_count(2)
    expect(button).to_be_hidden()
    expect(block).to_have_attribute("data-copy-hover", "false")

    block.locator("pre").focus()
    expect(button).to_be_hidden()

    block.hover()
    expect(block).to_have_attribute("data-copy-hover", "true")
    expect(button).to_be_visible()

    page.locator(".post__content h2").first.hover()
    expect(block).to_have_attribute("data-copy-hover", "false")
    expect(button).to_be_hidden()


def test_every_fenced_block_receives_its_own_copy_control(
    driver: DriverFactory,
) -> None:
    """Enhance every fenced block in a controlled multi-block Markdown post."""

    page = driver(
        load_interactive_visuals=False,
        initial_path=MARKDOWN_FIXTURE_PATH,
    )
    fenced_blocks = page.locator(".post__content pre:has(code)")
    enhanced_blocks = page.locator(".post__content .code-block")
    buttons = page.locator(".post__content .code-copy-button")

    expect(fenced_blocks).to_have_count(2)
    expect(enhanced_blocks).to_have_count(2)
    expect(buttons).to_have_count(2)

    for index in range(2):
        block = enhanced_blocks.nth(index)
        own_button = block.locator(":scope > .code-copy-button")
        expect(own_button).to_have_count(1)
        block.hover()
        expect(own_button).to_be_visible()
        expect(buttons.nth(1 - index)).to_be_hidden()


def test_code_copy_button_writes_the_rendered_source(
    driver: DriverFactory,
) -> None:
    """Copy one fenced source block and expose brief success feedback.

    The clipboard shim is installed as a context init script so the fixture
    can open the Markdown fixture directly.  This preserves the important
    ordering (the shim exists before site JavaScript binds the copy handler)
    while avoiding an otherwise discarded homepage navigation.
    """

    page = driver(
        load_interactive_visuals=False,
        reduced_motion="reduce",
        initial_path=MARKDOWN_FIXTURE_PATH,
        init_scripts=(CLIPBOARD_INIT_SCRIPT,),
    )

    block = page.locator(".post__content .code-block").first
    expected_source = block.locator("code").text_content()
    block.scroll_into_view_if_needed()
    block.hover()
    button = block.locator(".code-copy-button")
    expect(button).to_be_visible()
    expect(button).to_have_css("opacity", "1")
    expect(button).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 0)")
    button.click()

    expect(button).to_have_attribute("data-copy-state", "copied")
    expect(button).to_have_text("Copied")
    expect(button).to_have_attribute(
        "aria-label",
        "Code copied to clipboard",
    )
    assert page.evaluate("window.__copiedCode") == expected_source


@pytest.mark.parametrize(
    "clipboard_mode",
    ["missing", "rejected", "insecure"],
)
def test_code_copy_falls_back_when_modern_clipboard_is_unavailable(
    driver: DriverFactory,
    clipboard_mode: str,
) -> None:
    """Use execCommand when Clipboard API is missing, insecure, or rejected."""

    setup = {
        "missing": "value: undefined",
        "rejected": (
            'value: {writeText: async () => { throw new Error("blocked"); }}'
        ),
        "insecure": (
            "value: {writeText: async () => { "
            "window.__unexpectedClipboardWrite = true; }}"
        ),
    }[clipboard_mode]
    secure = "false" if clipboard_mode == "insecure" else "true"
    page = driver(
        initial_path=MARKDOWN_FIXTURE_PATH,
        load_interactive_visuals=False,
        init_scripts=(
            f"""
            (() => {{
              window.__copyFallbackCalls = [];
              Object.defineProperty(window, "isSecureContext", {{
                configurable: true,
                value: {secure},
              }});
              Object.defineProperty(navigator, "clipboard", {{
                configurable: true,
                {setup},
              }});
              document.execCommand = (command) => {{
                const textarea = document.querySelector("body > textarea");
                window.__copyFallbackCalls.push({{
                  command,
                  value: textarea?.value ?? null,
                }});
                return true;
              }};
            }})();
            """,
        ),
    )
    block = page.locator(".post__content .code-block").first
    expected_source = block.locator("code").text_content()
    button = block.locator(".code-copy-button")

    block.scroll_into_view_if_needed()
    block.hover()
    expect(button).to_be_visible()
    expect(button).to_have_css("opacity", "1")
    expect(button).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 0)")
    button.click()
    expect(button).to_have_attribute("data-copy-state", "copied")
    expect(button).to_have_text("Copied")
    expect(button).to_have_attribute("aria-label", "Code copied to clipboard")
    assert page.evaluate("window.__copyFallbackCalls") == [
        {"command": "copy", "value": expected_source}
    ]
    assert page.locator("body > textarea").count() == 0
    assert page.evaluate("window.__unexpectedClipboardWrite || false") is False


def test_code_copy_reports_accessible_error_when_all_copy_methods_fail(
    driver: DriverFactory,
) -> None:
    """Expose accessible error feedback when both copy mechanisms fail."""

    page = driver(
        initial_path=MARKDOWN_FIXTURE_PATH,
        load_interactive_visuals=False,
        init_scripts=(
            """
            Object.defineProperty(navigator, "clipboard", {
              configurable: true,
              value: {writeText: async () => { throw new Error("blocked"); }},
            });
            document.execCommand = () => false;
            """,
        ),
    )
    block = page.locator(".post__content .code-block").first
    button = block.locator(".code-copy-button")

    block.hover()
    button.click()
    expect(button).to_have_attribute("data-copy-state", "error")
    expect(button).to_have_text("Try again")
    expect(button).to_have_attribute("aria-label", "Code could not be copied")
    assert page.locator("body > textarea").count() == 0


@pytest.mark.parametrize(
    "fallback_failure",
    [
        'document.execCommand = () => { throw new Error("legacy copy blocked"); };',
        (
            "HTMLTextAreaElement.prototype.select = () => { "
            'throw new Error("selection blocked"); };'
        ),
    ],
    ids=["exec-command-throws", "textarea-select-throws"],
)
def test_code_copy_removes_temporary_textarea_when_fallback_throws(
    driver: DriverFactory,
    fallback_failure: str,
) -> None:
    """Remove the fallback textarea when selection or execCommand raises."""

    page = driver(
        initial_path=MARKDOWN_FIXTURE_PATH,
        load_interactive_visuals=False,
        init_scripts=(
            """
            Object.defineProperty(navigator, "clipboard", {
              configurable: true,
              value: {writeText: async () => { throw new Error("blocked"); }},
            });
            """
            + fallback_failure,
        ),
    )
    button = page.locator(
        ".post__content .code-block .code-copy-button"
    ).first
    button.evaluate("element => element.click()")

    expect(button).to_have_attribute("data-copy-state", "error")
    expect(button).to_have_text("Try again")
    expect(button).to_have_attribute("aria-label", "Code could not be copied")
    expect(page.locator("body > textarea")).to_have_count(0)


def test_code_copy_feedback_resets_and_restarts_its_timer(
    driver: DriverFactory,
) -> None:
    """Reset copy feedback after 1800 ms and restart that timer on a new copy."""

    page = driver(
        initial_path=MARKDOWN_FIXTURE_PATH,
        load_interactive_visuals=False,
        init_scripts=(
            CLIPBOARD_INIT_SCRIPT,
            """
            (() => {
              const nativeSetTimeout = window.setTimeout.bind(window);
              const nativeClearTimeout = window.clearTimeout.bind(window);
              let nextId = 9000;
              window.__copyTimers = [];
              window.setTimeout = (callback, delay, ...args) => {
                if (delay !== 1800) return nativeSetTimeout(callback, delay, ...args);
                const timer = {id: ++nextId, callback, cancelled: false, delay};
                window.__copyTimers.push(timer);
                return timer.id;
              };
              window.clearTimeout = (id) => {
                const timer = window.__copyTimers.find((item) => item.id === id);
                if (timer) timer.cancelled = true;
                else nativeClearTimeout(id);
              };
              window.__runCopyTimer = (id) => {
                const timer = window.__copyTimers.find((item) => item.id === id);
                if (timer && !timer.cancelled) timer.callback();
              };
            })();
            """,
        ),
    )
    block = page.locator(".post__content .code-block").first
    button = block.locator(".code-copy-button")

    block.scroll_into_view_if_needed()
    block.hover()
    expect(button).to_be_visible()
    expect(button).to_have_css("opacity", "1")
    expect(button).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 0)")
    button.click()
    expect(button).to_have_attribute("data-copy-state", "copied")
    first_id = page.evaluate("window.__copyTimers[0].id")
    button.evaluate("element => element.click()")
    expect(button).to_have_attribute("data-copy-state", "copied")
    timers = page.evaluate(
        "window.__copyTimers.map(({id, cancelled, delay}) => ({id, cancelled, delay}))"
    )
    assert timers == [
        {"id": first_id, "cancelled": True, "delay": 1800},
        {"id": first_id + 1, "cancelled": False, "delay": 1800},
    ]
    page.evaluate("id => window.__runCopyTimer(id)", first_id)
    expect(button).to_have_attribute("data-copy-state", "copied")
    page.evaluate("id => window.__runCopyTimer(id)", first_id + 1)
    expect(button).to_have_attribute("data-copy-state", "idle")
    expect(button).to_have_text("Copy")
    expect(button).to_have_attribute("aria-label", "Copy code to clipboard")


def test_post_tags_render_only_when_present_and_link_to_terms(
    driver: DriverFactory,
) -> None:
    """List linked post tags at the bottom and omit an empty tag region."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}{MARKDOWN_FIXTURE_PATH}", wait_until="domcontentloaded")

    tag_region = page.locator(".post__tags")
    expect(tag_region).to_be_visible()
    expect(tag_region).to_have_attribute(
        "aria-label",
        "Topics in this publication",
    )
    expect(tag_region.locator("h2")).to_have_count(0)
    expect(tag_region.locator("a")).to_have_count(2)
    assert {
        text.strip() for text in tag_region.locator("a").all_text_contents()
    } == {
        "[#testing]",
        "[#verification]",
    }
    verification = tag_region.get_by_role("link", name="#verification")
    expect(verification).to_have_attribute(
        "href",
        "/tags/verification/",
    )
    expect(verification).to_have_class(re.compile(r"\bsignal-tag--copy\b"))
    expect(verification.locator(".signal-tag__bracket")).to_have_count(2)
    expect(verification.locator(".signal-tag__bracket").first).to_have_text("[#")
    expect(verification.locator(".signal-tag__bracket").last).to_have_text("]")
    expect(verification.locator(".signal-tag__label")).to_have_text("verification")
    expect(verification).to_have_css("border-top-width", "0px")
    verification.hover()
    expect(verification.locator(".signal-tag__label")).to_have_css(
        "text-decoration-line",
        "underline",
    )

    page.goto(f"{origin}{UNTAGGED_FIXTURE_PATH}", wait_until="domcontentloaded")
    expect(page.locator(".post__tags")).to_have_count(0)


@pytest.mark.parametrize(
    "viewport",
    [(1280, 720), (390, 844)],
    ids=["desktop", "phone"],
)
def test_post_tags_match_article_copy_typography(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Match tag-link size and weight to article copy at every width."""

    page = driver(
        viewport=viewport,
        load_interactive_visuals=False,
        initial_path=MARKDOWN_FIXTURE_PATH,
    )
    article_typography = page.locator(".post__content").evaluate(
        """
        element => ({
          size: getComputedStyle(element).fontSize,
          weight: getComputedStyle(element).fontWeight,
        })
        """
    )
    tag_typography = page.locator(".post__tags a").first.evaluate(
        """
        element => ({
          size: getComputedStyle(element).fontSize,
          weight: getComputedStyle(element).fontWeight,
        })
        """
    )

    assert tag_typography == article_typography


def test_tag_link_opens_a_distinct_filtered_collection(
    driver: DriverFactory,
) -> None:
    """Find a tagged fixture across every page without admitting untagged posts."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}{MARKDOWN_FIXTURE_PATH}", wait_until="domcontentloaded")
    page.locator(".post__tags").get_by_role(
        "link",
        name="#verification",
    ).click()

    expect(page).to_have_url(re.compile(r"/tags/verification/$"))
    collection = page.locator('.blog-index[data-collection-kind="tag"]')
    expect(collection).to_be_visible()
    expect(collection.locator(".section-index")).to_have_text(
        "PUBLICATIONS / TAGGED SIGNALS"
    )
    expect(collection.locator("h1")).to_have_text(
        "Signals tagged [#verification]."
    )
    heading_tag = collection.locator("h1 .signal-tag--display")
    expect(heading_tag).to_be_visible()
    expect(heading_tag).to_have_attribute("href", "/tags/verification/")
    expect(heading_tag.locator(".signal-tag__bracket")).to_have_count(2)
    heading_size = collection.locator("h1").evaluate(
        "element => Number.parseFloat(getComputedStyle(element).fontSize)"
    )
    tag_size = heading_tag.evaluate(
        "element => Number.parseFloat(getComputedStyle(element).fontSize)"
    )
    assert abs(tag_size - heading_size) <= 0.1
    heading_tag.click()
    expect(page).to_have_url(re.compile(r"/tags/verification/$"))

    post_urls: set[str] = set()
    while True:
        post_urls.update(
            collection.locator(".blog-card__link").evaluate_all(
                "(links) => links.map((link) => link.getAttribute('href'))"
            )
        )
        older = collection.get_by_role("link", name="Older →")
        if older.count() == 0:
            break
        older.click()
        page.wait_for_load_state("domcontentloaded")

    assert MARKDOWN_FIXTURE_PATH in post_urls
    assert UNTAGGED_FIXTURE_PATH not in post_urls


def test_long_display_tag_wraps_as_text_without_detaching_its_bracket(
    driver: DriverFactory,
) -> None:
    """Keep a long term and its closing bracket in one inline text flow."""

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
        initial_path="/tags/survivor-investigation/",
    )
    geometry = page.locator("h1 .signal-tag--display").evaluate(
        """
        element => {
          const label = element.querySelector(".signal-tag__label");
          const brackets = element.querySelectorAll(".signal-tag__bracket");
          const labelLines = [...label.getClientRects()];
          const closingBracket = brackets[1].getBoundingClientRect();
          return {
            display: getComputedStyle(element).display,
            lineCount: labelLines.length,
            closingGap:
              closingBracket.left - labelLines[labelLines.length - 1].right,
            overflows:
              document.documentElement.scrollWidth >
              document.documentElement.clientWidth,
          };
        }
        """
    )

    assert geometry["display"] == "inline"
    assert geometry["lineCount"] >= 2
    assert abs(geometry["closingGap"]) <= 2
    assert geometry["overflows"] is False


def test_tag_collection_paginates_and_punctuates_titles_once(
    driver: DriverFactory,
) -> None:
    """Paginate the controlled tag and give every list title one final period."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}{CONTROLLED_TAG_PATH}", wait_until="domcontentloaded")

    titles = page.locator(".blog-card h2").all_text_contents()
    authored_period = f"Controlled tag signal {PAGER_SIZE + 2:02d}."
    automatic_period = f"Controlled tag signal {PAGER_SIZE + 1:02d}."
    assert authored_period in titles
    assert automatic_period in titles
    assert all(title.endswith(".") for title in titles)
    assert all(not title.endswith("..") for title in titles)

    expect(page.locator(".blog-card")).to_have_count(PAGER_SIZE)
    expect(page.locator(".blog-pagination__page")).to_have_count(2)
    expect(page.get_by_role("link", name="Older →")).to_have_attribute(
        "href",
        f"{CONTROLLED_TAG_PATH}page/2/",
    )
    first_page_urls = page.locator(".blog-card__link").evaluate_all(
        "(links) => links.map((link) => link.getAttribute('href'))"
    )

    page.get_by_role("link", name="Older →").click()
    expect(page).to_have_url(
        re.compile(r"/tags/controlled-pagination/page/2/$")
    )
    expect(page.locator(".blog-card")).to_have_count(2)
    second_page_urls = page.locator(".blog-card__link").evaluate_all(
        "(links) => links.map((link) => link.getAttribute('href'))"
    )

    all_urls = first_page_urls + second_page_urls
    assert len(all_urls) == PAGER_SIZE + 2
    assert len(all_urls) == len(set(all_urls))


def test_post_page_title_receives_exactly_one_final_period(
    driver: DriverFactory,
) -> None:
    """Punctuate article titles without wrapping or restyling the existing h1."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    generated_title_number = PAGER_SIZE + 2

    page.goto(f"{origin}{MARKDOWN_FIXTURE_PATH}", wait_until="domcontentloaded")
    heading = page.locator(".post__header > h1")
    expect(heading).to_have_text("Markdown feature fixture.")
    expect(heading.locator(":scope > *")).to_have_count(0)

    page.goto(
        f"{origin}/blog/tag-pagination-{generated_title_number:02d}/",
        wait_until="domcontentloaded",
    )
    heading = page.locator(".post__header > h1")
    expect(heading).to_have_text(
        f"Controlled tag signal {generated_title_number:02d}."
    )
    assert not heading.inner_text().endswith("..")


def test_language_fenced_code_uses_themed_syntax_highlighting(
    driver: DriverFactory,
) -> None:
    """Give Python token classes distinct, theme-specific Chroma colors."""

    page = driver(color_scheme="light", load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}{MARKDOWN_FIXTURE_PATH}", wait_until="domcontentloaded")

    code = page.locator("code.language-python")
    expect(code).to_have_attribute("data-lang", "python")
    for token_class in ("k", "nf", "s2", "nb"):
        expect(code.locator(f".{token_class}").first).to_be_visible()

    def palette() -> dict[str, str]:
        return page.evaluate(
            """
            () => {
              const root = document.querySelector("code.language-python");
              const color = (selector) =>
                getComputedStyle(root.querySelector(selector)).color;
              return {
                background: getComputedStyle(root.closest("pre")).backgroundColor,
                text: getComputedStyle(root).color,
                keyword: color(".k"),
                function: color(".nf"),
                string: color(".s2"),
                builtin: color(".nb"),
              };
            }
            """
        )

    light = palette()
    assert len(set(light.values())) >= 5

    page.get_by_role("button", name="Toggle color theme").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    dark = palette()

    assert len(set(dark.values())) >= 5
    assert dark["background"] != light["background"]
    for token in ("text", "keyword", "function", "string", "builtin"):
        assert dark[token] != light[token]


def test_blog_theme_persists_across_navigation(driver: DriverFactory) -> None:
    """Preserve a manual theme choice from the home page through a blog article."""

    page = driver(color_scheme="light")
    origin = page.url.rstrip("/")
    page.get_by_role("button", name="Toggle color theme").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    page.goto(f"{origin}/blog/", wait_until="domcontentloaded")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.locator(".blog-card__link").first.click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


def test_desktop_post_centers_copy_and_moves_toc_to_the_left(
    driver: DriverFactory,
) -> None:
    """Center desktop article copy on the viewport without TOC-induced bias."""

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}{MARKDOWN_FIXTURE_PATH}",
        wait_until="domcontentloaded",
    )

    geometry = page.evaluate(
        """
        () => {
          const copy = document.querySelector(".post__content");
          const toc = document.querySelector(".post__toc");
          const copyRect = copy.getBoundingClientRect();
          const tocRect = toc.getBoundingClientRect();
          return {
            viewportCenter: document.documentElement.clientWidth / 2,
            copyCenter: copyRect.left + copyRect.width / 2,
            copyLeft: copyRect.left,
            tocLeft: tocRect.left,
            tocRight: tocRect.right,
          };
        }
        """
    )

    assert abs(geometry["copyCenter"] - geometry["viewportCenter"]) <= 1
    assert geometry["tocLeft"] < geometry["copyLeft"]
    assert geometry["tocRight"] <= geometry["copyLeft"]

    toc = page.locator("details.post__toc")
    expect(toc).to_have_attribute("open", "")
    expect(toc.locator(".post__toc-label--desktop")).to_be_visible()
    expect(toc.locator(".post__toc-label--desktop")).to_have_text("On this page")
    expect(toc.locator(".post__toc-label--mobile")).to_be_hidden()
    expect(toc.locator(".post__toc-toggle")).to_be_hidden()


@pytest.mark.parametrize(
    "viewport",
    [(900, 900), (390, 844)],
    ids=["medium", "phone"],
)
def test_responsive_post_table_of_contents_expands_on_request(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Share one compact, expandable outline across medium and phone widths."""

    page = driver(
        viewport=viewport,
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}{MARKDOWN_FIXTURE_PATH}",
        wait_until="domcontentloaded",
    )

    toc = page.locator("details.post__toc")
    summary = toc.locator("summary")
    navigation = toc.locator("#TableOfContents")
    desktop_label = toc.locator(".post__toc-label--desktop")
    mobile_label = toc.locator(".post__toc-label--mobile")
    toggle = toc.locator(".post__toc-toggle")

    expect(toc).not_to_have_attribute("open", "")
    expect(summary).to_be_visible()
    expect(desktop_label).to_be_hidden()
    expect(mobile_label).to_be_visible()
    expect(mobile_label).to_have_text("3 sections")
    expect(toggle).to_be_visible()
    expect(navigation).to_be_hidden()

    summary.hover()
    expect(summary).to_have_css("text-decoration-line", "none")
    expect(mobile_label).to_have_css("text-decoration-line", "underline")
    expect(toggle).to_have_css("text-decoration-line", "none")

    collapsed_geometry = toc.evaluate(
        """
        element => {
          const summary = element.querySelector("summary");
          const label = element.querySelector(".post__toc-label--mobile");
          const toggle = element.querySelector(".post__toc-toggle");
          const header = document.querySelector(".post__header");
          const metadata = document.querySelector(".post__meta");
          const firstContent = document.querySelector(
            ".post__content > :first-child",
          );
          const tocRect = element.getBoundingClientRect();
          const summaryRect = summary.getBoundingClientRect();
          const labelRect = label.getBoundingClientRect();
          const toggleRect = toggle.getBoundingClientRect();
          const headerRect = header.getBoundingClientRect();
          const metadataRect = metadata.getBoundingClientRect();
          const firstContentRect = firstContent.getBoundingClientRect();
          const center = (headerRect.bottom + tocRect.bottom) / 2;

          return {
            height: tocRect.height,
            gapFromHeader: tocRect.top - headerRect.bottom,
            gapFromMetadata: headerRect.bottom - metadataRect.bottom,
            gapToFirstContent: firstContentRect.top - tocRect.bottom,
            summaryHeight: summaryRect.height,
            labelDelta: labelRect.top + labelRect.height / 2 - center,
            toggleDelta: toggleRect.top + toggleRect.height / 2 - center,
            labelColor: getComputedStyle(label).color,
            toggleColor: getComputedStyle(toggle).color,
            labelInset: labelRect.left - summaryRect.left,
            toggleInset: summaryRect.right - toggleRect.right,
            toggleContent: getComputedStyle(toggle, "::before").content,
          };
        }
        """
    )
    assert collapsed_geometry["height"] <= 44
    assert abs(collapsed_geometry["gapFromHeader"]) <= 1
    assert 26 <= collapsed_geometry["gapFromMetadata"] <= 28
    assert 26 <= collapsed_geometry["gapToFirstContent"] <= 28
    assert abs(
        collapsed_geometry["gapFromMetadata"]
        - collapsed_geometry["gapToFirstContent"]
    ) <= 0.5
    assert collapsed_geometry["summaryHeight"] >= 40
    assert abs(collapsed_geometry["labelDelta"]) <= 1
    assert abs(collapsed_geometry["toggleDelta"]) <= 1
    assert collapsed_geometry["toggleColor"] == collapsed_geometry["labelColor"]
    assert abs(
        collapsed_geometry["toggleInset"] - collapsed_geometry["labelInset"]
    ) <= 0.5
    assert collapsed_geometry["toggleContent"] == '"+"'

    collapsed_content_top = page.locator(".post__content").evaluate(
        "element => element.getBoundingClientRect().top"
    )
    summary.click()

    expect(toc).to_have_attribute("open", "")
    expect(navigation).to_be_visible()
    expect(navigation.locator('a[href^="#"]')).not_to_have_count(0)
    summary.hover()
    expect(summary).to_have_css("text-decoration-line", "none")
    expect(mobile_label).to_have_css("text-decoration-line", "underline")
    expect(toggle).to_have_css("text-decoration-line", "none")
    assert toggle.evaluate(
        'element => getComputedStyle(element, "::before").content'
    ) == '"−"'
    open_outline_spacing = toc.evaluate(
        """
        element => {
          const summary = element.querySelector("summary");
          const links = Array.from(
            element.querySelectorAll('#TableOfContents a[href^="#"]'),
          );
          const tocRect = element.getBoundingClientRect();
          const summaryRect = summary.getBoundingClientRect();
          const firstLinkRect = links[0].getBoundingClientRect();
          const lastLinkRect = links.at(-1).getBoundingClientRect();
          return {
            top: firstLinkRect.top - summaryRect.bottom,
            bottom: tocRect.bottom - lastLinkRect.bottom,
          };
        }
        """
    )
    assert 26 <= open_outline_spacing["top"] <= 28
    assert 26 <= open_outline_spacing["bottom"] <= 28
    assert abs(
        open_outline_spacing["top"] - open_outline_spacing["bottom"]
    ) <= 1
    expanded_content_top = page.locator(".post__content").evaluate(
        "element => element.getBoundingClientRect().top"
    )
    expanded_content_gap = page.evaluate(
        """
        () => {
          const toc = document.querySelector(".post__toc");
          const firstContent = document.querySelector(
            ".post__content > :first-child",
          );
          return firstContent.getBoundingClientRect().top
            - toc.getBoundingClientRect().bottom;
        }
        """
    )
    assert expanded_content_top > collapsed_content_top
    assert 26 <= expanded_content_gap <= 28


def test_post_toc_tracks_live_breakpoint_changes_and_cannot_close_on_desktop(
    driver: DriverFactory,
) -> None:
    """Track live TOC breakpoints and reject attempts to collapse it on desktop."""

    page = driver(
        viewport=(900, 900),
        load_interactive_visuals=False,
        initial_path=MARKDOWN_FIXTURE_PATH,
    )
    toc = page.locator("details.post__toc")
    expect(toc).not_to_have_attribute("open", "")

    page.set_viewport_size({"width": 1200, "height": 900})
    expect(toc).to_have_attribute("open", "")
    toc.evaluate(
        """
        (element) => {
          element.removeAttribute("open");
          element.dispatchEvent(new Event("toggle"));
        }
        """
    )
    expect(toc).to_have_attribute("open", "")

    page.set_viewport_size({"width": 900, "height": 900})
    expect(toc).not_to_have_attribute("open", "")


def test_single_edge_recommendation_spans_the_post_navigation(
    driver: DriverFactory,
) -> None:
    """Expand lone Previous and Next recommendations across the full nav width.

    The temporary test site contains only synthetic posts. Dedicated fixtures
    pin both ends of that controlled collection, so publishing or reordering a
    real article cannot change this layout contract.
    """

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    origin = page.url.rstrip("/")

    edge_expectations = {
        "/blog/newest-navigation-edge-fixture/": (
            "post__nav-link--previous",
            "/blog/localized-post-fixture/",
        ),
        "/blog/oldest-navigation-edge-fixture/": (
            "post__nav-link--next",
            "/blog/author-pagination-01/",
        ),
    }
    directions: set[str] = set()
    for post_url, (expected_direction, expected_href) in edge_expectations.items():
        response = page.goto(
            f"{origin}{post_url}",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.ok
        navigation = page.locator(".post__nav")
        link = navigation.locator(".post__nav-link")
        expect(link).to_have_count(1)

        navigation_box = navigation.bounding_box()
        link_box = link.bounding_box()
        assert navigation_box is not None
        assert link_box is not None
        assert abs(link_box["width"] - navigation_box["width"]) <= 1

        classes = link.get_attribute("class") or ""
        assert set(classes.split()) == {"post__nav-link", expected_direction}
        assert link.get_attribute("href") == expected_href
        directions.add(expected_direction.removeprefix("post__nav-link--"))

    assert directions == {"previous", "next"}


def test_post_navigation_has_equal_space_above_and_below(
    driver: DriverFactory,
) -> None:
    """Match the nav-to-card gap with the card-to-footer gap."""

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}{MARKDOWN_FIXTURE_PATH}",
        wait_until="domcontentloaded",
    )

    spacing = page.evaluate(
        """
        () => {
          const navigation = document.querySelector(".post__nav");
          const cards = [...navigation.querySelectorAll(".post__nav-link")];
          const footer = document.querySelector(".site-footer");
          const navigationRect = navigation.getBoundingClientRect();
          const firstCardTop = Math.min(
            ...cards.map((card) => card.getBoundingClientRect().top),
          );
          return {
            above: firstCardTop - navigationRect.top,
            below:
              footer.getBoundingClientRect().top -
              navigationRect.bottom,
          };
        }
        """
    )

    assert abs(spacing["above"] - spacing["below"]) <= 1


def test_blog_pagination_covers_posts_and_both_directional_edges(
    driver: DriverFactory,
) -> None:
    """Cover every direct page route plus the real links at both edges.

    Two former tests repeated the initial blog load: one directly loaded every
    numbered route to audit card coverage, while the other followed the first
    ``Older`` link and inspected the outward controls. The merged flow keeps
    both navigation modes deliberately. It first clicks the real link and
    verifies page two, then directly reloads each numbered URL (including page
    two) so a working link cannot hide a broken cold route. The apparently
    redundant reload therefore preserves the original test semantics while
    removing only the duplicated browser context and page-one bootstrap.
    """

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}/blog/", wait_until="domcontentloaded")
    page_links = page.locator(".blog-pagination__page")
    collected_urls: list[str] = []
    page_counts: list[int] = []
    if page_links.count() == 0:
        expect(page.locator(".blog-pagination")).to_have_count(0)
        cards = page.locator(".blog-card")
        page_counts.append(cards.count())
        collected_urls.extend(
            cards.locator(".blog-card__link").evaluate_all(
                "(links) => links.map((link) => link.getAttribute('href'))"
            )
        )
    else:
        total_pages = max(int(value) for value in page_links.all_text_contents())
        expect(
            page.locator('.blog-pagination__page[aria-current="page"]')
        ).to_have_text("1")
        expect(
            page.locator('.blog-pagination__direction[aria-disabled="true"]')
        ).to_have_text("← Newer")
        older = page.get_by_role("link", name="Older →")
        expect(older).to_have_attribute("href", "/blog/page/2/")

        page_one_cards = page.locator(".blog-card")
        page_counts.append(page_one_cards.count())
        collected_urls.extend(
            page_one_cards.locator(".blog-card__link").evaluate_all(
                "(links) => links.map((link) => link.getAttribute('href'))"
            )
        )

        older.click()
        expect(page).to_have_url(re.compile(r"/blog/page/2/$"))

        for page_number in range(2, total_pages + 1):
            path = f"/blog/page/{page_number}/"
            page.goto(f"{origin}{path}", wait_until="domcontentloaded")
            expect(
                page.locator('.blog-pagination__page[aria-current="page"]')
            ).to_have_text(str(page_number))
            cards = page.locator(".blog-card")
            page_counts.append(cards.count())
            collected_urls.extend(
                cards.locator(".blog-card__link").evaluate_all(
                    "(links) => links.map((link) => link.getAttribute('href'))"
                )
            )

        newer_url = (
            "/blog/"
            if total_pages == 2
            else f"/blog/page/{total_pages - 1}/"
        )
        expect(page.get_by_role("link", name="← Newer")).to_have_attribute(
            "href",
            newer_url,
        )
        expect(
            page.locator('.blog-pagination__direction[aria-disabled="true"]')
        ).to_have_text("Older →")

    assert len(collected_urls) == len(set(collected_urls))
    assert all(count == PAGER_SIZE for count in page_counts[:-1])
    assert 1 <= page_counts[-1] <= PAGER_SIZE


def test_blog_pagination_stays_inside_a_narrow_viewport(
    driver: DriverFactory,
) -> None:
    """Stack mobile page numbers above directions without viewport overflow."""

    page = driver(
        viewport=(390, 844),
        load_interactive_visuals=False,
    )
    page.goto(f"{page.url.rstrip('/')}/blog/", wait_until="domcontentloaded")
    pagination = page.locator(".blog-pagination")

    if pagination.count() == 0:
        assert page.evaluate(
            "document.documentElement.scrollWidth "
            "<= document.documentElement.clientWidth"
        )
        return

    expect(pagination).to_be_visible()

    geometry = page.evaluate(
        """
        () => {
          const pages = document.querySelector(".blog-pagination__pages");
          const direction = document.querySelector(
            ".blog-pagination__direction",
          );
          return {
            overflows:
              document.documentElement.scrollWidth >
              document.documentElement.clientWidth,
            pagesTop: pages.getBoundingClientRect().top,
            directionTop: direction.getBoundingClientRect().top,
          };
        }
        """
    )
    assert geometry["overflows"] is False
    assert geometry["pagesTop"] < geometry["directionTop"]


def test_blog_pagination_has_equal_space_above_and_below(
    driver: DriverFactory,
) -> None:
    """Match the list-to-controls gap with the controls-to-footer gap."""

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    page.goto(f"{page.url.rstrip('/')}/blog/", wait_until="domcontentloaded")
    pagination = page.locator(".blog-pagination")

    if pagination.count() == 0:
        return

    spacing = page.evaluate(
        """
        () => {
          const list = document.querySelector(".blog-list");
          const pagination = document.querySelector(".blog-pagination");
          const footer = document.querySelector(".site-footer");
          const listRect = list.getBoundingClientRect();
          const paginationRect = pagination.getBoundingClientRect();
          const footerRect = footer.getBoundingClientRect();
          return {
            above: paginationRect.top - listRect.bottom,
            below: footerRect.top - paginationRect.bottom,
          };
        }
        """
    )

    assert abs(spacing["above"] - spacing["below"]) <= 1
