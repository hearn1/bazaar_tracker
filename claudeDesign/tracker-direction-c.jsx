// Direction C — Compact rail
// Ultra-dense single column. Build = thin status bar with inline confidence.
// Checklist takes the spotlight. Adjacent builds = horizontal scroller.
// Search / Override / Prompts as a footer drawer toggle.

function CStatusBar({ active }) {
  return (
    <div style={{
      padding: '10px 14px',
      borderBottom: `1px solid ${T.amberBorder}`,
      background: 'linear-gradient(180deg, rgba(230,169,32,0.10), rgba(230,169,32,0.02))',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Mono size={9} color={T.amber}>On</Mono>
        <h2 style={{
          fontFamily: T.fontDisplay, fontSize: 18, fontWeight: 800,
          color: '#fff', margin: 0, lineHeight: 1, flexShrink: 0,
        }}>{active.name}</h2>
        <div style={{ flex: 1, minWidth: 30 }}>
          <ConfidenceBar value={active.confidence} height={4} />
        </div>
        <Mono size={11} color="#fff7d9">{Math.round(active.confidence * 100)}%</Mono>
        <Pill tone={active.is_manual ? 'purple' : 'blue'} style={{ padding: '2px 6px', fontSize: 8 }}>
          {active.is_manual ? 'Manual' : 'Auto'}
        </Pill>
      </div>
    </div>
  );
}

function CAdjacentRail({ archetypes }) {
  const positive = archetypes.filter(a => a.score > 0 && !a.active);
  if (positive.length === 0) return null;
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 6 }}>
        <Eyebrow>Could pivot to</Eyebrow>
        <Mono size={9} color={T.textFaint}>{positive.length} options</Mono>
      </div>
      <div style={{
        display: 'flex', gap: 6,
        overflowX: 'auto', paddingBottom: 4,
        scrollbarWidth: 'none',
      }}>
        {positive.map(a => (
          <button key={a.name} style={{
            flexShrink: 0,
            padding: '8px 12px',
            borderRadius: 10,
            border: `1px solid ${T.amberBorder}`,
            background: T.amberSoft,
            color: '#ffe9b0',
            fontFamily: T.fontUI, fontSize: 12, fontWeight: 600,
            cursor: 'pointer',
            display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2,
            minWidth: 100,
          }}>
            <span style={{ whiteSpace: 'nowrap' }}>{a.name}</span>
            <Mono size={9} color={T.amber}>{Math.round(a.score * 100)}% fit</Mono>
          </button>
        ))}
        <button style={{
          flexShrink: 0,
          padding: '8px 12px',
          borderRadius: 10,
          border: `1px dashed ${T.border}`,
          background: 'transparent',
          color: T.textFaint,
          fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
          letterSpacing: '0.1em', textTransform: 'uppercase',
          cursor: 'pointer',
          minWidth: 80,
        }}>All builds →</button>
      </div>
    </div>
  );
}

