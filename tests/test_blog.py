"""Browser coverage for the Hugo blog and Markdown article templates."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from playwright.sync_api import expect

from conftest import DriverFactory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUGO_CONFIG = tomllib.loads((PROJECT_ROOT / "hugo.toml").read_text())
PAGER_SIZE = int(HUGO_CONFIG["pagination"]["pagerSize"])
MARKDOWN_FIXTURE_PATH = "/blog/markdown-feature-fixture/"
UNTAGGED_FIXTURE_PATH = "/blog/unauthored-fixture/"
CONTROLLED_TAG_PATH = "/tags/controlled-pagination/"
REAL_CODE_POST_PATHS = (
    "/blog/why-agent-throughput-breaks-human-era-verification/",
    "/blog/determinism-is-a-product-feature/",
)


def test_blog_index_uses_shared_chrome(driver: DriverFactory) -> None:
    """Keep the blog index inside the site's shared navigation and theme chrome."""

    page = driver()
    page.goto(f"{page.url.rstrip('/')}/blog/", wait_until="domcontentloaded")

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
        re.compile(r"/css/site\.[0-9a-f]{64}\.css$"),
    )
    expect(script).to_have_attribute(
        "src",
        re.compile(r"/js/site\.[0-9a-f]{64}\.js$"),
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


def test_blog_cards_open_individual_posts(driver: DriverFactory) -> None:
    """Open a card while keeping list-only title punctuation out of the article."""

    page = driver()
    origin = page.url.rstrip("/")
    page.goto(f"{origin}/blog/", wait_until="domcontentloaded")
    first_title = page.locator(".blog-card h2").first.inner_text()
    page.locator(".blog-card__link").first.click()

    expect(page.locator(".post__header .section-index")).to_have_text("BLOG")
    article_title = page.locator(".post__header h1").inner_text()
    expected_list_title = (
        article_title if article_title.endswith(".") else f"{article_title}."
    )
    assert first_title == expected_list_title
    expect(page).to_have_url(re.compile(r"/blog/[^/]+/$"))


def test_blog_and_tag_pages_share_one_post_list_partial() -> None:
    """Keep separate collection templates while sharing all list markup."""

    blog_template = (
        PROJECT_ROOT / "themes/mutating/layouts/blog/list.html"
    ).read_text()
    tag_template = (
        PROJECT_ROOT / "themes/mutating/layouts/_default/term.html"
    ).read_text()
    shared_partial = (
        PROJECT_ROOT
        / "themes/mutating/layouts/partials/post-list.html"
    ).read_text()

    assert 'partial "post-list.html"' in blog_template
    assert 'partial "post-list.html"' in tag_template
    assert "blog-card" not in blog_template
    assert "blog-card" not in tag_template
    assert 'class="blog-card"' in shared_partial
    assert 'class="blog-pagination"' in shared_partial


def test_post_renders_full_markdown_features(driver: DriverFactory) -> None:
    """Render every supported Markdown structure from the controlled test post."""

    page = driver()
    origin = page.url.rstrip("/")
    page.goto(f"{origin}{MARKDOWN_FIXTURE_PATH}", wait_until="domcontentloaded")

    article = page.locator(".post__content")
    expect(article.locator("h2")).to_have_count(3)
    expect(article.locator("ol")).to_have_count(1)
    expect(article.locator("blockquote")).to_have_count(1)
    expect(article.locator("table")).to_have_count(1)
    expect(article.locator("pre code")).to_have_count(1)


def test_post_heading_links_stay_visually_quiet_until_hover(
    driver: DriverFactory,
) -> None:
    """Keep headings unchanged at rest, then reveal their underline and chain."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}{MARKDOWN_FIXTURE_PATH}",
        wait_until="domcontentloaded",
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


def test_post_heading_link_navigates_to_its_fragment(
    driver: DriverFactory,
) -> None:
    """Update the URL hash when a reader clicks a Markdown heading."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}{MARKDOWN_FIXTURE_PATH}",
        wait_until="domcontentloaded",
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

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}{MARKDOWN_FIXTURE_PATH}",
        wait_until="domcontentloaded",
    )

    block = page.locator(".post__content .code-block")
    button = block.get_by_role("button", name="Copy code to clipboard")
    expect(block).to_have_count(1)
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


