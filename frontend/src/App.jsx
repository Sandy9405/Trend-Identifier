import React, { useEffect, useMemo, useRef, useState } from 'react'
import { getSegments, getSlate, getTrend, uploadDataFile } from './api.js'
import Slate from './components/Slate.jsx'
import TrendDetail from './components/TrendDetail.jsx'

/* Single page, two view states: grid (default) and detail, per the design
   handoff. Hash routing keeps the states linkable (#/ and #/trend/<bucket>).
   Everything rendered comes from the scoring backend; the header's segment
   selects and upload button are fully wired. */
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
      .catch((e) => setError(String(e.message || e)))
  }, [])

  useEffect(() => {
    if (!seg) return
    setSlateData(null)
    getSlate(seg).then(setSlateData).catch((e) => setError(String(e.message || e)))
  }, [seg])

  const bucket = route.startsWith('#/trend/') ? route.slice('#/trend/'.length) : null

  useEffect(() => {
    if (!bucket || !seg) { setTrend(null); return }
    setTrend(null)
    getTrend(bucket, seg).then(setTrend).catch(() => { window.location.hash = '#/' })
  }, [bucket, seg])

  const changeSegment = (next) => {
    window.location.hash = '#/'
    setSeg(next)
  }

  const onDataChanged = (resp) => {
    window.location.hash = '#/'
    setSegments(resp.segments)
    setSeg(resp.default)
    setSlateData(null)
  }

  return (
    <div className="page">
      <Header segments={segments} seg={seg} onChange={changeSegment}
        onDataChanged={onDataChanged} />
      {error ? (
        <div className="error-card">
          <b>Scoring run unavailable.</b><br />
          {error}. Locally, check uvicorn is running on :8000; on Vercel, check
          the function logs for /api/index.
        </div>
      ) : bucket ? (
        trend
          ? <TrendDetail key={`${seg?.category}/${seg?.sub_category}/${bucket}`}
              trend={trend} seg={seg}
              onBack={() => { window.location.hash = '#/' }} />
          : <GridSkeleton n={3} />
      ) : slateData ? (
        <Slate slate={slateData.slate} meta={slateData.meta} seg={seg}
          onOpen={(b) => { window.location.hash = `#/trend/${b}` }} />
      ) : (
        <GridSkeleton n={6} />
      )}
    </div>
  )
}

/* Loading skeleton for the grid (per handoff: no unmodeled loading states). */
function GridSkeleton({ n }) {
  return (
    <div className="grid">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="skel-card">
          <div className="skel-line w60" />
          <div className="skel-num" />
          <div className="skel-line w80" />
          <div className="skel-line w40" />
        </div>
      ))}
    </div>
  )
}

function Header({ segments, seg, onChange, onDataChanged }) {
  const fileRef = useRef(null)
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState(null)

  const categories = useMemo(
    () => (segments ? [...new Set(segments.map((s) => s.category))] : []), [segments])
  const subs = segments && seg ? segments.filter((s) => s.category === seg.category) : []

  const onCat = (category) => {
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
        .filter((c) => c.products_delta !== undefined)
        .map((c) => `${c.category}/${c.sub_category} ${c.products_delta > 0 ? '+' : ''}${c.products_delta}`)
        .join(', ')
      setNotice({
        kind: 'ok',
        text: `Accepted ${resp.saved_as} (${resp.matched_adapter}): `
          + `${resp.products_parsed}/${resp.rows_in_file} rows usable. `
          + (deltas ? `Changed: ${deltas}. ` : '') + resp.persistence,
      })
      onDataChanged(resp)
    } catch (err) {
      setNotice({ kind: 'err', text: `Upload rejected, nothing was changed. ${err.message || err}` })
    } finally {
      setUploading(false)
    }
  }

  return (
    <>
      <div className="header">
        <div>
          <div className="wordmark">Trend Bet Workbench</div>
          <div className="tagline">
            One verdict, one honest number, one direction per trend, so a buyer
            can size a bet in seconds, not spreadsheets.
          </div>
        </div>
        <div className="controls">
          {seg && (
            <>
              <select value={seg.category} onChange={(e) => onCat(e.target.value)}>
                {categories.map((c) => <option key={c} value={c}>{cap(c)}</option>)}
              </select>
              <select value={seg.sub_category}
                onChange={(e) => onChange({ ...seg, sub_category: e.target.value })}>
                {subs.map((s) => (
                  <option key={s.sub_category} value={s.sub_category}>
                    {cap(s.sub_category)} · {s.count.toLocaleString('en-IN')} items
                  </option>
                ))}
              </select>
            </>
          )}
          <button className="btn-dark" disabled={uploading}
            onClick={() => fileRef.current?.click()}>
            {uploading ? 'Validating…' : 'Upload data file'}
          </button>
          <input ref={fileRef} type="file" accept=".json,.csv"
            style={{ display: 'none' }} onChange={onFile} />
        </div>
      </div>
      {notice && (
        <div className={`upload-notice ${notice.kind}`}>
          {notice.text}
          <button className="dismiss" onClick={() => setNotice(null)}>✕</button>
        </div>
      )}
    </>
  )
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1)
