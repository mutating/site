# The Mutating Company

The company website is a self-contained [Hugo](https://gohugo.io/) site. The
landing page, responsive interactions, light/dark themes, blog, local fonts,
artwork, WebGL tentacles, and tokamak renderer all ship with the custom
`mutating` theme.

## Requirements

- Hugo Extended `0.164.0` (the version used by local development and CI)
- Python `3.11` or newer for the browser test suite

On macOS, Hugo can be installed with:

```bash
brew install hugo
```

Verify the installation:

```bash
hugo version
```

## Run locally

Start Hugo's development server:

```bash
hugo server
```

Open [http://localhost:1313](http://localhost:1313). Hugo watches the content,
theme templates, CSS, JavaScript, and static assets and rebuilds after changes.

Build the static site with the URL configured in `hugo.toml`:

```bash
hugo --cleanDestinationDir --minify
```

For a deployable production build, override the local `baseURL` with the real
public URL:

```bash
hugo --cleanDestinationDir --environment production --minify \
  --baseURL https://mutating.tech/
```

The generated site is written to `public/`. That directory is build output and
is intentionally ignored by Git.

## Content

Every authored page lives in `content/`:

- `content/_index.md` contains the landing-page content.
- `content/blog/_index.md` contains the blog-list introduction.
- `content/blog/*.md` contains individual posts.
- `content/authors/_index.md` contains the author-directory introduction.
- `content/authors/*.md` contains author profiles.
- `content/author-posts/<author-slug>/_index.md` configures the paginated post
  archive for an author.

Everything that defines presentation or behavior lives in `themes/mutating/`:

- `layouts/_default/baseof.html` is the shared HTML document.
- `layouts/partials/header.html` and `footer.html` are shared by every page.
- `layouts/index.html` renders the landing page.
- `layouts/blog/list.html` and `single.html` render the blog entry points.
- `layouts/_default/term.html` renders filtered tag collections.
- `layouts/author-posts/list.html` renders author-specific post archives.
- `layouts/partials/post-list.html` provides the shared post cards and
  pagination used by blog, tag, and author archive pages.
- `layouts/authors/list.html` and `single.html` render author pages.
- `layouts/_default/_markup/render-heading.html` adds self-links to Markdown
  headings in blog posts.
- `layouts/partials/title-with-period.html` and `author-byline.html` provide
  shared post-title and author metadata behavior.
- `assets/css/site.css` contains both themes and responsive styles and is
  fingerprinted by Hugo.
- `assets/js/site.js` contains the site interactions and is fingerprinted by
  Hugo.
- `static/` also contains every local image, font, SVG, and standalone
  interactive HTML asset.

## Add a blog post

Create a new Markdown file:

```bash
hugo new content blog/my-new-field-note.md
```

If no archetype is configured, a post can also be created manually:

```markdown
---
title: "My new field note"
date: 2026-07-30T12:00:00+03:00
description: "A short summary used on the blog list and in metadata."
author: "muta"
tags:
  - verification
  - agents
---

Opening paragraph.

## A section heading

Hugo renders ordinary Markdown, including **emphasis**, links, lists, tables,
blockquotes, images, and fenced code blocks.
```

Keep draft work out of production with `draft: true` in the front matter. View
drafts locally with:

```bash
hugo server --buildDrafts
```

Post URLs use `/blog/<slug>/`. The slug defaults to the filename; add
`slug: "custom-slug"` to front matter when a different URL is required.

Post titles are displayed with exactly one trailing period on list and single
pages; the source title in front matter is not modified. Markdown headings in
the post body receive hover-revealed anchor links. Fenced code blocks use the
language declared after the opening fence for syntax highlighting and reveal a
copy button on hover.

The optional `author` value is the filename of a profile in `content/authors/`
without the `.md` extension. For example, `author: "muta"` resolves
`content/authors/muta.md`, adds a `By Muta` link to the post metadata, and
routes it to `/authors/muta/`. If `author` is omitted, no author metadata is
rendered.

The optional `tags` list is rendered at the bottom of a post. Each tag links
to `/tags/<tag>/`, where Hugo uses the same cards and pagination as the main
blog while keeping a tag-specific heading. If `tags` is empty or omitted, the
tag region is not rendered.

## Add an author

Create `content/authors/<author-slug>.md`. Author URLs always use the filename,
so `content/authors/jane-doe.md` becomes `/authors/jane-doe/` regardless of the
display name:

```markdown
---
title: "Jane Doe"
status: "human"
photo: "/authors/jane-doe.webp"
links:
  - label: "GitHub"
    url: "https://github.com/jane-doe"
  - label: "Website"
    url: "https://example.com"
---

Jane builds verification systems for distributed software agents.

## Current focus

This body supports the same Markdown features as a blog post.
```

`status` should be `human` or `robot`. `links` is optional and its entire
region is omitted when the list is absent. The profile automatically collects
all posts whose `author` value matches its filename and derives up to five of
the most frequent tags across those posts.

If the author has posts, also create
`content/author-posts/<author-slug>/_index.md` so the profile can link to a
paginated archive of all their posts:

```markdown
---
title: "Jane Doe's field notes"
description: "Every field note published by Jane Doe, newest first."
author: "jane-doe"
type: "author-posts"
url: "/authors/jane-doe/posts/"
bodyClass: "blog-page"
---

Notes by Jane Doe, ordered from the newest signal to the oldest.
```

The `author` value must match the profile filename without `.md`, and `url`
must follow `/authors/<author-slug>/posts/`.

## Python test environment

All Python packages must be installed into the repository-local `venv`; do not
install test dependencies globally:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements_dev.txt
python -m playwright install chromium
```

On Windows, activate it with:

```powershell
venv\Scripts\activate
```

Run the complete suite:

```bash
python -m pytest
```

The session fixture first performs a clean Hugo build, then launches a temporary
Hugo server on an available nonstandard port. Each test receives its browser
page through the `driver` fixture. At the end of the session the server process
is terminated and its temporary build directories are removed by pytest.

An explicitly configured already-running URL remains available for focused
diagnostics:

```bash
TEST_BASE_URL=http://127.0.0.1:1313 python -m pytest tests/test_blog.py
```

## Continuous integration

`.github/workflows/tests.yml` runs on every push to every branch. The GitHub
runner installs Hugo Extended `0.164.0` and Python `3.11`, creates the same
repository-local `venv` used for local development, installs Chromium and its
system dependencies through Playwright, and runs the complete pytest suite.
The tests perform their own temporary Hugo build and server lifecycle through
`tests/conftest.py`.

## Production deployment

`.github/workflows/deploy.yml` publishes the site after every push to `main`.
It can also be started manually from **Actions → Deploy production → Run
workflow**. The workflow creates a clean, minified Hugo production build in
`public/`, verifies the SSH destination, and synchronizes that directory to the
server with `rsync --delete-delay`. Files no longer present in the current Hugo
build are removed from the server, so the remote directory becomes an exact
copy of the generated site without deleting it before a successful transfer.

Configure these GitHub repository secrets under **Settings → Secrets and
variables → Actions**:

| Secret | Purpose |
| --- | --- |
| `SITE_BASE_URL` | Public site URL used by Hugo, including the scheme and preferably the trailing slash, for example `https://mutating.tech/`. |
| `DEPLOY_HOST` | SSH hostname or IP address of the production server. |
| `DEPLOY_PORT` | Numeric SSH port, normally `22`. |
| `DEPLOY_USER` | Dedicated SSH user that owns or can write the deployment directory. |
| `DEPLOY_PATH` | Existing absolute server directory containing only this site, for example `/var/www/mutating.tech`. Broad paths such as `/`, `/var`, and `/var/www` are rejected. |
| `DEPLOY_SSH_PRIVATE_KEY` | Non-interactive private key for the dedicated deploy user, including its `BEGIN` and `END` lines. Store the matching public key in that user's `authorized_keys`. |
| `DEPLOY_KNOWN_HOSTS` | Verified SSH host-key line for the server. Generate it with `ssh-keyscan -p <port> <host>`, verify its fingerprint out of band, and then save the complete output as the secret. |

The server must have `rsync` installed, and `DEPLOY_PATH` must already exist and
be writable by `DEPLOY_USER`. Use a dedicated unprivileged deploy account whose
permissions are limited to that directory. No checked-in password, private key,
or server address is required.

## Writing tests

- Put tests in `tests/` and name files `test_*.py`.
- Follow pytest conventions.
- Give every test function a docstring explaining the idea and the exact
  behavior under test.
- Receive browser access through the `driver` fixture in `tests/conftest.py`;
  tests must not launch their own browser or WebDriver.
- Prefer Playwright locators and `expect(...)` assertions over fixed sleeps.
- Ask `driver(...)` for the required viewport, system color scheme, stored
  theme, reduced-motion preference, or isolated browser capability.
- Keep tests independent; the fixture creates and closes a separate browser
  context for each test.
- Add new Python dependencies to `requirements_dev.txt` and install them only
  after activating `venv`.
