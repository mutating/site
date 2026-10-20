"""Browser coverage for optional post authors and Hugo author profiles."""

from __future__ import annotations

import re
import tomllib
from datetime import datetime
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import DriverFactory


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPOSITORY_ROOT / "site"
HUGO_CONFIG = tomllib.loads((PROJECT_ROOT / "hugo.toml").read_text())
PAGER_SIZE = int(HUGO_CONFIG["pagination"]["pagerSize"])


def test_author_content_uses_a_boolean_robot_flag() -> None:
    """Define human-versus-robot identity with boolean robot front matter."""

    flat_author_files = sorted(
        path
        for path in (PROJECT_ROOT / "content" / "authors").glob("*.md")
        if not path.name.startswith("_index.")
    )
    bundled_author_files = sorted(
        (PROJECT_ROOT / "content" / "authors").glob("*/index.md")
    )
    author_files = flat_author_files + bundled_author_files
    assert author_files

    for author_file in author_files:
        source = author_file.read_text()
        assert re.search(r"^status\s*:", source, re.MULTILINE) is None
        assert re.search(
            r"^robot\s*:\s*(?:true|false)\s*$",
            source,
            re.MULTILINE,
        ) is not None


def test_home_muta_name_links_to_the_author_profile(
    driver: DriverFactory,
) -> None:
    """Navigate from Muta's hero caption to the corresponding author page."""

    page = driver(load_interactive_visuals=False)

    author = page.locator(".mascot-card__author")
    expect(author).to_have_text("Meet Muta")
    expect(author).to_have_attribute("href", "/authors/muta/")
    expect(author).to_have_attribute("rel", "author")
    author.click()

    expect(page).to_have_url(re.compile(r"/authors/muta/$"))
    expect(page.locator(".author-profile h1")).to_have_text("Muta")


