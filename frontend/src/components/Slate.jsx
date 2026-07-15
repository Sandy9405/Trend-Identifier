import React, { useEffect, useState } from 'react'
import { allocateBudget } from '../api.js'

const TIERS = ['BUY', 'TRIAL', 'WATCH', 'SKIP']
const TIER_LABEL = { BUY: 'Buy', TRIAL: 'Trial', WATCH: 'Watch', SKIP: 'Skip' }

/* GRID VIEW, per the design handoff: legend/control bar (tier counts +
   open-to-buy input) above a responsive card grid. Card face: name, tier
   badge, mono score, tier-colored bar, direction line, italic serif verdict
   sentence, keyword footer. Everything computed by the backend. */
export default function Slate({ slate, meta, seg, onOpen }) {
  const [budget, setBudget] = useState('')
  const [alloc, setAlloc] = useState(null)

  useEffect(() => {
    const b = parseBudget(budget)
    if (!b) { setAlloc(null); return }
    const t = setTimeout(() => {
      allocateBudget(seg, b).then(setAlloc).catch(() => setAlloc(null))
    }, 350)
    return () => clearTimeout(t)
  }, [budget, seg])

  const counts = TIERS.reduce((acc, v) => (
    { ...acc, [v]: slate.filter((t) => t.verdict === v).length }), {})
  const amountFor = (bucket) => alloc?.rows?.find((r) => r.bucket === bucket)

  return (
    <>
      <div className="legendbar">
        {TIERS.map((v) => (
          <div key={v} className="legend-item">
            <span className={`legend-dot`} style={{ background: `var(--${v.toLowerCase()})` }} />
            <span className="legend-label">{TIER_LABEL[v]}</span>
            <span className="legend-count">{counts[v]} trends</span>
          </div>
        ))}
        <div className="legend-spacer" />
        <div className="budget-wrap">
          <span className="lbl">Open-to-buy this cycle</span>
          <span className="rupee">₹</span>
          <input value={budget} placeholder="20,00,000" inputMode="numeric"
            onChange={(e) => setBudget(e.target.value)} />
        </div>
        {alloc && (
          <div className="alloc-note">
            ₹{inr(alloc.allocated)} allocated across {alloc.rows.filter((r) => r.amount > 0).length} trends
            {alloc.held_back > 0 ? `, ₹${inr(alloc.held_back)} held back` : ''}
            {alloc.held_back_note ? ` (${alloc.held_back_note})` : ''}. {alloc.method}
          </div>
        )}
      </div>

      {slate.length === 0 && (
        <div className="empty-note">
          No silhouette bucket in this segment reaches the qualification bar
          ({meta?.qualification_rule}); too little data here to call anything a trend.
        </div>
      )}

      <div className="grid">
        {slate.map((t) => {
          const tier = t.verdict.toLowerCase()
          const a = amountFor(t.bucket)
          return (
            <div key={t.bucket} className={`tcard ${tier}`} onClick={() => onOpen(t.bucket)}>
              <div className="tcard-top">
                <div className="tcard-name">{t.display_name}</div>
                <span className={`pill ${tier}`}>{TIER_LABEL[t.verdict]}</span>
              </div>
              <div className="score-row">
                <span className={`score ${tier}`}>{t.face_confidence}</span>
                <span className="score-cap">/100 confidence</span>
              </div>
              <div className="bar"><div className={tier} style={{ width: `${t.face_confidence}%` }} /></div>
              <div className="direction">
                {t.direction.short}{t.direction.single_source ? ' · 1 source' : ''}
              </div>
              {t.merchant_line && <p className="blurb">{t.merchant_line}</p>}
              {(t.buy_instruction || a) && (
                <div className="keyword-line">
                  {t.buy_instruction ? cap(t.buy_instruction) + '.' : ''}
                  {a && (
                    <div className={`alloc-line ${a.amount === 0 ? 'zero' : ''}`}>
                      {a.amount > 0 ? `₹${inr(a.amount)} allocated` : 'No spend this cycle'}
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <Coverage meta={meta} />
    </>
  )
}

/* Data-coverage footnote: sources, auto-detected limitations, missing roles. */
function Coverage({ meta }) {
  if (!meta) return null
  return (
    <div className="coverage">
      {meta.platforms.map((p) => (
        <span key={p.file}>
          <b>{p.key}</b> ({p.effective_roles.join(' + ') || 'no usable role'}, {p.products_used} items)
          {p.declared_roles.length !== p.effective_roles.length &&
            <span className="warn"> demand auto-disabled</span>}
          {' · '}
        </span>
      ))}
      {meta.west_sources && (
        <span className={meta.west_sources.count < 2 ? 'warn' : ''}>
          {meta.west_sources.count} Western source{meta.west_sources.count === 1 ? '' : 's'}.{' '}
        </span>
      )}
      {meta.missing_roles?.length > 0 && (
        <span className="warn">Missing roles: {meta.missing_roles.join(', ')}. </span>
      )}
      {meta.products_outside_segment > 0 && (
        <span>{meta.products_outside_segment} items belong to other segments (nothing is discarded). </span>
      )}
      <span>All numbers recompute from the files in /data.</span>
    </div>
  )
}

function parseBudget(s) {
  const n = parseFloat(String(s).replace(/[^\d.]/g, ''))
  return Number.isFinite(n) && n > 0 ? n : null
}
export function inr(n) {
  if (n >= 10000000) return `${(n / 10000000).toFixed(2)}Cr`
  if (n >= 100000) return `${(n / 100000).toFixed(1)}L`
  return n.toLocaleString('en-IN')
}
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1)
