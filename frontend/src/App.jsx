import React, { useEffect, useState } from 'react'
import { getSlate, getTrend } from './api.js'
import Slate from './components/Slate.jsx'
import TrendDetail from './components/TrendDetail.jsx'

/* Tiny hash router: #/ = slate, #/trend/<bucket> = detail. */
export default function App() {
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
    getSlate().then(setSlateData).catch((e) => setError(String(e)))
  }, [])

  const bucket = route.startsWith('#/trend/') ? route.slice('#/trend/'.length) : null

  useEffect(() => {
    if (!bucket) { setTrend(null); return }
    setTrend(null)
    getTrend(bucket).then(setTrend).catch((e) => setError(String(e)))
  }, [bucket])

  if (error) return <div className="container error">Backend unreachable: {error}. Is uvicorn running on :8000?</div>
  if (!slateData) return <div className="container loading">Computing slate from /data…</div>

  return (
    <div className="container">
      {bucket
        ? (trend
            ? <TrendDetail key={bucket} trend={trend} onBack={() => { window.location.hash = '#/' }} />
            : <div className="loading">Loading {bucket}…</div>)
        : <Slate slate={slateData.slate} meta={slateData.meta}
            onOpen={(b) => { window.location.hash = `#/trend/${b}` }} />}
    </div>
  )
}