def test_author_breadcrumb_separates_directory_and_profile_links(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Link AUTHORS and the current slug separately while leaving the slash inert."""

    page = driver(
        load_interactive_visuals=False,
        initial_path="/authors/test-author/",
    )
    origin = base_url.rstrip("/")
    profile_url = f"{origin}/authors/test-author/"

    breadcrumb = page.locator(".author-profile__breadcrumb")
    directory = breadcrumb.locator('a[href="/authors/"]')
    current = breadcrumb.locator('a[aria-current="page"]')
    separator = breadcrumb.locator("span")

    expect(directory).to_have_text("AUTHORS")
    expect(current).to_have_text("TEST-AUTHOR")
    expect(current).to_have_attribute("href", "/authors/test-author/")
    expect(separator).to_have_text("/")
    expect(separator.locator("a")).to_have_count(0)

    default_style = breadcrumb.evaluate(
        """
        (element) => {
          const parent = getComputedStyle(element);
          return [...element.querySelectorAll("a")].map((link) => {
            const style = getComputedStyle(link);
            return {
              colorMatches: style.color === parent.color,
              fontMatches: style.fontFamily === parent.fontFamily,
              decoration: style.textDecorationLine,
            };
          });
        }
        """
    )
    assert default_style == [
        {"colorMatches": True, "fontMatches": True, "decoration": "none"},
        {"colorMatches": True, "fontMatches": True, "decoration": "none"},
    ]

    current.click()
    expect(page).to_have_url(profile_url)

    page.locator(".author-profile__breadcrumb a[href=\"/authors/\"]").click()
    expect(page).to_have_url(f"{origin}/authors/")


def test_post_author_slug_resolves_to_an_optional_metadata_link(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Link a declared author file while leaving an unauthored post unchanged."""

    page = driver(
        load_interactive_visuals=False,
        initial_path="/blog/markdown-feature-fixture/",
    )
    origin = base_url.rstrip("/")

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
    assert 'T "author_by"' in post_list
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
        (
            "test-robot-author",
            "By Controlled Robot Author",
            "robot",
            "rgb(255, 64, 95)",
        ),
        (
            "test-author",
            "By Controlled Human",
            "human",
            "rgb(36, 200, 117)",
        ),
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
        (
            "test-robot-author",
            "robot",
            "rgb(255, 64, 95)",
            "an automated author",
        ),
        (
            "test-author",
            "human",
            "rgb(36, 200, 117)",
            "a human author",
        ),
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
            "robot-author-fixture",
            "robot",
            "rgb(255, 64, 95)",
            "By Controlled Robot Author",
            "/authors/test-robot-author/",
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


def test_narrow_post_author_tooltip_matches_header_and_stays_in_viewport(
    driver: DriverFactory,
) -> None:
    """Keep the post-author disclosure inside a narrow viewport in both themes.

    Dark mode is reached through the real toggle and a reload, so its assertions
    still run against a fresh document bootstrapped from a stored preference.
    Reusing only the browser context preserves the exact narrow geometry while
    avoiding a second Chromium context and duplicate page-resource startup.
    """

    page = driver(
        stored_theme="light",
        viewport=(390, 844),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{page.url.rstrip('/')}/blog/markdown-feature-fixture/",
        wait_until="domcontentloaded",
    )

    def assert_tooltip_presentation(theme: str) -> None:
        expect(page.locator("html")).to_have_attribute("data-theme", theme)

        status = page.locator(".post__meta .author-status--post")
        status.hover()
        tooltip = status.locator(".author-status__tooltip")
        expect(tooltip).to_be_visible()

        presentation = page.evaluate(
            """
            () => {
              const header = document.querySelector(".tmc-header");
              const metadata = document.querySelector(".post__meta");
              const tooltip = document.querySelector(
                ".post__meta .author-status__tooltip",
              );
              const headerStyle = getComputedStyle(header);
              const tooltipStyle = getComputedStyle(tooltip);
              const metadataRect = metadata.getBoundingClientRect();
              const tooltipRect = tooltip.getBoundingClientRect();

              return {
                viewportWidth: document.documentElement.clientWidth,
                metadataLeft: metadataRect.left,
                metadataRight: metadataRect.right,
                tooltipLeft: tooltipRect.left,
                tooltipRight: tooltipRect.right,
                header: {
                  background: headerStyle.backgroundColor,
                  borderColor: headerStyle.borderColor,
                  borderRadius: headerStyle.borderRadius,
                  backdropFilter: headerStyle.backdropFilter,
                  boxShadow: headerStyle.boxShadow,
                },
                tooltip: {
                  background: tooltipStyle.backgroundColor,
                  borderColor: tooltipStyle.borderColor,
                  borderRadius: tooltipStyle.borderRadius,
                  backdropFilter: tooltipStyle.backdropFilter,
                  boxShadow: tooltipStyle.boxShadow,
                },
              };
            }
            """
        )

        diagnostic = f"narrow author tooltip in {theme} theme: {presentation}"
        assert presentation["tooltipLeft"] >= 0, diagnostic
        assert presentation["tooltipRight"] <= presentation["viewportWidth"], (
            diagnostic
        )
        assert abs(
            presentation["tooltipLeft"] - presentation["metadataLeft"]
        ) <= 1, diagnostic
        assert abs(
            presentation["tooltipRight"] - presentation["metadataRight"]
        ) <= 1, diagnostic
        assert presentation["tooltip"] == presentation["header"], diagnostic

    assert_tooltip_presentation("light")

    page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    ).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"
    page.reload(wait_until="domcontentloaded")
    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"

    assert_tooltip_presentation("dark")


def test_controlled_authors_are_visible_in_the_directory(
    driver: DriverFactory,
) -> None:
    """Color directory identity labels and lights for humans and robots."""

    page = driver(
        load_interactive_visuals=False,
        initial_path="/authors/",
    )

    human = page.locator(
        '.author-directory-card[href="/authors/test-author/"]'
    )
    expect(human).to_have_count(1)
    expect(human.locator("h2")).to_have_text("Controlled Human")
    human_status = human.locator(".author-status--directory")
    expect(human_status).to_have_attribute("data-author-status", "human")
    expect(human_status).to_have_text("HUMAN")
    expect(human_status).to_have_css("color", "rgb(36, 200, 117)")
    expect(human_status.locator(".author-status__light")).to_have_css(
        "background-color",
        "rgb(36, 200, 117)",
    )

    robot_status = page.locator(
        '.author-directory-card[href="/authors/test-robot-author/"] '
        ".author-status--directory"
    )
    expect(robot_status).to_have_attribute("data-author-status", "robot")
    expect(robot_status).to_have_text("ROBOT")
    expect(robot_status).to_have_css("color", "rgb(255, 64, 95)")
    expect(robot_status.locator(".author-status__light")).to_have_css(
        "background-color",
        "rgb(255, 64, 95)",
    )


