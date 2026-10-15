"""Deployment-equivalent technical SEO contracts for the real site."""

from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPOSITORY_ROOT / "site"
PRODUCTION_ORIGIN = "https://mutating.tech"
pytestmark = pytest.mark.seo


class HeadParser(HTMLParser):
    """Collect SEO-relevant head fields without depending on HTML formatting."""

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.metas: dict[str, list[str]] = {}
        self.links: list[dict[str, str]] = []
        self.json_ld: list[str] = []
        self._in_title = False
        self._h1_depth = 0
        self._in_json_ld = False
        self._json_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._h1_depth += 1
        elif tag == "meta":
            key = values.get("name") or values.get("property")
            if key:
                self.metas.setdefault(key, []).append(values.get("content", ""))
        elif tag == "link":
            self.links.append(values)
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._in_json_ld = True
            self._json_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._h1_depth = max(0, self._h1_depth - 1)
        elif tag == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_parts).strip())
            self._in_json_ld = False
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._h1_depth:
            self.h1_parts.append(data)
        if self._in_json_ld:
            self._json_parts.append(data)

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()

    @property
    def h1(self) -> str:
        return " ".join("".join(self.h1_parts).split())


def _build_site(destination: Path, *, content: Path | None = None) -> None:
    command = [
        "hugo",
        "--source",
        str(PROJECT_ROOT),
        "--destination",
        str(destination),
        "--environment",
        "production",
        "--minify",
        "--cleanDestinationDir",
        "--panicOnWarning",
        "--noBuildLock",
    ]
    if content is not None:
        command.extend(("--contentDir", str(content)))
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def production_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("technical-seo") / "public"
    _build_site(destination)
    return destination


def _document(path: Path) -> HeadParser:
    parser = HeadParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def _output_path(root: Path, url: str) -> Path:
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}" == PRODUCTION_ORIGIN
    relative = parsed.path.lstrip("/")
    if not relative or parsed.path.endswith("/"):
        relative = f"{relative}index.html"
    return root / relative


def _sitemap_urls(root: Path) -> list[str]:
    sitemap_index = ET.parse(root / "sitemap.xml").getroot()
    sitemap_urls = [
        element.text or ""
        for element in sitemap_index.findall(".//{*}loc")
    ]
    urls: list[str] = []
    for sitemap_url in sitemap_urls:
        sitemap_path = _output_path(root, sitemap_url)
        child = ET.parse(sitemap_path).getroot()
        urls.extend(
            element.text or ""
            for element in child.findall(".//{*}loc")
        )
    return urls


def _canonical(document: HeadParser) -> list[str]:
    return [
        link.get("href", "")
        for link in document.links
        if link.get("rel") == "canonical"
    ]


def _alternates(document: HeadParser) -> dict[str, str]:
    return {
        link["hreflang"]: link["href"]
        for link in document.links
        if link.get("rel") == "alternate" and "hreflang" in link
    }


def _schema_types(document: HeadParser) -> set[str]:
    types: set[str] = set()
    for raw_schema in document.json_ld:
        schema = json.loads(raw_schema)
        nodes = schema if isinstance(schema, list) else [schema]
        for node in nodes:
            node_type = node.get("@type")
            if isinstance(node_type, str):
                types.add(node_type)
            main_entity = node.get("mainEntity")
            if isinstance(main_entity, dict) and isinstance(main_entity.get("@type"), str):
                types.add(main_entity["@type"])
    return types


def test_production_environment_owns_the_canonical_origin(
    production_site: Path,
) -> None:
    """A plain production build must not depend on a workflow secret for its URL."""

    source = (production_site / "index.html").read_text(encoding="utf-8")
    assert "http://localhost" not in source
    assert "127.0.0.1" not in source
    assert _canonical(_document(production_site / "index.html")) == [
        f"{PRODUCTION_ORIGIN}/"
    ]


