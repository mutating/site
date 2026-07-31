"""Browser coverage for optional post authors and Hugo author profiles."""

from __future__ import annotations

import re
import tomllib
from datetime import datetime
from pathlib import Path

from playwright.sync_api import expect

from conftest import DriverFactory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUGO_CONFIG = tomllib.loads((PROJECT_ROOT / "hugo.toml").read_text())
PAGER_SIZE = int(HUGO_CONFIG["pagination"]["pagerSize"])


def test_home_muta_name_links_to_the_author_profile(
    driver: DriverFactory,
) -> None:
    """Navigate from Muta's hero caption to the corresponding author page."""

    page = driver(load_interactive_visuals=False)
    page.goto(page.url, wait_until="domcontentloaded")

    author = page.locator(".mascot-card__author")
    expect(author).to_have_text("Meet Muta")
    expect(author).to_have_attribute("href", "/authors/muta/")
    expect(author).to_have_attribute("rel", "author")
    author.click()

    expect(page).to_have_url(re.compile(r"/authors/muta/$"))
    expect(page.locator(".author-profile h1")).to_have_text("Muta")


def test_post_author_slug_resolves_to_an_optional_metadata_link(
    driver: DriverFactory,
) -> None:
    """Link a declared author file while leaving an unauthored post unchanged."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(
        f"{origin}/blog/markdown-feature-fixture/",
        wait_until="domcontentloaded",
    )

    author = page.locator(".post__author")
    expect(author).to_have_text("By Controlled Human")
    expect(author).to_have_attribute("href", "/authors/test-author/")
    expect(author).to_have_attribute("rel", "author")

    page.goto(
        f"{origin}/blog/unauthored-fixture/",
        wait_until="domcontentloaded",
    )
    expect(page.locator(".post__author")).to_have_count(0)


def test_author_byline_partial_is_shared_by_posts_and_post_lists() -> None:
    """Keep the colored author name and light in one reusable Hugo component."""

    partials = PROJECT_ROOT / "themes" / "mutating" / "layouts" / "partials"
    byline = (partials / "author-byline.html").read_text()
    post_status = (partials / "author-status.html").read_text()
    post_list = (partials / "post-list.html").read_text()

    assert 'partial "author-byline.html"' in post_status
    assert 'partial "author-byline.html"' in post_list
    assert 'class="post__author"' not in post_status
    assert "author-status__light" in byline
    assert "data-author-status" in byline


def test_post_lists_color_author_names_and_lights_by_identity(
    driver: DriverFactory,
) -> None:
    """Show red robot and green human bylines in shared paginated post lists."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    cases = (
        ("muta", "Muta", "robot", "rgb(255, 64, 95)"),
        ("test-author", "Controlled Human", "human", "rgb(36, 200, 117)"),
    )

    for slug, name, identity, color in cases:
        page.goto(
            f"{origin}/authors/{slug}/posts/",
            wait_until="domcontentloaded",
        )
        byline = page.locator(".blog-card__meta .author-byline").first
        expect(byline).to_have_attribute("data-author-status", identity)
        expect(byline).to_have_text(name)
        expect(byline).to_have_css("color", color)
        expect(byline.locator(".author-status__light")).to_have_css(
            "background-color",
            color,
        )


def test_author_status_uses_identity_color_and_explains_the_disclosure(
    driver: DriverFactory,
) -> None:
    """Color human and robot lights distinctly and explain both on hover."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")

    expected_profiles = (
        ("muta", "robot", "rgb(255, 64, 95)", "an automated author"),
        ("ada-vale", "human", "rgb(36, 200, 117)", "a human author"),
    )
    for slug, status, color, disclosure in expected_profiles:
        page.goto(
            f"{origin}/authors/{slug}/",
            wait_until="domcontentloaded",
        )
        badge = page.locator(".author-profile__status")
        expect(badge).to_have_attribute("data-author-status", status)
        expect(badge.locator(".author-status__light")).to_have_css(
            "background-color",
            color,
        )
        page.mouse.move(0, 0)
        expect(badge.locator(".author-status__tooltip")).to_be_hidden()

        badge.hover()
        tooltip = badge.locator(".author-status__tooltip")
        expect(tooltip).to_be_visible()
        expect(tooltip).to_contain_text(
            "identifies every author as human or automated"
        )
        expect(tooltip).to_contain_text(disclosure)


def test_post_byline_reuses_the_author_identity_indicator(
    driver: DriverFactory,
) -> None:
    """Show the resolved author's robot or human light beside a post byline."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    cases = (
        (
            "why-agent-throughput-breaks-human-era-verification",
            "robot",
            "rgb(255, 64, 95)",
            "By Muta",
            "/authors/muta/",
        ),
        (
            "markdown-feature-fixture",
            "human",
            "rgb(36, 200, 117)",
            "By Controlled Human",
            "/authors/test-author/",
        ),
    )

    for slug, identity, color, byline, author_url in cases:
        page.goto(
            f"{origin}/blog/{slug}/",
            wait_until="domcontentloaded",
        )
        status = page.locator(".post__meta .author-status")
        expect(status).to_have_attribute("data-author-status", identity)
        expect(status.locator(".author-status__light")).to_have_css(
            "background-color",
            color,
        )
        expect(status.locator(".post__author")).to_have_text(byline)
        expect(status.locator(".post__author")).to_have_attribute(
            "href",
            author_url,
        )


