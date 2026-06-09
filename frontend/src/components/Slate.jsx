import React from 'react'
import { BetBadge, LeadLagTag, RawVsAdjusted } from './shared.jsx'

/* LANDING — the ranked slate. Every card is rendered from /api/slate;
   nothing on this page is hardcoded. */
export default function Slate({ slate, meta, onOpen }) {
  return (
    <>
      <div className="header">
        <h1>Trend Bet Workbench — Women's Tops, India</h1>
        <div className="sub">
          Decision support under uncertainty: you commit inventory <em>before</em> demand is
          obvious. Each trend below is scored from the raw scrapes in <code>/data</code>, with
          every distortion shown, never hidden. The blue bar is what the signals claim; the
          second bar is what survives the honesty checks. Bet the second bar.
        </div>
      </div>

      <Coverage meta={meta} />

      <div className="grid">
        {slate.map((t, i) => (
          <div key={t.bucket} className="card" onClick={() => onOpen(t.bucket)}>
            <div className="row1">
              <h3><span className="rank">#{i + 1}</span>{t.display_name}</h3>
              <BetBadge label={t.bet.label} />
            </div>
            <div><LeadLagTag label={t.lead_lag} /></div>
            <div className="why">{t.why}</div>
            <div className="row1">
              <div>
                <div className="conf-num">{t.confidence.adjusted}</div>
                <div className="conf-label">adjusted confidence · {t.counts.total} products</div>
              </div>
            </div>
            <RawVsAdjusted base={t.confidence.base} adjusted={t.confidence.adjusted} compact />
          </div>
        ))}
      </div>
    </>
  )
}

/* Honest data-coverage strip — driven by /api/meta, including auto-detected
   limitations (e.g. a platform whose review counts are all zero). */
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
      {meta.missing_roles.length > 0 && (
        <span className="warn">missing roles: {meta.missing_roles.join(', ')}</span>
      )}
      <span>{meta.products_unbucketed} of {meta.products_total} products matched no silhouette ("other")</span>
      <span>{meta.qualification_rule}</span>
    </div>
  )
}
