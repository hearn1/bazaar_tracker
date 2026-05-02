// App composer — design canvas with all 3 directions × 4 tab states.

function App() {
  return (
    <DesignCanvas defaultBackground="#0a0c12">
      <DCSection
        id="readme"
        title="Bazaar Tracker — redesign feedback"
        subtitle="Three structural directions for the Coach tab. Each shows all 4 states: Coach · Review · Run · Idle."
      >
        <DCArtboard id="readme-card" label="Read me first" width={520} height={680}>
          <ReadmeCard />
        </DCArtboard>
      </DCSection>

      <DCSection
        id="dirA"
        title="Direction A · Refined stack"
        subtitle="Closest to today. Collapsible sections + denser rows. Active build pinned-feel at top, support collapsed by default, header stats moved into Run."
      >
        <DCArtboard id="A-coach"  label="Coach"  width={400} height={800}><Wrap><DirectionA tab="coach" /></Wrap></DCArtboard>
        <DCArtboard id="A-review" label="Review" width={400} height={800}><Wrap><DirectionA tab="review" /></Wrap></DCArtboard>
        <DCArtboard id="A-run"    label="Run"    width={400} height={800}><Wrap><DirectionA tab="run" /></Wrap></DCArtboard>
        <DCArtboard id="A-idle"   label="Idle"   width={400} height={800}><Wrap><DirectionA tab="idle" /></Wrap></DCArtboard>
      </DCSection>

      <DCSection
        id="dirB"
        title="Direction B · Build-first, two-zone"
        subtitle="Active Build = always-visible header strip. Item Checklist is the entire main scroll. Pivots / search / prompts / notes live in a sub-tab strip below."
      >
        <DCArtboard id="B-coach"  label="Coach"  width={400} height={800}><Wrap><DirectionB tab="coach" /></Wrap></DCArtboard>
        <DCArtboard id="B-review" label="Review" width={400} height={800}><Wrap><DirectionB tab="review" /></Wrap></DCArtboard>
        <DCArtboard id="B-run"    label="Run"    width={400} height={800}><Wrap><DirectionB tab="run" /></Wrap></DCArtboard>
        <DCArtboard id="B-idle"   label="Idle"   width={400} height={800}><Wrap><DirectionB tab="idle" /></Wrap></DCArtboard>
      </DCSection>

      <DCSection
        id="dirC"
        title="Direction C · Compact rail"
        subtitle="Build collapses to a thin status strip. Pivots = horizontal scroller. Tools (search / prompts / notes) tuck into a footer drawer."
      >
        <DCArtboard id="C-coach"  label="Coach"  width={400} height={800}><Wrap><DirectionC tab="coach" /></Wrap></DCArtboard>
        <DCArtboard id="C-review" label="Review" width={400} height={800}><Wrap><DirectionC tab="review" /></Wrap></DCArtboard>
        <DCArtboard id="C-run"    label="Run"    width={400} height={800}><Wrap><DirectionC tab="run" /></Wrap></DCArtboard>
        <DCArtboard id="C-idle"   label="Idle"   width={400} height={800}><Wrap><DirectionC tab="idle" /></Wrap></DCArtboard>
      </DCSection>

      <DCSection
        id="pivots"
        title="Pivots — visualizing distance"
        subtitle="Direction B's Pivots pane, reworked. ~5 reasonable pivots with real scores, ~14 long shots at 0% you still want to know about."
      >
        <DCArtboard id="P-readme"  label="Read me"   width={420} height={760}><PivotsReadmeCard /></DCArtboard>
        <DCArtboard id="P-tiered"  label="A · Tiered groups"     width={400} height={800}><Wrap><DirectionB tab="coach" pivotsVariant="tiered" /></Wrap></DCArtboard>
        <DCArtboard id="P-dials"   label="B · Dials (bare)"      width={400} height={800}><Wrap><DirectionB tab="coach" pivotsVariant="dials" /></Wrap></DCArtboard>
        <DCArtboard id="P-ladder"  label="C · Heat ladder"       width={400} height={800}><Wrap><DirectionB tab="coach" pivotsVariant="ladder" /></Wrap></DCArtboard>
        <DCArtboard id="P-hotchip" label="D · Hot list + chips"  width={400} height={800}><Wrap><DirectionB tab="coach" pivotsVariant="hotchip" /></Wrap></DCArtboard>
      </DCSection>

      <DCSection
        id="pivots-b-fill"
        title="Filling the dial's dead space"
        subtitle="Variant B has horizontal room next to the dial. Three options for what to put there. Compare against D (which uses the same width for inline progress fill)."
      >
        <DCArtboard id="PB-shares"  label="B1 · Shared items"   width={400} height={800}><Wrap><DirectionB tab="coach" pivotsVariant="dials-shares" /></Wrap></DCArtboard>
        <DCArtboard id="PB-trigger" label="B2 · Needs X"        width={400} height={800}><Wrap><DirectionB tab="coach" pivotsVariant="dials-trigger" /></Wrap></DCArtboard>
        <DCArtboard id="PB-trend"   label="B3 · Trend + tier"   width={400} height={800}><Wrap><DirectionB tab="coach" pivotsVariant="dials-trend" /></Wrap></DCArtboard>
        <DCArtboard id="PB-vsD"     label="D · Hot+chips"       width={400} height={800}><Wrap><DirectionB tab="coach" pivotsVariant="hotchip" /></Wrap></DCArtboard>
      </DCSection>
    </DesignCanvas>
  );
}

