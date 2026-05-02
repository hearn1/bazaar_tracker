// Shared primitives + mock data for the Bazaar Tracker redesign.
// Three directions consume these. Keep visual language consistent so
// differences between directions read as STRUCTURAL, not stylistic.

const { useState, useMemo, useEffect, useRef } = React;

// ─── Design tokens ─────────────────────────────────────────────────
const T = {
  bg: '#0a0c12',
  bgRaised: 'rgba(255,255,255,0.025)',
  bgCard: 'rgba(255,255,255,0.04)',
  border: 'rgba(255,255,255,0.06)',
  borderStrong: 'rgba(255,255,255,0.10)',
  text: '#e5ebf5',
  textDim: 'rgba(255,255,255,0.55)',
  textFaint: 'rgba(255,255,255,0.35)',

  // Brand / role colors
  amber: '#e6a920',
  amberSoft: 'rgba(230,169,32,0.13)',
  amberBorder: 'rgba(230,169,32,0.32)',
  blue: '#4d9cf5',
  blueSoft: 'rgba(77,156,245,0.13)',
  blueBorder: 'rgba(77,156,245,0.32)',
  purple: '#a78bfa',
  purpleSoft: 'rgba(167,139,250,0.13)',
  purpleBorder: 'rgba(167,139,250,0.32)',
  green: '#3ecf8e',
  greenSoft: 'rgba(62,207,142,0.12)',
  greenBorder: 'rgba(62,207,142,0.32)',
  red: '#f04444',
  redSoft: 'rgba(240,68,68,0.13)',
  redBorder: 'rgba(240,68,68,0.32)',

  fontUI: 'DM Sans, system-ui, sans-serif',
  fontMono: 'IBM Plex Mono, ui-monospace, monospace',
  fontDisplay: 'Syne, system-ui, sans-serif',
};

const SLOT = {
  core:    { label: 'Core',    color: T.amber,  blurb: 'Required engine' },
  carry:   { label: 'Carry',   color: T.blue,   blurb: 'Damage plan' },
  support: { label: 'Support', color: T.purple, blurb: 'Glue and utility' },
};

const TIER_COLORS = {
  S: '#ffd875', A: '#bfeaff', B: '#c4d2ff', C: '#a8aec3', D: '#9b8585', F: '#ff8585',
};

