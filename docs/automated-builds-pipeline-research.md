# Automated Builds Pipeline — Source Shape Research

*Probe session: 2026-05-05. Executes the pre-implementation research task defined at the end of §1
of `automated-builds-pipeline-design.md`. No pipeline code written. Samples committed to
`bazaar-builds/research/samples/`. This note unblocks subtask 1 (signal-design deep-dive).*

---

## 1. bazaardb.gg/run/meta

### HTTP Shape

Next.js **App Router** (confirmed via `/_next/static/chunks/main-app-*` scripts). There is **no
`__NEXT_DATA__` blob** — App Router uses RSC streaming rather than the Pages Router hydration JSON.
All rendered data is baked into SSR HTML. CSS class names are hashed (CSS Modules), so no
class-based selector is reliable.

Extractable anchors in the rendered DOM:

- `img[alt="Item Name"]` — each displayed item card has its name in the `alt` attribute.
- Anchor text pattern `"N runs · X%"` — frequency of an item within an archetype context.
- Section headers ("CORE ITEMS", "SUPPORTING ITEMS", "POPULAR SKILLS") are plain text nodes.

There is no JSON endpoint visible in network traffic. Fetching the page via `Invoke-WebRequest`
or `curl` returns a Cloudflare **managed challenge** ("Enable JavaScript and cookies to continue")
on every request variant tested (`?hero=karnok`, `/meta/karnok`, `?days=30`, `?window=30`). The
page loads correctly in a live browser session with no login required.

### Freshness Model

**Patch-window, not calendar-based.** The page header states:

> "These meta stats are based on data uploaded by the community for the **most recent numbered
> patch**."

The "Apr 29" indicator in the top navigation links to patch notes — it is not a date filter. There
is no time-window URL parameter, dropdown, or query string that controls the observation window.
The Filters panel (opened via the Filters button) exposes hero, item size, tier, and type/tag
filters only — no date range.

**Design impact — blocker for §1:** The design specifies a "fixed last-30-days window per cron
run." That concept has no mapping in bazaardb. The source is patch-scoped, not calendar-scoped.
Every data point on the page covers whatever community uploads exist since the last numbered patch
(currently Apr 29 → ~6 days old at probe time). Subtask 1 must decide whether to:

- Treat each cron run's bazaardb snapshot as "current patch window" rather than "last 30 days," and
  adjust the consecutive-window counting logic to use patch windows instead of calendar windows; or
- Accept that bazaardb's window is effectively "since last patch" and align the cron schedule to
  fire shortly after each patch.

### Auth / Rate Limiting

- **Cloudflare managed challenge** on all plain HTTP requests — requires a JS-capable browser with
  Cloudflare cookies. `curl` and PowerShell `Invoke-WebRequest` fail unconditionally.
- No login, no paywall — public data once the Cloudflare challenge passes in a browser.
- Rate limiting unknown; not encountered in the browser session.

### Sample Location

`bazaar-builds/research/samples/bazaardb/` — page text captured via browser JS execution
(browser-rendered `document.body.innerText`). No curl artifact possible.

The rendered page at probe time: 20,099 total runs, 9.05 avg wins. First archetype visible:
core items Flying Potion + Boiling Flask + Atmospheric Sampler (510 runs, 9.7 avg wins), with
supporting items Caustic Solvent (176 runs · 35%), Vitality Potion (106 · 21%), etc.

### Recommended Ingestion Approach

**HTTP + JS render (headless browser required)** — Playwright or Puppeteer, not a plain HTTP
client. After render:

1. Optionally click a hero button in the filter panel (client-side filtering, no URL change).
2. Extract `img[alt]` for item names and adjacent "N runs · X%" text for frequencies.
3. Walk the DOM to associate items with archetype groupings (CORE ITEMS / SUPPORTING ITEMS headers).

Alternatively, scrape the unfiltered page and accept cross-hero data, then filter post-hoc by
matching item names against the hero's known item pool (feasible using `card_cache_names.txt` and
`<hero>_builds.json` existing items).

### Blockers for Subtask 1

1. **No 30-day window** — freshness model is patch-scoped, not calendar-scoped. Definition of an
   "observation window" for bazaardb must be revised before the threshold rules (§2) can be written.
