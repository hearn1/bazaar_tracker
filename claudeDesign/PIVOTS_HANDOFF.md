# Bazaar Tracker Overlay — Direction B Implementation Handoff

**Decision:** Adopt **Direction B (Build-first, two-zone)** for the overlay, with **Variant D (Hot list + chips)** as the canonical Pivots pane.

This document covers concrete changes across **all four states**: Coach · Review · Run · Idle.

---

## 1. Overall structure

The overlay window keeps a single top-level **TabBar** with three tabs: **Coach · Review · Run**. The **Idle** state replaces the entire body when no run is active (no tabs shown).

```
┌──────────────────────────┐
│ Header (status pill)     │  ← always visible
├──────────────────────────┤
│ Active Build hero strip  │  ← Coach tab only, pinned below header
├──────────────────────────┤
│ TabBar: Coach│Review│Run │  ← only when not Idle
├──────────────────────────┤
│ Scrollable body          │
│  · Coach  → checklist + sub-tabs (pivots/find/coach/notes)
│  · Review → tallies + decision log
│  · Run    → context, phase, hero, pivot signals
│  · Idle   → "waiting" + last-run summary
└──────────────────────────┘
```

### Header changes

- **Remove** the PvP / Tier strip from the header. It's run-context, not always-on context.
- Keep only the live/idle status indicator and any global controls (settings, etc.).
- The PvP / Tier values now live in the Run tab's "Current snapshot" card.

---

## 2. Coach tab

### 2a. Active Build hero strip (pinned)

A compact, always-visible strip below the header (only shown in Coach tab). It is **outside** the scrollable body so it never scrolls away.

```jsx
function ActiveBuildHero({ active }) {
  return (
    <div className="active-build-hero">
      <div className="active-build-hero__main">
        <div className="active-build-hero__eyebrow">ON BUILD</div>
        <div className="active-build-hero__title-row">
          <h2>{active.name}</h2>
          <span className="active-build-hero__pct">
            {Math.round(active.confidence * 100)}%
          </span>
        </div>
        <ConfidenceBar value={active.confidence} height={4} />
      </div>
      <Pill tone={active.is_manual ? 'purple' : 'blue'}>
        {active.is_manual ? 'Manual' : 'Auto'}
      </Pill>
    </div>
  );
}
```

```css
.active-build-hero {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  background: linear-gradient(180deg, rgba(230,169,32,0.12), rgba(230,169,32,0.02));
  border-bottom: 1px solid var(--amber-border);
  flex-shrink: 0;
}
.active-build-hero h2 {
  font: 800 26px/1 var(--font-display);
  color: #fff;
  margin: 0;
}
.active-build-hero__pct {
  font: 500 11px/1 var(--font-mono);
  color: #fff7d9;
}
.active-build-hero__eyebrow {
  font: 700 9px/1 var(--font-mono);
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--amber);
  margin-bottom: 4px;
}
.active-build-hero__title-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}
```

**Behavioral changes:**
- The active build's full text/summary is **removed** from this hero — moved to the "Notes" sub-tab.
- The toggle to expand notes is no longer here.

### 2b. Item Checklist (the main scroll content)

The checklist now owns the top of the Coach scroll area. **Core** and **Carry** slots are always expanded; **Support** is collapsed by default to a chip-line of names.

```jsx
function ItemChecklist({ checklist }) {
  const [supportOpen, setSupportOpen] = useState(false);

  return (
    <Card>
      <header className="checklist-header">
        <h3>Item checklist</h3>
        <span className="checklist-eyebrow">What to look for</span>
      </header>

      {/* Core + Carry — always expanded, not collapsible */}
      {['core', 'carry'].map(slot => (
        <ChecklistSection
          key={slot}
          slot={slot}
          items={checklist[slot]}
          collapsible={false}
        />
      ))}

      {/* Support — collapsed to chip-line by default */}
      <SupportSection
        items={checklist.support}
        open={supportOpen}
        onToggle={() => setSupportOpen(o => !o)}
      />
    </Card>
  );
}

function SupportSection({ items, open, onToggle }) {
  const owned = items.filter(i => i.owned).length;
  return (
    <div>
      <SlotHeader
        slotKey="support"
        count={owned}
        total={items.length}
        open={open}
        onToggle={onToggle}
      />
      {!open ? (
        <div className="support-chips">
          {items.slice(0, 5).map(i => (
            <span key={i.name} className="support-chip">{i.name}</span>
          ))}
          {items.length > 5 && (
            <span className="support-chip-more">+{items.length - 5}</span>
          )}
        </div>
      ) : (
        <div className="support-rows">
          {items.map(it => (
            <ItemRow key={it.name} item={it} slotKey="support" dense showSlot={false} />
          ))}
        </div>
      )}
    </div>
  );
}
```

