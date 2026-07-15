import React, { useEffect, useRef, useState } from 'react'
import { allocateBudget } from '../api.js'

const TIERS = ['BUY', 'TRIAL', 'WATCH', 'SKIP']
const TIER_LABEL = { BUY: 'Buy', TRIAL: 'Trial', WATCH: 'Watch', SKIP: 'Skip' }
const SNAP = 25000          // drag snap step, ₹
const TRIAL_CAP = 0.08      // mirror of the backend trial cap, for the amber warning
const UNITS_PER_SKU = 50    // order-sheet assumption, stated on the sheet

/* GRID VIEW with a draggable money plan.
   The engine proposes the split; the buyer drags a card's allocation bar to
   disagree. Dragged amounts become PINS the backend honors while re-solving
   the remainder, so money is conserved and every override is explicit.
   Ghost ticks always show the engine's recommendation. */
export default function Slate({ slate, meta, seg, onOpen }) {
  const [budget, setBudget] = useState('')
  const [alloc, setAlloc] = useState(null)
  const [pins, setPins] = useState({})
  const [sheetOpen, setSheetOpen] = useState(false)
  const b = parseBudget(budget)

  useEffect(() => {
    if (!b) { setAlloc(null); return }
    const t = setTimeout(() => {
      allocateBudget(seg, b, Object.keys(pins).length ? pins : undefined)
        .then(setAlloc).catch(() => setAlloc(null))
    }, 250)
    return () => clearTimeout(t)
  }, [b, pins, seg])

  useEffect(() => { setPins({}); setAlloc(null) }, [seg])

  const counts = TIERS.reduce((acc, v) => (
    { ...acc, [v]: slate.filter((t) => t.verdict === v).length }), {})
  const rowFor = (bucket) => alloc?.rows?.find((r) => r.bucket === bucket)
  const pinCap = (bucket) =>
    b ? b - Object.entries(pins).reduce((s, [k, v]) => k === bucket ? s : s + v, 0) : 0

  const setPin = (bucket, amount) =>
    setPins((p) => ({ ...p, [bucket]: Math.max(0, Math.min(amount, pinCap(bucket))) }))
  const clearPin = (bucket) =>
    setPins((p) => { const n = { ...p }; delete n[bucket]; return n })

  return (
    <>
      <div className="legendbar">
        {TIERS.map((v) => (
          <div key={v} className="legend-item">
            <span className="legend-dot" style={{ background: `var(--${v.toLowerCase()})` }} />
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
          <>
            {Object.keys(pins).length > 0 && (
              <button className="fb-btn" onClick={() => setPins({})}>Reset to engine plan</button>
            )}
            <button className="btn-dark" onClick={() => setSheetOpen(true)}>Lock plan</button>
          </>
        )}
        {alloc && <MoneyBar alloc={alloc} />}
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
          const row = rowFor(t.bucket)
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
              <div className="keyword-line">
                {t.buy_instruction ? cap(t.buy_instruction) + '.' : ''}
                {row && (
                  <AllocBar row={row} budget={alloc.budget} verdict={t.verdict}
                    onCommit={(amt) => setPin(t.bucket, amt)}
                    onClear={() => clearPin(t.bucket)} />
                )}
              </div>
            </div>
          )
        })}
      </div>

      <Coverage meta={meta} />

      {sheetOpen && alloc && (
        <OrderSheet alloc={alloc} slate={slate} seg={seg} onClose={() => setSheetOpen(false)} />
      )}
    </>
  )
}

/* The Money Bar: the whole budget as one stacked bar, held-back in grey. */
function MoneyBar({ alloc }) {
  const spent = alloc.rows.filter((r) => r.amount > 0)
  return (
    <div className="moneybar-wrap">
      <div className="moneybar">
        {spent.map((r) => (
          <div key={r.bucket} className={`seg ${r.verdict.toLowerCase()}`}
            style={{ width: `${(r.amount / alloc.budget) * 100}%` }}
            title={`${r.display_name}: ₹${inr(r.amount)}${r.pinned ? ' (pinned)' : ''}`} />
        ))}
        {alloc.held_back > 0 && (
          <div className="seg held" style={{ width: `${(alloc.held_back / alloc.budget) * 100}%` }}
            title={`Held back: ₹${inr(alloc.held_back)}`} />
        )}
      </div>
      <div className="alloc-note">
        ₹{inr(alloc.allocated)} allocated across {spent.length} trends
        {alloc.held_back > 0 ? `, ₹${inr(alloc.held_back)} held back` : ''}
        {alloc.moved_from_engine > 0 && (
          <span className="moved"> · you moved ₹{inr(alloc.moved_from_engine)} from the engine plan (logged)</span>
        )}
        {alloc.held_back_note ? ` · ${alloc.held_back_note}` : ''}
      </div>
    </div>
  )
}

