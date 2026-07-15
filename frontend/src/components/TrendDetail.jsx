import React, { useMemo, useState } from 'react'
import { recompute } from '../api.js'

const TIER_LABEL = { BUY: 'Buy', TRIAL: 'Trial', WATCH: 'Watch', SKIP: 'Skip' }
const AXIS_LABELS = {
  climate_fit: 'Climate fit',
  modesty_fit: 'Modesty fit',
  occasion_fit: 'Occasion fit',
  price_band_fit: 'Price-band fit',
}

/* DETAIL VIEW, per the design handoff.
   Rich tier (Buy/Trial): evidence + watch-outs two-column, then "Your move"
   with fabric/colour mix and India-fit sliders (live recompute against the
   real backend). Sparse tier (Watch/Skip): a single "Evidence so far" note.
   Both end with "Confidence & feedback" (single-select toggle). */
export default function TrendDetail({ trend, seg, onBack }) {
  const [live, setLive] = useState(null)          // recomputed payload after overrides
  const [overrides, setOverrides] = useState({})
  const [feedback, setFeedback] = useState(null)  // single-select per design
  const d = live || trend
  const m = d.metrics
  const tier = (d.verdict || 'WATCH').toLowerCase()
  const rich = d.verdict === 'BUY' || d.verdict === 'TRIAL'
  const score = Math.round(d.confidence.adjusted)
  const dirLine = trend.direction.short
    + (trend.direction.single_source ? ' · 1 source' : '')

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

  const evidence = [
    ['Your own sales (POS)', m.own_sales?.value],
    ['Demand strength (India)', m.demand_strength.value],
    ['Supply conviction (India)', m.supply_conviction.value],
    ['West signal (supply only)', m.west_signal.value],
    ['Cross-platform agreement', m.cross_platform_agreement.value],
  ].filter(([, v]) => v !== null && v !== undefined)

  const derivations = [
    m.demand_strength, m.supply_conviction, m.west_signal,
    m.cross_platform_agreement, m.lead_lag, m.discount_penalty,
    m.replication, d.confidence,
  ].map((x) => x?.derivation).filter(Boolean)

  return (
    <div>
      <span className="back" onClick={onBack}>← All trends</span>

      {/* header card */}
      <div className="dcard head">
        <div className="dhead-row">
          <div className="dtitle">{trend.display_name}</div>
          <span className={`pill ${tier}`}>{TIER_LABEL[d.verdict]}</span>
          <span className="dhead-dir">{dirLine}</span>
          {live && <span className="live-note">recomputed with your overrides</span>}
        </div>
        <div className="score-row" style={{ marginTop: 18 }}>
          <span className={`score lg ${tier}`}>{score}</span>
          <span className="score-cap" style={{ fontSize: 13 }}>/100, {d.bet.caption}</span>
        </div>
        <div className="bar lg"><div className={tier} style={{ width: `${score}%` }} /></div>
        <p className={`dquote ${tier}`}>{d.merchant_line}</p>
      </div>

      {rich ? (
        <>
          <div className="two-col">
            <div className="dcard" style={{ margin: 0 }}>
              <div className="sec-label">What looks real</div>
              {evidence.map(([label, value]) => (
                <div className="ev-row" key={label}>
                  <span className="lbl">{label}</span>
                  <span className="val">{Number(value).toFixed(1)}</span>
                </div>
              ))}
              {trend.trajectory && (
                <div className="ev-row">
                  <span className="lbl">Trajectory (listing-date proxy)</span>
                  <span className="val">{trend.trajectory.arrow}</span>
                </div>
              )}
              <details className="deriv">
                <summary>How these numbers were computed</summary>
                {derivations.map((t, i) => <p key={i}>{t}</p>)}
              </details>
            </div>
            <div className="dcard" style={{ margin: 0 }}>
              <div className="sec-label warn">What could mislead</div>
              {d.watchouts.map((w) => (
                <div className="watchout" key={w.label}>
                  <div className="lbl">{w.label}</div>
                  <div className="note">{w.note}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="dcard">
            <div className="sec-label">Your move</div>
            <div className="move-action">
              {d.bet.band}
              {trend.lag_estimate && ` Western lead of ${trend.lag_estimate.window} for this silhouette (estimate).`}
            </div>
            {trend.subtrends?.buy_instruction && (
              <div className="move-keywords">
                {cap(trend.subtrends.buy_instruction)}.
              </div>
            )}

            {(trend.subtrends?.fabrics?.length > 0 || trend.subtrends?.colors?.length > 0) && (
              <div className="mix-grid">
                <MixColumn title="Fabric mix" rows={trend.subtrends.fabrics} cls="fabric" />
                <MixColumn title="Colour mix" rows={trend.subtrends.colors} cls="color" />
              </div>
            )}

            <div className="sec-label" style={{ marginBottom: 12 }}>India-fit overrides</div>
            {Object.entries(m.replication.axes).map(([axis, a]) => {
              const val = overrides[axis] ?? a.override ?? a.default
              return (
                <div className="slider-row" key={axis}>
                  <div className="slider-head">
                    <span>{AXIS_LABELS[axis] || axis}</span>
                    <span className="val">{val}</span>
                  </div>
                  <input type="range" min="0" max="100" value={val}
                    onChange={(e) => onSlide(axis, e.target.value)} />
                  <div className="slider-note">{a.rationale}</div>
                </div>
              )
            })}
          </div>
        </>
      ) : (
        <div className="dcard">
          <div className="sec-label">Evidence so far</div>
          <div className="sparse-note">
            {d.why}. {d.disagreement.conflict[0] || d.disagreement.agree[0] || ''}{' '}
            {d.bet.band}
          </div>
        </div>
      )}

      <div className="dcard">
        <div className="sec-label">Confidence &amp; feedback</div>
        <div className="conf-note">{d.confidence_note}</div>
        <div className="fb-row">
          {['Agree', 'Disagree', 'Would buy'].map((label) => (
            <button key={label}
              className={`fb-btn ${feedback === label ? `active ${tier}` : ''}`}
              onClick={() => setFeedback(feedback === label ? null : label)}>
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function MixColumn({ title, rows, cls }) {
  if (!rows?.length) return null
  return (
    <div>
      <div className="mix-title">{title}</div>
      {rows.map((r) => (
        <div className="mix-row" key={r.name}>
          <span className="lbl">{cap(r.name)}</span>
          <div className="track"><div className={cls} style={{ width: `${r.share}%` }} /></div>
          <span className="pct">{r.share}%</span>
        </div>
      ))}
    </div>
  )
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1)