function Wrap({ children }) {
  return (
    <div style={{
      padding: 20, minHeight: '100%',
      background: '#0a0c12',
      display: 'grid', placeItems: 'start center',
    }}>{children}</div>
  );
}

function ReadmeCard() {
  const Section = ({ title, children }) => (
    <div style={{ marginBottom: 18 }}>
      <div style={{
        fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
        letterSpacing: '0.16em', textTransform: 'uppercase',
        color: T.amber, marginBottom: 8,
      }}>{title}</div>
      <div style={{ fontSize: 13.5, lineHeight: 1.55, color: T.textDim }}>{children}</div>
    </div>
  );
  const Bullet = ({ children, label }) => (
    <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: 10, marginBottom: 6 }}>
      <span style={{
        fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
        letterSpacing: '0.1em', textTransform: 'uppercase',
        color: T.amber, paddingTop: 2,
      }}>{label}</span>
      <span>{children}</span>
    </div>
  );
  return (
    <div style={{
      padding: '32px 36px',
      background: 'linear-gradient(180deg, #0e1220, #0a0c12)',
      color: T.text, fontFamily: T.fontUI,
      height: '100%', overflow: 'auto',
    }}>
      <div style={{ fontFamily: T.fontMono, fontSize: 10, fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: T.amber, marginBottom: 8 }}>Design feedback</div>
      <h1 style={{ fontFamily: T.fontDisplay, fontSize: 32, fontWeight: 800, color: '#fff', margin: '0 0 6px', lineHeight: 1.05 }}>Where the overload comes from</h1>
      <div style={{ fontSize: 14, color: T.textDim, marginBottom: 26, lineHeight: 1.5 }}>
        The Coach tab today has 6 equally-weighted sections all open by default. Even fully expanded it scrolls past the fold. That's an information-architecture problem, not a styling one. Three directions explore different ways to fix it.
      </div>

      <Section title="Diagnosis">
        <Bullet label="Equal weight">Active Build, Search, Override, Checklist, Prompts all read at the same hierarchy. Your priority order makes it clear they shouldn't.</Bullet>
        <Bullet label="Default open">Sections that are rarely touched (search, prompts, full override grid) are visible by default and consume scroll real estate.</Bullet>
        <Bullet label="Support bloat">The 1/10 support list is the single tallest block on screen and the least-glanced.</Bullet>
        <Bullet label="Header stats">PvP / Tier are run-context, not coach-context. They duplicate what Run shows.</Bullet>
      </Section>

      <Section title="Shared moves">
        <Bullet label="Demote">Coach prompts, full search, build text → collapsed by default.</Bullet>
        <Bullet label="Trim">Build override only shows builds with score &gt; 0%; "show {`{N}`} inactive" reveals the rest.</Bullet>
        <Bullet label="Relocate">PvP / Tier strip moves into the Run tab where it belongs.</Bullet>
        <Bullet label="Compress">Support checklist defaults to a chip-line of names; expand to see rows.</Bullet>
        <Bullet label="Filter">Review tab gets MISSED / SUBOPTIMAL filter chips so the learning surface is one tap away.</Bullet>
      </Section>

      <Section title="The three directions">
        <Bullet label="A · Stack">Same vertical stack as today, just disciplined. Lowest risk; best if you want the smallest visual change.</Bullet>
        <Bullet label="B · Two-zone">Active Build pins to a strip below the title; checklist owns the main scroll; everything else is a 4-tab strip below the checklist. Best if "what build, what items" really is 95% of usage.</Bullet>
        <Bullet label="C · Rail">Build collapses to a one-line status bar with inline confidence; pivots become a horizontal scroller; tools live in a footer drawer. Densest; best for narrow always-on overlay use.</Bullet>
      </Section>

      <Section title="Notes">
        <div style={{ fontSize: 12, lineHeight: 1.55 }}>
          All three reuse the same component primitives so picking pieces from each is easy. Drag the title to reorder, click a label to focus an artboard fullscreen (←/→/Esc).
        </div>
      </Section>
    </div>
  );
}

