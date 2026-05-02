// Direction A — Refined stack
// Closest to current; collapsible sections, denser rows, header stats moved to Run tab.
// Coach order: Active Build (always visible) → Item Checklist (Core/Carry/Support) →
// Adjacent Builds (>0% only) → Find a Card → Coach Prompts (collapsed by default) → Build text (collapsed)

function ABuildHero({ active, expanded, onToggle }) {
  return (
    <Card accent={T.amber}>
      <Eyebrow color={T.amber}>Active build read</Eyebrow>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, marginBottom: 8 }}>
        <h2 style={{ fontFamily: T.fontDisplay, fontSize: 26, fontWeight: 800, color: '#fff', margin: 0, lineHeight: 1 }}>{active.name}</h2>
        <Pill tone={active.is_manual ? 'purple' : 'blue'}>{active.is_manual ? 'Manual' : 'Auto'}</Pill>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <div style={{ flex: 1 }}><ConfidenceBar value={active.confidence} height={6} /></div>
        <Mono size={11} color="#fff7d9">{Math.round(active.confidence * 100)}%</Mono>
      </div>
      <button onClick={onToggle} style={{
        background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 5, color: T.textDim,
      }}>
        <Caret open={expanded} />
        <Mono size={9} color={T.textDim}>{expanded ? 'Hide notes' : 'Show notes'}</Mono>
      </button>
      {expanded && (
        <div style={{ fontSize: 12, color: T.textDim, lineHeight: 1.45, marginTop: 8 }}>
          {active.summary}
        </div>
      )}
    </Card>
  );
}

function AChecklist({ checklist }) {
  const [open, setOpen] = useState({ core: true, carry: true, support: false });
  const slots = ['core', 'carry', 'support'];
  return (
    <Card>
      <Eyebrow>What to look for</Eyebrow>
      <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: '0 0 12px' }}>Item checklist</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {slots.map(k => {
          const items = checklist[k];
          const owned = items.filter(i => i.owned).length;
          return (
            <div key={k}>
              <SlotHeader
                slotKey={k}
                count={owned}
                total={items.length}
                blurb={SLOT[k].blurb}
                open={open[k]}
                onToggle={() => setOpen(s => ({ ...s, [k]: !s[k] }))}
              />
              {open[k] && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
                  {items.map(it => <ItemRow key={it.name} item={it} slotKey={k} dense />)}
                </div>
              )}
              {!open[k] && k === 'support' && (
                <div style={{
                  marginTop: 4, fontSize: 11, color: T.textFaint, fontFamily: T.fontUI,
                  paddingLeft: 18, lineHeight: 1.5,
                }}>
                  {items.slice(0, 4).map(i => i.name).join(', ')}{items.length > 4 ? `, +${items.length - 4} more` : ''}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function AAdjacentBuilds({ archetypes, showAllInit = false }) {
  const [open, setOpen] = useState(true);
  const [showAll, setShowAll] = useState(showAllInit);
  const positive = archetypes.filter(a => a.score > 0 && !a.active);
  const zero = archetypes.filter(a => a.score === 0);
  const list = showAll ? [...positive, ...zero] : positive;
  return (
    <Card>
      <button onClick={() => setOpen(o => !o)} style={{
        background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 6, width: '100%',
      }}>
        <Caret open={open} />
        <Eyebrow>Pivots</Eyebrow>
        <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: 0, marginLeft: -2, flex: 1, textAlign: 'left' }}>Adjacent builds</h3>
        <Mono size={10} color={T.textFaint}>{positive.length} hot</Mono>
      </button>
      {open && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
            {list.map(arch => {
              const score = arch.score || 0;
              const hot = score >= 0.2;
              return (
                <button key={arch.name} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '5px 9px',
                  borderRadius: 999,
                  border: hot ? `1px solid ${T.amberBorder}` : `1px solid ${T.border}`,
                  background: hot ? T.amberSoft : 'rgba(255,255,255,0.025)',
                  color: hot ? '#ffe9b0' : T.textDim,
                  fontFamily: T.fontUI, fontSize: 11, fontWeight: 500,
                  cursor: 'pointer',
                }}>
                  <span>{arch.name}</span>
                  <span style={{ fontFamily: T.fontMono, fontSize: 10, fontWeight: 700, opacity: 0.85 }}>
                    {Math.round(score * 100)}%
                  </span>
                </button>
              );
            })}
          </div>
          {!showAll && zero.length > 0 && (
            <button onClick={() => setShowAll(true)} style={{
              marginTop: 8, background: 'transparent', border: 'none',
              color: T.textFaint, fontFamily: T.fontMono, fontSize: 10,
              fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase',
              cursor: 'pointer', padding: 0,
            }}>+ Show {zero.length} inactive</button>
          )}
        </div>
      )}
    </Card>
  );
}

