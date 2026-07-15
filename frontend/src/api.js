// Thin API client. EVERYTHING the UI shows comes from these endpoints,
// no trend names, scores, labels or segment lists live in the frontend.
const j = async (r) => {
  if (!r.ok) {
    // surface the backend's structured rejection (reason + how_to_fix) verbatim
    let msg = `API ${r.status}`
    try {
      const d = (await r.json()).detail
      if (typeof d === 'string') msg = d
      else if (d?.reason) {
        msg = d.reason + (d.how_to_fix ? `, ${d.how_to_fix}` : '')
        if (d.known_patterns) msg += ` Accepted filename patterns: ${
          Object.entries(d.known_patterns).map(([k, g]) => `${k}: ${[].concat(g).join(' | ')}`).join(' · ')}`
      }
    } catch { /* keep generic message */ }
    throw new Error(msg)
  }
  return r.json()
}

const qs = (seg) =>
  seg ? `?category=${encodeURIComponent(seg.category)}&sub_category=${encodeURIComponent(seg.sub_category)}` : ''

export const getSegments = () => fetch('/api/segments').then(j)
export const getSlate = (seg) => fetch(`/api/slate${qs(seg)}`).then(j)
export const getTrend = (bucket, seg) => fetch(`/api/trend/${bucket}${qs(seg)}`).then(j)
export const getMeta = (seg) => fetch(`/api/meta${qs(seg)}`).then(j)
export const recompute = (bucket, seg, overrides) =>
  fetch(`/api/recompute/${bucket}${qs(seg)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(overrides),
  }).then(j)

// Budget allocation: open-to-buy ₹ → per-trend money plan for the segment.
// `pins` = {bucket: rupees} the buyer dragged and locked; the engine re-solves
// the remainder around them (money conserved, deviation reported).
export const allocateBudget = (seg, budget, pins, adjustments) =>
  fetch(`/api/allocate${qs(seg)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ budget, pins, adjustments }),
  }).then(j)

// Data management: push a new/replacement scrape file → backend re-ingests
// and returns the fresh segment list; the UI then re-renders from it.
export const uploadDataFile = (file) => {
  const form = new FormData()
  form.append('file', file)
  return fetch('/api/upload', { method: 'POST', body: form }).then(j)
}
export const reloadData = () => fetch('/api/reload', { method: 'POST' }).then(j)