def test_author_page_aggregates_profile_posts_archive_and_frequent_tags(
    driver: DriverFactory,
) -> None:
    """Render identity, Markdown, two post cards, archive, and ranked tags."""

    page = driver(
        load_interactive_visuals=False,
        initial_path="/authors/test-author/",
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
    metadata_geometry = profile.locator(".author-profile__meta").evaluate(
        """
        (metadata) => {
          const intro = metadata.closest(".author-profile__intro");
          const breadcrumb = intro.querySelector(
            ".author-profile__breadcrumb",
          );
          const title = intro.querySelector("h1");
          const links = metadata.querySelector(".author-profile__links");
          const status = metadata.querySelector(".author-profile__status");
          const statusLabel = status.querySelector(
            ".author-status__label",
          );
          const link = links.querySelector("a");
          const linksRect = links.getBoundingClientRect();
          const statusRect = status.getBoundingClientRect();
          const statusLabelRect = statusLabel.getBoundingClientRect();
          const linkRect = link.getBoundingClientRect();
          const breadcrumbRect = breadcrumb.getBoundingClientRect();
          const titleRect = title.getBoundingClientRect();
          const metadataRect = metadata.getBoundingClientRect();
          return {
            gapAboveTitle: titleRect.top - breadcrumbRect.bottom,
            gapBelowTitle: metadataRect.top - titleRect.bottom,
            linkTop: linkRect.top,
            statusLabelTop: statusLabelRect.top,
            statusRight: statusRect.right,
            linksLeft: linksRect.left,
          };
        }
        """
    )
    assert abs(
        metadata_geometry["gapAboveTitle"]
        - metadata_geometry["gapBelowTitle"]
    ) <= 1
    assert abs(
        metadata_geometry["linkTop"]
        - metadata_geometry["statusLabelTop"]
    ) <= 1
    assert metadata_geometry["statusRight"] < metadata_geometry["linksLeft"]
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
    archive = profile.locator(".author-posts__archive")
    expect(archive).to_have_attribute("href", "/authors/test-author/posts/")
    expect(archive).to_have_text(re.compile(r"All publications"))

    frequent_tags = profile.locator(".author-profile__tags li")
    expect(profile.locator("#author-tags-heading")).to_have_text("FREQUENT TAGS")
    expect(frequent_tags).to_have_count(3)
    first_tag = frequent_tags.first.locator(".signal-tag--compact")
    expect(first_tag.locator(".signal-tag__label")).to_have_text("verification")
    expect(first_tag.locator(".signal-tag__count")).to_have_text("2")
    expect(first_tag).to_have_attribute(
        "href",
        "/tags/verification/",
    )
    expect(first_tag).to_have_attribute(
        "aria-label",
        "#verification, 2 authored signals",
    )
    tag_typography = profile.evaluate(
        """
        (profile) => {
          const contentStyle = getComputedStyle(
            profile.querySelector(".author-profile__content"),
          );
          const headingStyle = getComputedStyle(
            profile.querySelector(".author-profile__label"),
          );
          const tagStyle = getComputedStyle(
            profile.querySelector(".author-profile__tags .signal-tag"),
          );
          return {
            contentFamily: contentStyle.fontFamily,
            contentSize: Number.parseFloat(contentStyle.fontSize),
            headingFamily: headingStyle.fontFamily,
            headingSize: Number.parseFloat(headingStyle.fontSize),
            tagFamily: tagStyle.fontFamily,
            tagSize: Number.parseFloat(tagStyle.fontSize),
          };
        }
        """
    )
    assert tag_typography["headingFamily"] == tag_typography["contentFamily"]
    assert tag_typography["tagFamily"] == tag_typography["contentFamily"]
    assert tag_typography["headingSize"] == tag_typography["contentSize"]
    assert tag_typography["tagSize"] == tag_typography["contentSize"]
    first_tag.click()
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
        f"{page.url.rstrip('/')}/authors/test-robot-author/",
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


def test_author_tags_pack_four_items_into_a_wide_row(
    driver: DriverFactory,
) -> None:
    """Tighten responsive gaps before wrapping controlled tags on wide profiles."""

    page = driver(
        viewport=(1600, 1000),
        load_interactive_visuals=False,
        initial_path="/authors/test-robot-author/",
    )

    geometry = page.evaluate(
        """
        () => {
          const tags = document.querySelector(".author-profile__tags");
          const list = document.querySelector(".author-profile__tags ol");
          const items = [...list.querySelectorAll("li")];
          const rects = items.map((item) => {
            const rect = item.getBoundingClientRect();
            return {
              left: Math.round(rect.left),
              top: Math.round(rect.top),
            };
          });
          const firstRowTop = rects[0].top;
          const nextRow = rects.find((rect) => rect.top !== firstRowTop);
          const style = getComputedStyle(tags);
          return {
            firstRowCount: rects.filter(
              (rect) => rect.top === firstRowTop,
            ).length,
            nextRowLeft: nextRow.left,
            tagsLeft: Math.round(tags.getBoundingClientRect().left),
            columnGap: Number.parseFloat(style.columnGap),
            rowGap: Number.parseFloat(style.rowGap),
          };
        }
        """
    )

    assert geometry["firstRowCount"] == 4
    assert abs(geometry["nextRowLeft"] - geometry["tagsLeft"]) <= 1
    assert 16 <= geometry["columnGap"] <= 48
    assert 12 <= geometry["rowGap"] <= 28


def test_human_profile_uses_the_shared_surface_without_colored_halo(
    driver: DriverFactory,
) -> None:
    """Keep shared portrait geometry while removing every human-profile glow."""

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
        initial_path="/authors/test-author/",
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

    page = driver(
        load_interactive_visuals=False,
        initial_path="/authors/no-links-author/",
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

    page = driver(
        load_interactive_visuals=False,
        initial_path="/authors/no-links-author/",
    )

    expect(page.locator(".author-profile__tags")).to_have_count(0)
    expect(page.locator(".author-posts")).to_have_count(0)


def test_author_description_has_no_redundant_about_label(
    driver: DriverFactory,
) -> None:
    """Render the author description directly without a visible About heading."""

    page = driver(
        load_interactive_visuals=False,
        initial_path="/authors/test-author/",
    )

    bio = page.locator(".author-profile__bio")
    description = bio.locator(".author-profile__content")
    expect(bio.locator(":scope > .author-profile__label")).to_have_count(0)
    expect(page.locator("#author-about-heading")).to_have_count(0)
    expect(description).to_be_visible()
    expect(description).to_have_css("margin-top", "0px")


def test_author_without_posts_keeps_the_description_with_the_identity(
    driver: DriverFactory,
) -> None:
    """Reuse the posting-author grid when publication regions are absent."""

    page = driver(
        viewport=(1600, 900),
        load_interactive_visuals=False,
    )
    origin = page.url.rstrip("/")
    page.goto(
        f"{origin}/authors/no-links-author/",
        wait_until="domcontentloaded",
    )

    expect(page.locator(".author-profile__details--centered")).to_have_count(0)
    geometry = page.evaluate(
        """
        () => {
          const intro = document.querySelector('.author-profile__intro');
          const bio = document.querySelector('.author-profile__bio');
          const metadata = document.querySelector('.author-profile__meta');
          const firstParagraph = document.querySelector(
            '.author-profile__content > :first-child',
          );
          const bioRect = bio.getBoundingClientRect();
          return {
            introLeft: intro.getBoundingClientRect().left,
            bioLeft: bioRect.left,
            bioGap:
              firstParagraph.getBoundingClientRect().top
              - metadata.getBoundingClientRect().bottom,
          };
        }
        """
    )
    assert abs(geometry["introLeft"] - geometry["bioLeft"]) <= 1
    assert 29 <= geometry["bioGap"] <= 43

    page.goto(
        f"{origin}/authors/test-author/",
        wait_until="domcontentloaded",
    )
    expect(page.locator(".author-profile__details--centered")).to_have_count(0)


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
        f"{page.url.rstrip('/')}/authors/test-author/posts/",
        wait_until="domcontentloaded",
    )

    heading = page.locator(".blog-index__header h1")
    author = heading.locator(".blog-index__author-link")
    expect(heading).to_have_text("Signals from Controlled Human.")
    expect(author).to_have_text("Controlled Human")
    expect(author).to_have_attribute("href", "/authors/test-author/")
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
    expect(page).to_have_url(re.compile(r"/authors/test-author/$"))
    expect(page.locator(".author-profile h1")).to_have_text("Controlled Human")


def test_author_archive_filters_paginates_and_orders_only_its_posts(
    driver: DriverFactory,
) -> None:
    """Keep one complete, ordered, author-only archive across every page.

    Filtering and chronology used to be checked by two browser tests that both
    booted the same read-only archive and followed the same ``Older`` route.
    This test retains the real navigation and every former assertion while
    collecting each card's URL together with its publication time in one DOM
    pass. Keeping the pair intact matters: it verifies the newest-first order
    across the page boundary rather than accidentally comparing two unrelated
    lists. Walking until ``Older`` disappears ensures that every linked archive
    page participates in the aggregate checks.
    """

    page = driver(load_interactive_visuals=False)
    origin = page.url.rstrip("/")
    page.goto(
        f"{origin}/authors/test-author/posts/",
        wait_until="domcontentloaded",
    )

    archive = page.locator('.blog-index[data-collection-kind="author"]')
    expect(archive.locator("h1")).to_have_text("Signals from Controlled Human.")
    expect(archive.locator(".blog-card")).to_have_count(PAGER_SIZE)
    entries: list[dict[str, str]] = []
    page_number = 1
    while True:
        entries.extend(
            page.locator(".blog-card").evaluate_all(
                """
                (cards) => cards.map((card) => ({
                  href: card.querySelector('.blog-card__link')
                    .getAttribute('href'),
                  datetime: card.querySelector('time').dateTime,
                }))
                """
            )
        )
        older = page.get_by_role("link", name="Older →")
        if older.count() == 0:
            break
        older.click()
        page.wait_for_load_state("domcontentloaded")
        page_number += 1
        if page_number == 2:
            expect(page).to_have_url(
                re.compile(r"/authors/test-author/posts/page/2/$")
            )

    all_urls = [entry["href"] for entry in entries]
    assert len(all_urls) == PAGER_SIZE + 4
    assert len(all_urls) == len(set(all_urls))
    assert all("controlled-pagination" not in url for url in all_urls)
    publication_times = [
        datetime.fromisoformat(entry["datetime"].replace("Z", "+00:00"))
        for entry in entries
    ]
    assert len(publication_times) == PAGER_SIZE + 4
    assert publication_times == sorted(publication_times, reverse=True)


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
          const linkRect = document
            .querySelector(".author-profile__links a")
            .getBoundingClientRect();
          const statusLabelRect = document
            .querySelector(".author-profile__status .author-status__label")
            .getBoundingClientRect();
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
            metadataTextOffset: Math.abs(
              linkRect.top - statusLabelRect.top,
            ),
            viewportWidth: document.documentElement.clientWidth,
          };
        }
        """
    )

    assert geometry["overflows"] is False
    assert geometry["heroOverlaps"] is False
    assert geometry["metadataTextOffset"] <= 0.1
    assert geometry["widestCard"] <= geometry["viewportWidth"]


def test_author_profile_desktop_unifies_identity_bio_portrait_and_signals(
    driver: DriverFactory,
) -> None:
    """Keep the bio with the identity and bridge both columns with signals."""

    page = driver(
        viewport=(1500, 1000),
        load_interactive_visuals=False,
        initial_path="/authors/test-author/",
    )
    geometry = page.evaluate(
        """
        () => {
          const profile = document.querySelector(".author-profile");
          const intro = document.querySelector(".author-profile__intro");
          const links = document.querySelector(".author-profile__links");
          const firstParagraph = document.querySelector(
            ".author-profile__content > :first-child",
          );
          const portrait = document.querySelector(
            ".author-profile__portrait",
          );
          const tags = document.querySelector(".author-profile__tags");
          const tagItems = [...tags.querySelectorAll("li")];
          const tagList = tags.querySelector("ol");
          const postsHeading = document.querySelector(".author-posts h2");
          const archive = document.querySelector(".author-posts__archive");
          const firstCard = document.querySelector(".author-post-card");
          const title = intro.querySelector("h1");
          const profileRect = profile.getBoundingClientRect();
          const introRect = intro.getBoundingClientRect();
          const linksRect = links.getBoundingClientRect();
          const paragraphRect = firstParagraph.getBoundingClientRect();
          const portraitRect = portrait.getBoundingClientRect();
          const tagsRect = tags.getBoundingClientRect();
          const postsHeadingRect = postsHeading.getBoundingClientRect();
          const archiveRect = archive.getBoundingClientRect();
          const tagTops = tagItems.map(
            (item) => item.getBoundingClientRect().top,
          );
          return {
            profileLeft: profileRect.left,
            profileRight: profileRect.right,
            introLeft: introRect.left,
            paragraphLeft: paragraphRect.left,
            paragraphGap: paragraphRect.top - linksRect.bottom,
            portraitWidth: portraitRect.width,
            portraitRight: portraitRect.right,
            portraitBottom: portraitRect.bottom,
            tagsLeft: tagsRect.left,
            tagsRight: tagsRect.right,
            tagsTop: tagsRect.top,
            tagsBorder: getComputedStyle(tags).borderTopWidth,
            tagListBorder: getComputedStyle(tagList).borderTopWidth,
            tagItemBorder: getComputedStyle(tagItems[0]).borderBottomWidth,
            postsBorder: getComputedStyle(
              document.querySelector(".author-posts"),
            ).borderTopWidth,
            postsHeadingLeft: postsHeadingRect.left,
            archiveRight: archiveRect.right,
            cardMinHeight: getComputedStyle(firstCard).minHeight,
            titleSize: Number.parseFloat(getComputedStyle(title).fontSize),
            tagTopSpread: Math.max(...tagTops) - Math.min(...tagTops),
          };
        }
        """
    )

    assert abs(geometry["introLeft"] - geometry["paragraphLeft"]) <= 1
    assert 29 <= geometry["paragraphGap"] <= 43
    assert geometry["portraitWidth"] <= 461
    # The shared mascot surface is deliberately rotated by two degrees, so
    # its painted border extends slightly beyond the aligned layout box.
    assert 0 <= geometry["portraitRight"] - geometry["profileRight"] <= 9
    assert geometry["portraitBottom"] <= geometry["tagsTop"] + 1
    assert abs(geometry["tagsLeft"] - geometry["profileLeft"]) <= 1
    assert abs(geometry["tagsRight"] - geometry["profileRight"]) <= 1
    assert geometry["tagsBorder"] == "0px"
    assert geometry["tagListBorder"] == "0px"
    assert geometry["tagItemBorder"] == "0px"
    assert geometry["postsBorder"] == "0px"
    assert abs(geometry["postsHeadingLeft"] - geometry["profileLeft"]) <= 1
    assert abs(geometry["archiveRight"] - geometry["profileRight"]) <= 1
    assert geometry["cardMinHeight"] == "310px"
    assert geometry["titleSize"] <= 124
    assert geometry["tagTopSpread"] <= 1


@pytest.mark.parametrize(
    "viewport",
    [(900, 900), (600, 844), (390, 844)],
    ids=["medium", "narrow", "phone"],
)
def test_author_profile_mobile_flow_starts_with_the_identity(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Introduce the author before the portrait, bio, signals, and posts."""

    page = driver(
        viewport=viewport,
        load_interactive_visuals=False,
        initial_path="/authors/test-author/",
    )
    spacing = page.evaluate(
        """
        () => {
          const intro = document.querySelector(".author-profile__intro");
          const portrait = document.querySelector(
            ".author-profile__portrait",
          );
          const bio = document.querySelector(".author-profile__bio");
          const tags = document.querySelector(".author-profile__tags");
          const posts = document.querySelector(".author-posts");
          const introRect = intro.getBoundingClientRect();
          const portraitRect = portrait.getBoundingClientRect();
          const bioRect = bio.getBoundingClientRect();
          const tagsRect = tags.getBoundingClientRect();
          const postsRect = posts.getBoundingClientRect();
          const tagItems = [...tags.querySelectorAll("li")];
          const tagRects = tagItems.map((item) => {
            const rect = item.getBoundingClientRect();
            return {
              left: rect.left,
              right: rect.right,
              top: rect.top,
              bottom: rect.bottom,
            };
          });
          return {
            introBottom: introRect.bottom,
            portraitTop: portraitRect.top,
            portraitBottom: portraitRect.bottom,
            bioTop: bioRect.top,
            bioBottom: bioRect.bottom,
            tagsTop: tagsRect.top,
            tagsBottom: tagsRect.bottom,
            postsTop: postsRect.top,
            tagsBorder: getComputedStyle(tags).borderTopWidth,
            tagListBorder: getComputedStyle(
              tags.querySelector("ol"),
            ).borderTopWidth,
            tagItemBorder: getComputedStyle(tagItems[0]).borderBottomWidth,
            tagsFit: tagRects.every(
              (rect) =>
                rect.left >= tagsRect.left && rect.right <= tagsRect.right,
            ),
            tagsOverlap: tagRects.some((rect, index) =>
              tagRects.slice(index + 1).some(
                (other) =>
                  rect.left < other.right &&
                  rect.right > other.left &&
                  rect.top < other.bottom &&
                  rect.bottom > other.top,
              ),
            ),
            postsBorder: getComputedStyle(posts).borderTopWidth,
          };
        }
        """
    )

    assert spacing["introBottom"] < spacing["portraitTop"]
    assert spacing["portraitBottom"] <= spacing["bioTop"]
    assert spacing["bioBottom"] <= spacing["tagsTop"]
    assert spacing["tagsBottom"] <= spacing["postsTop"]
    assert spacing["tagsBorder"] == "0px"
    assert spacing["tagListBorder"] == "0px"
    assert spacing["tagItemBorder"] == "0px"
    assert spacing["tagsFit"] is True
    assert spacing["tagsOverlap"] is False
    assert spacing["postsBorder"] == "0px"