```css
.support-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
  padding-left: 18px;
}
.support-chip {
  font: 500 11px/1 var(--font-ui);
  color: var(--text-dim);
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(167,139,250,0.06);
  border: 1px solid rgba(167,139,250,0.18);
}
.support-chip-more {
  font: 500 10px/1 var(--font-mono);
  color: var(--text-faint);
  padding: 2px 4px;
}
```

**Removed:** The standalone "Build Override" section is gone — replaced by the Pivots sub-tab below.

### 2c. Sub-tab strip (Pivots / Find card / Coach / Notes)

Below the checklist, a 4-tab strip for everything that isn't items. Default tab is **Pivots**.

```jsx
function CoachSubTabs() {
  const [sub, setSub] = useState('pivots');
  const tabs = [
    { id: 'pivots',  label: 'Pivots' },
    { id: 'search',  label: 'Find card' },
    { id: 'prompts', label: 'Coach' },
    { id: 'notes',   label: 'Notes' },
  ];

  return (
    <Card>
      <nav className="subtab-strip">
        {tabs.map(t => (
          <button
            key={t.id}
            type="button"
            className={`subtab ${sub === t.id ? 'is-active' : ''}`}
            onClick={() => setSub(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {sub === 'pivots'  && <PivotsPane archetypes={archetypes} />}
      {sub === 'search'  && <FindCardPane />}
      {sub === 'prompts' && <CoachPromptsPane prompts={prompts} />}
      {sub === 'notes'   && <BuildNotesPane summary={active.summary} />}
    </Card>
  );
}
```

```css
.subtab-strip {
  display: flex;
  gap: 4px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 10px;
}
.subtab {
  padding: 6px 10px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-dim);
  font: 700 10px/1 var(--font-mono);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.subtab.is-active {
  background: var(--bg-raised);
  color: var(--amber);
  border-bottom-color: var(--amber);
}
```

### 2d. Pivots pane (Variant D — Hot list + chips)

The default sub-tab. Replaces the old Build Override grid.

#### Data model

Each archetype must carry:

```ts
type Archetype = {
  name: string;
  score: number;     // 0..1, fit confidence
  active?: boolean;  // current build, excluded from pivots list
};
```

#### Tier helper

```js
const PIVOT_TIERS = {
  strong:    { min: 0.30, label: 'Strong pivot', color: 'var(--amber)' },
  possible:  { min: 0.05, label: 'Possible',     color: 'var(--blue)'  },
  longshot:  { min: 0,    label: 'Long shot',    color: 'var(--text-faint)' },
};

function bucketize(archetypes) {
  const others = archetypes.filter(a => !a.active);
  return {
    strong:   others.filter(a => a.score >= PIVOT_TIERS.strong.min),
    possible: others.filter(a => a.score >= PIVOT_TIERS.possible.min && a.score < PIVOT_TIERS.strong.min),
    longshot: others.filter(a => a.score < PIVOT_TIERS.possible.min),
  };
}
```

#### Component