2. **Cloudflare** — the pipeline needs headless browser infra (Playwright) to fetch bazaardb.
   "HTTP client only" is not viable. This is a non-trivial infra addition to the GitHub Actions
   workflow; confirm environment supports it before subtask 4 plans the runtime.

---

## 2. mobalytics.gg/the-bazaar/guides/meta-builds and /builds

### HTTP Shape

React SPA (Create React App, hash-chunked JS/CSS). Plain `curl` with a browser User-Agent returns
HTTP 200 — **no Cloudflare challenge, no auth required.** The entire document data is serialized
into `window.__PRELOADED_STATE__=...;` in the page HTML.

#### /guides/meta-builds

PRELOADED_STATE path to build data:

```
theBazaarState
  .apollo.graphqlV2.queries[1]
  .state.data[0]
  .game.documents.userGeneratedDocumentBySlug.data
```

The document has:
- `content`: flat list of 34 CMS nodes, each with `__typename`, `id`, and `data`
- 21 nodes of type **`TheBazaarDocumentCmWidgetBoardCreatorV1`** — one per build

Each `TheBazaarDocumentCmWidgetBoardCreatorV1` node's `data`:

| Field | Content |
|---|---|
| `title` | Build name including hero, e.g. `"Self-Slow (Karnok)"` |
| `cards` | `[{name, size, slug, icon_url}]` — item list, ordered |
| `descriptionTheBazaarBoardCreator` | Lexical rich-text object with editorial description |
| `subTitleTheBazaarBoardCreator` | Usually null |

All 21 builds at probe time (season 13, updated 2026-04-17):

| Hero | Build name | Items |
|---|---|---|
| Mak | Runic Blade | Icicle, Runic Potion, Basilisk Fang, Runic Great Axe, Runic Daggers, Runic Blade |
| Mak | Poppy Field | Runic Potion, Hourglass, Goop Flail, Smelling Salts, Soulstone, Poppy Field |
| Mak | Potions | Potion Distillery, Flying Potion, Caustic Solvent, Boiling Flask, Fire Potion, Atmospheric Sampler |
| Vanessa | Tortuga | Tortuga, Zoarcid, Pesky Pete, Narwhal, Vampire Squid, Piranha, Sharkray |
| Vanessa | Ballista | Ballista, Nesting Doll, Pop Snappers, Dive Weights, Zoarcid, Harpoon, Holsters |
| Vanessa | Slumbering Primordial | Slumbering Primordial, Pop Snappers, Nesting Doll, Zoarcid, Dive Weights, Incendiary Rounds, Clamera, Holsters |
| Pygmalien | Private Hot Springs | Booby Trap, Ice Luge, Cold Room, Private Hot Springs, Gramophone |
| Pygmalien | Square | Lion Cane, Regal Blade, Cash Cannon, Booby Trap, Belt |
| Pygmalien | Money Furnace | Billboard, Abacus, Money Furnace, Gramophone, Belt |
| Dooley | Launcher Core | Fiber Optics, Cooling Fan, Metronome, Ice 9000, Coolant, Beta Ray, Launcher Core, Thrusters |
| Dooley | Weaponized Core | Nitro, Cool LEDs, GPU, Antimatter Chamber, Lightbulb, Capacitor, Weaponized Core |
| Dooley | Dooltron | Dooltron, Z-Shield, Monitor Lizard, Bunker |
| Stelle | Space Laser | Ornithopter, Sirens, Observatory, Space Laser, Headset |
| Stelle | Pillbuggy | Goggles, Pillbuggy, Cloud Tanker, Weather Machine, Headset |
| Stelle | Sky Anchor | Box Cutter, Goggles, Sky Anchor, Tugboat, Sirens, Stelle's Workshop |
| Jules | Spice Rack | Pantry, Instant Noodles, Spice Rack, Pizza, Meat Tenderizer, Zarlic |
| Jules | Freezer | Sorbet, Strawberries, Walk-In Freezer, Ice Cubes, Blender, Black Pepper, Sorbet |
| Jules | Farmer's Market | Zarlic, Strawberries, Farmer's Market, Pasta, Bread Knife, Oven |
| Karnok | Self-Slow | Flying Squirrel, Chains, Healing Draught, Ghillie Suit, Worry Wart, Bear Trap, Tent |
| Karnok | Pacifist | Flying Squirrel, Stretch Pants, Waterskin, Warding Glyphs, Furs, Karst |
| Karnok | Anaconda | Flying Squirrel, Anaconda, Waterskin, Messenger Sparrow, Flare, Karst |

