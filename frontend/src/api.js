// Thin API client. EVERYTHING the UI shows comes from these endpoints —
// no trend names, scores or labels live in the frontend.
const j = (r) => {
  if (!r.ok) throw new Error(`API ${r.status}`)
  return r.json()
}

export const getSlate = () => fetch('/api/slate').then(j)
export const getTrend = (bucket) => fetch(`/api/trend/${bucket}`).then(j)
export const getMeta = () => fetch('/api/meta').then(j)
export const recompute = (bucket, overrides) =>
  fetch(`/api/recompute/${bucket}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(overrides),
  }).then(j)
