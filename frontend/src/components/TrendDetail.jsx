import React, { useMemo, useState } from 'react'
import { recompute } from '../api.js'
import { LeadLagTag, RawVsAdjusted, Derivation } from './shared.jsx'

const AXIS_LABELS = {
  climate_fit: 'Climate fit',
  modesty_fit: 'Modesty fit',
  occasion_fit: 'Occasion fit',
  price_band_fit: 'Price-band fit',
}

/* DETAIL, four panels mirroring the buyer's reasoning path:
   1 what looks real · 2 what could mislead · 3 what should the buyer do ·
   4 what improves next time. All content from /api/trend + /api/recompute. */
export default function TrendDetail({ trend, seg, onBack }) {
  // `live` holds the recomputed payload after slider overrides; null = baseline
  const [live, setLive] = useState(null)
  const [overrides, setOverrides] = useState({})
  const [feedback, setFeedback] = useState({})
  const d = live || trend
  const m = d.metrics
  const axes = m.replication.axes

  const debouncedRecompute = useMemo(() => {
    let t
    return (next) => {
      clearTimeout(t)
      t = setTimeout(async () => {
        try { setLive(await recompute(trend.bucket, seg, next)) } catch { /* keep baseline */ }
      }, 200)
    }
  }, [trend.bucket, seg])

  const onSlide = (axis, value) => {
    const next = { ...overrides, [axis]: Number(value) }
    setOverrides(next)
    debouncedRecompute(next)
  }

  const positives = [
    ['Your own sales (POS)', m.own_sales],
    ['Demand strength (India)', m.demand_strength],
    ['Supply conviction (India)', m.supply_conviction],
    ['West signal, supply, not demand', m.west_signal],
    ['Cross-platform agreement', m.cross_platform_agreement],
  ].filter(([, sig]) => sig)

  return (
    <>
      <span className="back" onClick={onBack}>← back to slate</span>
      <div className="detail-head">
        <h2>{trend.display_name}</h2>
        <span className={`badge ${d.verdict?.toLowerCase() || ''}`}>{d.verdict || d.bet.label}</span>
        <LeadLagTag label={m.lead_lag.label} />
        <span className="note">{trend.counts.total} products · keywords: {trend.keywords.join(', ')}</span>
      </div>
      {trend.direction && (
        <div className="dir-line">
          <span className="glyph">{trend.direction.glyph}</span> {trend.direction.phrase}
          {trend.direction.single_source && (
            <span className="src-caution">, single Western source, low confidence</span>
          )}
        </div>
      )}
      {d.merchant_line && <div className="merchant">“{d.merchant_line}”</div>}
      <div className="why">{d.why}</div>
      <div className="bigbar">
        <RawVsAdjusted base={d.confidence.base} adjusted={d.confidence.adjusted} />
        <Derivation text={d.confidence.derivation} />
      </div>

      <div className="panels">
        {/* ── 1. WHAT LOOKS REAL ─────────────────────────────────────────── */}
        <div className="panel real">
          <h4>1 · What looks real</h4>
          <div className="panel-sub">Validated signals, each with its derivation.</div>
          {positives.map(([name, sig]) => (
            <div className="sig" key={name}>
              <div className="sig-row">
                <span className="name">{name}</span>
                <span className="val">{sig.value ?? 'n/a'}</span>
              </div>
              <Derivation text={sig.derivation} />
            </div>
          ))}
          <div className="sig">
            <div className="sig-row">
              <span className="name">Lead-lag position</span>
              <span className="val">{m.lead_lag.label}</span>
            </div>
            <Derivation text={m.lead_lag.derivation} />
          </div>
          {m.west_agreement && m.west_agreement.level !== 'none' && (
            <div className="sig">
              <div className="sig-row">
                <span className="name">Western source agreement</span>
                <span className="val">{m.west_agreement.sources_present}/{m.west_agreement.sources_total}</span>
              </div>
              <div className="note">{m.west_agreement.level}</div>
              <Derivation text={m.west_agreement.derivation} />
            </div>
          )}
          {trend.trajectory && (
            <div className="sig">
              <div className="sig-row">
                <span className="name">Trajectory (listing-date proxy)</span>
                <span className="val">{trend.trajectory.arrow}</span>
              </div>
              <div className="note">{trend.trajectory.label}</div>
              <Derivation text={trend.trajectory.derivation} />
            </div>
          )}
          <ul className="examples">
            {trend.examples.map((e) => (
              <li key={e.url || e.name}>
                <a href={e.url} target="_blank" rel="noreferrer">{e.name}</a>
                {' '}({e.platform}{e.price ? `, ${e.currency === 'GBP' ? '£' : '₹'}${e.price}` : ''})
              </li>
            ))}
          </ul>
        </div>

        {/* ── 2. WHAT COULD MISLEAD ──────────────────────────────────────── */}
        <div className="panel mislead">
          <h4>2 · What could mislead</h4>
          <div className="panel-sub">Each distortion, its penalty, and the plain caution.</div>
          <div className="sig">
            <div className="sig-row">
              <span className="name">Discount distortion</span>
              <span className="val pen">−{m.discount_penalty.value}</span>
            </div>
            <div className="note">
              {m.discount_penalty.median_discount != null
                ? `Median true discount ${m.discount_penalty.median_discount}% (market norm ${m.discount_penalty.market_median_discount}%). Traction at this price may be bought, not organic.`
                : 'No price/MRP pairs, distortion unknown, not absent.'}
            </div>
            <Derivation text={m.discount_penalty.derivation} />
          </div>
          <div className="sig">
            <div className="sig-row">
              <span className="name">Supply without demand</span>
              <span className="val pen">−{m.supply_without_demand_penalty.value}</span>
            </div>
            <div className="note">Platforms can push stock nobody asked for; shelf space is not proof of appetite.</div>
            <Derivation text={m.supply_without_demand_penalty.derivation} />
          </div>
          <div className="sig">
            <div className="sig-row">
              <span className="name">West signal is supply-only</span>
              <span className="val">caveat</span>
            </div>
            <div className="note">{m.west_signal.derivation}</div>
          </div>
          {m.replication.applies && (
            <div className="sig">
              <div className="sig-row">
                <span className="name">Early-stage replication gate</span>
                <span className="val pen">× {m.replication.value}%</span>
              </div>
              <div className="note">Lead-lag is Early: the West moved, India hasn't. Confidence is multiplied by India-fit probability.</div>
              <Derivation text={m.replication.derivation} />
            </div>
          )}
        </div>

        {/* ── 3. WHAT SHOULD THE BUYER DO ────────────────────────────────── */}
        <div className="panel action">
          <h4>3 · What should the buyer do</h4>
          <div className="panel-sub">
            Bet size from adjusted confidence. India-fit defaults are inferred from the
            bucket's keywords, drag to override, the bet recomputes live.
          </div>
          <div className="bet-line">
            <span className={`badge ${d.verdict?.toLowerCase() || ''}`}>{d.verdict || d.bet.label}</span>
            <span className="band">{d.bet.band}</span>
            {live && <span className="live">● recomputed with your overrides</span>}
          </div>
          <div className="note">{d.bet.rule}</div>
          {trend.lag_estimate && (
            <div className="lagbox">
              <b>Buying calendar:</b> Western lead of {trend.lag_estimate.window} for this
              silhouette (estimate).
              <Derivation text={trend.lag_estimate.derivation} />
            </div>
          )}
          {trend.subtrends?.buy_instruction && (
            <div className="subtrends">
              <b>Buy instruction:</b> {trend.display_name.toLowerCase()}, {trend.subtrends.buy_instruction}.
              <div className="chips">
                {trend.subtrends.fabrics.map((f) => (
                  <span key={`f-${f.name}`} className="chip fabric">{f.name} {f.share}%</span>
                ))}
                {trend.subtrends.colors.map((c) => (
                  <span key={`c-${c.name}`} className="chip color">{c.name} {c.share}%</span>
                ))}
              </div>
              <div className="note">{trend.subtrends.note}</div>
            </div>
          )}
          {!m.replication.applies && (
            <div className="note" style={{ marginTop: 8 }}>
              India-fit gate applies only to “Early” trends, shown here for context;
              this trend is {m.lead_lag.label}, so sliders affect the replication score
              but not the bet.
            </div>
          )}
          {Object.entries(axes).map(([axis, a]) => {
            const val = overrides[axis] ?? a.override ?? a.default
            return (
              <div className="axis" key={axis}>
                <div className="axis-row">
                  <span>
                    {AXIS_LABELS[axis] || axis}{' '}
                    {overrides[axis] != null && <span className="overridden">(override)</span>}
                  </span>
                  <span className="val">{val}</span>
                </div>
                <input type="range" min="0" max="100" value={val}
                  onChange={(e) => onSlide(axis, e.target.value)} />
                <div className="rationale">engine default {a.default}: {a.rationale}</div>
              </div>
            )
          })}
        </div>

        {/* ── 4. WHAT IMPROVES NEXT TIME ─────────────────────────────────── */}
        <div className="panel improve">
          <h4>4 · What improves next time</h4>
          <div className="panel-sub">Where the sources agree vs conflict, and the one thing that would resolve it.</div>
          {d.disagreement.agree.map((a) => <div className="agree" key={a}>✓ {a}</div>)}
          {d.disagreement.conflict.map((c) => <div className="conflict" key={c}>✗ {c}</div>)}
          <div className="resolver">
            <div className="pat">resolver · pattern: {d.disagreement.resolver.pattern}</div>
            <div>{d.disagreement.resolver.action}</div>
          </div>
          <div className="note" style={{ marginTop: 12 }}>
            Buyer feedback (captured locally; would feed the next scoring run):
          </div>
          <div className="fb">
            {['agree', 'disagree', 'would buy'].map((f) => (
              <button key={f} className={feedback[f] ? 'sel' : ''}
                onClick={() => setFeedback({ ...feedback, [f]: !feedback[f] })}>
                {f}
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