def test_real_human_author_is_visible_in_the_directory(
    driver: DriverFactory,
) -> None:
    """Color directory identity labels and lights for humans and robots."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}/authors/",
        wait_until="domcontentloaded",
    )

    ada = page.locator('.author-directory-card[href="/authors/ada-vale/"]')
    expect(ada).to_have_count(1)
    expect(ada.locator("h2")).to_have_text("Ada Vale")
    human_status = ada.locator(".author-status--directory")
    expect(human_status).to_have_attribute("data-author-status", "human")
    expect(human_status).to_have_text("HUMAN")
    expect(human_status).to_have_css("color", "rgb(36, 200, 117)")
    expect(human_status.locator(".author-status__light")).to_have_css(
        "background-color",
        "rgb(36, 200, 117)",
    )

    muta_status = page.locator(
        '.author-directory-card[href="/authors/muta/"] .author-status--directory'
    )
    expect(muta_status).to_have_attribute("data-author-status", "robot")
    expect(muta_status).to_have_text("ROBOT")
    expect(muta_status).to_have_css("color", "rgb(255, 64, 95)")
    expect(muta_status.locator(".author-status__light")).to_have_css(
        "background-color",
        "rgb(255, 64, 95)",
    )


def test_author_page_aggregates_profile_posts_and_frequent_tags(
    driver: DriverFactory,
) -> None:
    """Render identity, Markdown, links, authored posts, and ranked tag counts."""

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(
        f"{origin}/authors/test-author/",
        wait_until="domcontentloaded",
    )
    profile = page.locator(".author-profile")

    expect(profile.locator("h1")).to_have_text("Controlled Human")
    expect(profile.locator(".author-profile__status")).to_have_attribute(
        "data-author-status",
        "human",
    )
    expect(profile.locator(".author-profile__portrait img")).to_have_attribute(
        "src",
        "/muta-v2-transparent-v10.webp",
    )
    expect(profile.locator(".author-profile__links a")).to_have_attribute(
        "href",
        "https://example.com/controlled-human",
    )
    expect(profile.locator(".author-profile__content h2")).to_have_text(
        "Working method"
    )

    post_links = profile.locator(".author-post-card")
    expect(post_links).to_have_count(2)
    assert set(
        post_links.evaluate_all(
            "(links) => links.map((link) => link.getAttribute('href'))"
        )
    ) == {
        "/blog/markdown-feature-fixture/",
        "/blog/author-second-fixture/",
    }

    frequent_tags = profile.locator(".author-profile__tags li")
    assert 1 <= frequent_tags.count() <= 5
    expect(frequent_tags.first.locator("span")).to_have_text("#verification")
    expect(frequent_tags.first.locator("small")).to_have_text("2")
    expect(frequent_tags.first.locator("a")).to_have_attribute(
        "href",
        "/tags/verification/",
    )
    frequent_tags.first.locator("a").click()
    expect(page).to_have_url(
        re.compile(r"/tags/verification/$")
    )
    expect(page.locator('.blog-index[data-collection-kind="tag"]')).to_be_visible()


def test_robot_profile_reuses_the_home_mascot_surface(
    driver: DriverFactory,
) -> None:
    """Render robot portraits with the exact home mascot surface and image rules."""

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )

    def surface_style(selector: str) -> dict[str, str]:
        return page.locator(selector).evaluate(
            """
            (element) => {
              const style = getComputedStyle(element);
              const art = element.querySelector('.mascot-card__art');
              const artStyle = getComputedStyle(art);
              return {
                aspectRatio: style.aspectRatio,
                backgroundImage: style.backgroundImage,
                borderColor: style.borderColor,
                borderRadius: style.borderRadius,
                boxShadow: style.boxShadow,
                overflow: style.overflow,
                transform: style.transform,
                artObjectFit: artStyle.objectFit,
                artObjectPosition: artStyle.objectPosition,
                artFilter: artStyle.filter,
              };
            }
            """
        )

    home_style = surface_style(".mascot-card")
    page.goto(
        f"{page.url.rstrip('/')}/authors/muta/",
        wait_until="domcontentloaded",
    )
    portrait = page.locator(".author-profile__portrait")

    expect(portrait).to_have_class(re.compile(r"\bmascot-card\b"))
    expect(portrait).to_have_attribute("data-author-status", "robot")
    expect(portrait.locator(".mascot-card__art")).to_have_attribute(
        "srcset",
        "/muta-v2-transparent-v10.webp 1x, /muta-v2-transparent-2x-v10.webp 2x",
    )
    assert surface_style(".author-profile__portrait") == home_style


def test_human_profile_uses_the_shared_surface_without_colored_halo(
    driver: DriverFactory,
) -> None:
    """Keep shared portrait geometry while removing every human-profile glow."""

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}/authors/ada-vale/",
        wait_until="domcontentloaded",
    )

    portrait = page.locator(".author-profile__portrait")
    expect(portrait).to_have_class(re.compile(r"\bmascot-card\b"))
    expect(portrait).to_have_attribute("data-author-status", "human")
    expect(portrait.locator(".mascot-card__halo")).to_have_css(
        "display",
        "none",
    )
    expect(portrait).to_have_css("border-color", "rgb(229, 217, 223)")
    expect(portrait).to_have_css("background-image", "none")
    expect(portrait).to_have_css("box-shadow", "none")
    art_filter = portrait.locator(".mascot-card__art").evaluate(
        "element => getComputedStyle(element).filter"
    )
    assert "drop-shadow" not in art_filter


def test_author_without_external_links_omits_the_links_region(
    driver: DriverFactory,
) -> None:
    """Show a robot status and no empty navigation when links are unspecified."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}/authors/no-links-author/",
        wait_until="domcontentloaded",
    )

    expect(page.locator(".author-profile__status")).to_have_attribute(
        "data-author-status",
        "robot",
    )
    expect(page.locator(".author-profile__links")).to_have_count(0)