// ─── Mock state ────────────────────────────────────────────────────
const STATE = {
  run_id: 14,
  hero: 'Karnok',
  pvp: '9-4',
  pve: '14-2',
  tier: 'Silver',
  day: 7,
  decisions: 232,
  threshold_note: 'Above the ranked threshold.',
  active: {
    name: 'Burn',
    confidence: 0.87,
    is_manual: false,
    summary: 'You can get creative with tag boards here, but pivot if Burn Scar or Hunter\'s Sled do not show up by Day 7.',
  },
  // adjacent builds — sorted by score. Mix of strong / possible / long-shot / dead.
  archetypes: [
    { name: 'Burn',             score: 0.87, active: true,
      shares: 5, trigger: null,           trend: 0 },
    { name: 'Burn (Early)',     score: 0.62,
      shares: 4, trigger: null,           trend: +0.08 },
    { name: 'Sled - Weapon',    score: 0.41,
      shares: 3, trigger: 'Crook',        trend: +0.04 },
    { name: 'Heal Weapons',     score: 0.28,
      shares: 2, trigger: 'Bandage',      trend: -0.02 },
    { name: 'Wide Weapons',     score: 0.18,
      shares: 2, trigger: 'lvl 7',        trend: 0 },
    { name: 'Axe',              score: 0.12,
      shares: 1, trigger: 'Greataxe',     trend: -0.05 },
    { name: 'Sustain (Early)',  score: 0.08,
      shares: 1, trigger: 'Bandage',      trend: 0 },
    { name: 'Spear + Friends',  score: 0.04,
      shares: 1, trigger: 'Spear',        trend: 0 },
    { name: 'Max HP - Weapons', score: 0.0 },
    { name: 'Max HP - Sigil',   score: 0.0 },
    { name: 'Slow (Early)',     score: 0.0 },
    { name: 'Slow - Ammo',      score: 0.0 },
    { name: 'Slow - Non-ammo',  score: 0.0 },
    { name: 'Slow - Poison',    score: 0.0 },
    { name: 'Sustain',          score: 0.0 },
    { name: 'Giant Sling',      score: 0.0 },
    { name: 'Tree Club',        score: 0.0 },
    { name: 'Anaconda',         score: 0.0 },
    { name: 'Red Friends',      score: 0.0 },
  ],
  checklist: {
    core:    [{ name: "Hunter's Sled", tier: 'B', owned: true }],
    carry:   [
      { name: 'Burn Scar', tier: 'B', owned: true },
      { name: 'Tinderbox', tier: 'B', owned: true },
    ],
    support: [
      { name: 'Caustic Solvent', tier: 'C', owned: false },
      { name: 'Karst',           tier: 'A', owned: false },
      { name: 'Dryad',           tier: 'B', owned: false },
      { name: 'Waterskin',       tier: 'A', owned: false },
      { name: 'Fairies',         tier: 'B', owned: false },
      { name: 'Stretch Pants',   tier: 'A', owned: false },
      { name: 'Flying Squirrel', tier: 'S', owned: false },
      { name: 'Chains',          tier: 'A', owned: false },
      { name: 'Hunting Hawk',    tier: 'A', owned: false },
      { name: 'Ancient Locket',  tier: 'C', owned: false },
    ],
  },
  prompts: [
    { kind: 'Tempo reminder', text: 'Unspent gold = missed tempo. Spend if a relevant item is available.', tone: 'amber' },
    { kind: 'Pivot signal',   text: 'Board losing fights consistently despite having items. Consider a transition.', tone: 'blue' },
  ],
  // Decision log (Review tab) — newest first
  decisionLog: [
    { seq: 225, name: 'Ancient Locket', verdict: 'missed',     detail: 'Universal utility — strong pickup regardless of archetype.' },
    { seq: 220, name: 'Fairies',        verdict: 'missed',     detail: 'Would add support to Burn.' },
    { seq: 220, name: 'Trail Markers',  verdict: 'missed',     detail: "Skipped after 1 reroll(s) — missed: Support for Burn [Fairies]" },
    { seq: 217, name: 'Fairies',        verdict: 'missed',     detail: 'Would add support to Burn.' },
    { seq: 217, name: 'Night Vision',   verdict: 'missed',     detail: "Skipped after 1 reroll(s) — missed: Support for Burn [Fairies, Torch]" },
    { seq: 213, name: 'Burn Scar',      verdict: 'good',       detail: 'Carry item for committed build (Burn).' },
    { seq: 208, name: "Hunter's Axe",   verdict: 'unscored',   detail: 'Not in Karnok catalog — no score assigned.' },
    { seq: 204, name: 'Signal Fire',    verdict: 'suboptimal', detail: "Doesn't fit committed build (Burn) and is B-tier. Likely wasted pick." },
    { seq: 203, name: 'Flying Squirrel',verdict: 'good',       detail: 'Support item for committed build (Burn).' },
    { seq: 202, name: "Hunter's Journal", verdict: 'suboptimal', detail: "Not in Burn item list. Fits [Max HP - Weapons] instead. Consider pivoting or selling this." },
    { seq: 200, name: 'Karst',          verdict: 'missed',     detail: 'Would add support to Burn.' },
    { seq: 200, name: "Hunter's Sled",  verdict: 'good',       detail: 'Commits Burn (1/1 core + Burn Scar). Fits 2 late archetypes (Burn, Sled - Weapon). Strong at this point in the run.' },
    { seq: 197, name: 'Firefly Lantern',verdict: 'situational',detail: 'Could go in tempo/Burn lines — judgement call.' },
  ],
  decisionTallies: { good: 15, situational: 11, suboptimal: 16, missed: 35 },
};

const VERDICT_STYLES = {
  good:        { label: 'Good',        bg: 'rgba(62,207,142,0.13)', fg: '#3ecf8e', bd: 'rgba(62,207,142,0.32)' },
  situational: { label: 'Situational', bg: 'rgba(230,169,32,0.13)', fg: '#e6a920', bd: 'rgba(230,169,32,0.28)' },
  suboptimal:  { label: 'Suboptimal',  bg: 'rgba(240,68,68,0.10)',  fg: '#ff8585', bd: 'rgba(240,68,68,0.28)' },
  missed:      { label: 'Missed',      bg: 'rgba(240,68,68,0.10)',  fg: '#ff8585', bd: 'rgba(240,68,68,0.28)' },
  unscored:    { label: 'Unscored',    bg: 'rgba(255,255,255,0.04)',fg: 'rgba(255,255,255,0.45)', bd: 'rgba(255,255,255,0.10)' },
};