/* Draggable allocation bar on each card. Ghost tick = engine recommendation.
   Dragging commits a PIN; guardrails color the fill when the buyer goes
   against the engine (spend on Watch/Skip, or above the trial cap). */
function AllocBar({ row, budget, verdict, onCommit, onClear }) {
  const trackRef = useRef(null)
  const [dragVal, setDragVal] = useState(null)
  const val = dragVal ?? row.amount
  const pct = Math.min(100, (val / budget) * 100)
  const ghostPct = Math.min(100, (row.engine_amount / budget) * 100)
  const overCap = verdict === 'TRIAL' && val > TRIAL_CAP * budget
  const overVerdict = (verdict === 'WATCH' || verdict === 'SKIP') && val > 0
  const cls = overVerdict ? 'over-red' : overCap ? 'over-amber' : verdict.toLowerCase()

  const valueAt = (clientX) => {
    const rect = trackRef.current.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    return Math.round((frac * budget) / SNAP) * SNAP
  }
  const onPointerDown = (e) => {
    e.stopPropagation()
    e.preventDefault()
    trackRef.current.setPointerCapture(e.pointerId)
    setDragVal(valueAt(e.clientX))
  }
  const onPointerMove = (e) => {
    if (dragVal === null) return
    setDragVal(valueAt(e.clientX))
  }
  const onPointerUp = (e) => {
    if (dragVal === null) return
    e.stopPropagation()
    onCommit(dragVal)
    setDragVal(null)
  }

  return (
    <div className="allocbar" onClick={(e) => e.stopPropagation()}>
      <div className="allocbar-head">
        <span className="lbl">
          {row.pinned ? '📌 ' : ''}allocation
          {overVerdict && <span className="warn-red"> · engine says no spend here</span>}
          {!overVerdict && overCap && <span className="warn-amber"> · above trial cap</span>}
        </span>
        <span className="amt">₹{inr(val)}{dragVal !== null ? ' …' : ''}</span>
      </div>
      <div ref={trackRef} className="alloc-track"
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}>
        <div className={`alloc-fill ${cls}`} style={{ width: `${pct}%` }} />
        <div className="ghost-tick" style={{ left: `${ghostPct}%` }}
          title={`Engine recommends ₹${inr(row.engine_amount)}`} />
      </div>
      <div className="allocbar-foot">
        <span className="hint">drag to adjust · engine mark at ₹{inr(row.engine_amount)}</span>
        {row.pinned && <button className="unpin" onClick={onClear}>reset</button>}
      </div>
    </div>
  )
}

/* Lock plan → the artifact a sales team actually uses: a printable order sheet. */
function OrderSheet({ alloc, slate, seg, onClose }) {
  const byBucket = Object.fromEntries(slate.map((t) => [t.bucket, t]))
  const spend = alloc.rows.filter((r) => r.amount > 0)
  const today = new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })
  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="ordersheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <div>
            <div className="wordmark" style={{ fontSize: 22 }}>Order plan</div>
            <div className="tagline" style={{ marginTop: 2 }}>
              {seg?.category} / {seg?.sub_category} · {today} · budget ₹{inr(alloc.budget)}
            </div>
          </div>
          <div className="sheet-actions">
            <button className="fb-btn" onClick={() => window.print()}>Print / save PDF</button>
            <button className="fb-btn" onClick={onClose}>Close</button>
          </div>
        </div>
        <table className="sheet-table">
          <thead>
            <tr><th>Trend</th><th>Verdict</th><th>Amount</th><th>~SKUs</th><th>Buy instruction</th><th>Basis</th></tr>
          </thead>
          <tbody>
            {spend.map((r) => {
              const t = byBucket[r.bucket]
              const mp = t?.median_price_inr
              const skus = mp ? Math.max(1, Math.round(r.amount / (mp * UNITS_PER_SKU))) : null
              return (
                <tr key={r.bucket}>
                  <td className="tname">{r.display_name}</td>
                  <td><span className={`pill ${r.verdict.toLowerCase()}`}>{TIER_LABEL[r.verdict]}</span></td>
                  <td className="mono">₹{inr(r.amount)}</td>
                  <td className="mono">{skus ?? 'n/a'}</td>
                  <td>{t?.buy_instruction ? cap(t.buy_instruction) : ''}</td>
                  <td className="basis">{r.pinned ? 'Your pin' : 'Engine'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div className="sheet-foot">
          ₹{inr(alloc.allocated)} allocated{alloc.held_back > 0 ? `, ₹${inr(alloc.held_back)} held back` : ''}
          {alloc.moved_from_engine > 0 ? ` · ₹${inr(alloc.moved_from_engine)} moved from the engine plan by the buyer` : ''}.
          SKU counts assume ~{UNITS_PER_SKU} units per SKU at each trend's median Indian price;
          adjust to your depth policy. All numbers recompute from the files in /data.
        </div>
      </div>
    </div>
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
