# The Mutating Company

Hugo sources live in this `site/` directory. Shared CI, deployment, API, web UI,
and test commands are documented in the [repository README](../README.md).

The company website produces a self-contained [Hugo](https://gohugo.io/) build. The
landing page, responsive interactions, light/dark themes, blog, local fonts,
artwork, WebGL tentacles, and tokamak renderer all ship with the custom
`mutating` theme.

## Requirements

- Hugo Extended `0.164.0` (the version used by local development and CI)
- Node.js `24` and npm for the shared `@mutating/ui` header and `reboop` HTTP runtime
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

Run Hugo commands from this `site/` directory. Start the development server:

```bash
npm --prefix .. ci
npm --prefix .. run build:ui
hugo server
```

Open [http://localhost:1313](http://localhost:1313). Hugo watches the content,
theme templates, CSS, JavaScript, and static assets and rebuilds after changes.
Rebuild `@mutating/ui` after editing `../packages/ui`. Hugo mounts its generated
assets and Hugo partials directly from that package. Build it before running
Hugo or the Python browser tests; `npm run build:site` from the repository root
performs both build steps in order.

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

- `content/_index.md` contains the English landing-page content.
- `content/_index.ru.md` contains the Russian landing-page translation.
- `content/blog/_index.md` contains the blog-list introduction.
- `content/blog/_index.ru.md` contains its Russian translation.
- `content/blog/<post>/index.md` contains each post and its canonical metadata.
- `content/blog/<post>/translation.ru.md` contains an optional Russian
  translation of that post.
- `content/authors/_index.md` contains the author-directory introduction.
- `content/authors/_index.ru.md` contains its Russian translation.
- `content/authors/<author-slug>/index.md` contains each author profile and its
  canonical identity metadata.
- `content/authors/<author-slug>/translation.ru.md` contains an optional Russian
  translation of that profile.
- `content/author-posts/<author-slug>/_index.md` configures the paginated post
  archive for an author.
- `content/author-posts/<author-slug>/_index.ru.md` contains the localized
  archive title, description, and introduction.

Shared interface copy lives in Hugo translation tables:

- `i18n/en.toml` contains the complete default-language catalog.
- `i18n/ru.toml` contains the Russian interface translation.

Site presentation and behavior live in `themes/mutating/`, with the shared header
implemented in `../packages/ui`:

- `layouts/_default/baseof.html` is the shared HTML document.
- `layouts/partials/header.html` configures the library header's localized menu
  and page-specific language URLs; `footer.html` remains a site partial.
- `layouts/index.html` composes the landing page from `layouts/partials/home/`;
  those partials own its shared structure, assets, and interactive mounts.
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
  Hugo. Hugo bundles its import of the shared header runtime.
- `static/` also contains every local image, font, SVG, and standalone
  interactive HTML asset.
- The Tentacles and Tokamak iframes keep stable static paths, while their home
  partials inject each file's full SHA-256 into its `v` query during every
  build.

## Add a blog post

Create a new leaf bundle:

```bash
hugo new content blog/my-new-field-note/index.md
```

If no archetype is configured, a post can also be created manually:

```markdown
---
title: "My new field note"
slug: "my-new-field-note"
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

Post URLs use `/blog/<slug>/`. Without an explicit value Hugo derives the slug
from the title; keep `slug: "custom-slug"` in canonical front matter to make
the public URL explicit and stable.

Post titles are displayed with exactly one trailing period on list and single
pages; the source title in front matter is not modified. Markdown headings in
the post body receive hover-revealed anchor links. Fenced code blocks use the
language declared after the opening fence for syntax highlighting and reveal a
copy button on hover.

The optional `author` value is the bundle directory name of a profile in
`content/authors/`. For example, `author: "muta"` resolves
`content/authors/muta/index.md`, adds a `By Muta` link to the post metadata,
and routes it to `/authors/muta/`. If `author` is omitted, no author metadata
is rendered.

The optional `tags` list is rendered at the bottom of a post. Each tag links
to `/tags/<tag>/`, where Hugo uses the same cards and pagination as the main
blog while keeping a tag-specific heading. If `tags` is empty or omitted, the
tag region is not rendered.

### Translate a blog post

English is the default language and keeps its existing URLs. Hugo publishes
the same content under `/ru/` as an English fallback until a Russian page
resource exists. To translate a post, add `translation.ru.md` next to its
canonical `index.md`:

```text
content/blog/my-new-field-note/
├── index.md
└── translation.ru.md
```

The translation contains only fields that are actually localized:

```markdown
---
title: "Локализованный заголовок"
description: "Локализованное краткое описание."
---

Русский текст статьи.
```

Keep `date`, `author`, `tags`, `slug`, and all other shared metadata only in
`index.md`. The canonical page is mapped into both language sites through
Hugo's sites matrix; templates read the localized page resource for the title,
description, reading time, table of contents, and body. This gives tag, author,
archive, and blog collections one article per language site without duplicated
front matter. If `translation.ru.md` is absent, `/ru/blog/<slug>/` renders the
canonical English article.

### Translate the landing page

The landing page uses Hugo's native home-page translations:

```text
content/
├── _index.md
└── _index.ru.md
```

Both files define the same structured `home` fields for the hero, ticker,
content sections, dynamic-post heading, and contact block. The English and
Russian files contain copy only. Shared HTML, image sources, section order,
interactive mounts, and fingerprint generation live in
`themes/mutating/layouts/partials/home/`.

Add or rename a home content field in both language files, then consume it from
the corresponding partial. Do not duplicate home HTML in either content file.
The latest-posts partial resolves each card through the same localized
page-resource mechanism used by blog lists and articles.

### Translate collection introductions

Blog, author-directory, and author-archive headers are section content. Add a
native `_index.ru.md` beside each canonical `_index.md` and translate only its
`title`, `description`, and Markdown body. Keep structural values such as
`author`, `type`, and `bodyClass` in the canonical file; templates derive the
archive identity and page class without duplicating them in translations.

Tag collection headings are generated dynamically because tags come from post
metadata. Their surrounding copy belongs in `i18n/en.toml` and `i18n/ru.toml`
under `blog_tagged_signals`, `signals_tagged_prefix`, and
`tagged_posts_pagination`; the tag value itself remains canonical.

### Translate the interface

Navigation labels, reading times, pagination, author statuses, tooltips, dates,
and other shared interface copy use Hugo's `T` function and the tables in
`i18n/`. Add every new message key to both language files, using Hugo plural
forms for count-dependent text. Keep article and landing-page prose in
`content/` rather than moving it into the interface catalog.

The selected language controls both layers: `/ru/` renders Russian interface
messages and localized dates even when an untranslated article falls back to
its canonical English content. Check catalog gaps during development with:

```bash
hugo --printI18nWarnings
```

## Add an author

Create `content/authors/<author-slug>/index.md`. Author URLs always use the
bundle directory, so `content/authors/jane-doe/index.md` becomes
`/authors/jane-doe/` regardless of the display name:

```markdown
---
title: "Jane Doe"
robot: false
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

`robot` is a boolean: use `true` for an automated author and `false` for a
human. `links` is optional and its entire region is omitted when the list is
absent. The profile automatically collects all posts whose `author` value
matches its bundle directory and derives up to five of the most frequent tags
across those posts.

To translate the profile, add `translation.ru.md` beside `index.md` with only
the localized fields:

```text
content/authors/jane-doe/
├── index.md
└── translation.ru.md
```

```markdown
---
title: "Джейн Доу"
---

Джейн создаёт системы верификации для распределённых программных агентов.
```

Keep `robot`, photos, external link destinations, and other shared identity
metadata only in the canonical `index.md`. Until the translation exists, the
Russian URL renders the canonical profile as a fallback.

If the author has posts, also create
`content/author-posts/<author-slug>/_index.md` so the profile can link to a
paginated archive of all their posts:

```markdown
---
title: "Jane Doe's field notes"
description: "Every field note published by Jane Doe, newest first."
author: "jane-doe"
type: "author-posts"
bodyClass: "blog-page"
---

Notes by Jane Doe, ordered from the newest signal to the oldest.
```

The `author` value must match the profile filename without `.md`. Hugo's
permalink configuration publishes the archive at `/authors/<author-slug>/posts/`
and `/ru/authors/<author-slug>/posts/`.

## Python test environment

Run the following test commands from the repository root, one level above this
directory. All Python packages must be installed into the repository-local `venv`; do not
install test dependencies globally:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements_dev.txt
python -m playwright install chromium
npm ci
npm run build:ui
```

On Windows, activate it with:

```powershell
venv\Scripts\activate
```

Run the complete suite:

```bash
python -m pytest tests/site -n 2 --dist loadgroup
```

Header screenshot regressions run separately with `npm run test:site:header`
(install browsers using `npx playwright install chromium firefox`). The frozen original
header, interaction matrix and behavior audit are documented in
[`tests/site/header/README.md`](../tests/site/header/README.md).

The command uses two `pytest-xdist` workers. Override the degree of parallelism
for local diagnostics or CI capacity planning; `-n auto` selects one worker per
available logical CPU:

```bash
python -m pytest tests/site -n 16 --dist loadgroup
python -m pytest tests/site -n 1 --dist loadgroup
```

Each xdist worker performs its own content copy, cache, nonstandard port
selection, server process, and browser lifecycle. The immutable validation
build is protected by a file lock and performed once for the complete pytest
run; every worker then serves its own in-memory Hugo site. Tests that require
the real WebGL tentacle or tokamak renderers opt in with the
`interactive_visuals` marker; `--dist loadgroup` keeps each renderer's tests on
one resource group. All other browser tests intercept those heavyweight iframe
resources while still exercising the real page DOM and application code. At
the end of the session every worker terminates its complete Hugo process group
and pytest removes its temporary build directories.

An explicitly configured already-running URL remains available for focused
diagnostics:

```bash
TEST_BASE_URL=http://127.0.0.1:1313 python -m pytest tests/site/test_blog.py
```

### Scan the animated wordmark for cursor jitter

The wordmark scanner starts its own temporary Hugo server, enters the compact
logo with a real Playwright pointer, and holds that pointer at each candidate
coordinate while sampling the animation on every frame:

```bash
venv/bin/python site/scripts/find_wordmark_jitter.py \
  --output /tmp/wordmark-jitter.json
```

It scans the center and all four boundaries at one-CSS-pixel intervals and
tries the cursor move at the start, midpoint, and end of the 350 ms unfolding
transition. Use `--exhaustive` to test the complete two-dimensional grid, or
repeat `--viewport` and change the device scale factor to cover another layout:

```bash
venv/bin/python site/scripts/find_wordmark_jitter.py \
  --viewport 1280x720 \
  --viewport 920x720 \
  --device-scale-factor 2 \
  --exhaustive \
  --output /tmp/wordmark-jitter-exhaustive.json
```

Pass `--url http://127.0.0.1:1313` to inspect an already-running site and
`--channel chrome --headed` to use the installed Google Chrome build. The JSON
report contains viewport-relative and logo-relative coordinates plus the full
frame trace for each finding. Exit status `1` means jitter was found; `0` means
the requested scan completed without a finding.

The ordinary pytest suite also runs a bounded CI profile from
`tests/site/test_wordmark_jitter_scanner.py`. It samples the center, all four stage
boundaries, the outside margin, and five vertical slices at 32-CSS-pixel
intervals, repeating every point at the start, midpoint, and end of unfolding.
The one-pixel exhaustive scan remains a manual diagnostic because it contains
tens of thousands of real-pointer attempts.

Real asynchronous GPU timing for the Muta tentacle renderer is disabled by
default. Its opt-in query parameter, output fields, and disable procedure are
documented in [docs/tentacle-gpu-timing.md](docs/tentacle-gpu-timing.md).

## Continuous integration

The complete tests run on every push to every branch; `main` calls
`.github/workflows/tests.yml` through the deployment gate. The GitHub
runner installs Hugo Extended `0.164.0` and Python `3.11`, creates the same
repository-local `venv` used for local development, and installs Chromium and
its system dependencies through Playwright. A three-entry matrix runs ordinary
browser tests on two workers and gives each WebGL renderer its own one-worker
job. This prevents Chromium's software WebGL renderers on the Linux runner from
competing for CPU while allowing the three independent suites to run in
parallel. Verbose node IDs and the 25 slowest durations remain in every job log
even when another matrix entry fails. A 120-second per-test timeout turns a
stalled browser-side promise into an attributable failure instead of letting it
consume the complete job timeout. The tests perform their own temporary Hugo
build and server lifecycle through `../tests/site/conftest.py`.

## Production deployment

`.github/workflows/deploy.yml` publishes the site after every push to `main`.
It can also be started manually from **Actions → Deploy production → Run
workflow**. The workflow creates a clean, minified Hugo production build in
`site/public/`, verifies the SSH destination, and uploads it together with API
and web UI sources as one release. The activation script applies migrations,
starts the platform, and synchronizes the static build with `rsync --delete-delay`. Files no longer present in the current Hugo
build are removed from the server, so the remote directory becomes an exact
copy of the generated site without deleting it before a successful transfer.

### Provision a fresh server with Ansible

Shared platform setup and release activation are described in
[the deployment guide](../docs/deployment.md). The commands below run from the
repository root. The repository includes `infra/playbooks/setup-server.yml`. Run it from a
clean local checkout. The playbook supports Debian-based servers, builds the
shared UI locally, prepares the locked production HTTP runtime, and copies
`site/`, `packages/ui/{hugo,dist}` and the runtime `node_modules/` to the server,
installs Git, Hugo Extended, nginx, Certbot, OpenSSL,
and rsync, performs a production Hugo build, and configures nginx, TLS
certificate issuance/renewal, and the crawler-log report timer. The VPS needs
outbound Internet access, but it does not need access to the private GitHub
repository.

Running the playbook also rebuilds `/var/www/mutating.tech` from the selected
local checkout. Commit or stash unrelated local work first so that drafts and
untracked files are not copied to production accidentally. The playbook can be
run again safely after configuration changes; the Hugo build task intentionally
reports `changed` on every run, so idempotence does not mean `changed=0` in the
Ansible recap.

Source synchronization uses rsync through `ansible.posix.synchronize`, with
checksum comparison and SSH connection reuse. Generated output, local virtual
environments, caches, and Git metadata are excluded. A small private staging
tree under `/var/tmp` lets the same task work for both direct root access and an
administrative SSH account using Ansible privilege escalation; the staged tree
is then promoted into `/opt/mutating.tech` with normalized ownership and modes.
On the production VPS, the first measured 149-file/26-MB synchronization took
31 seconds; an unchanged repeat took 2.4 seconds and correctly reported `ok`
instead of `changed`. The complete provisioning play took 5 minutes 51 seconds,
with most of the remaining time spent in ordinary one-off Ansible modules over
the approximately 240-ms SSH round trip.

The regular GitHub deployment deliberately uses the unprivileged `user`
account and cannot change nginx, certificates, or systemd units. Re-run this
playbook with an administrative account whenever the playbook changes. The
deployment workflow checks the live server contract before `rsync`, so stale
server configuration fails without partially publishing the new build.

#### 1. Clone the repository locally

```bash
git clone https://github.com/mutating/site.git
cd site
```

#### 2. Prepare local build tools and Ansible

Install Node.js 24 locally, then install the workspace dependencies from the
repository root. Before synchronizing sources, the playbook runs
`npm run build:ui` and prepares `.runtime/site-runtime` with
`npm ci --omit=dev --ignore-scripts --workspaces=false --no-audit --no-fund`.
Only the locked production dependencies are uploaded for Hugo's `reboop`
import; Node.js and frontend build tools are not needed on the static-site VPS.

```bash
npm ci
```

```bash
ansible-playbook --version
```

On macOS, install it with `brew install ansible`. On Debian or Ubuntu, use:

```bash
sudo apt-get update
sudo apt-get install -y ansible rsync sshpass
```

Some OpenSSH-based Ansible installations need `sshpass` for password
authentication. If `--ask-pass` reports that it is missing, install it on the
machine from which Ansible is being run.

Validate the playbook locally before connecting to the server:

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
  ansible-playbook --syntax-check \
  -i 'localhost,' -c local \
  infra/playbooks/setup-server.yml
```

The explicit locale avoids `unsupported locale setting` on macOS installations
whose shell locale is not available to Homebrew's Python.

#### 3. Run the setup playbook

Use the server's public IPv4 address and an administrative SSH account. For a
direct root login, run:

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i 'SERVER_IP,' \
  -u root \
  --ask-pass \
  infra/playbooks/setup-server.yml
```

For example:

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i '203.0.113.10,' \
  -u root \
  --ask-pass \
  infra/playbooks/setup-server.yml
```

Enter the SSH password only at the prompt. Do not pass it through `-e`: doing so
exposes it in shell history and process arguments. If direct root login is
disabled, replace `root` with an account that can use sudo and add
`--ask-become-pass`. The unprivileged deployment account `user` is not suitable
unless it has explicitly been granted sudo access.

The defaults use `/opt/mutating.tech` for the copied source tree, with Hugo in
`/opt/mutating.tech/site` and the library in `/opt/mutating.tech/packages/ui`, and
`/var/www/mutating.tech` for the generated site served by nginx and updated
by CI. They target `mutating.tech` and the deployment account `user`.
Override `local_site_path`, `local_ui_path`, `local_runtime_path`, `site_domain`, `site_aliases`, `certbot_email`,
`deploy_user`, `deploy_group`, `source_path`, `source_staging_path`, and
`deploy_path` with additional variables when provisioning another site. Keep
the domain-dependent values consistent; for structured values such as
`site_aliases`, prefer a JSON extra variables file instead of shell quoting.

A successful run ends with `failed=0` and `unreachable=0`. Verify the resulting
server configuration and public origin:

```bash
ssh root@SERVER_IP 'nginx -t && systemctl is-active nginx certbot.timer mutating-cert-bootstrap.timer mutating-crawler-report.timer'
curl -fsSI http://mutating.tech/
curl -fsSI https://mutating.tech/
curl -fsSI https://www.mutating.tech/
```

The HTTP and `www` requests should redirect to the canonical HTTPS origin, and
the canonical HTTPS request should return `200`. Certificate issuance is
deliberately deferred when DNS for every configured hostname does not yet reach
the server; `mutating-cert-bootstrap.timer` retries it automatically.

### Prepare a fresh Debian server

The following procedure targets Debian 13, nginx, the domain
`mutating.tech`, the unprivileged deployment account `user`, and the deployment
directory `/var/www/mutating.tech`. Run the system-administration commands as
`root` or through an account with `sudo`. GitHub Actions later connects only as
`user`; that account does not need `sudo`.

#### 1. Confirm that the server is publicly reachable

The server needs a public IPv4 address. In the hosting-provider firewall or
security-group UI, permit incoming TCP connections to:

- the SSH port, normally `22`;
- port `80` for HTTP and certificate validation;
- port `443` for HTTPS.

Do not restrict SSH exclusively to a home or office IP if a standard
GitHub-hosted runner must deploy to the server. Those runners use changing
source addresses. A private address, VPN-only host, or IPv6-only SSH endpoint is
not reachable by the current workflow.

Connect through SSH or the provider's browser console:

```bash
ssh root@SERVER_IPV4
```

If direct root login is unavailable, connect through the provider's
administrator account and prefix the remaining administrative commands with
`sudo`.

#### 2. Update Debian and install the runtime packages

The production server does not need Hugo, Node.js, Python, or the repository.
Hugo runs inside GitHub Actions and only the generated static files reach the
server. Install nginx for HTTP serving and rsync for deployment:

```bash
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx rsync curl
```

Confirm the installed services and tools:

```bash
nginx -v
rsync --version | head -n 1
systemctl is-enabled nginx
```

If Debian itself uses UFW, allow the ports before enabling it. Replace `22`
with the actual SSH port first; enabling a firewall with the wrong SSH rule can
lock out the server:

```bash
apt-get install -y ufw
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose
```

Provider-level and operating-system firewalls are independent. Opening a port
in UFW does not open a blocked security-group rule in the hosting panel, and
vice versa.

#### 3. Create the unprivileged deployment account

Check whether `user` already exists:

```bash
id user
```

If it does not exist, create it and assign a strong, unique password:

```bash
adduser user
```

The password entered here becomes the value of the GitHub secret
`DEPLOY_PASSWORD`. Do not add `user` to `sudo`: the account only needs to write
the site directory. Test the credentials in a second terminal before closing
the administrative session:

```bash
ssh -4 user@SERVER_IPV4
```

The workflow currently uses password authentication. Check the effective SSH
server setting:

```bash
sshd -T | grep '^passwordauthentication'
```

If the result is not `passwordauthentication yes`, enable it in a dedicated
drop-in, validate the SSH configuration, and reload SSH without interrupting
existing sessions:

```bash
printf '%s\n' 'PasswordAuthentication yes' \
  > /etc/ssh/sshd_config.d/60-mutating-deploy.conf
sshd -t
systemctl reload ssh
```

Keep the original administrative session open until a fresh login as `user`
has succeeded. This prevents an SSH configuration mistake from locking all
access to the server.

#### 4. Create the deployment directory

Create the exact directory hard-coded in the deployment workflow and give only
the deployment account ownership:

```bash
install -d -o user -g user -m 0755 /var/www/mutating.tech
```

nginx only reads this directory. GitHub Actions writes it as `user` and applies
mode `0755` to directories and `0644` to files. Verify the ownership:

```bash
stat -c '%U:%G %a %n' /var/www/mutating.tech
```

The expected result begins with:

```text
user:user 755 /var/www/mutating.tech
```

#### 5. Configure nginx

Create `/etc/nginx/sites-available/mutating.tech` with this configuration:

```nginx
server {
    listen 80;
    listen [::]:80;

    server_name mutating.tech www.mutating.tech SERVER_IPV4;

    root /var/www/mutating.tech;
    index index.html;
    charset utf-8;
    server_tokens off;
    http2 on;
    sendfile on;
    tcp_nopush on;

    gzip on;
    gzip_vary on;
    gzip_comp_level 6;
    gzip_min_length 1024;
    gzip_types
        application/javascript
        application/json
        application/xml
        image/svg+xml
        text/css
        text/javascript;

    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
        default_type text/plain;
        try_files $uri =404;
    }

    location / {
        try_files $uri $uri/ $uri/index.html =404;
    }

    location ~* \.(?:css|js|mjs|json|jpg|jpeg|png|gif|webp|avif|svg|ico|woff|woff2|ttf|otf)$ {
        try_files $uri =404;
        expires 1y;
        add_header Cache-Control "public, immutable";
        add_header X-Content-Type-Options "nosniff" always;
    }

    location ~ /\.(?!well-known/) {
        deny all;
    }
}
```

Replace `SERVER_IPV4` with the server's public IPv4 address. Then enable the
site alongside the existing virtual hosts:

```bash
ln -sfn /etc/nginx/sites-available/mutating.tech \
  /etc/nginx/sites-enabled/mutating.tech
```

Always validate nginx before applying a change:

```bash
nginx -t
systemctl enable --now nginx
systemctl reload nginx
systemctl is-active nginx
```

For a pre-deployment HTTP check, create a temporary placeholder owned by the
deployment account:

```bash
printf '%s\n' 'Deployment target ready.' \
  > /var/www/mutating.tech/index.html
chown user:user /var/www/mutating.tech/index.html
chmod 0644 /var/www/mutating.tech/index.html

curl -I -H 'Host: mutating.tech' http://127.0.0.1/
curl -I -H 'Host: mutating.tech' http://SERVER_IPV4/
```

The loopback request runs on the server. Run the public-IP request from another
machine to verify the complete external network path. Both should return
`HTTP/1.1 200 OK`. The first successful GitHub deployment replaces the
placeholder.

#### 6. Point DNS to the server and enable HTTPS

Set the `A` records for `mutating.tech` and, if used, `www.mutating.tech` to the
server's public IPv4 address. If the records are proxied by Cloudflare, update
their origin address in Cloudflare; a public DNS lookup will continue to show
Cloudflare addresses while proxying is enabled.

Install Certbot and its nginx integration:

```bash
apt-get install -y certbot python3-certbot-nginx
```

Create an ACME webroot outside the Hugo deployment directory. This is important:
the production rsync intentionally deletes anything under
`/var/www/mutating.tech` that is absent from the Hugo build.

```bash
install -d -o root -g root -m 0755 \
  /var/www/letsencrypt/.well-known/acme-challenge

python3 -c 'import secrets; print(secrets.token_urlsafe(32))' \
  > /var/www/letsencrypt/.well-known/acme-challenge/mutating-origin-check

chmod 0644 \
  /var/www/letsencrypt/.well-known/acme-challenge/mutating-origin-check
```

After adding the ACME location shown in the nginx configuration above, validate
and reload nginx:

```bash
nginx -t
systemctl reload nginx

curl -fsS \
  -H 'Host: mutating.tech' \
  http://127.0.0.1/.well-known/acme-challenge/mutating-origin-check
```

The bootstrap script below waits until the same marker is visible through the
public domain. Therefore it does not repeatedly send failing certificate
requests while DNS still points elsewhere. Create
`/usr/local/sbin/mutating-cert-bootstrap`:

```sh
#!/bin/sh
set -eu

domain="mutating.tech"
certificate="/etc/letsencrypt/live/${domain}/fullchain.pem"
marker="/var/www/letsencrypt/.well-known/acme-challenge/mutating-origin-check"
probe="http://${domain}/.well-known/acme-challenge/mutating-origin-check"

if [ -s "$certificate" ]; then
    exit 0
fi

expected="$(cat "$marker")"
observed="$(curl --fail --silent --show-error --location --max-time 20 \
  "$probe" 2>/dev/null || true)"

if [ "$observed" != "$expected" ]; then
    echo "${domain} is not routed to this nginx origin yet; certificate issuance postponed."
    exit 0
fi

certbot --nginx \
    --non-interactive \
    --agree-tos \
    --email contact@mutating.tech \
    --redirect \
    -d "$domain"

nginx -t
systemctl reload nginx
```

Use a monitored address instead of `contact@mutating.tech` if certificate
notices should go elsewhere. Make the script root-owned and executable:

```bash
chown root:root /usr/local/sbin/mutating-cert-bootstrap
chmod 0755 /usr/local/sbin/mutating-cert-bootstrap
```

Create `/etc/systemd/system/mutating-cert-bootstrap.service`:

```ini
[Unit]
Description=Issue the initial TLS certificate for mutating.tech when DNS is ready
After=network-online.target nginx.service
Wants=network-online.target
ConditionPathExists=!/etc/letsencrypt/live/mutating.tech/fullchain.pem

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/mutating-cert-bootstrap
```

Create `/etc/systemd/system/mutating-cert-bootstrap.timer`:

```ini
[Unit]
Description=Retry initial TLS certificate issuance for mutating.tech

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
RandomizedDelaySec=2min
Persistent=true
Unit=mutating-cert-bootstrap.service

[Install]
WantedBy=timers.target
```

Certbot's Debian package supplies `certbot.timer`, which runs renewal checks
regularly. Add an explicit deploy hook so nginx is validated and reloaded after
every successful renewal. Create
`/etc/letsencrypt/renewal-hooks/deploy/reload-nginx`:

```sh
#!/bin/sh
set -eu

nginx -t
systemctl reload nginx
```

Set the hook permissions and enable both timers:

```bash
chown root:root /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx

systemd-analyze verify \
  /etc/systemd/system/mutating-cert-bootstrap.service \
  /etc/systemd/system/mutating-cert-bootstrap.timer

systemctl daemon-reload
systemctl enable --now certbot.timer mutating-cert-bootstrap.timer
systemctl start mutating-cert-bootstrap.service
```

Before DNS is ready, the bootstrap journal should say that issuance was
postponed. Once the public marker reaches this server, the next timer execution
requests the initial certificate, lets Certbot add HTTPS and the HTTP-to-HTTPS
redirect to nginx, and then stops doing work because the certificate file
exists. `certbot.timer` handles all subsequent renewals.

Inspect the state at any time:

```bash
systemctl list-timers certbot.timer mutating-cert-bootstrap.timer --all
journalctl -u mutating-cert-bootstrap.service -n 50 --no-pager
certbot certificates
```

If Cloudflare prevents the HTTP challenge from reaching this server, temporarily
switch the relevant records to **DNS only**, issue the certificate, verify
HTTPS, and then enable the proxy again. In particular, Cloudflare **Full
(strict)** cannot reach a new origin over HTTPS before that origin has a valid
certificate. Use **DNS only** for the initial issuance, then enable the proxy and
select **Full (strict)** after the certificate exists.

Verify nginx and certificate renewal:

```bash
nginx -t
systemctl status certbot.timer --no-pager
certbot renew --dry-run
curl -I https://mutating.tech/
```

#### 7. Add the GitHub deployment secrets

Follow the repository-secret instructions in the next section. For this server
configuration, use:

| Secret | Server value |
| --- | --- |
| `DEPLOY_HOST` | The server's public IPv4 address, without `https://` or a port |
| `DEPLOY_PORT` | `22`, unless SSH was explicitly moved |
| `DEPLOY_USER` | `user` |
| `DEPLOY_PASSWORD` | The password assigned to `user` |

`DEPLOY_PATH` is deliberately not a secret. The workflow and nginx both use
`/var/www/mutating.tech`; if that path ever changes, update both configurations
together.

#### 8. Run and verify the first deployment

Either push a commit to `main`, or open **Actions → Deploy production → Run
workflow**. The workflow performs these operations in order:

1. validates every required secret;
2. installs Hugo Extended and the deployment tooling on the disposable runner;
3. builds a clean, minified production site into `site/public/` and builds the platform UI;
4. verifies redirects, TLS hostname coverage, HTTP/2, HSTS, and HTML caching
   before touching the production files;
5. checks that the SSH destination exists, is writable, and has `rsync`;
6. uploads one release as `user`, backs up SQLite, applies migrations, starts the common service, and publishes the static build;
7. removes files that are no longer present in the Hugo build after a successful
   transfer.

After the job succeeds, verify the deployed server:

```bash
curl -I https://mutating.tech/
curl -I https://mutating.tech/blog/
ssh user@SERVER_IPV4 \
  "find /var/www/mutating.tech -type f | wc -l"
```

Every later push to `main` repeats this process automatically. Pushes to other
branches run the test workflow but do not publish production.

#### 9. Troubleshoot deployment and nginx

Use the exact SSH values locally before debugging GitHub Actions:

```bash
ssh -4 -p 22 user@SERVER_IPV4
```

Common failures:

- `Network is unreachable`: the configured host is private or IPv6-only.
- `Connection timed out`: the address exists, but a provider firewall,
  server firewall, NAT rule, or closed SSH port is dropping connections.
- `Permission denied`: verify `DEPLOY_USER`, `DEPLOY_PASSWORD`, SSH password
  authentication, and any intrusion-prevention blocklist.
- `Remote DEPLOY_PATH does not exist`: create `/var/www/mutating.tech` as shown
  above.
- `Remote DEPLOY_PATH is not writable`: restore ownership with
  `chown -R user:user /var/www/mutating.tech`.
- `rsync is not installed`: run `apt-get install -y rsync` on the server.
- nginx returns `403`: directories normally need mode `0755`, files need mode
  `0644`, and nginx needs traversal permission through every parent directory.
- nginx returns `404`: confirm that `/var/www/mutating.tech/index.html` exists
  and that the enabled nginx site's `root` matches the workflow path.

Server-side diagnostics:

```bash
nginx -t
systemctl status nginx --no-pager
ss -ltnp | grep -E ':(22|80|443)[[:space:]]'
stat -c '%U:%G %a %n' /var/www/mutating.tech
tail -n 100 /var/log/nginx/error.log
journalctl -u nginx -n 100 --no-pager
```

The deployment keeps no server-side release history. To roll back, revert the
problematic commit on `main` or manually rerun the workflow for a corrective
commit. Do not edit generated files directly on the server: the next deployment
will intentionally replace them with the Hugo build.

### Add the deployment secrets to GitHub

The deployment workflow reads its configurable connection
values through the GitHub Actions `secrets` context. Add them as repository
**Secrets**, not repository **Variables**:

1. Open the repository on GitHub.
2. Select **Settings**.
3. In the left sidebar, select **Secrets and variables → Actions**.
4. Open the **Secrets** tab.
5. Select **New repository secret**.
6. Enter the exact secret name and its value, then select **Add secret**.
7. Repeat the process for every secret in the following table.

The repository needs these four secrets:

| Secret | Purpose |
| --- | --- |
| `DEPLOY_HOST` | Public SSH hostname or IPv4 address of the production server. The standard GitHub-hosted runner deploys over IPv4. |
| `DEPLOY_PORT` | Numeric SSH port, normally `22`. |
| `DEPLOY_USER` | Dedicated SSH user that owns or can write the deployment directory. |
| `DEPLOY_PASSWORD` | SSH login password for `DEPLOY_USER`. Use a strong, unique password belonging to the dedicated deploy account. This is not a `sudo` password. |

Enter `DEPLOY_HOST` without `https://` or the SSH port.
The workflow fixes `SITE_BASE_URL` to `https://mutating.tech/`.
`DEPLOY_PASSWORD` must be the password accepted by the SSH server when logging
in as `DEPLOY_USER`; the remote activation uses the restricted passwordless
sudo rule installed by the platform playbook.
GitHub hides a secret after it has been saved; it can be replaced but its value
cannot be viewed again.

`DEPLOY_HOST` must resolve to a public IPv4 address, and the server firewall must
accept connections to `DEPLOY_PORT` from GitHub-hosted runners. An IPv6-only
address, an address from a private network, or an SSH service restricted to a
local/VPN network is not reachable from the standard `ubuntu-latest` runner.

The workflow uploads shared releases to `/opt/mutating/releases/`; the activation
wrapper publishes Hugo to `/var/www/mutating.tech`. The platform playbook creates
these directories and the restricted sudo rule for `DEPLOY_USER`. The SSH server
must allow password authentication for that account. No password is stored in Git.

The workflow validates all four secrets before installing dependencies or
building the site. A missing secret is reported by name, and an SSH preflight
runs before uploading. SSH host-key verification remains enabled; the trusted
public key lives in `deploy/known_hosts` under `HostKeyAlias=mutating-production`,
so no additional GitHub secret is required. If the server host key changes, verify
the replacement before updating that file. If a deployment fails, use the named
`::error` message together with the SSH or rsync output immediately above it;
the workflow never prints secret values.

## Writing tests

- Put site tests in `tests/site/` and name files `test_*.py`.
- Follow pytest conventions.
- Give every test function a docstring explaining the idea and the exact
  behavior under test.
- Receive browser access through the `driver` fixture in `tests/site/conftest.py`;
  tests must not launch their own browser or WebDriver.
- Prefer Playwright locators and `expect(...)` assertions over fixed sleeps.
- Ask `driver(...)` for the required viewport, system color scheme, stored
  theme, reduced-motion preference, or isolated browser capability.
- Keep tests independent; the fixture creates and closes a separate browser
  context for each test.
- Add `@pytest.mark.interactive_visuals` only when a test directly inspects or
  interacts with a real WebGL iframe. Add the matching `xdist_group` marker
  (`tentacle-webgl` or `tokamak-webgl`) so GPU-heavy tests remain robust at any
  requested worker count.
- Add new Python dependencies to `requirements_dev.txt` and install them only
  after activating `venv`.