// ─── Atoms ─────────────────────────────────────────────────────────
function Mono({ children, size = 10, color = T.textFaint, weight = 700, spacing = 0.14, style = {} }) {
  return (
    <span style={{
      fontFamily: T.fontMono,
      fontSize: size,
      fontWeight: weight,
      letterSpacing: `${spacing}em`,
      textTransform: 'uppercase',
      color,
      ...style,
    }}>{children}</span>
  );
}

function Eyebrow({ children, color = T.textFaint }) {
  return (
    <div style={{
      fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
      letterSpacing: '0.16em', textTransform: 'uppercase',
      color, marginBottom: 5,
    }}>{children}</div>
  );
}

function ItemThumb({ name, size = 44, owned = false }) {
  const seed = name.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  const hueA = (seed * 7) % 360;
  const hueB = (hueA + 35) % 360;
  const initials = name.split(/[\s—-]/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
  return (
    <div style={{
      width: size, height: size,
      borderRadius: 8,
      background: `linear-gradient(135deg, hsl(${hueA} 35% 28%), hsl(${hueB} 30% 18%))`,
      border: owned ? `1.5px solid ${T.greenBorder}` : `1px solid ${T.borderStrong}`,
      display: 'grid', placeItems: 'center',
      flexShrink: 0,
      position: 'relative',
      fontFamily: T.fontMono,
      fontSize: size * 0.32, fontWeight: 700,
      color: 'rgba(255,255,255,0.78)',
    }}>
      {initials}
      <div style={{
        position: 'absolute', inset: 0, borderRadius: 7, pointerEvents: 'none',
        background: 'radial-gradient(circle at 30% 20%, rgba(255,255,255,0.10), transparent 55%)',
      }} />
    </div>
  );
}

function TierBadge({ tier, size = 'md' }) {
  const dim = size === 'sm' ? { w: 20, h: 18, fs: 9 } : { w: 24, h: 22, fs: 10 };
  return (
    <div style={{
      minWidth: dim.w, height: dim.h,
      padding: '0 5px',
      display: 'inline-grid', placeItems: 'center',
      borderRadius: 5,
      background: 'rgba(255,255,255,0.04)',
      color: TIER_COLORS[tier] || '#a8aec3',
      border: '1px solid rgba(255,255,255,0.10)',
      fontFamily: T.fontMono,
      fontSize: dim.fs, fontWeight: 700, letterSpacing: '0.04em',
    }}>{tier}</div>
  );
}

function StatusPip({ owned, size = 22 }) {
  if (owned) {
    return (
      <div style={{
        width: size, height: size, borderRadius: 999,
        background: T.greenSoft,
        border: `1px solid ${T.greenBorder}`,
        color: T.green, fontSize: size * 0.55, fontWeight: 700,
        display: 'grid', placeItems: 'center', flexShrink: 0,
      }}>✓</div>
    );
  }
  return (
    <div style={{
      width: size, height: size, borderRadius: 999,
      border: '1px dashed rgba(255,255,255,0.18)',
      color: T.textFaint, fontSize: size * 0.5,
      display: 'grid', placeItems: 'center', flexShrink: 0,
    }}>·</div>
  );
}

function Pill({ children, tone = 'neutral', strong = false, style = {} }) {
  const tones = {
    neutral: { bg: 'rgba(255,255,255,0.04)', fg: T.textDim, bd: T.border },
    amber:   { bg: T.amberSoft, fg: '#ffe9b0', bd: T.amberBorder },
    blue:    { bg: T.blueSoft,  fg: '#bdd7ff', bd: T.blueBorder },
    purple:  { bg: T.purpleSoft,fg: '#c4b1ff', bd: T.purpleBorder },
    green:   { bg: T.greenSoft, fg: T.green,   bd: T.greenBorder },
    red:     { bg: T.redSoft,   fg: '#ff8585', bd: T.redBorder },
  };
  const c = tones[tone] || tones.neutral;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 8px',
      borderRadius: 999,
      background: c.bg, color: c.fg,
      border: `1px solid ${c.bd}`,
      fontFamily: T.fontMono,
      fontSize: 9, fontWeight: 700,
      letterSpacing: '0.1em', textTransform: 'uppercase',
      whiteSpace: 'nowrap',
      ...style,
    }}>{children}</span>
  );
}

function ConfidenceBar({ value, height = 6 }) {
  return (
    <div style={{
      height, borderRadius: 999,
      background: 'rgba(255,255,255,0.06)', overflow: 'hidden',
    }}>
      <div style={{
        height: '100%', width: `${Math.max(2, value * 100)}%`,
        background: 'linear-gradient(90deg, #e6a920, #ffd875)',
        borderRadius: 999,
        boxShadow: '0 0 8px rgba(230,169,32,0.35)',
      }} />
    </div>
  );
}