#### /builds (listing and individual articles)

The listing page (`/the-bazaar/builds`) has only 5 build slugs in SSR HTML; the full listing loads
client-side via GraphQL and is not accessible to a plain HTTP fetch. Slugs present at probe time:
`tortuga-vanessa-kripp`, `freeze-mak-kripp`, `aquatic-vanessa-kripp`, `money-furnace-pygmalien-kripp`,
`kinetic-cannon-dooley-kripp`.

Individual build articles follow the same PRELOADED_STATE pattern but use
**`NgfDocumentCmWidgetGameDataCardGridV2`** nodes instead of BoardCreator:

```
data.items = [{title, slug, subTitle (hero), imageUrl, iconStyle}]
```

The Tortuga-Vanessa-Kripp article has three `GameDataCardGridV2` sections:
"Example Early Game Path", "Example Mid Game Additions", "End Game Build Breakdown" — each with
a distinct ordered item list. This is richer phase-level structure than the meta-builds guide.

### KEY FINDING — Design Assumption Wrong

**The design (§9) assumes Mobalytics is "freeform article HTML → requires LLM parsing." This is
incorrect.** The PRELOADED_STATE contains fully structured JSON with explicit item name lists.
Item names are directly extractable without any LLM or HTML parsing.

The LLM is still useful for **carry/core/support classification** using the editorial description
text in `descriptionTheBazaarBoardCreator` (Lexical rich-text), but the "LLM parse pass" for item
name extraction (the first pass described in §9) is unnecessary for this source. Subtask 1 should
revise §9 step 2 accordingly.

### Freshness Model

No date range filter exists. The guide is an editorial document updated when the season changes:

- `createdAt`: 2024-12-10
- `updatedAt`: 2026-04-17 (18 days before probe)
- `firstPublishedAt`: 2024-12-13
- Document version: 537 (incremented on each edit)

The guide is updated on editorial cadence — not daily, not weekly, roughly per-patch or when the
meta shifts. **The "30-day window" concept does not apply.** A cron run fetches whatever the
current editorial state is; the version number can serve as a change-detection signal (only
re-parse if version differs from last run).

### Auth / Rate Limiting

None. Plain `curl` with `User-Agent: Mozilla/5.0 ...Chrome/124...` returns 200. No captcha, no
login, no Cloudflare challenge observed.

### LLM Parse Cost Estimate

For the meta-builds guide (`/guides/meta-builds`):
- Full HTML response: 325 KB (~81k tokens at 4 chars/token)
- PRELOADED_STATE JSON alone: ~106 KB (~26k tokens)
- After extracting only the 21 `TheBazaarDocumentCmWidgetBoardCreatorV1` nodes: ~15–20 KB
- Each build block's `descriptionTheBazaarBoardCreator` value (editorial text): ~500–2000 chars

For **classification only** (carry/core/support), the LLM input per archetype would be:
- Item list (from `cards`) + editorial description (~1–3k chars) + catalog state context
- Estimated ~2–5k tokens per archetype call at Sonnet pricing — pennies per full run

### Sample Location

- `bazaar-builds/research/samples/mobalytics/meta-builds-preloaded-state-builds.json` — all 21
  build blocks with title and item list extracted from PRELOADED_STATE
- `bazaar-builds/research/samples/mobalytics/tortuga-vanessa-kripp-build.json` — full
  `GameDataCardGridV2` extraction from an individual /builds article (3 sections, phase-annotated)
- `bazaar-builds/research/samples/mobalytics/builds-listing.txt` — 5 SSR-visible build slugs

### Recommended Ingestion Approach

**HTTP client only** (no JS render needed). Suggested flow:

1. `GET https://mobalytics.gg/the-bazaar/guides/meta-builds` with browser UA
2. Extract `window.__PRELOADED_STATE__=...;` using regex on response HTML
3. Parse JSON, walk `theBazaarState.apollo.graphqlV2.queries[1].state.data[0].game.documents
   .userGeneratedDocumentBySlug.data.content`