```jsx
function PivotsPane({ archetypes, onPick }) {
  const { strong, possible, longshot } = bucketize(archetypes);
  const hot = [...strong, ...possible].sort((a, b) => b.score - a.score);
  const [chipOpen, setChipOpen] = useState(true);

  if (hot.length === 0 && longshot.length === 0) {
    return <div className="pivots-empty">Current build is committed.</div>;
  }

  return (
    <div className="pivots">
      <div className="pivots-hot">
        {hot.map(a => <PivotHotRow key={a.name} archetype={a} onPick={onPick} />)}
      </div>

      {longshot.length > 0 && (
        <div className="pivots-cold">
          <button
            type="button"
            className="pivots-cold-toggle"
            onClick={() => setChipOpen(o => !o)}
          >
            <Caret open={chipOpen} />
            <span>{longshot.length} other builds · 0%</span>
          </button>
          {chipOpen && (
            <div className="pivots-cold-chips">
              {longshot.map(a => (
                <button
                  key={a.name}
                  type="button"
                  className="pivot-chip"
                  onClick={() => onPick?.(a)}
                >
                  {a.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PivotHotRow({ archetype: a, onPick }) {
  const tier = a.score >= PIVOT_TIERS.strong.min ? PIVOT_TIERS.strong : PIVOT_TIERS.possible;
  const fillPct = Math.round(a.score * 100);
  return (
    <button
      type="button"
      className="pivot-hot-row"
      style={{ '--tier-color': tier.color, '--fill-pct': `${fillPct}%` }}
      onClick={() => onPick?.(a)}
    >
      <div>
        <div className="pivot-hot-row__name">{a.name}</div>
        <div className="pivot-hot-row__tier">{tier.label}</div>
      </div>
      <div className="pivot-hot-row__score">{fillPct}%</div>
    </button>
  );
}
```

```css
.pivots-hot { display: flex; flex-direction: column; gap: 5px; }

.pivot-hot-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px;
  align-items: center;
  padding: 8px 12px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--tier-color) 24%, transparent);
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--tier-color) 15%, transparent) 0%,
    color-mix(in srgb, var(--tier-color) 15%, transparent) var(--fill-pct),
    color-mix(in srgb, var(--tier-color)  3%, transparent) var(--fill-pct),
    color-mix(in srgb, var(--tier-color)  3%, transparent) 100%
  );
  color: #fff;
  font: 600 12px/1 var(--font-ui);
  cursor: pointer;
  text-align: left;
  width: 100%;
  overflow: hidden;
}
.pivot-hot-row__name { margin-bottom: 2px; }
.pivot-hot-row__tier {
  font: 700 9px/1 var(--font-mono);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--tier-color);
}
.pivot-hot-row__score {
  font: 700 14px/1 var(--font-mono);
  color: var(--tier-color);
}

.pivots-cold { margin-top: 12px; }
.pivots-cold-toggle {
  background: transparent;
  border: none;
  padding: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  color: var(--text-faint);
  font: 700 10px/1 var(--font-mono);
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.pivots-cold-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.pivot-chip {
  font: 500 10px/1 var(--font-ui);
  color: var(--text-faint);
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid var(--border);
  cursor: pointer;
}
.pivot-chip:hover { color: #fff; border-color: rgba(255, 255, 255, 0.15); }
```

**Tier-threshold tuning:** start with 30% / 5%. Once you observe real distributions:
- If too many builds land in `strong`, raise `strong.min` to 0.40.
- If `possible` is consistently empty, lower `possible.min` to 0.03.
- Chip cloud should typically have 10–15 entries.

### 2e. Find card (sub-tab)

Drop-in replacement for the current Search section.

```jsx
function FindCardPane() {
  const [q, setQ] = useState('');
  return (
    <div>
      <div className="find-card-input">
        <span className="find-card-input__icon">⌕</span>
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="See it in shop, type the name…"
        />
      </div>
      <p className="find-card-hint">
        We'll show every <em>{heroName}</em> build it fits, and whether it's
        <span className="role-core"> core</span> /
        <span className="role-carry"> carry</span> /
        <span className="role-support"> support</span>.
      </p>
    </div>
  );
}
```

### 2f. Coach prompts (sub-tab)

Same as today but **no longer collapsed** — when the user is on the Coach sub-tab, prompts are the content. List them simply.

```jsx
function CoachPromptsPane({ prompts }) {
  if (!prompts.length) {
    return <div className="prompts-empty">No active prompts.</div>;
  }
  return (
    <div className="prompts-list">
      {prompts.map((p, i) => (
        <div key={i} className={`prompt prompt--${p.tone}`}>
          <div className="prompt__kind">{p.kind}</div>
          <div className="prompt__text">{p.text}</div>
        </div>
      ))}
    </div>
  );
}
```

### 2g. Notes (sub-tab) — new

Holds the full active-build summary text that used to live in the Active Build card.

```jsx
function BuildNotesPane({ summary }) {
  return <div className="build-notes">{summary}</div>;
}
```