def test_robots_declares_the_production_sitemap(production_site: Path) -> None:
    robots = (production_site / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert f"Sitemap: {PRODUCTION_ORIGIN}/sitemap.xml" in robots


def test_sitemap_is_the_canonical_indexable_corpus(production_site: Path) -> None:
    urls = _sitemap_urls(production_site)
    assert urls
    assert len(urls) == len(set(urls))
    assert not any("/author-posts/" in url for url in urls)
    assert not any("/page/1/" in url for url in urls)
    assert not any(url == f"{PRODUCTION_ORIGIN}/en/" for url in urls)

    canonical_corpus: set[str] = set()
    for output in production_site.glob("**/index.html"):
        document = _document(output)
        robots = document.metas.get("robots", [])
        if any("noindex" in directive for directive in robots):
            continue
        canonical = _canonical(document)
        if canonical:
            assert len(canonical) == 1, output
            canonical_corpus.add(canonical[0])

    assert set(urls) == canonical_corpus

    for url in urls:
        output = _output_path(production_site, url)
        assert output.is_file(), url
        document = _document(output)
        assert document.title, url
        assert document.h1, url
        assert document.metas.get("description", [""])[0], url
        assert _canonical(document) == [url]


def test_every_sitemap_page_has_reciprocal_hreflang_and_social_metadata(
    production_site: Path,
) -> None:
    for url in _sitemap_urls(production_site):
        document = _document(_output_path(production_site, url))
        alternates = _alternates(document)
        assert set(alternates) == {"en-US", "ru-RU", "x-default"}, url
        assert alternates["x-default"] == alternates["en-US"], url

        for language, target in alternates.items():
            target_document = _document(_output_path(production_site, target))
            assert _canonical(target_document) == [target], (url, language, target)
            if language != "x-default":
                target_alternates = _alternates(target_document)
                assert target_alternates[language] == target

        required_meta = {
            "og:title",
            "og:description",
            "og:type",
            "og:url",
            "og:site_name",
            "og:locale",
            "og:locale:alternate",
            "og:image",
            "og:image:alt",
            "og:image:type",
            "og:image:width",
            "og:image:height",
            "twitter:card",
            "twitter:title",
            "twitter:description",
            "twitter:image",
            "twitter:image:alt",
        }
        assert required_meta <= document.metas.keys(), url
        assert document.metas["og:url"] == [url]
        assert document.metas["og:title"] == [document.title]
        assert document.metas["twitter:title"] == [document.title]
        assert document.metas["og:description"] == document.metas["description"]
        assert document.metas["twitter:description"] == document.metas["description"]
        assert document.metas["twitter:image"] == document.metas["og:image"]


def test_language_sitemaps_include_the_complete_hreflang_cluster(
    production_site: Path,
) -> None:
    sitemap_index = ET.parse(production_site / "sitemap.xml").getroot()
    for sitemap_element in sitemap_index.findall(".//{*}loc"):
        sitemap_url = sitemap_element.text or ""
        sitemap = ET.parse(_output_path(production_site, sitemap_url)).getroot()
        for url_element in sitemap.findall("{*}url"):
            alternates = {
                link.attrib["hreflang"]: link.attrib["href"]
                for link in url_element.findall(
                    "{http://www.w3.org/1999/xhtml}link"
                )
            }
            assert set(alternates) == {"en-US", "ru-RU", "x-default"}
            assert alternates["x-default"] == alternates["en-US"]


def test_structured_data_matches_supported_page_types(production_site: Path) -> None:
    home = _document(production_site / "index.html")
    assert {"Organization", "WebSite"} <= _schema_types(home)

    blog_pages = sorted(production_site.glob("blog/*/index.html"))
    blog_pages += sorted(production_site.glob("ru/blog/*/index.html"))
    blog_pages = [page for page in blog_pages if "introducing-" not in page.parent.name]
    assert blog_pages
    for article_page in blog_pages:
        document = _document(article_page)
        assert {"Organization", "BlogPosting"} <= _schema_types(document), (
            article_page
        )
        for raw_schema in document.json_ld:
            json.loads(raw_schema)

    news_pages = sorted(production_site.glob("news/*/index.html"))
    news_pages += sorted(production_site.glob("ru/news/*/index.html"))
    assert news_pages
    for article_page in news_pages:
        document = _document(article_page)
        assert {"Organization", "NewsArticle"} <= _schema_types(document), (
            article_page
        )
        for raw_schema in document.json_ld:
            json.loads(raw_schema)

    author_pages = sorted(production_site.glob("authors/*/index.html"))
    author_pages += sorted(production_site.glob("ru/authors/*/index.html"))
    assert author_pages
    for author_page in author_pages:
        document = _document(author_page)
        schema_types = _schema_types(document)
        assert {"Organization", "ProfilePage"} <= schema_types, author_page
        assert {"Person", "Thing"} & schema_types, author_page
        for raw_schema in document.json_ld:
            json.loads(raw_schema)


def test_technical_iframes_are_not_indexable(production_site: Path) -> None:
    sitemap_urls = set(_sitemap_urls(production_site))
    for filename in ("muta-tentacles.html", "tokamak-reactor.html"):
        document = _document(production_site / filename)
        assert document.metas.get("robots") == ["noindex, nofollow"]
        assert f"{PRODUCTION_ORIGIN}/{filename}" not in sitemap_urls


def test_hugo_does_not_emit_meta_refresh_aliases(production_site: Path) -> None:
    assert not (production_site / "en" / "index.html").exists()
    assert not list(production_site.glob("**/page/1/index.html"))


def test_page_two_owns_its_metadata_and_language_cluster(tmp_path: Path) -> None:
    content = tmp_path / "content"
    shutil.copytree(PROJECT_ROOT / "content", content)
    blog = content / "blog"
    for number in range(1, 26):
        (blog / f"seo-pagination-{number:02d}.md").write_text(
            "\n".join(
                (
                    "---",
                    f'title: "SEO pagination fixture {number:02d}"',
                    f"date: 1996-01-{number:02d}T00:00:00Z",
                    f'slug: "seo-pagination-{number:02d}"',
                    'description: "A temporary production SEO fixture."',
                    'author: "muta"',
                    "tags:",
                    "  - seo-pagination",
                    "---",
                    "",
                    "Temporary test body.",
                    "",
                )
            ),
            encoding="utf-8",
        )

    destination = tmp_path / "public"
    _build_site(destination, content=content)
    cases = (
        ("/blog/page/2/", "/ru/blog/page/2/"),
        ("/tags/seo-pagination/page/2/", "/ru/tags/seo-pagination/page/2/"),
        ("/authors/muta/posts/page/2/", "/ru/authors/muta/posts/page/2/"),
    )
    for english_path, russian_path in cases:
        for path, language in ((english_path, "en-US"), (russian_path, "ru-RU")):
            url = f"{PRODUCTION_ORIGIN}{path}"
            document = _document(_output_path(destination, url))
            assert _canonical(document) == [url]
            assert "Page 2" in document.title or "Страница 2" in document.title
            alternates = _alternates(document)
            assert alternates[language] == url
            assert alternates["en-US"] == f"{PRODUCTION_ORIGIN}{english_path}"
            assert alternates["ru-RU"] == f"{PRODUCTION_ORIGIN}{russian_path}"
            assert alternates["x-default"] == f"{PRODUCTION_ORIGIN}{english_path}"

    third_page_cases = (
        "/blog/page/3/",
        "/ru/blog/page/3/",
        "/tags/seo-pagination/page/3/",
        "/ru/tags/seo-pagination/page/3/",
        "/authors/muta/posts/page/3/",
        "/ru/authors/muta/posts/page/3/",
    )
    for path in third_page_cases:
        url = f"{PRODUCTION_ORIGIN}{path}"
        document = _document(_output_path(destination, url))
        assert _canonical(document) == [url]
        assert "Page 3" in document.title or "Страница 3" in document.title


def test_crawler_log_report_is_privacy_minimized_and_actionable(tmp_path: Path) -> None:
    module_path = PROJECT_ROOT / "scripts" / "analyze_crawler_logs.py"
    spec = importlib.util.spec_from_file_location("crawler_report", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    now = datetime.now(timezone.utc)
    log = tmp_path / "access.json.log"
    records = [
        {
            "time": now.isoformat(),
            "host": "mutating.tech",
            "uri": "/blog/",
            "status": 200,
            "user_agent": "Mozilla/5.0 (compatible; Googlebot/2.1)",
        },
        {
            "time": now.isoformat(),
            "host": "www.mutating.tech",
            "uri": "/page/1/",
            "has_args": "?",
            "status": 308,
            "user_agent": "Mozilla/5.0 (compatible; bingbot/2.0)",
        },
    ]
    log.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    report = module.build_report(
        [log],
        canonical_host="mutating.tech",
        since=now - timedelta(hours=1),
    )
    assert report["total_requests"] == 2
    assert report["status_by_crawler"]["googlebot"] == {"200": 1}
    assert report["status_by_crawler"]["bingbot"] == {"308": 1}
    assert report["noncanonical_hosts"] == [("www.mutating.tech", 1)]
    assert report["crawl_waste_paths"] == [("/page/1/", 1)]
    assert report["query_string_requests"] == [("/page/1/", 1)]


def test_server_and_deploy_configuration_enforce_the_canonical_origin() -> None:
    playbook = (REPOSITORY_ROOT / "infra/playbooks/setup-site.yml").read_text()
    deploy = (REPOSITORY_ROOT / ".github/workflows/deploy.yml").read_text()

    assert "hugo_version: 0.164.0" in playbook
    assert "hugo_extended_{{ hugo_version }}" in playbook
    assert "certbot certonly" in playbook
    assert "--webroot" in playbook
    assert 'set -- "$@" -d "$name"' in playbook
    assert "certificate_covers_all_names" in playbook
    assert "openssl x509" in playbook
    assert '--cert-name "$domain"' in playbook
    assert "ConditionPathExists" not in playbook
    assert "certbot --nginx" not in playbook
    assert "server_name {{ site_aliases | join(' ') }};" in playbook
    assert "return 308 https://{{ site_domain }}$request_uri;" in playbook
    assert 'if ($request_uri ~ "^(.*/)index\\.html(?:\\?.*)?$")' in playbook
    assert "return 308 https://{{ site_domain }}$1$is_args$args;" in playbook
    assert "http2 on;" in playbook
    assert "gzip_vary on;" in playbook
    assert 'Cache-Control "public, max-age=31536000, immutable"' in playbook
    assert 'Cache-Control "public, max-age=604800"' in playbook
    assert "log_format seo_json" in playbook
    assert "mutating-crawler-report.timer" in playbook
    assert "ansible.posix.synchronize:" in playbook
    assert "- source_sync" in playbook
    assert "ssh_connection_multiplexing: true" in playbook
    assert "use_ssh_args: true" in playbook
    assert "ControlPath=~/.ansible/cp/mutating-tech-%C" in playbook
    assert "checksum: true" in playbook
    assert "delete: true" in playbook
    assert "--delete-excluded" in playbook
    assert "--chown={{ deploy_user }}:{{ deploy_group }}" in playbook
    assert "Copy the local site checkout to the server" not in playbook

    assert "SITE_BASE_URL: https://mutating.tech/" in deploy
    assert "sudo apt-get install --yes ripgrep rsync sshpass" in deploy
    assert "Verify the production server before upload" in deploy
    assert "--panicOnWarning" in deploy
    assert "Verify the deployed content contracts" in deploy
    assert "openssl x509 -noout -checkhost www.mutating.tech" in deploy
    assert "ALPN protocol" in deploy


def test_readme_documents_the_verified_ansible_invocation() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text()
    ansible_instructions = readme.split(
        "### Provision a fresh server with Ansible",
        maxsplit=1,
    )[1].split("### Prepare a fresh Debian server", maxsplit=1)[0]

    assert "git clone https://github.com/mutating/site.git" in ansible_instructions
    assert "LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook" in ansible_instructions
    assert "-i 'SERVER_IP,'" in ansible_instructions
    assert "-u root" in ansible_instructions
    assert "--ask-pass" in ansible_instructions
    assert "--ask-become-pass" in ansible_instructions
    assert "ansible_password" not in ansible_instructions
    assert "failed=0" in ansible_instructions
    assert "ansible.posix.synchronize" in ansible_instructions
    assert "ansible rsync sshpass" in ansible_instructions
    assert "unchanged repeat took 2.4 seconds" in ansible_instructions