def test_author_pages_match_the_blog_content_shell_and_top_spacing(
    driver: DriverFactory,
) -> None:
    """Use the blog index's narrower shell and top spacing for author pages."""

    page = driver(
        viewport=(1600, 900),
        load_interactive_visuals=False,
    )
    origin = page.url.rstrip("/")
    page.goto(
        f"{origin}/blog/",
        wait_until="domcontentloaded",
    )
    blog_geometry = page.evaluate(
        """
        () => {
          const shell = document
            .querySelector(".blog-index")
            .getBoundingClientRect();
          const eyebrow = document
            .querySelector(".blog-index__header > .section-index")
            .getBoundingClientRect();
          const header = document
            .querySelector(".tmc-header")
            .getBoundingClientRect();
          return {
            left: shell.left,
            right: shell.right,
            topGap: eyebrow.top - header.bottom,
          };
        }
        """
    )

    page.goto(
        f"{origin}/authors/",
        wait_until="domcontentloaded",
    )
    directory_geometry = page.evaluate(
        """
        () => {
          const shell = document
            .querySelector(".authors-index")
            .getBoundingClientRect();
          const eyebrow = document
            .querySelector(".authors-index__header > .section-index")
            .getBoundingClientRect();
          const header = document
            .querySelector(".tmc-header")
            .getBoundingClientRect();
          return {
            left: shell.left,
            right: shell.right,
            topGap: eyebrow.top - header.bottom,
          };
        }
        """
    )

    assert abs(directory_geometry["left"] - blog_geometry["left"]) <= 1
    assert abs(directory_geometry["right"] - blog_geometry["right"]) <= 1
    assert abs(directory_geometry["topGap"] - blog_geometry["topGap"]) <= 1

    page.goto(
        f"{origin}/authors/test-author/",
        wait_until="domcontentloaded",
    )

    author_geometry = page.evaluate(
        """
        () => {
          const shell = document
            .querySelector(".author-profile")
            .getBoundingClientRect();
          const intro = document
            .querySelector(".author-profile__intro")
            .getBoundingClientRect();
          const eyebrow = document
            .querySelector(".author-profile__breadcrumb")
            .getBoundingClientRect();
          const header = document
            .querySelector(".tmc-header")
            .getBoundingClientRect();
          return {
            left: shell.left,
            right: shell.right,
            introLeft: intro.left,
            topGap: eyebrow.top - header.bottom,
          };
        }
        """
    )

    assert abs(author_geometry["left"] - blog_geometry["left"]) <= 1
    assert abs(author_geometry["right"] - blog_geometry["right"]) <= 1
    assert abs(author_geometry["introLeft"] - blog_geometry["left"]) <= 1
    assert abs(author_geometry["topGap"] - blog_geometry["topGap"]) <= 1


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