```css
.build-notes {
  font: 500 12px/1.5 var(--font-ui);
  color: var(--text-dim);
}
```

---

## 3. Review tab

The Review tab gets **filter chips** for the four verdict types so the learning surface is one tap away. The decision log itself stays the same shape but rows are denser.

```jsx
function ReviewTab() {
  const [filter, setFilter] = useState('all');
  const t = decisionTallies; // { good, situational, suboptimal, missed }
  const filters = [
    { id: 'all',         label: 'All',         count: t.good + t.situational + t.suboptimal + t.missed },
    { id: 'good',        label: 'Good',        count: t.good,        color: 'var(--green)' },
    { id: 'suboptimal',  label: 'Suboptimal',  count: t.suboptimal,  color: 'var(--red)'   },
    { id: 'missed',      label: 'Missed',      count: t.missed,      color: 'var(--red)'   },
  ];
  const visible = filter === 'all'
    ? decisionLog
    : decisionLog.filter(d => d.verdict === filter);

  return (
    <>
      <Card>
        <Eyebrow>Run review</Eyebrow>
        <h3>Decision log</h3>
        <p className="review-blurb">Live · Build-defining choices only.</p>
        <div className="review-filters">
          {filters.map(f => (
            <button
              key={f.id}
              type="button"
              className={`review-filter ${filter === f.id ? 'is-active' : ''}`}
              onClick={() => setFilter(f.id)}
            >
              <div className="review-filter__label">{f.label}</div>
              <div
                className="review-filter__count"
                style={{ color: f.color || (filter === f.id ? 'var(--amber)' : '#fff') }}
              >
                {f.count}
              </div>
            </button>
          ))}
        </div>
      </Card>

      <DecisionLogList entries={visible} />
    </>
  );
}
```

```css
.review-filters {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
  margin-bottom: 10px;
}
.review-filter {
  padding: 8px 4px;
  border-radius: 8px;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  cursor: pointer;
  text-align: center;
}
.review-filter.is-active {
  background: var(--amber-soft);
  border-color: var(--amber-border);
}
.review-filter__label {
  font: 700 8px/1 var(--font-mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-faint);
}
.review-filter__count {
  font: 700 16px/1 var(--font-mono);
  margin-top: 2px;
  color: #fff;
}
```

**Decision-log row density:** clamp the detail text to **2 lines**, drop the thumbnail to **32×32**, and keep the verdict pill compact.

```css
.decision-row {
  display: grid;
  grid-template-columns: auto 32px 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
}
.decision-row__detail {
  font: 500 10px/1.35 var(--font-ui);
  color: var(--text-dim);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
```

---

## 4. Run tab

The Run tab **absorbs the PvP / Tier strip** that used to live in the header. It also adds Phase guidance, Hero reminder, and Pivot signals as separate cards.

```jsx
function RunTab() {
  return (
    <>
      <Card>
        <Eyebrow>Run context</Eyebrow>
        <h3>Current snapshot</h3>
        <div className="run-stats">
          {[
            { label: 'Tier',      value: state.tier      },
            { label: 'PvP',       value: state.pvp       },
            { label: 'PvE',       value: state.pve       },
            { label: 'Decisions', value: state.decisions },
          ].map(s => (
            <div key={s.label} className="run-stat">
              <div className="run-stat__label">{s.label}</div>
              <div className="run-stat__value">{s.value}</div>
            </div>
          ))}
        </div>
        <p className="run-stats__note">{state.threshold_note}</p>
      </Card>

      <Card>
        <Eyebrow>Phase guidance</Eyebrow>
        <h3>{state.phase}</h3>
        <p>{state.phaseGuidance}</p>
      </Card>

      <Card>
        <Eyebrow>Hero reminder</Eyebrow>
        <h3>{state.heroName} fundamentals</h3>
        <p>{state.heroBlurb}</p>
      </Card>

      <Card>
        <Eyebrow>Pivot signals</Eyebrow>
        <h3>Watch-outs</h3>
        {state.pivotSignals.map((s, i) => (
          <div key={i} className="pivot-signal">{s}</div>
        ))}
      </Card>
    </>
  );
}
```