def test_author_without_posts_omits_signals_and_post_sections(
    driver: DriverFactory,
) -> None:
    """Hide both publication-derived regions when an author has no posts."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}/authors/no-links-author/",
        wait_until="domcontentloaded",
    )

    expect(page.locator(".author-profile__tags")).to_have_count(0)
    expect(page.locator(".author-posts")).to_have_count(0)


def test_author_without_posts_centers_the_description_column(
    driver: DriverFactory,
) -> None:
    """Center the About copy only when no authored-post region can follow it."""

    page = driver(
        viewport=(1600, 900),
        load_interactive_visuals=False,
    )
    origin = page.url.rstrip("/")
    page.goto(
        f"{origin}/authors/no-links-author/",
        wait_until="domcontentloaded",
    )

    details = page.locator(".author-profile__details")
    expect(details).to_have_class(
        re.compile(r"\bauthor-profile__details--centered\b")
    )
    geometry = page.evaluate(
        """
        () => {
          const profile = document.querySelector('.author-profile');
          const bio = document.querySelector('.author-profile__bio');
          const profileRect = profile.getBoundingClientRect();
          const bioRect = bio.getBoundingClientRect();
          return {
            profileCenter: profileRect.left + profileRect.width / 2,
            bioCenter: bioRect.left + bioRect.width / 2,
          };
        }
        """
    )
    assert abs(geometry["profileCenter"] - geometry["bioCenter"]) <= 1

    page.goto(
        f"{origin}/authors/test-author/",
        wait_until="domcontentloaded",
    )
    expect(page.locator(".author-profile__details--centered")).to_have_count(0)


def test_muta_profile_shows_only_latest_posts_and_archive_link(
    driver: DriverFactory,
) -> None:
    """Keep two newest Muta cards on the profile and route the arrow to all posts."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}/authors/muta/",
        wait_until="domcontentloaded",
    )

    cards = page.locator(".author-post-card")
    expect(cards).to_have_count(2)
    assert set(
        cards.evaluate_all(
            "(links) => links.map((link) => link.getAttribute('href'))"
        )
    ) == {
        "/blog/why-agent-throughput-breaks-human-era-verification/",
        "/blog/determinism-is-a-product-feature/",
    }
    archive = page.locator(".author-posts__archive")
    expect(archive).to_have_attribute("href", "/authors/muta/posts/")
    expect(archive).to_have_text(re.compile(r"All field notes"))


