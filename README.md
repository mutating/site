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

Automated checks:

```bash
npm test
npm run lint
```

## Project map

- `app/page.tsx` — complete page content and interactive behavior.
- `app/globals.css` — light/dark themes, responsive layout, and animations.
- `app/layout.tsx` — metadata, social preview, fonts, and theme bootstrap.
- `public/muta-eye-surface.webp` — animated header-eye surface.
- `public/muta-v2-transparent.png` — Muta artwork used in the hero section.
- `public/muta-tentacles.html` — self-contained interactive WebGL tentacle surface used behind the contact call-to-action.
- `public/tokamak-reactor.html` — self-contained interactive tokamak model.
- `public/og.png` — social sharing image.
- `tests/rendered-html.test.mjs` — checks the rendered page and required local assets.
- `.openai/hosting.json` — Sites project binding.

All site-critical visual assets are stored locally under `public/`; the site
does not depend on temporary desktop files.
