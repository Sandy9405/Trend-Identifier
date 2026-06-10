import React, { useEffect, useState } from 'react'
import { allocateBudget } from '../api.js'

/* LANDING, a traffic-light board. Each tile FACE shows only the decision:
   trend name, ONE verdict (BUY/TRIAL/WATCH), ONE number (adjusted,
   distortion-honest confidence, never raw), ONE West→India direction.
   Everything else lives behind the click. All content from /api/slate. */
export default function Slate({ slate, meta, seg, onOpen }) {
  const [budget, setBudget] = useState('')
  const [alloc, setAlloc] = useState(null)

  useEffect(() => {
    const b = parseFloat(budget)
    if (!b || b <= 0) { setAlloc(null); return }
    const t = setTimeout(() => {
      allocateBudget(seg, b).then(setAlloc).catch(() => setAlloc(null))
    }, 350)
    return () => clearTimeout(t)
  }, [budget, seg])

  const amountFor = (bucket) =>
    alloc?.rows?.find((r) => r.bucket === bucket)

  return (
    <>
      <div className="header">
        <h1>
          Trend Bet Workbench
          {meta?.segment && `, ${meta.segment.category} / ${meta.segment.sub_category}`}
        </h1>
        <div className="sub">
          One verdict, one honest number, one direction per trend, scannable in
          three seconds. Click any tile for the full evidence: raw vs adjusted,
          distortions, India-fit sliders and the resolver.
        </div>
      </div>

      <Coverage meta={meta} />

      <div className="budgetbar">
        <label>
          Budget this cycle (₹)
          <input type="number" min="0" step="100000" placeholder="e.g. 2000000"
            value={budget} onChange={(e) => setBudget(e.target.value)} />
        </label>
        {alloc && (
          <span className="alloc-summary">
            ₹{fmtINR(alloc.allocated)} allocated · ₹{fmtINR(alloc.held_back)} held back
            {alloc.held_back_note ? `, ${alloc.held_back_note}` : ''} · {alloc.method}
          </span>
        )}
        {!alloc && <span className="note">enter an open-to-buy amount to turn the slate into a money plan</span>}
      </div>

      {slate.length === 0 && (
        <div className="loading">
          No silhouette bucket in this segment reaches the qualification bar
          ({meta?.qualification_rule}), too little data here to call anything a trend.
        </div>
      )}

      <div className="grid">
        {slate.map((t) => {
          const a = amountFor(t.bucket)
          return (
            <div key={t.bucket} className={`card face ${t.verdict.toLowerCase()}`}
              onClick={() => onOpen(t.bucket)}>
              <div className="row1">
                <h3>{t.display_name}</h3>
                <span className={`badge ${t.verdict.toLowerCase()}`}>{t.verdict}</span>
              </div>
              <div className="face-num">{t.face_confidence}</div>
              <div className="face-dir">
                <span className="glyph">{t.direction.glyph}</span> {t.direction.short}
                {t.direction.caution && <span className="src-caution"> {t.direction.caution}</span>}
              </div>
              {t.merchant_line && <div className="face-merchant">“{t.merchant_line}”</div>}
              {t.buy_instruction && (
                <div className="face-instruction">
                  {t.display_name.toLowerCase()}, {t.buy_instruction}
                </div>
              )}
              {a && (
                <div className={`face-alloc ${a.amount === 0 ? 'zero' : ''}`}>
                  {a.amount > 0 ? `₹${fmtINR(a.amount)}` : 'no spend, monitoring'}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </>
  )
}

function fmtINR(n) {
  if (n >= 100000) return `${(n / 100000).toFixed(1)}L`
  if (n >= 1000) return `${Math.round(n / 1000)}k`
  return String(n)
}

/* Honest data-coverage strip, driven by /api/meta, including auto-detected
   limitations and how many Western sources back the directions. */
function Coverage({ meta }) {
  if (!meta) return null
  return (
    <div className="coverage">
      {meta.platforms.map((p) => (
        <span key={p.file}>
          <b>{p.key}</b> ({p.effective_roles.join(' + ') || 'no usable role'}) ·{' '}
          {p.products_used} products
          {p.declared_roles.length !== p.effective_roles.length && (
            <span className="warn"> · demand signal auto-disabled (rating counts all zero)</span>
          )}
        </span>
      ))}
      {meta.west_sources && (
        <span className={meta.west_sources.count < 2 ? 'warn' : ''}>
          {meta.west_sources.count} Western source{meta.west_sources.count === 1 ? '' : 's'}, {meta.west_sources.note}
        </span>
      )}
      {meta.missing_roles?.length > 0 && (
        <span className="warn">missing roles: {meta.missing_roles.join(', ')}</span>
      )}
      {meta.products_outside_segment > 0 && (
        <span>{meta.products_outside_segment} products belong to other segments (use the dropdowns, nothing is discarded)</span>
      )}
    </div>
  )
}
