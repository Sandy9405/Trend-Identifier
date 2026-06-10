import React, { useEffect, useMemo, useRef, useState } from 'react'
import { getSegments, getSlate, getTrend, uploadDataFile } from './api.js'
import Slate from './components/Slate.jsx'
import TrendDetail from './components/TrendDetail.jsx'

/* Tiny hash router: #/ = slate, #/trend/<bucket> = detail.
   The selected segment (category / sub-category) scopes every API call;
   the dropdown options come from /api/segments, detected from the data,
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

  // After an upload the backend has re-ingested: adopt the fresh segment list
  // and default, drop any open detail page, and let the slate refetch.
  const onDataChanged = (resp) => {
    window.location.hash = '#/'
    setSegments(resp.segments)
    setSeg(resp.default)
    setSlateData(null)
  }

  if (error) return (
    <div className="container error">
      Backend API not responding ({error}). Locally: is uvicorn running on :8000?
      On Vercel: check the function logs for /api/index and that the project's
      Root Directory is the repo root.
    </div>
  )
  if (!segments || !seg) return <div className="container loading">Detecting segments in /data…</div>

  return (
    <div className="container">
      <SegmentPicker segments={segments} seg={seg} onChange={changeSegment}
        onDataChanged={onDataChanged} />
      {bucket
        ? (trend
            ? <TrendDetail key={`${seg.category}/${seg.sub_category}/${bucket}`}
                trend={trend} seg={seg}
                onBack={() => { window.location.hash = '#/' }} />
            : <div className="loading">Loading {bucket}…</div>)
        : (slateData
            ? <Slate slate={slateData.slate} meta={slateData.meta} seg={seg}
                onOpen={(b) => { window.location.hash = `#/trend/${b}` }} />
            : <div className="loading">Computing slate for {seg.category} / {seg.sub_category}…</div>)}
    </div>
  )
}

/* Category + sub-category dropdowns, populated from the data. Mixed scrapes
   (e.g. a "tops" search that returned sarees and jeans) are scoped, not
   silently discarded, every row is reachable through some segment. */
function SegmentPicker({ segments, seg, onChange, onDataChanged }) {
  const categories = useMemo(
    () => [...new Set(segments.map((s) => s.category))], [segments])
  const subs = segments.filter((s) => s.category === seg.category)
  const fileRef = useRef(null)
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState(null)

  const onCat = (category) => {
    // keep sub-category if it exists under the new category, else take the largest
    const under = segments.filter((s) => s.category === category)
    const keep = under.find((s) => s.sub_category === seg.sub_category) || under[0]
    onChange({ category, sub_category: keep.sub_category })
  }

  const onFile = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setUploading(true)
    setNotice(null)
    try {
      const resp = await uploadDataFile(file)
      const deltas = (resp.segment_changes || [])
        .map((c) => `${c.category}/${c.sub_category} ${c.products_delta > 0 ? '+' : ''}${c.products_delta}`)
        .join(', ')
      setNotice({
        kind: 'ok',
        text: `Accepted ${resp.saved_as} → ${resp.matched_adapter} (${resp.roles.join(', ')}): `
          + `${resp.products_parsed}/${resp.rows_in_file} rows usable. `
          + (deltas ? `Segments changed: ${deltas}. ` : 'No segment counts changed, duplicates of existing products. ')
          + resp.persistence,
      })
      onDataChanged(resp)
    } catch (err) {
      setNotice({ kind: 'err', text: `Upload rejected, nothing was changed. ${err.message || err}` })
    } finally {
      setUploading(false)
    }
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
      <button className="upload-btn" disabled={uploading}
        onClick={() => fileRef.current?.click()}>
        {uploading ? 'validating…' : 'upload data file'}
      </button>
      <input ref={fileRef} type="file" accept=".json,.csv" style={{ display: 'none' }}
        onChange={onFile} />
      {!notice && <span className="note">segments detected from the data files, not predefined</span>}
      {notice && (
        <div className={`upload-notice ${notice.kind}`}>
          {notice.text}
          <button className="dismiss" onClick={() => setNotice(null)}>✕</button>
        </div>
      )}
    </div>
  )
}