4. Filter nodes to `__typename == "TheBazaarDocumentCmWidgetBoardCreatorV1"`
5. Extract `data.title` (hero + archetype name) and `data.cards[].name` (item list)
6. Cache `version` field; skip re-parse if version unchanged since last cron run

For the `/builds` listing: the 5 SSR-visible slugs can be hard-coded or fetched from the HTML.
The full listing requires JS render or a direct GraphQL call (endpoint discoverable from network
traffic in a real browser session — not probed in this session).

### Blockers / Open Questions for Subtask 1

1. **LLM parse pass for item extraction is unnecessary** — revise §9 step 2 to reflect that item
   names come directly from PRELOADED_STATE JSON; LLM is used only for classification.
2. **`/builds` full listing not accessible** — only 5 articles load server-side; subtask 1 should
   decide whether to hard-code known slugs, probe for the GraphQL endpoint, or treat the meta-builds
   guide as the sole Mobalytics ingestion point.
3. **`condition_items` handling (§9 open question)** — the structured item list from Mobalytics
   gives no carry/core/condition/support signal directly; classification must come entirely from the
   `descriptionTheBazaarBoardCreator` text + catalog context, as originally planned.

---

## 3. bazaar-builds.net/category/builds/

### HTTP Shape

WordPress + Elementor (identified from page structure, CSS class names, and JSON-LD @graph).
Plain HTML, **no JavaScript required** for content. Accessible with the enricher's existing UA
(`BazaarTracker/BuildEnricher`).

Category page structure: archive of post cards, each containing:
- Thumbnail image (`<img class="elementor-post__thumbnail">`)
- Post title anchor (plain text with build name)
- Author, plain-text date ("May 5, 2026")
- "Read More »" link

Individual post structure:
- JSON-LD `@graph` with `@type: Article` node containing `datePublished` (ISO 8601),
  `headline`, `url`
- Post body: navigation + metadata + **"Items:" heading followed by item names** (plain text or
  comma-separated) + "Player's Quote" section

Item extraction example (anaconda-karnok-10-5-build-22704-legend-maplemushy):
```
Items:
Anaconda, Ancient Locket, Bat, Hunting Hawk, Karst
```

### Freshness Model

Reverse-chronological listing; newest posts are first. Pagination via `/page/N/`. The category
page exposes dates as plain text ("May 5, 2026", "May 1, 2026"), not as `<time datetime="...">` 
attributes. Individual posts expose `datePublished` in JSON-LD.

**The 30-day window is expressible** — it just requires reading dates from individual post JSON-LD
rather than the category listing. With `--fetch-posts`, the enricher visits each post page and
can read the date from JSON-LD.

### Auth / Rate Limiting

None observed. Plain curl and the enricher UA both succeed.

### Smoke Test — Selector Regression Report

Ran `bazaar_build_enricher.py` against the Karnok category with `--days 30 --limit 20` (read-only,
no commit). **Two date-related regressions found; item extraction is intact.**

#### Regression A — Category page date extraction broken

- **What changed**: Category listing no longer uses `<time datetime="YYYY-MM-DD">` attributes.
  Dates are now plain text only: `"May 5, 2026"` in a bare `<div>`.
- **Enricher behavior**: `SimpleHTMLTextParser.dates` only captures `datetime=` attribute values
  from `<time>` tags. Returns empty list → all records are undated.
- **Consequence**: `filter_records()` with a `since` date keeps undated records unconditionally
  (`if record_date is not None and record_date < since: continue`). The 30-day window filter
  is silently inoperative — posts from any date pass through.
- **Silent regression risk**: The cron will run without error, produce output, but the window
  filter does nothing. Stale posts from months ago accumulate in the artifact.
- **Fix**: Parse "Month D, YYYY" text-format dates from the category HTML in
  `extract_category_records()`, or alternatively rely exclusively on the per-post JSON-LD date
  (see Regression B fix).

#### Regression B — JSON-LD `datePublished` not extracted when items are absent

- **What changed**: Individual post JSON-LD `@graph` contains an `Article` node with
  `datePublished: "2026-05-05T09:13:45+00:00"` and `headline`, but no `articleBody`.
- **Enricher behavior**: `extract_json_ld_records()` creates a `BuildRecord` with title + date
  but empty `items`. In `enrich_post()`, the guard is `rich = next((row for row in
  json_ld_records if row.items), None)` — so `rich` is `None` and the date is never copied to
  the record. The fallback `parser.dates` is also empty (no `<time datetime>`). Record remains
  undated.
