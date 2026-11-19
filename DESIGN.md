# guideline-checker — Design contract

> Product design contract for guideline-checker's human-facing surfaces. Rule:
> `standards/rules/design.md` · method and acceptance criteria:
> `shared-standards/docs/DESIGN-CONTRACT.md`. Read it before changing the workshop UI, a report
> layout or what the CLI prints, and update it in the same change.
>
> Status of every statement: **[implemented]** links the code · **[decided]** cites the ADR ·
> **[proposed]** is not agreed. A plan is never described as shipped.

## 1. Surface and status

- **Surfaces:**
  - **Workshop web UI:** one self-contained page (inline CSS and JS, no build, no CDN), served on
    `/` by the optional FastAPI app **[implemented]**
    ([index.html](guideline_checker/web/static/index.html), [app.py](guideline_checker/web/app.py)).
    `GC_MODE=viewer` refuses every write route ([mode.py](guideline_checker/web/mode.py)).
  - **Generated HTML reports:** per repository and per workspace synthesis **[implemented]**
    ([html.py](guideline_checker/reporters/html.py),
    [synthesis_html.py](guideline_checker/reporters/synthesis_html.py)).
  - **CLI output:** plain text, plus Markdown (PR comments), JSON (versioned contract, D-0022)
    and SARIF **[implemented]** ([cli.py](guideline_checker/cli.py)).
- **Lifecycle:** shipped and public ([README.md](README.md)). The real frontend replaced the
  embedded HTML string **[decided]** (D-0011 in [DECISIONS.md](DECISIONS.md), 2026-07-18).
- **Detailed specs:** none. Folder rules: [web/static/README.md](guideline_checker/web/static/README.md).

## 2. Users, devices and priority tasks

| User | Context and device | Priority tasks |
| ---- | ------------------ | -------------- |
| Developer maintaining agent instructions | Desktop browser on localhost or a trusted host | 1. scan a project and read its grade and rule health · 2. find dead or unresolvable rules and their source file · 3. propose and prove a detector for a rule |
| Reviewer or CI reader | Browser (HTML report), PR (Markdown), Code Scanning (SARIF) | see what violates which rule, with file and line |

Primary action: *Scan* in the topbar; in the workshop panel, *Propose & replay*. **[implemented]**

## 3. Journeys and navigation

- **J1 — Scan a project:** pick a project in the select, or *Browse…* a folder in a dialog →
  *Scan* → grade, health tiles and rule rows. Polling stops after 30 s. **[implemented]**
- **J2 — Fleet view:** *All projects* → one row per project. Rows are real links (`#project`),
  open in a new tab, and *Back* returns to the fleet view. **[implemented]**
  ([e2e test](tests/e2e/test_project_row_navigation.py))
- **J3 — Workshop:** type a rule → *Propose & replay* → proof hits and a diff → arm or persist
  the detector. In viewer mode the server refuses with 403. **[implemented]**
- **Information architecture:** one page. The project is in the URL hash; the filter (`q`) and
  sort are in the query string, kept on reload and shareable. Health tiles act as filters.
- **Density:** compact rows with mono metadata; on narrow screens the layout drops to one column
  (880, 720 and 560 px rules).

## 4. Identity

- **Direction:** "Exaggerated Minimalism · slate/green · mono" **[decided]** (D-0011). Dark
  slate ground, green for *proven*, sky blue for *armed*, pink for *dead*.
- **Signature element:** the large mono letter grade with its score, above the health tiles.
- **Reports differ:** HTML reports use their own light palette (`--clr-*`) and do not share the
  workshop tokens.

## 5. Tokens and components (owned elsewhere)

| Concern | Source of truth | Rules of use |
| ------- | --------------- | ------------ |
| Colour roles and themes | `:root` and `:root[data-theme="light"]` in [index.html](guideline_checker/web/static/index.html) | rule states `--proven/--armed/--dead/--advisory/--warn`; chip tint `--tint` (16 % dark, 8 % light) |
| Typography | `--sans` (Fira Sans, system fallbacks), `--mono` (Fira Code, system fallbacks) | mono for data, labels and buttons |
| Spacing, shape | `--sp-1` … `--sp-5`, `--radius`, `--radius-sm`, `--shadow` | — |
| Motion | literal transitions of 0.1–0.2 s, a spinner, a reduced-motion block | functional only |
| Report palette | `--clr-*` in [html.py](guideline_checker/reporters/html.py) | light only |
| Components | CSS classes in the page: `.btn`, `.chip`, `.tile`, `.grade`, `.state`, `.toast`, `dialog.browser` | no library |

