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

Everything that defines presentation or behavior lives in `themes/mutating/`:

- `layouts/_default/baseof.html` is the shared HTML document.
- `layouts/partials/header.html` and `footer.html` are shared by every page.
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
  Hugo.
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

The default command uses `pytest-xdist` with `-n auto`, so pytest selects one
worker per available logical CPU. Override the degree of parallelism freely for
local diagnostics or CI capacity planning:

```bash
python -m pytest -n 16
python -m pytest -n 1
```

Each xdist worker performs its own clean Hugo build, cache, content copy,
nonstandard port selection, server process, and browser lifecycle. Nothing is
shared between worker filesystems or server processes. A successful validation
build is removed immediately and the development server renders to memory, so
raising `-n` does not multiply full static-site copies on disk. Tests that
require the real WebGL tentacle or tokamak renderers opt in with the
`interactive_visuals` marker; `--dist loadgroup` keeps each renderer's tests on
one resource group so changing `-n` does not create competing GPU contexts.
All other browser tests intercept those heavyweight iframe resources while
still exercising the real page DOM and application code. At the end of the
session every worker terminates its complete Hugo process group and pytest
removes its temporary build directories.

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

### Provision a fresh server with Ansible

The repository includes `infra/playbooks/setup-server.yml`. Run it from a
local checkout. The playbook copies that checkout to the server, installs Git,
Hugo, nginx, and Certbot, performs the first Hugo build, and configures nginx
and certificate renewal. The VPS does not need access to the private GitHub
repository.

#### 1. Clone the repository locally

```bash
git clone https://github.com/pomponchik/site.git
cd site
```

#### 2. Check that Ansible is installed

```bash
ansible-playbook --version
```

On macOS, install it with `brew install ansible`. On Debian or Ubuntu, use:

```bash
sudo apt-get update && sudo apt-get install -y ansible sshpass
```

`sshpass` is required when the initial SSH login uses a password.

#### 3. Run the setup playbook

Replace the three values with the server's public IPv4 address, initial SSH
username, and password:

```bash
ansible-playbook \
  -i 'SERVER_IP,' \
  infra/playbooks/setup-server.yml \
  -e "ansible_host=SERVER_IP ansible_user=SERVER_USER ansible_password=SERVER_PASSWORD"
```

For example:

```bash
ansible-playbook \
  -i '203.0.113.10,' \
  infra/playbooks/setup-server.yml \
  -e "ansible_host=203.0.113.10 ansible_user=root ansible_password='change-me'"
```

The password is visible in shell history and process arguments while the
command runs. For a safer interactive alternative, omit `ansible_password`
and add `--ask-pass`.

The defaults use `/opt/mutating.tech` for the copied source checkout and
`/var/www/mutating.tech` for the generated site served by nginx and updated
by CI. They target `mutating.tech` and the deployment account `user`.
Override these values with additional `-e` options for a different site.

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
    listen 80 default_server;
    listen [::]:80 default_server;

    server_name mutating.tech www.mutating.tech SERVER_IPV4 _;

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
site and disable Debian's placeholder site:

```bash
ln -sfn /etc/nginx/sites-available/mutating.tech \
  /etc/nginx/sites-enabled/mutating.tech

if [ -L /etc/nginx/sites-enabled/default ]; then
  unlink /etc/nginx/sites-enabled/default
fi
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
| `SITE_BASE_URL` | `https://mutating.tech/` |
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
2. installs Hugo Extended on the disposable runner;
3. builds a clean, minified production site into `public/`;
4. checks that the SSH destination exists, is writable, and has `rsync`;
5. uploads the build as `user` with directory mode `0755` and file mode `0644`;
6. removes files that are no longer present in the Hugo build after a successful
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

The deployment workflow reads its configurable connection and production-URL
values through the GitHub Actions `secrets` context. Add them as repository
**Secrets**, not repository **Variables**:

1. Open the repository on GitHub.
2. Select **Settings**.
3. In the left sidebar, select **Secrets and variables → Actions**.
4. Open the **Secrets** tab.
5. Select **New repository secret**.
6. Enter the exact secret name and its value, then select **Add secret**.
7. Repeat the process for every secret in the following table.

The repository needs these five secrets:

| Secret | Purpose |
| --- | --- |
| `SITE_BASE_URL` | Public site URL used by Hugo, including the scheme and preferably the trailing slash, for example `https://mutating.tech/`. |
| `DEPLOY_HOST` | Public SSH hostname or IPv4 address of the production server. The standard GitHub-hosted runner deploys over IPv4. |
| `DEPLOY_PORT` | Numeric SSH port, normally `22`. |
| `DEPLOY_USER` | Dedicated SSH user that owns or can write the deployment directory. |
| `DEPLOY_PASSWORD` | SSH login password for `DEPLOY_USER`. Use a strong, unique password belonging to the dedicated deploy account. This is not a `sudo` password. |

Enter `DEPLOY_HOST` without `https://` or the SSH port. Enter
`SITE_BASE_URL` as a complete public URL, including `https://` and preferably a
trailing slash. `DEPLOY_PASSWORD` must be the password accepted by the SSH
server when logging in as `DEPLOY_USER`; the workflow never invokes `sudo`.
GitHub hides a secret after it has been saved; it can be replaced but its value
cannot be viewed again.

`DEPLOY_HOST` must resolve to a public IPv4 address, and the server firewall must
accept connections to `DEPLOY_PORT` from GitHub-hosted runners. An IPv6-only
address, an address from a private network, or an SSH service restricted to a
local/VPN network is not reachable from the standard `ubuntu-latest` runner.

The workflow always deploys to `/var/www/mutating.tech`. The server must have
`rsync` installed, and that directory must already exist and be writable by
`DEPLOY_USER`. The SSH server must allow password authentication for that
account. Use a dedicated unprivileged deploy account whose permissions are
limited to that directory. No checked-in password or server address is required.

The workflow validates all five secrets before installing dependencies or
building the site. A missing secret is reported by name, and an SSH preflight
runs before uploading. SSH host-key verification is intentionally disabled for
the disposable GitHub runner, so no separate host-key secret is required. This
makes setup simpler, but means the workflow does not protect the SSH connection
from server impersonation. If a deployment fails, use the named `::error`
message together with the SSH or rsync output immediately above it; the workflow
never prints secret values.

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
- Add `@pytest.mark.interactive_visuals` only when a test directly inspects or
  interacts with a real WebGL iframe. Add the matching `xdist_group` marker
  (`tentacle-webgl` or `tokamak-webgl`) so GPU-heavy tests remain robust at any
  requested worker count.
- Add new Python dependencies to `requirements_dev.txt` and install them only
  after activating `venv`.