function CChecklist({ checklist }) {
  const [supportOpen, setSupportOpen] = useState(false);
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8 }}>
        <Eyebrow>What to look for</Eyebrow>
        <Mono size={9} color={T.textFaint}>Item checklist</Mono>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {['core', 'carry'].map(k => {
          const items = checklist[k];
          const owned = items.filter(i => i.owned).length;
          return (
            <div key={k}>
              <SlotHeader
                slotKey={k}
                count={owned}
                total={items.length}
                blurb={SLOT[k].blurb}
                collapsible={false}
              />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
                {items.map(it => <ItemRow key={it.name} item={it} slotKey={k} dense showSlot={false} />)}
              </div>
            </div>
          );
        })}
        <div>
          <SlotHeader
            slotKey="support"
            count={checklist.support.filter(i => i.owned).length}
            total={checklist.support.length}
            blurb={SLOT.support.blurb}
            open={supportOpen}
            onToggle={() => setSupportOpen(o => !o)}
          />
          {!supportOpen && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6, paddingLeft: 18 }}>
              {checklist.support.slice(0, 6).map(i => (
                <span key={i.name} style={{
                  fontFamily: T.fontUI, fontSize: 10,
                  color: T.textDim,
                  padding: '2px 6px', borderRadius: 999,
                  background: 'rgba(167,139,250,0.06)',
                  border: `1px solid rgba(167,139,250,0.18)`,
                }}>{i.name}</span>
              ))}
              {checklist.support.length > 6 && (
                <span style={{ fontFamily: T.fontMono, fontSize: 9, color: T.textFaint, padding: '2px 4px' }}>
                  +{checklist.support.length - 6}
                </span>
              )}
            </div>
          )}
          {supportOpen && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
              {checklist.support.map(it => <ItemRow key={it.name} item={it} slotKey="support" dense showSlot={false} />)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function CDrawer({ open, onToggle, prompts, summary }) {
  const [pane, setPane] = useState('search');
  const [q, setQ] = useState('');
  return (
    <div style={{
      borderTop: `1px solid ${T.border}`,
      background: 'rgba(0,0,0,0.32)',
      flexShrink: 0,
    }}>
      <button onClick={onToggle} style={{
        width: '100%', padding: '8px 14px',
        background: 'transparent', border: 'none',
        display: 'flex', alignItems: 'center', gap: 8,
        cursor: 'pointer',
        color: T.textDim,
      }}>
        <Caret open={open} />
        <Mono size={10} color={T.textDim}>Tools</Mono>
        <span style={{ flex: 1 }} />
        <Mono size={9} color={T.textFaint}>Search · Prompts · Notes</Mono>
      </button>
      {open && (
        <div style={{ padding: '0 14px 14px' }}>
          <div style={{ display: 'flex', gap: 4, marginBottom: 10 }}>
            {[
              { id: 'search',  label: 'Search' },
              { id: 'prompts', label: `Coach (${prompts.length})` },
              { id: 'notes',   label: 'Notes' },
            ].map(t => (
              <button key={t.id} onClick={() => setPane(t.id)} style={{
                padding: '5px 10px',
                borderRadius: 6, border: 'none',
                background: pane === t.id ? T.bgRaised : 'transparent',
                color: pane === t.id ? T.amber : T.textDim,
                fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
                letterSpacing: '0.1em', textTransform: 'uppercase',
                cursor: 'pointer',
              }}>{t.label}</button>
            ))}
          </div>
          {pane === 'search' && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 10px', borderRadius: 8,
              background: 'rgba(0,0,0,0.42)', border: `1px solid ${T.border}`,
            }}>
              <span style={{ fontSize: 13, color: T.textFaint }}>⌕</span>
              <input value={q} onChange={e => setQ(e.target.value)} placeholder="Type any item name…" style={{
                flex: 1, background: 'transparent', border: 'none', outline: 'none',
                fontFamily: T.fontUI, fontSize: 12, color: '#fff',
              }} />
            </div>
          )}
          {pane === 'prompts' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {prompts.map((p, i) => (
                <div key={i} style={{
                  padding: '8px 10px', borderRadius: 8,
                  background: p.tone === 'amber' ? T.amberSoft : T.blueSoft,
                  border: `1px solid ${p.tone === 'amber' ? T.amberBorder : T.blueBorder}`,
                }}>
                  <Mono size={9} color={p.tone === 'amber' ? T.amber : T.blue}>{p.kind}</Mono>
                  <div style={{ fontSize: 12, color: T.text, marginTop: 4, lineHeight: 1.4 }}>{p.text}</div>
                </div>
              ))}
            </div>
          )}
          {pane === 'notes' && (
            <div style={{ fontSize: 12, color: T.textDim, lineHeight: 1.5 }}>{summary}</div>
          )}
        </div>
      )}
    </div>
  );
}

function CCoachTab() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <>
      <Scroller padding="10px 12px 12px">
        <CAdjacentRail archetypes={STATE.archetypes} />
        <CChecklist checklist={STATE.checklist} />
      </Scroller>
      <CDrawer open={drawerOpen} onToggle={() => setDrawerOpen(o => !o)} prompts={STATE.prompts} summary={STATE.active.summary} />
    </>
  );
}

function CReviewTab() { return <Scroller><AReviewTab /></Scroller>; }
function CRunTab()    { return <Scroller><ARunTab /></Scroller>; }
function CIdleTab()   { return <Scroller><AIdleTab /></Scroller>; }

function DirectionC({ tab = 'coach' }) {
  const isIdle = tab === 'idle';
  return (
    <Frame width={360} height={760}>
      <Header status={isIdle ? 'idle' : 'live'} state={STATE} compact />
      {tab === 'coach' && !isIdle && <CStatusBar active={STATE.active} />}
      {!isIdle && (
        <div style={{ padding: '8px 12px 0' }}>
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
      {tab === 'coach'  && <CCoachTab />}
      {tab === 'review' && <CReviewTab />}
      {tab === 'run'    && <CRunTab />}
      {tab === 'idle'   && <CIdleTab />}
    </Frame>
  );
}

Object.assign(window, { DirectionC });