function ASearch() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  return (
    <Card>
      <button onClick={() => setOpen(o => !o)} style={{
        background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 6, width: '100%',
      }}>
        <Caret open={open} />
        <Eyebrow>Find a card</Eyebrow>
        <span style={{ flex: 1 }} />
        <Mono size={10} color={T.textFaint}>What build does it fit?</Mono>
      </button>
      {open && (
        <div style={{ marginTop: 10 }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '8px 10px', borderRadius: 8,
            background: 'rgba(0,0,0,0.32)',
            border: `1px solid ${T.border}`,
          }}>
            <span style={{ fontSize: 13, color: T.textFaint }}>⌕</span>
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Type any item name…" style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              fontFamily: T.fontUI, fontSize: 12, color: '#fff',
            }} />
          </div>
        </div>
      )}
    </Card>
  );
}

function APrompts({ prompts }) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <button onClick={() => setOpen(o => !o)} style={{
        background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 6, width: '100%',
      }}>
        <Caret open={open} />
        <Eyebrow>Coach prompts</Eyebrow>
        <span style={{ flex: 1 }} />
        <Mono size={10} color={T.textFaint}>{prompts.length} active</Mono>
      </button>
      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
          {prompts.map((p, i) => (
            <div key={i} style={{
              padding: '8px 10px',
              borderRadius: 10,
              background: p.tone === 'amber' ? T.amberSoft : T.blueSoft,
              border: `1px solid ${p.tone === 'amber' ? T.amberBorder : T.blueBorder}`,
            }}>
              <Mono size={9} color={p.tone === 'amber' ? T.amber : T.blue}>{p.kind}</Mono>
              <div style={{ fontSize: 12, color: T.text, marginTop: 4, lineHeight: 1.4 }}>{p.text}</div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function ACoachTab() {
  const [heroExpanded, setHeroExpanded] = useState(false);
  return (
    <>
      <ABuildHero active={STATE.active} expanded={heroExpanded} onToggle={() => setHeroExpanded(e => !e)} />
      <AChecklist checklist={STATE.checklist} />
      <AAdjacentBuilds archetypes={STATE.archetypes} />
      <ASearch />
      <APrompts prompts={STATE.prompts} />
    </>
  );
}

function AReviewTab() {
  const [filter, setFilter] = useState('all');
  const tallies = STATE.decisionTallies;
  const filters = [
    { id: 'all',         label: 'All',         count: tallies.good + tallies.situational + tallies.suboptimal + tallies.missed },
    { id: 'good',        label: 'Good',        count: tallies.good,        color: T.green },
    { id: 'suboptimal',  label: 'Suboptimal',  count: tallies.suboptimal,  color: '#ff8585' },
    { id: 'missed',      label: 'Missed',      count: tallies.missed,      color: '#ff8585' },
  ];
  const visible = filter === 'all'
    ? STATE.decisionLog
    : STATE.decisionLog.filter(d => d.verdict === filter);
  return (
    <>
      <Card>
        <Eyebrow>Run review</Eyebrow>
        <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: '0 0 4px' }}>Decision log</h3>
        <div style={{ fontSize: 11, color: T.textFaint, marginBottom: 10 }}>Live · Build-defining choices only.</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4, marginBottom: 10 }}>
          {filters.map(f => (
            <button key={f.id} onClick={() => setFilter(f.id)} style={{
              padding: '8px 4px',
              borderRadius: 8,
              background: filter === f.id ? T.amberSoft : T.bgRaised,
              border: filter === f.id ? `1px solid ${T.amberBorder}` : `1px solid ${T.border}`,
              cursor: 'pointer',
              textAlign: 'center',
            }}>
              <Mono size={8} color={T.textFaint}>{f.label}</Mono>
              <div style={{
                fontFamily: T.fontMono, fontSize: 16, fontWeight: 700,
                color: f.color || (filter === f.id ? T.amber : '#fff'),
                marginTop: 2,
              }}>{f.count}</div>
            </button>
          ))}
        </div>
      </Card>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {visible.map((d, i) => {
          const v = VERDICT_STYLES[d.verdict];
          return (
            <div key={i} style={{
              display: 'grid',
              gridTemplateColumns: 'auto 32px 1fr auto',
              gap: 10, alignItems: 'center',
              padding: '8px 10px',
              borderRadius: 10,
              background: T.bgRaised,
              border: `1px solid ${T.border}`,
            }}>
              <Mono size={9} color={T.textFaint}>#{d.seq}</Mono>
              <ItemThumb name={d.name} size={32} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#eef3fb', marginBottom: 2 }}>{d.name}</div>
                <div style={{ fontSize: 10, color: T.textDim, lineHeight: 1.35,
                  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
                }}>{d.detail}</div>
              </div>
              <span style={{
                padding: '3px 7px', borderRadius: 999,
                background: v.bg, color: v.fg, border: `1px solid ${v.bd}`,
                fontFamily: T.fontMono, fontSize: 9, fontWeight: 700,
                letterSpacing: '0.08em', textTransform: 'uppercase',
                whiteSpace: 'nowrap',
              }}>{v.label}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}

function ARunTab() {
  return (
    <>
      <Card>
        <Eyebrow>Run context</Eyebrow>
        <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: '0 0 10px' }}>Current snapshot</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 6 }}>
          {[
            { label: 'Tier', value: STATE.tier },
            { label: 'PvP', value: STATE.pvp },
            { label: 'PvE', value: STATE.pve },
            { label: 'Decisions', value: STATE.decisions },
          ].map(s => (
            <div key={s.label} style={{
              padding: '10px 9px', borderRadius: 10,
              background: T.bgRaised, border: `1px solid ${T.border}`, textAlign: 'center',
            }}>
              <Mono size={9} color={T.textFaint}>{s.label}</Mono>
              <div style={{ fontFamily: T.fontMono, fontSize: 14, fontWeight: 700, color: '#fff', marginTop: 4 }}>{s.value}</div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 10, fontSize: 11, color: T.textDim }}>{STATE.threshold_note}</div>
      </Card>
      <Card>
        <Eyebrow>Phase guidance</Eyebrow>
        <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: '0 0 8px' }}>Late</h3>
        <div style={{ fontSize: 12, color: T.textDim, lineHeight: 1.45 }}>
          Final build archetypes. Commit by Day 7. No strong builds past Day 13 — win before then.
        </div>
      </Card>
      <Card>
        <Eyebrow>Hero reminder</Eyebrow>
        <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: '0 0 8px' }}>Karnok fundamentals</h3>
        <div style={{ fontSize: 12, color: T.textDim, lineHeight: 1.5 }}>
          Karnok is a tempo hero. Aim to end runs before Day 13, spend gold aggressively, and be careful with Hidden Lake if your final board is still unclear. Enrage synergies stay valuable across nearly every line.
        </div>
      </Card>
      <Card>
        <Eyebrow>Pivot signals</Eyebrow>
        <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: '0 0 8px' }}>Watch-outs</h3>
        <div style={{
          padding: '8px 10px', borderRadius: 8,
          background: T.blueSoft, borderLeft: `2px solid ${T.blue}`,
          fontSize: 12, color: T.text,
        }}>Board losing fights consistently despite having items.</div>
      </Card>
    </>
  );
}

