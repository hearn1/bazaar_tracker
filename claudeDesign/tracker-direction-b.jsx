// Direction B — Build-first, two-zone
// Active Build pinned at top + Item Checklist always visible.
// Everything else (Search / Override / Prompts) lives in a sub-tab strip below.

function BBuildHero({ active }) {
  return (
    <div style={{
      padding: '12px 14px',
      background: 'linear-gradient(180deg, rgba(230,169,32,0.12), rgba(230,169,32,0.02))',
      borderBottom: `1px solid ${T.amberBorder}`,
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <Eyebrow color={T.amber}>On build</Eyebrow>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
            <h2 style={{ fontFamily: T.fontDisplay, fontSize: 26, fontWeight: 800, color: '#fff', margin: 0, lineHeight: 1 }}>{active.name}</h2>
            <Mono size={11} color="#fff7d9">{Math.round(active.confidence * 100)}%</Mono>
          </div>
          <ConfidenceBar value={active.confidence} height={4} />
        </div>
        <Pill tone={active.is_manual ? 'purple' : 'blue'}>{active.is_manual ? 'Manual' : 'Auto'}</Pill>
      </div>
    </div>
  );
}

function BChecklist({ checklist }) {
  const [supportOpen, setSupportOpen] = useState(false);
  return (
    <Card padding={12}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ fontFamily: T.fontDisplay, fontSize: 14, fontWeight: 700, color: '#fff', margin: 0 }}>Item checklist</h3>
        <Mono size={9} color={T.textFaint}>What to look for</Mono>
      </div>
      {/* Core + Carry always visible */}
      {['core', 'carry'].map(k => {
        const items = checklist[k];
        const owned = items.filter(i => i.owned).length;
        return (
          <div key={k} style={{ marginBottom: 10 }}>
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
      {/* Support — collapsed by default, names in chip line */}
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
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6, paddingLeft: 18,
          }}>
            {checklist.support.slice(0, 5).map(i => (
              <span key={i.name} style={{
                fontFamily: T.fontUI, fontSize: 11,
                color: T.textDim,
                padding: '2px 7px', borderRadius: 999,
                background: 'rgba(167,139,250,0.06)',
                border: `1px solid rgba(167,139,250,0.18)`,
              }}>{i.name}</span>
            ))}
            {checklist.support.length > 5 && (
              <span style={{ fontFamily: T.fontMono, fontSize: 10, color: T.textFaint, padding: '2px 4px' }}>
                +{checklist.support.length - 5}
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
    </Card>
  );
}

function BSubtabs({ active, onChange }) {
  const tabs = [
    { id: 'pivots',  label: 'Pivots' },
    { id: 'search',  label: 'Find card' },
    { id: 'prompts', label: 'Coach' },
    { id: 'notes',   label: 'Notes' },
  ];
  return (
    <div style={{
      display: 'flex', gap: 4,
      padding: '6px 0',
      borderBottom: `1px solid ${T.border}`,
      marginBottom: 10,
    }}>
      {tabs.map(t => {
        const isActive = active === t.id;
        return (
          <button key={t.id} onClick={() => onChange(t.id)} style={{
            padding: '6px 10px',
            borderRadius: 6,
            border: 'none',
            background: isActive ? T.bgRaised : 'transparent',
            color: isActive ? T.amber : T.textDim,
            fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
            letterSpacing: '0.1em', textTransform: 'uppercase',
            cursor: 'pointer',
            borderBottom: isActive ? `2px solid ${T.amber}` : '2px solid transparent',
            marginBottom: -1,
          }}>{t.label}</button>
        );
      })}
    </div>
  );
}

function BPivotsPane({ archetypes }) {
  const [showAll, setShowAll] = useState(false);
  const positive = archetypes.filter(a => a.score > 0 && !a.active);
  const zero = archetypes.filter(a => a.score === 0);
  const list = showAll ? [...positive, ...zero] : positive;
  return (
    <div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {list.map(arch => {
          const score = arch.score || 0;
          const hot = score >= 0.2;
          return (
            <button key={arch.name} style={{
              display: 'grid',
              gridTemplateColumns: '1fr 60px auto',
              gap: 8, alignItems: 'center',
              padding: '7px 10px',
              borderRadius: 8,
              border: hot ? `1px solid ${T.amberBorder}` : `1px solid ${T.border}`,
              background: hot ? T.amberSoft : 'rgba(255,255,255,0.025)',
              color: hot ? '#ffe9b0' : T.textDim,
              fontFamily: T.fontUI, fontSize: 12, fontWeight: 500,
              cursor: 'pointer',
              textAlign: 'left',
            }}>
              <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{arch.name}</span>
              <div style={{ height: 4, borderRadius: 999, background: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${score * 100}%`, background: hot ? T.amber : T.textFaint, borderRadius: 999 }} />
              </div>
              <Mono size={10} color={hot ? T.amber : T.textFaint}>{Math.round(score * 100)}%</Mono>
            </button>
          );
        })}
      </div>
      {!showAll && zero.length > 0 && (
        <button onClick={() => setShowAll(true)} style={{
          marginTop: 8, background: 'transparent', border: 'none',
          color: T.textFaint, fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
          letterSpacing: '0.1em', textTransform: 'uppercase', cursor: 'pointer', padding: 0,
        }}>+ Show {zero.length} inactive builds</button>
      )}
    </div>
  );
}

function BSearchPane() {
  const [q, setQ] = useState('');
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '9px 12px', borderRadius: 10,
        background: 'rgba(0,0,0,0.32)', border: `1px solid ${T.border}`,
      }}>
        <span style={{ fontSize: 14, color: T.textFaint }}>⌕</span>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="See it in shop, type the name…" style={{
          flex: 1, background: 'transparent', border: 'none', outline: 'none',
          fontFamily: T.fontUI, fontSize: 12, color: '#fff',
        }} />
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: T.textFaint, lineHeight: 1.5 }}>
        We'll show every Karnok build it fits, and whether it's <span style={{ color: T.amber }}>core</span> / <span style={{ color: T.blue }}>carry</span> / <span style={{ color: T.purple }}>support</span>.
      </div>
    </div>
  );
}

function BPromptsPane({ prompts }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {prompts.map((p, i) => (
        <div key={i} style={{
          padding: '8px 10px', borderRadius: 10,
          background: p.tone === 'amber' ? T.amberSoft : T.blueSoft,
          border: `1px solid ${p.tone === 'amber' ? T.amberBorder : T.blueBorder}`,
        }}>
          <Mono size={9} color={p.tone === 'amber' ? T.amber : T.blue}>{p.kind}</Mono>
          <div style={{ fontSize: 12, color: T.text, marginTop: 4, lineHeight: 1.4 }}>{p.text}</div>
        </div>
      ))}
    </div>
  );
}

function BNotesPane({ summary }) {
  return (
    <div style={{ fontSize: 12, color: T.textDim, lineHeight: 1.5 }}>
      {summary}
    </div>
  );
}

function BCoachTab({ pivotsVariant = 'tiered' }) {
  const [sub, setSub] = useState('pivots');
  const Pivots =
    pivotsVariant === 'dials'         ? PivotsDials        :
    pivotsVariant === 'dials-shares'  ? PivotsDialsShares  :
    pivotsVariant === 'dials-trigger' ? PivotsDialsTrigger :
    pivotsVariant === 'dials-trend'   ? PivotsDialsTrend   :
    pivotsVariant === 'ladder'        ? PivotsLadder       :
    pivotsVariant === 'hotchip'       ? PivotsHotChip      :
    PivotsTiered;
  return (
    <>
      <BChecklist checklist={STATE.checklist} />
      <Card padding={12}>
        <BSubtabs active={sub} onChange={setSub} />
        {sub === 'pivots'  && <Pivots archetypes={STATE.archetypes} />}
        {sub === 'search'  && <BSearchPane />}
        {sub === 'prompts' && <BPromptsPane prompts={STATE.prompts} />}
        {sub === 'notes'   && <BNotesPane summary={STATE.active.summary} />}
      </Card>
    </>
  );
}

// Reuse review/run/idle from Direction A by re-implementing locally to keep
// each direction self-contained (small tweaks happen per direction)
function BReviewTab() { return <AReviewTab />; }
function BRunTab() { return <ARunTab />; }
function BIdleTab() { return <AIdleTab />; }

function DirectionB({ tab = 'coach', pivotsVariant = 'tiered' }) {
  const isIdle = tab === 'idle';
  return (
    <Frame width={360} height={760}>
      <Header status={isIdle ? 'idle' : 'live'} state={STATE} compact />
      {tab === 'coach' && !isIdle && <BBuildHero active={STATE.active} />}
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
        {tab === 'coach'  && <BCoachTab pivotsVariant={pivotsVariant} />}
        {tab === 'review' && <BReviewTab />}
        {tab === 'run'    && <BRunTab />}
        {tab === 'idle'   && <BIdleTab />}
      </Scroller>
    </Frame>
  );
}

Object.assign(window, { DirectionB });