def test_author_archive_reuses_shared_paginated_post_list() -> None:
    """Build author archives from the same post-list partial as tag collections."""

    archive_template = (
        PROJECT_ROOT / "themes/mutating/layouts/author-posts/list.html"
    ).read_text()

    assert 'partial "post-list.html"' in archive_template
    assert '"kind" "author"' in archive_template
    assert "blog-card" not in archive_template


def test_author_archive_heading_links_its_pink_name_to_the_profile(
    driver: DriverFactory,
) -> None:
    """Link only the pink author name without changing the archive title type."""

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}/authors/muta/posts/",
        wait_until="domcontentloaded",
    )

    heading = page.locator(".blog-index__header h1")
    author = heading.locator(".blog-index__author-link")
    expect(heading).to_have_text("Signals from Muta.")
    expect(author).to_have_text("Muta")
    expect(author).to_have_attribute("href", "/authors/muta/")
    expect(author).to_have_attribute("rel", "author")
    expect(author).to_have_css("color", "rgb(255, 44, 146)")

    typography = author.evaluate(
        """
        (link) => {
          const headingStyle = getComputedStyle(link.parentElement);
          const linkStyle = getComputedStyle(link);
          return {
            headingFamily: headingStyle.fontFamily,
            linkFamily: linkStyle.fontFamily,
            headingSize: headingStyle.fontSize,
            linkSize: linkStyle.fontSize,
            headingWeight: headingStyle.fontWeight,
            linkWeight: linkStyle.fontWeight,
          };
        }
        """
    )
    assert typography["linkFamily"] == typography["headingFamily"]
    assert typography["linkSize"] == typography["headingSize"]
    assert typography["linkWeight"] == typography["headingWeight"]

    author.click()
    expect(page).to_have_url(re.compile(r"/authors/muta/$"))
    expect(page.locator(".author-profile h1")).to_have_text("Muta")


