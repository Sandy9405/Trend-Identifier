import React from 'react'

export function BetBadge({ label }) {
  const cls = label === 'Deeper Buy' ? 'deeper' : label === 'Small Trial' ? 'trial' : 'monitor'
  return <span className={`badge ${cls}`}>{label}</span>
}

export function LeadLagTag({ label }) {
  const cls = label.startsWith('Early') ? 'early'
    : label === 'Landed' ? 'landed'
    : label.startsWith('Late') ? 'late'
    : label.startsWith('India') ? 'india' : ''
  return <span className={`tag ${cls}`}>{label}</span>
}

/* Raw vs adjusted, the distortion made visible. Two lanes: what the signals
   said before penalties (raw/base) vs what survives honesty checks (adjusted). */
export function RawVsAdjusted({ base, adjusted, compact }) {
  const delta = Math.round((base - adjusted) * 10) / 10
  return (
    <div className={`rvabar ${compact ? 'compact' : ''}`}>
      <div className="lbl"><span>raw signal</span><span>{base}</span></div>
      <div className="lane"><div className="fill raw" style={{ width: `${base}%` }} /></div>
      <div className="lbl">
        <span>after distortion checks{delta > 0 && <span className="delta"> (−{delta})</span>}</span>
        <span>{adjusted}</span>
      </div>
      <div className="lane"><div className={`fill adj ${delta > 0 ? 'cut' : ''}`} style={{ width: `${adjusted}%` }} /></div>
    </div>
  )
}

export function Derivation({ text }) {
  if (!text) return null
  return (
    <details className="deriv">
      <summary>how this number was computed</summary>
      <p>{text}</p>
    </details>
  )
}