function PivotsReadmeCard() {
  const Section = ({ title, children }) => (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
        letterSpacing: '0.16em', textTransform: 'uppercase',
        color: T.amber, marginBottom: 8,
      }}>{title}</div>
      <div style={{ fontSize: 13, lineHeight: 1.55, color: T.textDim }}>{children}</div>
    </div>
  );
  const Bullet = ({ children, label, color = T.amber }) => (
    <div style={{ display: 'grid', gridTemplateColumns: '74px 1fr', gap: 10, marginBottom: 8 }}>
      <span style={{
        fontFamily: T.fontMono, fontSize: 10, fontWeight: 700,
        letterSpacing: '0.1em', textTransform: 'uppercase',
        color, paddingTop: 2,
      }}>{label}</span>
      <span>{children}</span>
    </div>
  );
  return (
    <div style={{
      padding: '28px 32px',
      background: 'linear-gradient(180deg, #0e1220, #0a0c12)',
      color: T.text, fontFamily: T.fontUI,
      height: '100%', overflow: 'auto',
    }}>
      <div style={{ fontFamily: T.fontMono, fontSize: 10, fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: T.amber, marginBottom: 8 }}>Pivots · variants</div>
      <h1 style={{ fontFamily: T.fontDisplay, fontSize: 26, fontWeight: 800, color: '#fff', margin: '0 0 8px', lineHeight: 1.1 }}>How close is each pivot?</h1>
      <div style={{ fontSize: 13, color: T.textDim, marginBottom: 22, lineHeight: 1.5 }}>
        15–20 builds per hero. 3–5 share enough overlap to be real pivots; the other 10–15 are long shots you still want surfaced. Each variant solves the "distance" problem differently.
      </div>

      <Section title="Tiers (shared)">
        <Bullet label="Strong" color={T.amber}>≥ 30%. Real overlap, plays now.</Bullet>
        <Bullet label="Possible" color={T.blue}>5–30%. Some overlap, watch shop.</Bullet>
        <Bullet label="Long shot" color={T.textFaint}>&lt; 5%. Know it exists.</Bullet>
      </Section>

      <Section title="The four">
        <Bullet label="A · Tiered">Explicit grouped sections. Long shots collapse to a chip line. Most legible, most vertical space.</Bullet>
        <Bullet label="B · Dials">One row per build with a small radial gauge. Distance reads as fill arc + glow on hot ones. Compact, gives every build equal layout weight.</Bullet>
        <Bullet label="C · Ladder">Vertical thermometer 0→100%. All builds pinned at their score on the same axis. Strongest spatial read of distance, but takes a column.</Bullet>
        <Bullet label="D · Hot+chips">Top 3–5 get fat rows with inline progress fill. Everything 0% is one chip cloud. Densest; biased to "what's playable now."</Bullet>
      </Section>

      <Section title="My pick">
        <div style={{ fontSize: 12.5, lineHeight: 1.55 }}>
          <strong style={{ color: '#fff' }}>D (Hot+chips)</strong> for the live overlay — eye stays on the 3–5 that matter and the long-tail is treated honestly as awareness, not action.
          <strong style={{ color: '#fff' }}> C (Ladder)</strong> if you want one glance to answer "how far am I from anything?" Pick one and I'll polish it.
        </div>
      </Section>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