- **Fix**: In `enrich_post()`, copy the date from JSON-LD records regardless of whether items
  are present: `record.date = record.date or next((r.date for r in json_ld_records if r.date), None)`.

#### What is working correctly

- Category link collection: all build post links extracted (Anaconda, Frog Hollow, Trapvine,
  Self-Slow, Wild Boar, Max Health Burn, etc.)
- Tag inference from post titles ("Anaconda Build", "Self-Slow Build") — correct
- Cross-hero filtering: Jules and Stelle navigation links correctly excluded
- **Item text extraction from individual post pages: working** — with `--fetch-posts`, items
  from the "Items:" section are matched against `card_cache_names.txt` (415 known items)
- Item extraction was validated against anaconda-karnok-10-5-build-22704: extracted
  Anaconda, Ancient Locket, Hunting Hawk, Karst

### Sample Location

- `bazaar-builds/research/samples/bazaar_builds_net/karnok_smoke_test.json` — enricher
  output (no `--fetch-posts`): 17 records, correct links, no dates, no items
- `bazaar-builds/research/samples/bazaar_builds_net/karnok_smoke_test_with_posts.json` — enricher
  output with `--fetch-posts --limit 5`: items extracted, dates still missing (Regression B)
- `bazaar-builds/research/samples/bazaar_builds_net/anaconda-karnok-post-sample.json` — single
  post extraction: date from JSON-LD, item list, confirms regression root causes

### Recommended Ingestion Approach

**HTTP client only** (existing enricher, two bug fixes required before cron use):

1. Fix Regression A: add "Month D, YYYY" date parsing to the category link extractor
2. Fix Regression B: propagate JSON-LD `datePublished` to the record even when no items found
3. Run with `--fetch-posts` (required — items only come from individual post pages)
4. The 30-day window then works correctly via per-post `datePublished` dates

Both fixes are small, localized changes to `bazaar_build_enricher.py`. Neither requires selector
changes — the content structure is otherwise intact.

---

## Cross-Source Summary for Subtask 1

| Dimension | bazaardb.gg/run/meta | mobalytics /guides/meta-builds | bazaar-builds.net |
|---|---|---|---|
| HTTP shape | SSR HTML (Next.js App Router) | PRELOADED_STATE JSON blob in HTML | Plain WordPress HTML |
| Fetch method | Headless browser (Cloudflare blocks curl) | Plain curl + browser UA | Plain curl |
| Auth required | No (post-challenge) | No | No |
| Time window | Patch-scoped (no calendar control) | Editorial cadence (no window) | 30-day via post `datePublished` |
| 30-day pin possible | **No** — concept doesn't exist | **No** — editorial document | **Yes** — with enricher fix |
| Structured fragments | `img[alt]`, "N runs · X%" text | PRELOADED_STATE JSON (`cards[].name`) | "Items:" text list in post body |
| LLM needed for items | No (DOM extraction) | **No** — already structured (design wrong) | No (text match) |
| LLM needed for classification | Possibly (no carry/core/support signal) | Yes (description text → classification) | No (frequency-based, or LLM if desired) |
| Blocker for pipeline | Headless browser infra; no 30-day window | None (accessible, structured); /builds listing client-side only | Two date bugs in enricher |

### Design Decisions for Subtask 1 to Resolve

1. **bazaardb window model**: patch-window or calendar-window? If patch-window, how do "4
   consecutive windows" for removes work when patch cadence is irregular?
2. **Mobalytics LLM parse pass**: revise §9 step 2 — item extraction is deterministic from
   JSON; LLM is only needed for carry/core/support classification using description text.
3. **bazaardb headless browser**: confirm GitHub Actions can run Playwright/Puppeteer before
   designing the scraper; if not, bazaardb may need to be deferred to a later subtask.
4. **bazaar-builds.net fixes**: two small `bazaar_build_enricher.py` bugs to fix before the
   cron runs unattended (Regression A + B above).
5. **Mobalytics /builds full listing**: decide whether to ingest only the 5 SSR-visible build
   articles, probe for the GraphQL endpoint in a browser session, or skip /builds in favor of
   /guides/meta-builds only (which has broader coverage: 21 builds vs. 5 visible).
