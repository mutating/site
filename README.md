# The Mutating Company

Complete source for the company website:
[the-mutating-company.pomponchiks.chatgpt.site](https://the-mutating-company.pomponchiks.chatgpt.site).

The project is a single-page React/Next application built with
[vinext](https://github.com/cloudflare/vinext) for Sites.

## Local development

Node.js `>=22.13.0` is required.

```bash
npm ci
npm run dev
```

Production validation:

```bash
npm run build
npm run start
```

## Python test environment

Python packages must be installed only inside the project-local `venv`.
Create it and install the development dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements_dev.txt
python -m playwright install chromium
```

On Windows, activate the environment with:

```powershell
venv\Scripts\activate
```

Do not install `pytest`, Playwright, or any other test dependency globally.
Whenever dependencies change, update `requirements_dev.txt` and reinstall them
inside `venv`.

## Running tests

Activate `venv`, then run:

```bash
python -m pytest
```

The test session starts the site on an available local port, launches a
headless Chromium browser, and shuts both down when the run finishes. To test an
already running instance instead, provide its URL:

```bash
TEST_BASE_URL=http://127.0.0.1:3000 python -m pytest
```

The npm shortcut uses the active Python environment:

```bash
npm test
```

Run the remaining production checks separately:

```bash
npm run build
npm run lint
```

## Writing tests

- Put tests in `tests/` and name files `test_*.py`.
- Use `pytest`; every test function must include a docstring that explains the
  idea of the test and the exact behavior being checked.
- Receive browser access through the `driver` fixture from
  `tests/conftest.py`. Tests must not launch Playwright or a browser directly.
- Ask `driver(...)` for an isolated page and declare the required
  `color_scheme`, saved theme, storage availability, or system-theme
  availability explicitly.
- Prefer Playwright locators and `expect(...)` assertions over fixed sleeps.
- Keep each test independent. The fixture creates a fresh browser context and
  closes it after the test.
- Add any new Python test dependency to `requirements_dev.txt` and install it
  only after activating `venv`.

## Project map

- `app/page.tsx` — complete page content and interactive behavior.
- `app/globals.css` — light/dark themes, responsive layout, and animations.
- `app/layout.tsx` — metadata, social preview, fonts, and theme bootstrap.
- `public/muta-eye-surface.webp` — animated header-eye surface.
- `public/muta-v2-transparent.png` — Muta artwork used in the hero section.
- `public/muta-tentacles.html` — self-contained interactive WebGL tentacle surface used behind the contact call-to-action.
- `public/tokamak-reactor.html` — self-contained interactive tokamak model.
- `public/og.png` — social sharing image.
- `tests/conftest.py` — local server, Chromium, and shared `driver` fixtures.
- `tests/test_anchor_navigation.py` — fragment targets, clicks, reloads, browser
  history, and anchor-label interaction states.
- `tests/test_theme.py` — system, fallback, and persisted theme behavior.
- `tests/test_site_smoke.py` — rendered-page and local-asset smoke checks.
- `requirements_dev.txt` — Python dependencies needed by the test suite.
- `.openai/hosting.json` — Sites project binding.

All site-critical visual assets are stored locally under `public/`; the site
does not depend on temporary desktop files.
