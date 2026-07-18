# web/static

**Role.** Bundled static assets for the single-page workshop UI served by the
FastAPI app on `/`. Replaces the former 412-line `_DASHBOARD_HTML` Python string
(ADR D-0011).

## Structure

| Path         | Purpose                                                        |
| ------------ | -------------------------------------------------------------- |
| `index.html` | Self-contained workshop UI (inline CSS/JS, no build, no CDN).  |

## Should contain

- Self-contained static files (HTML/CSS/JS/SVG) consumed by the browser and
  shipped in the wheel via `[tool.setuptools.package-data]`.

## Should NOT contain

- Python, secrets, or server logic — those live in `web/app.py`.
- External CDN references — the page is served on the public `/` route and must
  stay self-contained (no key leakage, no third-party fetches).

## Rules

- The UI consumes the JSON API only (`/api/scan`, `/api/rules-health`,
  `/api/propose`, `/api/rules/detector`); it never embeds the server API key —
  the browser prompts for it and stores it in `sessionStorage` (`X-Api-Key`).
- Loaded through `importlib.resources` in `app._dashboard_html()`, so any file
  added here must be declared in `package-data` to ship in the wheel.