def test_real_posts_receive_copy_controls_for_every_fenced_block(
    driver: DriverFactory,
) -> None:
    """Enhance every real Markdown code block, not only the test fixture."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")

    for post_path in REAL_CODE_POST_PATHS:
        page.goto(f"{origin}{post_path}", wait_until="domcontentloaded")
        fenced_blocks = page.locator(".post__content pre:has(code)")
        enhanced_blocks = page.locator(".post__content .code-block")
        buttons = page.locator(".post__content .code-copy-button")

        assert fenced_blocks.count() > 0
        expect(enhanced_blocks).to_have_count(fenced_blocks.count())
        expect(buttons).to_have_count(fenced_blocks.count())

        enhanced_blocks.first.hover()
        expect(buttons.first).to_be_visible()


def test_code_copy_button_writes_the_rendered_source(
    driver: DriverFactory,
) -> None:
    """Copy one fenced source block and expose brief success feedback."""

    page = driver(load_interactive_visuals=False)
    page.add_init_script(
        """
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
    )
    page.goto(
        f"{page.url.rstrip('/')}{MARKDOWN_FIXTURE_PATH}",
        wait_until="domcontentloaded",
    )

    block = page.locator(".post__content .code-block")
    expected_source = block.locator("code").text_content()
    block.hover()
    button = block.locator(".code-copy-button")
    button.click()

    expect(button).to_have_attribute("data-copy-state", "copied")
    expect(button).to_have_text("Copied")
    expect(button).to_have_attribute(
        "aria-label",
        "Code copied to clipboard",
    )
    assert page.evaluate("window.__copiedCode") == expected_source


def test_post_tags_render_only_when_present_and_link_to_terms(
    driver: DriverFactory,
) -> None:
    """List linked post tags at the bottom and omit an empty tag region."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}{MARKDOWN_FIXTURE_PATH}", wait_until="domcontentloaded")

    tag_region = page.locator(".post__tags")
    expect(tag_region).to_be_visible()
    expect(tag_region.locator("a")).to_have_count(2)
    assert set(tag_region.locator("a").all_text_contents()) == {
        "#Testing",
        "#Verification",
    }
    expect(tag_region.get_by_role("link", name="#Verification")).to_have_attribute(
        "href",
        "/tags/verification/",
    )

    page.goto(f"{origin}{UNTAGGED_FIXTURE_PATH}", wait_until="domcontentloaded")
    expect(page.locator(".post__tags")).to_have_count(0)


def test_tag_link_opens_a_distinct_filtered_collection(
    driver: DriverFactory,
) -> None:
    """Find a tagged fixture across every page without admitting untagged posts."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}{MARKDOWN_FIXTURE_PATH}", wait_until="domcontentloaded")
    page.locator(".post__tags").get_by_role(
        "link",
        name="#Verification",
    ).click()

    expect(page).to_have_url(re.compile(r"/tags/verification/$"))
    collection = page.locator('.blog-index[data-collection-kind="tag"]')
    expect(collection).to_be_visible()
    expect(collection.locator(".section-index")).to_have_text(
        "BLOG / TAGGED SIGNALS"
    )
    expect(collection.locator("h1")).to_have_text(
        "Signals tagged #Verification."
    )

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


def test_tag_collection_reuses_full_pagination(
    driver: DriverFactory,
) -> None:
    """Paginate a controlled tag independently from the repository post pool."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}{CONTROLLED_TAG_PATH}", wait_until="domcontentloaded")

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


def test_post_list_titles_receive_exactly_one_final_period(
    driver: DriverFactory,
) -> None:
    """Append a period to list titles without duplicating an existing one."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}{CONTROLLED_TAG_PATH}",
        wait_until="domcontentloaded",
    )

    titles = page.locator(".blog-card h2").all_text_contents()
    authored_period = f"Controlled tag signal {PAGER_SIZE + 2:02d}."
    automatic_period = f"Controlled tag signal {PAGER_SIZE + 1:02d}."

    assert authored_period in titles
    assert automatic_period in titles
    assert all(title.endswith(".") for title in titles)
    assert all(not title.endswith("..") for title in titles)


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