def test_author_archive_filters_and_paginates_only_its_posts(
    driver: DriverFactory,
) -> None:
    """Paginate a controlled author archive without leaking other authors' posts."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}/authors/test-author/posts/",
        wait_until="domcontentloaded",
    )

    archive = page.locator('.blog-index[data-collection-kind="author"]')
    expect(archive.locator("h1")).to_have_text("Signals from Controlled Human.")
    expect(archive.locator(".blog-card")).to_have_count(PAGER_SIZE)
    first_page_urls = archive.locator(".blog-card__link").evaluate_all(
        "(links) => links.map((link) => link.getAttribute('href'))"
    )

    archive.get_by_role("link", name="Older →").click()
    expect(page).to_have_url(re.compile(r"/authors/test-author/posts/page/2/$"))
    second_page_urls = page.locator(".blog-card__link").evaluate_all(
        "(links) => links.map((link) => link.getAttribute('href'))"
    )

    all_urls = first_page_urls + second_page_urls
    assert len(all_urls) == PAGER_SIZE + 4
    assert len(all_urls) == len(set(all_urls))
    assert all("controlled-pagination" not in url for url in all_urls)


def test_muta_archive_posts_are_strictly_older_than_existing_content() -> None:
    """Keep all thirty added Muta fixtures before the prior publication timeline."""

    blog_directory = PROJECT_ROOT / "content" / "blog"
    archive_posts = sorted(blog_directory.glob("muta-archive-*.md"))
    existing_posts = sorted(
        path for path in blog_directory.glob("*.md")
        if path.name != "_index.md" and not path.name.startswith("muta-archive-")
    )

    def publication_date(path: Path) -> datetime:
        match = re.search(r"^date:\s*(.+)$", path.read_text(), re.MULTILINE)
        assert match is not None
        return datetime.fromisoformat(match.group(1))

    assert len(archive_posts) == 30
    assert max(map(publication_date, archive_posts)) < min(
        map(publication_date, existing_posts)
    )


def test_author_directory_uses_content_filenames_as_profile_slugs(
    driver: DriverFactory,
) -> None:
    """List controlled authors at URLs derived from their content filenames."""

    page = driver(load_interactive_visuals=False)
    page.goto(
        f"{page.url.rstrip('/')}/authors/",
        wait_until="domcontentloaded",
    )

    expect(
        page.locator('.author-directory-card[href="/authors/test-author/"]')
    ).to_have_count(1)
    expect(
        page.locator(
            '.author-directory-card[href="/authors/no-links-author/"]'
        )
    ).to_have_count(1)


def test_author_directory_portrait_is_prominent_on_narrow_screens(
    driver: DriverFactory,
) -> None:
    """Give a mobile author portrait at least half of its card width."""

    page = driver(
        viewport=(620, 900),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}/authors/",
        wait_until="domcontentloaded",
    )

    geometry = page.evaluate(
        """
        () => {
          const card = document.querySelector(".author-directory-card");
          const portrait = card.querySelector("img");
          const cardRect = card.getBoundingClientRect();
          const portraitRect = portrait.getBoundingClientRect();
          return {
            ratio: portraitRect.width / cardRect.width,
            fits:
              portraitRect.left >= cardRect.left &&
              portraitRect.right <= cardRect.right,
          };
        }
        """
    )

    assert geometry["ratio"] >= 0.5
    assert geometry["fits"] is True


def test_author_profile_reflows_without_overlap_on_a_narrow_screen(
    driver: DriverFactory,
) -> None:
    """Stack portrait, profile copy, tags, and post cards within 390 pixels."""

    page = driver(
        viewport=(390, 844),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}/authors/test-author/",
        wait_until="domcontentloaded",
    )

    geometry = page.evaluate(
        """
        () => {
          const intro = document.querySelector(".author-profile__intro");
          const portrait = document.querySelector(
            ".author-profile__portrait",
          );
          const cards = [...document.querySelectorAll(".author-post-card")];
          const introRect = intro.getBoundingClientRect();
          const portraitRect = portrait.getBoundingClientRect();
          return {
            overflows:
              document.documentElement.scrollWidth >
              document.documentElement.clientWidth,
            heroOverlaps:
              introRect.top < portraitRect.bottom &&
              introRect.bottom > portraitRect.top,
            widestCard: Math.max(
              ...cards.map((card) => card.getBoundingClientRect().width),
            ),
            viewportWidth: document.documentElement.clientWidth,
          };
        }
        """
    )

    assert geometry["overflows"] is False
    assert geometry["heroOverlaps"] is False
    assert geometry["widestCard"] <= geometry["viewportWidth"]


def test_author_profile_aligns_with_the_shared_left_edge(
    driver: DriverFactory,
) -> None:
    """Align wide author content with the shared header and section gutter."""

    page = driver(
        viewport=(1600, 900),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}/authors/test-author/",
        wait_until="domcontentloaded",
    )

    left_edges = page.evaluate(
        """
        () => ({
          header: document
            .querySelector(".site-header")
            .getBoundingClientRect().left,
          profile: document
            .querySelector(".author-profile")
            .getBoundingClientRect().left,
          intro: document
            .querySelector(".author-profile__intro")
            .getBoundingClientRect().left,
        })
        """
    )

    assert abs(left_edges["profile"] - left_edges["header"]) <= 1
    assert abs(left_edges["intro"] - left_edges["header"]) <= 1


def test_author_posts_keep_only_their_own_bottom_spacing(
    driver: DriverFactory,
) -> None:
    """Remove page and footer gaps below authored-post cards."""

    page = driver(
        viewport=(1600, 900),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}/authors/test-author/",
        wait_until="domcontentloaded",
    )

    spacing = page.evaluate(
        """
        () => {
          const section = document.querySelector(".author-posts");
          const cards = [...document.querySelectorAll(".author-post-card")];
          const footer = document.querySelector(".site-footer");
          const sectionStyle = getComputedStyle(section);
          const lastCardBottom = Math.max(
            ...cards.map((card) => card.getBoundingClientRect().bottom),
          );
          return {
            expected: parseFloat(sectionStyle.paddingBottom),
            actual: footer.getBoundingClientRect().top - lastCardBottom,
          };
        }
        """
    )

    assert abs(spacing["actual"] - spacing["expected"]) <= 1