function AIdleTab() {
  return (
    <>
      <Card accent={T.blue}>
        <Eyebrow color={T.blue}>Idle</Eyebrow>
        <h2 style={{ fontFamily: T.fontDisplay, fontSize: 24, fontWeight: 700, color: '#fff', margin: '0 0 8px', lineHeight: 1.1 }}>
          Waiting for run to start…
        </h2>
        <div style={{ fontSize: 12, color: T.textDim, lineHeight: 1.45 }}>
          The overlay will switch back to live coaching as soon as the next run records its first decision.
        </div>
      </Card>
      <Card>
        <Eyebrow>Last completed run</Eyebrow>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: 0 }}>Defeat</h3>
          <Pill tone="red">Defeat</Pill>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginBottom: 8 }}>
          {[
            { label: 'Tier', value: STATE.tier },
            { label: 'PvP',  value: STATE.pvp },
            { label: 'PvE',  value: STATE.pve },
          ].map(s => (
            <div key={s.label} style={{
              padding: '10px 6px', borderRadius: 10,
              background: T.bgRaised, border: `1px solid ${T.border}`, textAlign: 'center',
            }}>
              <Mono size={9} color={T.textFaint}>{s.label}</Mono>
              <div style={{ fontFamily: T.fontMono, fontSize: 14, fontWeight: 700, color: '#fff', marginTop: 4 }}>{s.value}</div>
            </div>
          ))}
        </div>
        <button style={{
          width: '100%', padding: '10px 12px',
          borderRadius: 8, border: `1px solid ${T.amberBorder}`,
          background: T.amberSoft, color: T.amber,
          fontFamily: T.fontMono, fontSize: 11, fontWeight: 700,
          letterSpacing: '0.12em', textTransform: 'uppercase',
          cursor: 'pointer',
        }}>Review last run</button>
      </Card>
    </>
  );
}

// Direction A frame
function DirectionA({ tab = 'coach' }) {
  const isIdle = tab === 'idle';
  const showHeaderStats = false; // moved to Run tab in this direction
  return (
    <Frame width={360} height={760}>
      <Header status={isIdle ? 'idle' : 'live'} state={STATE} compact showStats={showHeaderStats} />
      {!isIdle && (
        <div style={{ padding: '10px 12px 0' }}>
          <TabBar
            tabs={[
              { id: 'coach',  label: 'Coach' },
              { id: 'review', label: 'Review' },
              { id: 'run',    label: 'Run' },
            ]}
            active={tab}
          />
        </div>
      )}
      <Scroller>
        {tab === 'coach'  && <ACoachTab />}
        {tab === 'review' && <AReviewTab />}
        {tab === 'run'    && <ARunTab />}
        {tab === 'idle'   && <AIdleTab />}
      </Scroller>
    </Frame>
  );
}

Object.assign(window, { DirectionA });
