import { useEffect, useRef, useState } from 'react'

// The old whole-frame system's verdict, shown only when it says something different from the object-scoped one.
function Legacy({ now }) {
  const old = now.legacy
  if (!old || old.reasons.join('|') === now.reasons.join('|')) return null
  return <div className="legacy">The old whole-image check would have said: {old.passed ? 'good' : old.reasons.join(', ')}.</div>
}

export default function App() {
  const [views, setViews] = useState([])
  const [info, setInfo] = useState({ title: 'Capture', guide: [0.1, 0.1, 0.9, 0.9] }) // from the active pack
  const [i, setI] = useState(0)
  const [camError, setCamError] = useState('')
  const [busy, setBusy] = useState(false)
  const [shot, setShot] = useState(null) // { blob, url, result }
  const [accepted, setAccepted] = useState({}) // view_id -> { blob, url, result }
  const [saved, setSaved] = useState('')
  const videoRef = useRef(null)
  const camMeta = useRef({})
  const streamRef = useRef(null)

  useEffect(() => {
    fetch('/api/views').then(r => r.json()).then(setViews)
    fetch('/api/info').then(r => r.json()).then(setInfo)
  }, [])

  useEffect(() => {
    let stream
    if (!navigator.mediaDevices?.getUserMedia) { setCamError('Camera API unavailable (page must be served over HTTPS)'); return }
    navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 3840 }, height: { ideal: 2160 } },
      audio: false,
    }).then(s => {
      stream = streamRef.current = s
      videoEl(videoRef.current)
    }).catch(e => setCamError(e.message || 'Camera unavailable (needs HTTPS and permission)'))
    return () => stream?.getTracks().forEach(t => t.stop())
  }, [])

  const done = views.length > 0 && i >= views.length
  // Runs on every <video> mount (first render, after the summary screen). Safari needs an explicit play().
  function videoEl(el) {
    videoRef.current = el
    if (el && streamRef.current) {
      el.srcObject = streamRef.current
      el.play().catch(e => setCamError(`Camera preview could not start: ${e.message}`))
    }
  }

  async function submit(blob) {
    setBusy(true)
    const fd = new FormData()
    fd.append('image', blob, 'shot.jpg')
    const result = await (await fetch('/api/check', { method: 'POST', body: fd })).json()
    setShot({ blob, url: URL.createObjectURL(blob), result })
    setBusy(false)
  }

  function snap() {
    const v = videoRef.current
    // The stage shows a 3:4 crop of the video (object-fit: cover). Send exactly that crop so the guide box means the same thing on both sides.
    const A = 3 / 4
    const vw = v.videoWidth, vh = v.videoHeight
    const cw = vw / vh > A ? vh * A : vw
    const ch = vw / vh > A ? vh : vw / A
    const c = document.createElement('canvas')
    c.width = Math.round(cw)
    c.height = Math.round(ch)
    camMeta.current = { width: vw, height: vh, sent: [c.width, c.height] }
    c.getContext('2d').drawImage(v, (vw - cw) / 2, (vh - ch) / 2, cw, ch, 0, 0, c.width, c.height)
    c.toBlob(submit, 'image/jpeg', 0.95)
  }

  function accept() {
    setAccepted(a => ({ ...a, [views[i].id]: shot }))
    setShot(null)
    setI(i + 1)
  }

  async function save() {
    const ids = Object.keys(accepted)
    const fd = new FormData()
    ids.forEach(id => fd.append('images', accepted[id].blob, `${id}.jpg`))
    fd.append('view_ids', JSON.stringify(ids))
    fd.append('client', JSON.stringify({ userAgent: navigator.userAgent, camera: camMeta.current }))
    const r = await (await fetch('/api/session', { method: 'POST', body: fd })).json()
    setSaved(r.session_id)
  }

  if (!views.length) return <div className="app">Loading…</div>

  if (done) {
    return (
      <div className="app">
        <h1>All {views.length} views captured</h1>
        <div className="grid">
          {views.map(v => (
            <figure key={v.id}>
              <img src={accepted[v.id].url} />
              <figcaption>{v.label}: passed</figcaption>
            </figure>
          ))}
        </div>
        {saved ? <div className="pass">Saved session {saved}</div> : <button onClick={save}>Save session</button>}
        <button className="secondary" onClick={() => { setAccepted({}); setSaved(''); setI(0) }}>Start over</button>
      </div>
    )
  }

  const view = views[i]
  return (
    <div className="app">
      <h1>{info.title}</h1>
      <div className="step">View {i + 1} of {views.length}: <b>{view.label}</b></div>
      <div className="stage">
        {/* video stays mounted (hidden) so the camera stream survives a retake */}
        <video ref={videoEl} autoPlay playsInline muted style={shot ? { display: 'none' } : undefined} />
        {shot && <img src={shot.url} />}
        {!shot && <div className="guide" style={{ left: `${info.guide[0] * 100}%`, top: `${info.guide[1] * 100}%`, width: `${(info.guide[2] - info.guide[0]) * 100}%`, height: `${(info.guide[3] - info.guide[1]) * 100}%` }} />}
      </div>
      {shot ? (
        <>
          {shot.result.passed
            ? <div className="pass">Good image.</div>
            : <div className="fail">Please retake:<ul>{shot.result.reasons.map(r => <li key={r}>{r}</li>)}</ul></div>}
          <Legacy now={shot.result} />
          <div className="row">
            <button className="secondary" onClick={() => setShot(null)}>Retake</button>
            {shot.result.passed && <button onClick={accept}>{i + 1 === views.length ? 'Finish' : 'Next view'}</button>}
          </div>
        </>
      ) : camError ? (
        <>
          <div className="fail">{camError}. Use the fallback below.</div>
          <label className="btn">Take photo
            <input type="file" accept="image/*" capture="environment" hidden onChange={e => e.target.files[0] && submit(e.target.files[0])} />
          </label>
        </>
      ) : (
        <button onClick={snap} disabled={busy}>{busy ? 'Checking…' : 'Capture'}</button>
      )}
    </div>
  )
}
