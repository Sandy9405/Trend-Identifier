import React, { useEffect, useMemo, useState } from 'react'
import { getSegments, getSlate, getTrend } from './api.js'
import Slate from './components/Slate.jsx'
import TrendDetail from './components/TrendDetail.jsx'

/* Tiny hash router: #/ = slate, #/trend/<bucket> = detail.
   The selected segment (category / sub-category) scopes every API call;
   the dropdown options come from /api/segments — detected from the data,
   never predefined. */
export default function App() {
  const [segments, setSegments] = useState(null)
  const [seg, setSeg] = useState(null)
  const [slateData, setSlateData] = useState(null)
  const [trend, setTrend] = useState(null)
  const [error, setError] = useState(null)
  const [route, setRoute] = useState(window.location.hash)

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    getSegments()
      .then((d) => { setSegments(d.segments); setSeg(d.default) })
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    if (!seg) return
    setSlateData(null)
    getSlate(seg).then(setSlateData).catch((e) => setError(String(e)))
  }, [seg])

  const bucket = route.startsWith('#/trend/') ? route.slice('#/trend/'.length) : null

  useEffect(() => {
    if (!bucket || !seg) { setTrend(null); return }
    setTrend(null)
    getTrend(bucket, seg).then(setTrend).catch((e) => setError(String(e)))
  }, [bucket, seg])

  const changeSegment = (next) => {
    window.location.hash = '#/'   // a bucket may not exist in the new segment
    setSeg(next)
  }

  if (error) return <div className="container error">Backend unreachable or segment empty: {error}. Is uvicorn running on :8000?</div>
  if (!segments || !seg) return <div className="container loading">Detecting segments in /data…</div>

  return (
    <div className="container">
      <SegmentPicker segments={segments} seg={seg} onChange={changeSegment} />
      {bucket
        ? (trend
            ? <TrendDetail key={`${seg.category}/${seg.sub_category}/${bucket}`}
                trend={trend} seg={seg}
                onBack={() => { window.location.hash = '#/' }} />
            : <div className="loading">Loading {bucket}…</div>)
        : (slateData
            ? <Slate slate={slateData.slate} meta={slateData.meta}
                onOpen={(b) => { window.location.hash = `#/trend/${b}` }} />
            : <div className="loading">Computing slate for {seg.category} / {seg.sub_category}…</div>)}
    </div>
  )
}

/* Category + sub-category dropdowns, populated from the data. Mixed scrapes
   (e.g. a "tops" search that returned sarees and jeans) are scoped, not
   silently discarded — every row is reachable through some segment. */
function SegmentPicker({ segments, seg, onChange }) {
  const categories = useMemo(
    () => [...new Set(segments.map((s) => s.category))], [segments])
  const subs = segments.filter((s) => s.category === seg.category)

  const onCat = (category) => {
    // keep sub-category if it exists under the new category, else take the largest
    const under = segments.filter((s) => s.category === category)
    const keep = under.find((s) => s.sub_category === seg.sub_category) || under[0]
    onChange({ category, sub_category: keep.sub_category })
  }

  return (
    <div className="segbar">
      <label>
        category{' '}
        <select value={seg.category} onChange={(e) => onCat(e.target.value)}>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </label>
      <label>
        sub-category{' '}
        <select value={seg.sub_category}
          onChange={(e) => onChange({ ...seg, sub_category: e.target.value })}>
          {subs.map((s) => (
            <option key={s.sub_category} value={s.sub_category}>
              {s.sub_category} ({s.count})
            </option>
          ))}
        </select>
      </label>
      <span className="note">segments detected from the files in /data — not predefined</span>
    </div>
  )
}