def test_single_edge_recommendation_spans_the_post_navigation(
    driver: DriverFactory,
) -> None:
    """Expand lone Previous and Next recommendations across the full nav width."""

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    origin = page.url.rstrip("/")
    page.goto(f"{origin}/blog/", wait_until="domcontentloaded")

    first_post_url = page.locator(".blog-card__link").first.get_attribute("href")
    page_links = page.locator(".blog-pagination__page")
    last_page = (
        max(int(value) for value in page_links.all_text_contents())
        if page_links.count()
        else 1
    )
    if last_page > 1:
        page.goto(
            f"{origin}/blog/page/{last_page}/",
            wait_until="domcontentloaded",
        )
    last_post_url = page.locator(".blog-card__link").last.get_attribute("href")

    assert first_post_url is not None
    assert last_post_url is not None
    assert first_post_url != last_post_url

    directions: set[str] = set()
    for post_url in (first_post_url, last_post_url):
        page.goto(f"{origin}{post_url}", wait_until="domcontentloaded")
        navigation = page.locator(".post__nav")
        link = navigation.locator(".post__nav-link")
        expect(link).to_have_count(1)

        navigation_box = navigation.bounding_box()
        link_box = link.bounding_box()
        assert navigation_box is not None
        assert link_box is not None
        assert abs(link_box["width"] - navigation_box["width"]) <= 1

        classes = link.get_attribute("class") or ""
        if "post__nav-link--previous" in classes:
            directions.add("previous")
        if "post__nav-link--next" in classes:
            directions.add("next")

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


def test_blog_pagination_covers_every_post_once(driver: DriverFactory) -> None:
    """Walk every page with configured sizes and no duplicate post URLs."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}/blog/", wait_until="domcontentloaded")
    page_links = page.locator(".blog-pagination__page")
    total_pages = (
        max(int(value) for value in page_links.all_text_contents())
        if page_links.count()
        else 1
    )
    collected_urls: list[str] = []
    page_counts: list[int] = []

    for page_number in range(1, total_pages + 1):
        path = "/blog/" if page_number == 1 else f"/blog/page/{page_number}/"
        page.goto(f"{origin}{path}", wait_until="domcontentloaded")
        cards = page.locator(".blog-card")
        page_counts.append(cards.count())

        if total_pages > 1:
            expect(
                page.locator('.blog-pagination__page[aria-current="page"]')
            ).to_have_text(str(page_number))
        collected_urls.extend(
            cards.locator(".blog-card__link").evaluate_all(
                "(links) => links.map((link) => link.getAttribute('href'))"
            )
        )

    assert len(collected_urls) == len(set(collected_urls))
    assert all(count == PAGER_SIZE for count in page_counts[:-1])
    assert 1 <= page_counts[-1] <= PAGER_SIZE


def test_blog_pagination_directions_reach_both_edges(
    driver: DriverFactory,
) -> None:
    """Link adjacent pages and disable outward controls at both collection edges."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(f"{origin}/blog/", wait_until="domcontentloaded")
    page_links = page.locator(".blog-pagination__page")

    if page_links.count() == 0:
        expect(page.locator(".blog-pagination")).to_have_count(0)
        assert page.locator(".blog-card").count() <= PAGER_SIZE
        return

    total_pages = max(int(value) for value in page_links.all_text_contents())

    expect(
        page.locator('.blog-pagination__direction[aria-disabled="true"]')
    ).to_have_text("← Newer")
    expect(page.get_by_role("link", name="Older →")).to_have_attribute(
        "href",
        "/blog/page/2/",
    )
    page.get_by_role("link", name="Older →").click()
    expect(page).to_have_url(re.compile(r"/blog/page/2/$"))

    page.goto(
        f"{origin}/blog/page/{total_pages}/",
        wait_until="domcontentloaded",
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