// Caret/chevron for collapsible headers
function Caret({ open, size = 10 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 10 10" style={{
      transform: `rotate(${open ? 90 : 0}deg)`,
      transition: 'transform 0.15s',
      flexShrink: 0,
    }}>
      <path d="M3 1.5L7 5L3 8.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Section card
function Card({ children, accent, style = {}, padding = 14 }) {
  return (
    <section style={{
      padding,
      borderRadius: 14,
      background: accent
        ? `linear-gradient(180deg, ${accent}1A, ${accent}05), ${T.bgRaised}`
        : T.bgRaised,
      border: `1px solid ${accent ? `${accent}3D` : T.border}`,
      ...style,
    }}>{children}</section>
  );
}

// Tab bar
function TabBar({ tabs, active, onChange }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: `repeat(${tabs.length}, 1fr)`,
      gap: 6,
      padding: '4px',
      borderRadius: 12,
      background: 'rgba(0,0,0,0.32)',
      border: `1px solid ${T.border}`,
    }}>
      {tabs.map(t => {
        const isActive = active === t.id;
        return (
          <button
            key={t.id}
            onClick={() => onChange?.(t.id)}
            style={{
              padding: '8px 6px',
              borderRadius: 8,
              border: 'none',
              background: isActive ? T.amberSoft : 'transparent',
              color: isActive ? T.amber : T.textDim,
              fontFamily: T.fontMono,
              fontSize: 10, fontWeight: 700,
              letterSpacing: '0.14em', textTransform: 'uppercase',
              cursor: 'pointer',
              transition: 'all 0.12s',
              boxShadow: isActive ? `inset 0 0 0 1px ${T.amberBorder}` : 'none',
            }}
          >{t.label}</button>
        );
      })}
    </div>
  );
}