- **Shared contract:** `contract.css` not imported; `@chrysa/ui` not used. The page must stay
  self-contained (folder rules).
- **Design debt:** motion durations are literals, not tokens; inline `style` on the workshop
  buttons; two palettes (workshop and reports).

## 6. Interaction and states

- **Component states:** hover, `:focus-visible` outline in the accent, `disabled` on *Scan*
  while it runs. **[implemented]**
- **Action feedback:** a spinner line for pending work; a toast (`role="status"`) for success
  and failure.
- **Forms:** the rule text area keeps its content after a failure.
- **Data states:**

  | State | Where it appears | Behaviour |
  | ----- | ---------------- | --------- |
  | Loading | rule list | spinner and message, `aria-busy="true"` |
  | Empty | rule list, stamp | "Not scanned yet." and a hint to scan |
  | Error | rule list, toast | `⚠` message and "press Scan to retry"; the text is `path → status` |
  | Offline / backend unreachable | — | no dedicated state; shown as an error |
  | Stale data | stamp under the summary | time of the last scan |
  | Reconnect | — | none |

- **Real-time:** bounded polling after a scan (60 × 500 ms per project; 240 × 1 s for the fleet,
  then a timeout message). The single-project poll ends without a message.
- **Motion:** no entrance effect for views or new rows (FE-053 gap). Reduced motion cancels the
  transitions, the spinner and smooth scroll. **[implemented]**
- **Auth:** on 401 the page asks for the API key with `window.prompt` and keeps it in
  `sessionStorage`.

## 7. Responsive and support conventions

Breakpoints at 880, 720 and 560 px, plus a `pointer: coarse` rule. HTML reports have a fixed
280 px navigation column. Sizes have not been verified in a browser for this contract.

## 8. Accessibility

- **Target:** WCAG 2.1 AA (canon floor).
- **Keyboard:** skip link to the results; native `dialog` for the folder browser; project rows
  are anchors, so they open without a key handler. **[implemented]**
- **Perception:** light-theme contrast was measured and raised to a worst case of 4.62:1 (dark:
  4.53:1), as recorded in the token comment. States are chips with words, never colour alone.
- **Assistive technology:** visually hidden labels, `aria-live` on the summary, stamp and
  results; `lang` follows the EN/FR switch.

## 9. Assets

| Asset | Source or generator | Author | Licence and attribution | Permitted use | Formats and variants | Location |
| ----- | ------------------- | ------ | ----------------------- | ------------- | -------------------- | -------- |
| Fonts | none shipped: Fira Sans and Fira Code are named, system fallbacks apply | — | — | — | — | [index.html](guideline_checker/web/static/index.html) |
| Theme icon | inline SVG drawn in the page | project | [LICENSE](LICENSE) (MIT) | UI | SVG | [index.html](guideline_checker/web/static/index.html) |

Reports link to the project's GitHub page in their footer. No image file ships.

## 10. Acceptance criteria and evidence

- **Criteria:** J1–J3 at 1440, 880 and 375 px; keyboard only; viewer mode shows a readable
  refusal; a failed scan says what to do; reduced motion stops the spinner; contrast stays at
  4.5:1 or more in both themes.
- **Existing checks:** Playwright row navigation ([tests/e2e](tests/e2e/test_project_row_navigation.py));
  API, auth and mode tests ([test_web.py](tests/test_web.py), [test_web_mode.py](tests/test_web_mode.py));
  report tests ([test_html_reporter.py](tests/test_html_reporter.py)).
- **Evidence log:**

  | Date | Change | Checks run (tool, sizes, modes) | Result | Not run, and why |
  | ---- | ------ | ------------------------------- | ------ | ---------------- |
  | 2026-11-08 | contract written from the code | source review | gaps listed in §11 | no browser run: documentation-only change |

## 11. Open choices, known gaps and maintenance

- **Known gaps:** raw `path → status` error text; no offline state; viewer-mode 403 not
  explained in the UI; `window.prompt` for the API key; the single-project scan times out
  silently; no entrance motion; motion literals; two palettes.
- **Open choice [proposed]:** share one token set between the workshop and the HTML reports.
  Rationale: one identity per product; the alternative is to keep reports neutral for CI use.
- **Maintenance:** the owner updates this file with every change to the page, the reports or
  the printed output, and with the matching tests.
