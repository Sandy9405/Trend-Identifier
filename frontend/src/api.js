// Thin API client. EVERYTHING the UI shows comes from these endpoints —
// no trend names, scores, labels or segment lists live in the frontend.
const j = (r) => {
  if (!r.ok) throw new Error(`API ${r.status}`)
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