// Header — variants per direction differ in compactness
function Header({ status = 'live', state = STATE, compact = false, showStats = false }) {
  const live = status === 'live';
  const idle = status === 'idle';
  const defeat = status === 'defeat';
  return (
    <div style={{
      padding: compact ? '12px 14px 10px' : '14px 14px 12px',
      borderBottom: `1px solid ${T.amberBorder}`,
      background:
        'linear-gradient(180deg, rgba(230,169,32,0.10), rgba(230,169,32,0.02))',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <Eyebrow color={T.amber}>{idle ? 'Idle' : 'Live coach'}</Eyebrow>
          <h1 style={{
            fontFamily: T.fontDisplay,
            fontSize: compact ? 20 : 22, fontWeight: 800, lineHeight: 0.95,
            color: '#fff', margin: 0, letterSpacing: '0.005em',
          }}>Bazaar Tracker</h1>
          <div style={{
            marginTop: 4, fontSize: 11, color: T.textDim, fontFamily: T.fontUI,
          }}>{idle ? 'Waiting for first tracked decision · ' + state.hero : `Run #${state.run_id} · ${state.hero}`}</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <Pill tone={live ? 'green' : defeat ? 'red' : 'neutral'}>
            {live && (
              <span style={{
                width: 6, height: 6, borderRadius: 999,
                background: T.green,
                boxShadow: `0 0 6px ${T.green}`,
                animation: 'pulse 2s infinite',
              }} />
            )}
            {live ? 'Live' : defeat ? 'Defeat' : 'Idle'}
          </Pill>
          <button style={{
            width: 22, height: 22, borderRadius: 999,
            border: `1px solid ${T.borderStrong}`,
            background: 'rgba(255,255,255,0.04)',
            color: T.textFaint,
            fontSize: 13, lineHeight: 1, cursor: 'pointer',
            display: 'grid', placeItems: 'center',
          }}>×</button>
        </div>
      </div>
      {showStats && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginTop: 10 }}>
          {[
            { label: 'PVP', value: state.pvp },
            { label: 'Tier', value: state.tier },
          ].map(s => (
            <div key={s.label} style={{
              padding: '7px 9px',
              borderRadius: 10,
              background: T.bgRaised,
              border: `1px solid ${T.border}`,
            }}>
              <Mono size={9} color={T.textFaint}>{s.label}</Mono>
              <div style={{
                fontFamily: T.fontMono, fontSize: 13, fontWeight: 600, color: '#f7f9fc', marginTop: 2,
              }}>{s.value}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Outer window
function Frame({ children, width = 360, height = 760 }) {
  return (
    <div style={{
      width, height,
      borderRadius: 16,
      overflow: 'hidden',
      border: `1px solid ${T.amberBorder}`,
      background: `
        radial-gradient(circle at top right, rgba(77, 156, 245, 0.10), transparent 35%),
        radial-gradient(circle at top left, rgba(230, 169, 32, 0.08), transparent 28%),
        linear-gradient(180deg, rgba(15, 19, 30, 0.98), rgba(9, 12, 20, 0.96))
      `,
      boxShadow: '0 24px 50px rgba(0, 0, 0, 0.5)',
      display: 'flex', flexDirection: 'column',
      fontFamily: T.fontUI,
      color: T.text,
    }}>{children}</div>
  );
}

function Scroller({ children, padding = '12px 12px 16px' }) {
  return (
    <div style={{
      flex: 1,
      overflow: 'auto',
      padding,
      display: 'flex', flexDirection: 'column', gap: 10,
      scrollbarWidth: 'thin',
      scrollbarColor: 'rgba(230,169,32,0.2) transparent',
    }}>{children}</div>
  );
}

// Item row — slot-aware, dense
function ItemRow({ item, slotKey, dense = false, showSlot = true }) {
  const slot = SLOT[slotKey];
  const owned = item.owned;
  const thumbSize = dense ? 36 : 44;
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: `${thumbSize}px 1fr auto auto`,
      gap: 10,
      alignItems: 'center',
      padding: dense ? '6px 8px' : '8px 10px',
      borderRadius: 10,
      background: owned ? T.greenSoft : 'rgba(255,255,255,0.02)',
      border: owned ? `1px solid ${T.greenBorder}` : `1px solid ${T.border}`,
    }}>
      <ItemThumb name={item.name} size={thumbSize} owned={owned} />
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontSize: dense ? 12 : 13, fontWeight: 600,
          color: owned ? '#a8f0c8' : '#eef3fb',
          lineHeight: 1.2,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>{item.name}</div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 5, marginTop: 2,
        }}>
          {showSlot && (
            <span style={{
              fontFamily: T.fontMono, fontSize: 9, fontWeight: 700,
              letterSpacing: '0.08em', textTransform: 'uppercase',
              color: slot.color,
            }}>{slot.label}</span>
          )}
          <span style={{
            fontFamily: T.fontMono, fontSize: 9, fontWeight: 600,
            letterSpacing: '0.08em', textTransform: 'uppercase',
            color: owned ? T.green : T.textFaint,
          }}>{showSlot && '· '}{owned ? 'In hand' : 'Looking for'}</span>
        </div>
      </div>
      <TierBadge tier={item.tier} size={dense ? 'sm' : 'md'} />
      <StatusPip owned={owned} size={dense ? 18 : 20} />
    </div>
  );
}

// Slot section header
function SlotHeader({ slotKey, count, total, blurb, open, onToggle, collapsible = true }) {
  const slot = SLOT[slotKey];
  const pct = total > 0 ? (count / total) * 100 : 0;
  return (
    <div style={{ marginBottom: 6 }}>
      <button onClick={onToggle} disabled={!collapsible} style={{
        display: 'grid',
        gridTemplateColumns: 'auto auto 1fr auto',
        gap: 8, alignItems: 'center',
        width: '100%',
        background: 'transparent', border: 'none', padding: 0,
        cursor: collapsible ? 'pointer' : 'default',
        marginBottom: 4,
        color: T.textDim,
      }}>
        {collapsible ? <Caret open={open} /> : <span style={{ width: 10 }} />}
        <Pill tone={slotKey === 'core' ? 'amber' : slotKey === 'carry' ? 'blue' : 'purple'}>
          <span style={{ width: 5, height: 5, borderRadius: 999, background: slot.color }} />
          {slot.label}
        </Pill>
        <Mono size={10} color={T.textFaint} style={{ textAlign: 'left', marginLeft: 4 }}>
          {count}/{total}{blurb ? ` · ${blurb}` : ''}
        </Mono>
        <Mono size={10} color={slot.color}>{Math.round(pct)}%</Mono>
      </button>
      <div style={{
        height: 2, borderRadius: 999,
        background: 'rgba(255,255,255,0.04)', overflow: 'hidden',
      }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          background: slot.color, opacity: 0.85,
          transition: 'width 0.4s', borderRadius: 999,
        }} />
      </div>
    </div>
  );
}

Object.assign(window, {
  T, SLOT, STATE, VERDICT_STYLES,
  Mono, Eyebrow, ItemThumb, TierBadge, StatusPip, Pill, ConfidenceBar, Caret,
  Card, TabBar, Header, Frame, Scroller, ItemRow, SlotHeader,
});
