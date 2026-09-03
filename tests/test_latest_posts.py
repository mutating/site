"""Coverage for the dynamic latest-posts section on the Hugo home page."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import DriverFactory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"


def _render_home_with_posts(
    tmp_path: Path,
    post_count: int,
    news_count: int = 0,
) -> str:
    """Build controlled HTML without copying unrelated pass-through assets."""

    content_dir = tmp_path / "content"
    blog_dir = content_dir / "blog"
    news_dir = content_dir / "news"
    destination = tmp_path / "public"
    source_theme = PROJECT_ROOT / "themes" / "mutating"
    themes_dir = tmp_path / "themes"
    blog_dir.mkdir(parents=True)
    news_dir.mkdir(parents=True)
    shutil.copytree(
        source_theme,
        themes_dir / "mutating",
        ignore=lambda directory, _names: (
            {"static"} if Path(directory) == source_theme else set()
        ),
    )
    shutil.copy2(CONTENT_ROOT / "_index.md", content_dir / "_index.md")
    shutil.copy2(CONTENT_ROOT / "blog" / "_index.md", blog_dir / "_index.md")
    shutil.copy2(CONTENT_ROOT / "news" / "_index.md", news_dir / "_index.md")
    for post_index in range(post_count):
        post_number = post_index + 1
        (blog_dir / f"controlled-signal-{post_number}.md").write_text(
            (
                "---\n"
                f'title: "Controlled signal {post_number}"\n'
                f"date: 2000-01-{post_number:02d}T00:00:00Z\n"
                f'description: "Synthetic post {post_number}."\n'
                f'slug: "controlled-signal-{post_number}"\n'
                "---\n\n"
                f"Body for controlled signal {post_number}.\n"
            ),
            encoding="utf-8",
        )

    for news_index in range(news_count):
        news_number = news_index + 1
        (news_dir / f"controlled-announcement-{news_number}.md").write_text(
            (
                "---\n"
                f'title: "Controlled announcement {news_number}"\n'
                f"date: 2001-01-{news_number:02d}T00:00:00Z\n"
                f'description: "Synthetic announcement {news_number}."\n'
                f'slug: "controlled-announcement-{news_number}"\n'
                "---\n\n"
                f"Body for controlled announcement {news_number}.\n"
            ),
            encoding="utf-8",
        )

    build = subprocess.run(
        [
            "hugo",
            "--source",
            str(PROJECT_ROOT),
            "--contentDir",
            str(content_dir),
            "--destination",
            str(destination),
            "--themesDir",
            str(themes_dir),
            "--cleanDestinationDir",
            "--noBuildLock",
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    assert build.returncode == 0, build.stdout
    return (destination / "index.html").read_text()


def test_home_shows_the_latest_blog_post_and_news_item(
    driver: DriverFactory,
) -> None:
    """Keep the latest entry from each publication section on the home page."""

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    section = page.locator(".dispatches")
    cards = section.locator(".dispatch-card")

    expect(section).to_be_visible()
    expect(section.locator("h2")).to_have_text("Fresh signals from the lab.")
    expect(section.locator("h2 > a")).to_have_count(0)
    expect(section.locator(".dispatches__status")).to_have_text(
        "Latest publications"
    )
    expect(cards).to_have_count(2)
    expect(cards.nth(0)).to_have_class(re.compile(r"\bdispatch-card--featured\b"))

    home_posts = [
        {
            "title": cards.nth(index).locator("h3").inner_text(),
            "href": cards.nth(index).get_attribute("href"),
            "slug": cards.nth(index).locator("> p").inner_text(),
        }
        for index in range(cards.count())
    ]
    for post in home_posts:
        assert post["href"] is not None
        assert post["slug"] == post["href"].strip("/").split("/")[-1]

    assert {
        str(post["href"]).strip("/").split("/")[0]
        for post in home_posts
    } == {"blog", "news"}
    expect(
        section.locator('.dispatch-card[href^="/blog/"] .dispatch-card__cta')
    ).to_contain_text("Read the field note")
    expect(
        section.locator('.dispatch-card[href^="/news/"] .dispatch-card__cta')
    ).to_contain_text("Read the announcement")

    if cards.count() == 2:
        widths = [cards.nth(index).bounding_box()["width"] for index in range(2)]
        assert widths[0] > widths[1] * 1.5

    origin = page.url.rstrip("/")
    for section_name in ("blog", "news"):
        home_post = next(
            post
            for post in home_posts
            if str(post["href"]).startswith(f"/{section_name}/")
        )
        page.goto(f"{origin}/{section_name}/", wait_until="domcontentloaded")
        list_post = {
            "title": page.locator(".blog-card h2").first.inner_text(),
            "href": page.locator(".blog-card__link").first.get_attribute("href"),
        }
        assert home_post["href"] == list_post["href"]
        expected_list_title = str(home_post["title"])
        if not expected_list_title.endswith("."):
            expected_list_title += "."
        assert list_post["title"] == expected_list_title

    page.goto(origin, wait_until="domcontentloaded")
    page.locator(".dispatch-card").first.locator("h3").click()
    expected_article_title = str(home_posts[0]["title"])
    if not expected_article_title.endswith("."):
        expected_article_title += "."
    expect(page.locator(".post__header h1")).to_have_text(
        expected_article_title
    )


@pytest.mark.parametrize(
    ("post_count", "expected_cards"),
    [
        (0, 0),
        (1, 1),
    ],
    ids=["no-posts", "one-post"],
)
def test_home_latest_posts_handles_empty_and_single_post_collections(
    tmp_path: Path,
    post_count: int,
    expected_cards: int,
) -> None:
    """Hide an empty feed and expand a lone synthetic post across the full row."""

    html = _render_home_with_posts(tmp_path, post_count)
    card_count = html.count('<a class="dispatch-card')
    assert card_count == expected_cards

    if expected_cards == 0:
        assert '<section class="dispatches' not in html
        assert 'href="#open-source"' not in html
        assert (
            '<a class="text-link" href="https://github.com/mutating">'
            in html
        )
    else:
        assert (
            '<div class="dispatch-grid dispatch-grid--single">'
            in html
        )
        assert "Controlled signal 1" in html
        assert "controlled-signal-1" in html
        assert 'id="field-notes"' in html


def test_home_balances_blog_and_news_when_one_section_is_newer(
    tmp_path: Path,
) -> None:
    """Reserve one card for each section instead of letting one dominate."""

    html = _render_home_with_posts(tmp_path, post_count=3, news_count=2)

    assert html.count('<a class="dispatch-card') == 2
    assert "Controlled signal 3" in html
    assert "Controlled announcement 2" in html
    assert "Controlled signal 2" not in html
    assert "Controlled announcement 1" not in html
    assert "Read the field note" in html
    assert "Read the announcement" in html