```css
.run-stats {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px;
}
.run-stat {
  padding: 10px 9px;
  border-radius: 10px;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  text-align: center;
}
.run-stat__label {
  font: 700 9px/1 var(--font-mono);
  color: var(--text-faint);
  text-transform: uppercase;
}
.run-stat__value {
  font: 700 14px/1 var(--font-mono);
  color: #fff;
  margin-top: 4px;
}
.pivot-signal {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--blue-soft);
  border-left: 2px solid var(--blue);
  font: 500 12px/1.4 var(--font-ui);
  color: var(--text);
}
```

---

## 5. Idle state

When no run is active, the entire body is replaced with the Idle layout. **No tabs are shown.** The header switches to an "Idle" status pill.

```jsx
function IdleBody({ lastRun }) {
  return (
    <>
      <Card accent="var(--blue)">
        <Eyebrow color="var(--blue)">Idle</Eyebrow>
        <h2>Waiting for run to start…</h2>
        <p>The overlay will switch back to live coaching as soon as the next run records its first decision.</p>
      </Card>

      {lastRun && (
        <Card>
          <Eyebrow>Last completed run</Eyebrow>
          <header className="idle-last-run-header">
            <h3>{lastRun.outcome}</h3>
            <Pill tone={lastRun.outcome === 'Victory' ? 'green' : 'red'}>{lastRun.outcome}</Pill>
          </header>
          <div className="idle-last-run-stats">
            {[
              { label: 'Tier', value: lastRun.tier },
              { label: 'PvP',  value: lastRun.pvp  },
              { label: 'PvE',  value: lastRun.pve  },
            ].map(s => (
              <div key={s.label} className="run-stat">
                <div className="run-stat__label">{s.label}</div>
                <div className="run-stat__value">{s.value}</div>
              </div>
            ))}
          </div>
          <button type="button" className="idle-review-btn">Review last run</button>
        </Card>
      )}
    </>
  );
}
```

```css
.idle-last-run-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-bottom: 8px;
}
.idle-review-btn {
  width: 100%;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--amber-border);
  background: var(--amber-soft);
  color: var(--amber);
  font: 700 11px/1 var(--font-mono);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  cursor: pointer;
}
```

---

## 6. Top-level frame

```jsx
function Overlay() {
  const [tab, setTab] = useState('coach');
  const isIdle = state.status === 'idle';

  return (
    <div className="overlay-frame">
      <Header status={isIdle ? 'idle' : 'live'} />

      {tab === 'coach' && !isIdle && <ActiveBuildHero active={state.active} />}

      {!isIdle && (
        <TabBar
          tabs={[
            { id: 'coach',  label: 'Coach' },
            { id: 'review', label: 'Review' },
            { id: 'run',    label: 'Run' },
          ]}
          active={tab}
          onChange={setTab}
        />
      )}

      <div className="overlay-body">
        {isIdle           && <IdleBody lastRun={state.lastRun} />}
        {!isIdle && tab === 'coach'  && <CoachTab />}
        {!isIdle && tab === 'review' && <ReviewTab />}
        {!isIdle && tab === 'run'    && <RunTab />}
      </div>
    </div>
  );
}

function CoachTab() {
  return (
    <>
      <ItemChecklist checklist={state.checklist} />
      <CoachSubTabs />
    </>
  );
}
```

---

## 7. Summary of removed / moved items

| Item | Before | After |
|---|---|---|
| PvP / Tier strip | Header (always visible) | Run tab → Current snapshot card |
| Active build summary text | Inline in Active Build card | Coach → Notes sub-tab |
| Build Override grid | Coach tab section | Replaced by Pivots sub-tab (Variant D) |
| Adjacent builds list | Coach tab section | Merged into Pivots sub-tab |
| Coach prompts | Coach tab section (default open) | Coach → Coach sub-tab |
| Find card / search | Coach tab section | Coach → Find card sub-tab |
| Support checklist | Default expanded | Default collapsed (chip line) |
| Decision log filter chips | None | Review tab → 4-button filter row |

---

## 8. Reference

Working prototype: `Bazaar Tracker Redesign.html`
- **Direction B** section, all four tab states (Coach / Review / Run / Idle)
- **"Pivots — visualizing distance"** section, artboard **D · Hot list + chips**
- Source files: `tracker-direction-b.jsx`, `tracker-pivots.jsx`, `tracker-shared.jsx`
