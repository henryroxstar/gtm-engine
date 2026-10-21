/*
 * mc-video.js — Mission Control video runtime (proposal, MCDL 2.1 · DR-016).
 *
 * A deterministic, time-driven renderer for 16:9 motion-graphics trailers. Every frame is a pure
 * function of time: `renderAt(t)` draws the frame for t seconds, with no hidden state, so a headless
 * browser can step it frame by frame and pipe the frames to ffmpeg (see ../tools/render-video.mjs).
 *
 * - Values come from the token CSS (tokens.css): colours, fonts, weights and the
 *   `--video-*` timings. Nothing here invents a hex — the fallbacks only apply when the CSS is missing.
 * - Copy, timings and building blocks come from a JSON script in the page:
 *     <script type="application/json" id="mcv-script">{ … }</script>
 * - Randomness is seeded (mulberry32), so every render of the same script is identical.
 * - Classic script (not a module) so artboards open straight from disk (file://).
 *
 * Public API: window.MCVideo = { ready, renderAt(t), play(), pause(), duration, fps, width, height }
 */
(function () {
  'use strict'

  // ------------------------------------------------------------------ maths
  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x))
  const prog = (t, a, b) => (b === a ? (t >= a ? 1 : 0) : clamp((t - a) / (b - a)))
  const lerp = (a, b, k) => a + (b - a) * k
  const eo = (x) => 1 - Math.pow(1 - x, 3)                                   // ease-out cubic
  const eio = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2) // in-out cubic
  let eob = (x) => { const c = 1.4; x = clamp(x); return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2) } // arrive (overshoot) — replaced by the token curve when present

  function bezier(p1x, p1y, p2x, p2y) { // CSS cubic-bezier → JS easing (Newton–Raphson on x)
    const cx = 3 * p1x, bx = 3 * (p2x - p1x) - cx, ax = 1 - cx - bx
    const cy = 3 * p1y, by = 3 * (p2y - p1y) - cy, ay = 1 - cy - by
    const sx = (t) => ((ax * t + bx) * t + cx) * t, sy = (t) => ((ay * t + by) * t + cy) * t
    const dx = (t) => (3 * ax * t + 2 * bx) * t + cx
    return (x) => {
      x = clamp(x); let t = x
      for (let i = 0; i < 8; i++) { const e = sx(t) - x; if (Math.abs(e) < 1e-5) break; const d = dx(t); if (Math.abs(d) < 1e-6) break; t -= e / d }
      return sy(clamp(t))
    }
  }
  function rng(seed) { // mulberry32
    let a = seed >>> 0
    const r = () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296 }
    r.uniform = (a, b) => a + (b - a) * r()
    r.expo = (rate) => -Math.log(1 - r()) / rate
    r.pick = (arr) => arr[Math.floor(r() * arr.length)]
    return r
  }
  function appear(t, t0, t1, din = 0.4, dout = 0.2) { // → [alpha, drift] for an enter/exit window
    if (t < t0 || t > t1) return [0, 0]
    const k = eo(prog(t, t0, t0 + din)), o = 1 - prog(t, t1 - dout, t1)
    return [k * o, (1 - k) - (1 - o) * 0.6]
  }

  // ------------------------------------------------------------------ tokens
  const css = getComputedStyle(document.documentElement)
  const v = (name, fb) => (css.getPropertyValue(name).trim() || fb)
  const ms = (name, fb) => { const s = v(name, ''); if (!s) return fb; const n = parseFloat(s); return /ms$/.test(s) ? n / 1000 : n }
  const num = (name, fb) => { const s = v(name, ''); return s ? parseFloat(s) : fb }

  const rawCanvas = v('--surface-canvas', '#000000')
  const TOK = {
    canvas: v('--motion-glow-on-focal', '0') === '1' ? v('--color-black', '#000000') : rawCanvas,
    ink: v('--text-primary', '#ffffff'),
    t2: v('--text-secondary', '#888888'),
    t3: v('--text-tertiary', '#666666'),
    hair: v('--border-hairline', 'rgba(255,255,255,.12)'),
    hair2: v('--border-hairline-strong', 'rgba(255,255,255,.2)'),
    blue: v('--highlight-primary', '#ffffff'),
    cyan: v('--highlight-secondary', '#cccccc'),
    ok: v('--color-live-ok', '#3bd68a'),
    bad: v('--color-live-bad', '#ef4444'),
    warn: v('--status-warning', '#f2a93b'),
    grid: v('--grid-line', 'rgba(255,255,255,.08)'),
    panel: v('--surface-panel', 'rgba(255,255,255,.03)'),
    display: v('--font-family-display', 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'),
    mono: v('--font-family-mono', '"SF Mono", Monaco, Inconsolata, "Courier New", monospace'),
    wBold: v('--font-weight-bold', '700'),
    wMed: v('--font-weight-medium', '500'),
    wReg: v('--font-weight-regular', '400'),
    fps: num('--video-fps', 25),
    titleSafe: num('--video-title-safe', 0.05),
    headline: num('--video-type-headline', 0.067),
    label: num('--video-type-label-min', 0.014),
    stagger: ms('--video-stagger-word', 0.045),
    cut: ms('--video-duration-cut', 0.4),
    dur: {
      hook: ms('--video-duration-hook', 3.6),
      intro: ms('--video-duration-reveal', 3.0),
      claim: ms('--video-duration-claim', 4.6),
      outcome: ms('--video-duration-outcome', 4.5),
      endcard: ms('--video-duration-endcard', 5.1),
    },
  }
  const ARRIVE = v('--video-easing-arrive', '')
  if (ARRIVE) {
    const p = ARRIVE.match(/-?[\d.]+/g)
    if (p && p.length === 4) eob = bezier(...p.map(Number))
    else if (ARRIVE.startsWith('linear(')) eob = bezier(0.34, 1.4, 0.64, 1)
  }

  // colour → [r,g,b,a] (parsed once by the browser)
  const _pc = document.createElement('canvas').getContext('2d', { willReadFrequently: true })
  const _cache = new Map()
  function rgb(col) {
    if (_cache.has(col)) return _cache.get(col)
    _pc.clearRect(0, 0, 1, 1); _pc.fillStyle = '#000'; _pc.fillStyle = col; _pc.fillRect(0, 0, 1, 1)
    const d = _pc.getImageData(0, 0, 1, 1).data
    const m = String(col).match(/rgba?\(([^)]+)\)/); const a = m && m[1].split(',').length === 4 ? parseFloat(m[1].split(',')[3]) : 1
    const out = [d[0], d[1], d[2], a]
    // translucent colours were premultiplied over black above — read the channels from the string instead
    if (m) { const p = m[1].split(',').map((s) => parseFloat(s)); out[0] = p[0]; out[1] = p[1]; out[2] = p[2] }
    _cache.set(col, out); return out
  }
  const rgba = (col, a = 1) => { const c = rgb(col); return `rgba(${c[0]},${c[1]},${c[2]},${clamp(c[3] * a)})` }
  const mix = (c1, c2, k) => { const a = rgb(c1), b = rgb(c2); return `rgba(${Math.round(lerp(a[0], b[0], k))},${Math.round(lerp(a[1], b[1], k))},${Math.round(lerp(a[2], b[2], k))},${lerp(a[3], b[3], k)})` }

  // ------------------------------------------------------------------ stage + drawing kit
  const S = { ctx: null, W: 1920, H: 1080, u: 1, imgs: {}, cfg: null }

  function font(size, weight, family) { return `${weight} ${size}px ${family}` }
  function text(s, x, y, size, o = {}) {
    const c = S.ctx, a = o.a ?? 1
    if (a <= 0.003 || !s) return 0
    c.save()
    c.font = font(size, o.weight ?? TOK.wBold, o.family ?? TOK.display)
    c.letterSpacing = `${o.track ?? (o.family === TOK.mono ? 0 : -0.012 * size)}px`
    c.textAlign = o.align ?? 'center'; c.textBaseline = 'alphabetic'
    c.fillStyle = o.fill ?? rgba(o.col ?? TOK.ink, a)
    if (o.fill) c.globalAlpha = a
    c.fillText(s, x, y)
    const w = c.measureText(s).width
    c.restore(); return w
  }
  function measure(s, size, weight = TOK.wBold, family = TOK.display, track) {
    const c = S.ctx; c.save(); c.font = font(size, weight, family)
    c.letterSpacing = `${track ?? (family === TOK.mono ? 0 : -0.012 * size)}px`
    const w = c.measureText(s).width; c.restore(); return w
  }
  function mono(s, x, y, size, col = TOK.t2, a = 1, align = 'left') {
    return text(String(s).toUpperCase(), x, y, size, { family: TOK.mono, weight: TOK.wMed, track: 0.14 * size, col, a, align })
  }
  function rrect(x, y, w, h, r) {
    const c = S.ctx; r = Math.min(r, w / 2, h / 2)
    c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r)
    c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath()
  }
  function panel(x, y, w, h, a = 1, border = TOK.hair, fill = TOK.panel) {
    if (a <= 0) return
    const c = S.ctx; rrect(x, y, w, h, 3); c.fillStyle = rgba(fill, a); c.fill(); c.strokeStyle = rgba(border, a); c.lineWidth = 1.2; c.stroke()
  }
  function glow(x, y, r, col, a = 1) {
    if (a <= 0) return
    const c = S.ctx, g = c.createRadialGradient(x, y, 0, x, y, r)
    g.addColorStop(0, rgba(col, 0.55 * a)); g.addColorStop(1, rgba(col, 0))
    c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill()
  }
  function dot(x, y, r, col, a = 1) { if (a <= 0) return; const c = S.ctx; c.fillStyle = rgba(col, a); c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill() }
  function ring(x, y, r, col, a = 1, w = 1.2, from = 0, to = Math.PI * 2, dash) {
    if (a <= 0) return
    const c = S.ctx; c.save(); c.strokeStyle = rgba(col, a); c.lineWidth = w; if (dash) c.setLineDash(dash)
    c.beginPath(); c.arc(x, y, r, from, to); c.stroke(); c.restore()
  }
  function line(x0, y0, x1, y1, col, a = 1, w = 1.2, dash) {
    if (a <= 0) return
    const c = S.ctx; c.save(); c.strokeStyle = rgba(col, a); c.lineWidth = w; if (dash) c.setLineDash(dash)
    c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); c.restore()
  }
  function gradline(x0, y, x1, w, a = 1) { // the accent gradient — a mark, never a fill
    if (x1 - x0 < 1 || a <= 0) return
    const c = S.ctx, g = c.createLinearGradient(x0, 0, x1, 0)
    g.addColorStop(0, rgba(TOK.blue, a)); g.addColorStop(1, rgba(TOK.cyan, a))
    c.fillStyle = g; c.fillRect(x0, y - w / 2, x1 - x0, w)
  }
  function pill(s, x, y, col = TOK.ink, border = TOK.hair2, a = 1, size = 16, fill = TOK.canvas) {
    if (a <= 0) return 0
    const w = measure(s.toUpperCase(), size, TOK.wMed, TOK.mono, 0.06 * size) + 36, h = size * 2.3
    const c = S.ctx; rrect(x - w / 2, y - h / 2, w, h, h / 2); c.fillStyle = rgba(fill, a); c.fill(); c.strokeStyle = rgba(border, a); c.lineWidth = 1.4; c.stroke()
    text(s.toUpperCase(), x, y + size * 0.36, size, { family: TOK.mono, weight: TOK.wMed, track: 0.06 * size, col, a })
    return w
  }
  function check(x, y, s, col, a, k = 1, w = 3) {
    if (a <= 0 || k <= 0) return
    const P = [[x - 0.5 * s, y], [x - 0.15 * s, y + 0.35 * s], [x + 0.55 * s, y - 0.4 * s]]
    const L1 = Math.hypot(P[1][0] - P[0][0], P[1][1] - P[0][1]), L2 = Math.hypot(P[2][0] - P[1][0], P[2][1] - P[1][1]), d = k * (L1 + L2)
    const c = S.ctx; c.save(); c.strokeStyle = rgba(col, a); c.lineWidth = w; c.lineCap = 'round'; c.lineJoin = 'round'
    c.beginPath(); c.moveTo(...P[0])
    if (d <= L1) c.lineTo(lerp(P[0][0], P[1][0], d / L1), lerp(P[0][1], P[1][1], d / L1))
    else { c.lineTo(...P[1]); const q = (d - L1) / L2; c.lineTo(lerp(P[1][0], P[2][0], q), lerp(P[1][1], P[2][1], q)) }
    c.stroke(); c.restore()
  }
  function cross(x, y, s, col, a, k = 1, w = 3) {
    if (a <= 0 || k <= 0) return
    const c = S.ctx, k1 = clamp(k * 2), k2 = clamp(k * 2 - 1)
    c.save(); c.strokeStyle = rgba(col, a); c.lineWidth = w; c.lineCap = 'round'; c.beginPath()
    c.moveTo(x - s / 2, y - s / 2); c.lineTo(x - s / 2 + s * k1, y - s / 2 + s * k1)
    if (k2 > 0) { c.moveTo(x + s / 2, y - s / 2); c.lineTo(x + s / 2 - s * k2, y - s / 2 + s * k2) }
    c.stroke(); c.restore()
  }
  function image(src, x, y, w, a = 1) {
    const img = S.imgs[src]; if (!img || a <= 0) return 0
    const h = (img.naturalHeight / img.naturalWidth) * w
    const c = S.ctx; c.save(); c.globalAlpha = a; c.drawImage(img, x, y, w, h); c.restore(); return h
  }
  const imgH = (src, w) => { const i = S.imgs[src]; return i ? (i.naturalHeight / i.naturalWidth) * w : 0 }
  const clock = (t) => { const s = Math.floor(4 * 60 + t); return `12:${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}` }

  /**
   * Headline with a word-by-word arrival. Wrap the payoff in [brackets] to paint it with the accent
   * gradient — one accent span per heading (DR-015).
   */
  function headline(s, x, y, size, t, t0, t1, o = {}) {
    if (t < t0 || t > t1 || !s) return
    const accentM = s.match(/\[(.+?)\]/)
    const plain = s.replace(/[[\]]/g, '')
    const words = plain.split(' ')
    const accentWords = new Set()
    if (accentM) { const pre = s.slice(0, s.indexOf('[')).replace(/[[\]]/g, '').split(' ').filter(Boolean).length; accentM[1].split(' ').forEach((_, i) => accentWords.add(pre + i)) }
    const weight = o.weight ?? TOK.wBold, family = o.family ?? TOK.display
    const sp = measure(' ', size, weight, family, 0)
    const ws = words.map((w) => measure(w, size, weight, family))
    const total = ws.reduce((p, q) => p + q, 0) + sp * (words.length - 1)
    let cx = (o.align ?? 'center') === 'center' ? x - total / 2 : x
    const out = 1 - prog(t, t1 - 0.22, t1)
    // accent gradient spans the payoff words
    let ax0 = null, ax1 = null, xx = cx
    words.forEach((w, i) => { if (accentWords.has(i)) { if (ax0 === null) ax0 = xx; ax1 = xx + ws[i] } xx += ws[i] + sp })
    let grad = null
    if (ax0 !== null) { grad = S.ctx.createLinearGradient(ax0, 0, ax1, 0); grad.addColorStop(0, TOK.blue); grad.addColorStop(1, TOK.cyan) }
    const stag = o.stagger ?? TOK.stagger
    words.forEach((w, i) => {
      const k = prog(t, t0 + i * stag, t0 + i * stag + 0.42), a = eo(k) * out
      if (a > 0) text(w, cx, y + (1 - eob(k)) * 34 - (1 - out) * 10, size, { weight, family, col: o.col ?? TOK.ink, a, align: 'left', fill: accentWords.has(i) ? grad : undefined })
      cx += ws[i] + sp
    })
  }

  // ------------------------------------------------------------------ frame furniture
  function background() {
    const { ctx: c, W, H } = S
    c.fillStyle = TOK.canvas; c.fillRect(0, 0, W, H)
    const cell = 60; c.strokeStyle = TOK.grid; c.lineWidth = 1; c.beginPath()
    for (let x = (W / 2) % cell; x < W; x += cell) { c.moveTo(Math.round(x) + 0.5, 0); c.lineTo(Math.round(x) + 0.5, H) }
    for (let y = (H / 2) % cell; y < H; y += cell) { c.moveTo(0, Math.round(y) + 0.5); c.lineTo(W, Math.round(y) + 0.5) }
    c.stroke()
    const g = c.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, Math.max(W, H) * 0.62)
    g.addColorStop(0, rgba(TOK.canvas, 0.25)); g.addColorStop(0.55, rgba(TOK.canvas, 0.55)); g.addColorStop(1, rgba(TOK.canvas, 0.97))
    c.fillStyle = g; c.fillRect(0, 0, W, H)
  }
  function hud(t) {
    const { W, H } = S, m = 48, L = 22
    ;[[m, m, 1, 1], [W - m, m, -1, 1], [m, H - m, 1, -1], [W - m, H - m, -1, -1]].forEach(([x, y, sx, sy]) => {
      line(x, y + sy * L, x, y, TOK.hair2); line(x, y, x + sx * L, y, TOK.hair2)
    })
    const blink = 0.55 + 0.45 * Math.cos((t * Math.PI * 2) / 1.6)
    dot(m + 16, m + 36, 4, TOK.ok, blink); glow(m + 16, m + 36, 12, TOK.ok, 0.6 * blink)
    mono(`${S.cfg.product} · live`, m + 30, m + 41, 15, TOK.t3)
    mono(`SGT ${clock(t)}`, W - m - 6, m + 41, 15, TOK.t3, 1, 'right')
  }
  function scan(t, tc) { // the cut: a cyan scan line sweeps the frame
    const p = prog(t, tc - 0.12, tc - 0.12 + TOK.cut + 0.1)
    if (p <= 0 || p >= 1) return
    const { ctx: c, W, H } = S, x = lerp(-60, W + 60, eio(p)), a = Math.sin(p * Math.PI)
    const g = c.createLinearGradient(x - 140, 0, x + 6, 0)
    g.addColorStop(0, rgba(TOK.cyan, 0)); g.addColorStop(0.9, rgba(TOK.cyan, 0.1 * a)); g.addColorStop(1, rgba(TOK.cyan, 0))
    c.fillStyle = g; c.fillRect(x - 140, 0, 146, H); c.fillStyle = rgba(TOK.cyan, 0.55 * a); c.fillRect(x - 1, 0, 2, H)
  }
  function cam(lt, T, fn) { // scene camera: slide/zoom in, slow push, slide/zoom out
    const { ctx: c, W, H } = S
    const ki = eo(prog(lt, 0, 0.5)), ko = eio(prog(lt, T - 0.3, T))
    const s = (1.06 - 0.06 * ki) * (1 + 0.025 * prog(lt, 0, T)) * (1 - 0.05 * ko)
    c.save(); c.translate(W / 2 + (1 - ki) * 90 - ko * 90, H / 2); c.scale(s, s); c.translate(-W / 2, -H / 2)
    fn(ki * (1 - ko)); c.restore()
  }
  const TITLE_Y = 200
  const title = (s, lt, T) => headline(s, S.W / 2, TITLE_Y, 72, lt, 0.05, T)

  // ================================================================== BLOCKS
  // Each block: (cfg, lt, T, t) → draws one scene. lt = local time, T = scene length, t = global time.
  const BLOCKS = {}

  // ---- hook · constellation: agents connect to "your systems"; unknown agents arrive with the question
  BLOCKS['hook-constellation'] = (cfg, lt, T) => {
    const { W, H } = S, A = eo(prog(lt, 0, 0.4)) * (1 - prog(lt, T - 0.15, T - 0.02))
    const cx = W / 2, cy = H / 2 + 110, RX = 640, RY = 270, q = cfg.questionAt ?? T * 0.6
    if (!cfg._nodes) {
      const r = rng(cfg.seed ?? 5), n = cfg.agents ?? 14, unk = new Set(cfg.unknown ?? [3, 10])
      cfg._nodes = Array.from({ length: n }, (_, i) => {
        const nd = { ang: -Math.PI / 2 + (i * 2 * Math.PI) / n + r.uniform(-0.12, 0.12), ring: r.pick([0.62, 0.82, 1]), dep: r.uniform(0.45, 1),
          bend: r.uniform(-0.28, 0.28), unk: unk.has(i), rate: r.uniform(0.7, 2.6) }
        nd.born = nd.unk ? q - 0.1 : 0.1 + r.uniform(0, 0.9)
        const rp = rng(1000 + i), pk = []; let tt = nd.born - 0.2
        while (tt < T) { tt += rp.expo(nd.rate * (nd.unk ? 1.6 : 1)); pk.push([tt, rp.uniform(0.9, 1.7), rp() < 0.6]) }
        nd.pk = pk; return nd
      })
    }
    for (let k = 0; k < 2; k++) { const ph = (lt * 0.55 + k * 0.5) % 1; ring(cx, cy, 70 + 120 * ph, TOK.cyan, A * 0.25 * (1 - ph)) }
    const bez = (p0, p1, p2, k) => [(1 - k) ** 2 * p0[0] + 2 * (1 - k) * k * p1[0] + k * k * p2[0], (1 - k) ** 2 * p0[1] + 2 * (1 - k) * k * p1[1] + k * k * p2[1]]
    const c = S.ctx
    cfg._nodes.forEach((nd) => {
      const ap = eo(prog(lt, nd.born, nd.born + 0.45)) * A; if (ap <= 0) return
      const x = cx + Math.cos(nd.ang) * RX * nd.ring + Math.sin(lt * 0.8 + nd.ang * 3) * 5
      const y = cy + Math.sin(nd.ang) * RY * nd.ring + Math.cos(lt * 0.6 + nd.ang) * 4
      const mx = (x + cx) / 2, my = (y + cy) / 2, len = Math.hypot(cx - x, cy - y), nx = -(cy - y) / len, ny = (cx - x) / len
      const c1 = [mx + nx * nd.bend * len, my + ny * nd.bend * len]
      const lp = eo(prog(lt, nd.born, nd.born + 0.6))
      c.save(); c.strokeStyle = nd.unk ? rgba(TOK.warn, ap * 0.8) : rgba(TOK.hair2, ap * (0.55 + 0.45 * nd.dep)); c.lineWidth = 0.8 + 0.8 * nd.dep
      if (nd.unk) c.setLineDash([5, 6])
      c.beginPath(); c.moveTo(x, y); for (let j = 1; j <= Math.floor(28 * lp); j++) c.lineTo(...bez([x, y], c1, [cx, cy], j / 28)); c.stroke(); c.restore()
      if (lp > 0.98) nd.pk.forEach(([t0, sp, inb]) => {
        const p = (lt - t0) * sp * 0.9; if (p <= 0 || p >= 1) return
        const kk = inb ? eio(p) : 1 - eio(p), [px, py] = bez([x, y], c1, [cx, cy], kk)
        const fa = Math.min(1, p * 6, (1 - p) * 6) * ap, pc = nd.unk ? TOK.warn : inb ? TOK.cyan : TOK.ink
        glow(px, py, 8 + 6 * nd.dep, pc, 0.55 * fa); dot(px, py, 1.8 + 1.4 * nd.dep, pc, fa)
      })
      const nc = nd.unk ? TOK.warn : TOK.ink, rad = 4 + 5 * nd.dep
      glow(x, y, 18 + 16 * nd.dep, nc, 0.35 * ap); dot(x, y, rad, nc, ap * (0.55 + 0.45 * nd.dep)); ring(x, y, rad + 9, TOK.hair2, ap * nd.dep, 1)
      if (nd.unk) {
        ring(x, y, rad + 16, TOK.warn, ap * (0.5 + 0.5 * Math.sin(lt * 7)), 1.4, 0, Math.PI * 2, [3, 4])
        mono(cfg.unknownLabel ?? 'Unknown agent', x, y + rad + 40, 13, TOK.warn, appear(lt, nd.born + 0.3, T)[0] * ap, 'center')
      }
    })
    rrect(cx - 120, cy - 40, 240, 80, 4); c.fillStyle = rgba(TOK.canvas, A); c.fill(); c.strokeStyle = rgba(TOK.hair2, A); c.lineWidth = 1.5; c.stroke()
    mono(cfg.core ?? 'Your systems', cx, cy + 6, 15, TOK.t2, A, 'center')
    headline(cfg.line1, W / 2, TITLE_Y, 72, lt, 0.15, q - 0.15)
    headline(cfg.line2, W / 2, TITLE_Y, 72, lt, q, T - 0.05)
  }

  // ---- hook · traffic: irregular calls on lanes; risky ones flare; spend counter fed by exiting calls
  BLOCKS['hook-traffic'] = (cfg, lt, T) => {
    const { W, H } = S, c = S.ctx, A = eo(prog(lt, 0, 0.4)) * (1 - prog(lt, T - 0.15, T - 0.02)), q = cfg.questionAt ?? T * 0.6
    const lanes = [[-150, 0.35], [-95, 0.55], [-45, 0.8], [0, 1], [52, 0.85], [108, 0.6], [170, 0.4]]
    if (!cfg._ev) {
      const r = rng(cfg.seed ?? 7), ev = []
      lanes.forEach(([, dep], li) => {
        let tt = -4 + r() * 0.8
        while (tt < T) {
          const rate = 1 + 0.9 * Math.max(0, tt + 1) + (r() < 0.12 ? 2.5 : 0)
          tt += r.expo(rate) * (1.3 - 0.5 * dep)
          const len = Math.max(30, Math.min(190, Math.exp(4.1 + 0.45 * Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r()))))
          ev.push({ lane: li, t0: tt, v: (0.3 + 0.32 * r()) * (0.75 + 0.35 * dep), len, risk: false, cost: len * r.uniform(3.2, 6.5) })
        }
      })
      ;(cfg.risky ?? [[3, -0.9], [1, 0.2], [5, 0.75], [2, 1.55], [4, 2.6]]).forEach(([li, t0]) => ev.push({ lane: li, t0, v: 0.42, len: 110, risk: true, cost: 900 }))
      cfg._ev = ev
    }
    const X0 = 120, X1 = W - 120, span = X1 - X0, yc = H / 2 + 120, fadeL = 260
    const edge = (x) => clamp((x - X0) / fadeL) * clamp((X1 - x) / fadeL)
    lanes.forEach(([dy, dep]) => {
      const g = c.createLinearGradient(X0, 0, X1, 0), al = A * (0.5 + 0.5 * dep)
      g.addColorStop(0, rgba(TOK.hair, 0)); g.addColorStop(0.18, rgba(TOK.hair, al)); g.addColorStop(0.82, rgba(TOK.hair, al)); g.addColorStop(1, rgba(TOK.hair, 0))
      c.strokeStyle = g; c.lineWidth = 1; c.beginPath(); c.moveTo(X0, yc + dy); c.lineTo(X1, yc + dy); c.stroke()
    })
    let spent = 0
    cfg._ev.forEach((e) => {
      const [dy, dep] = lanes[e.lane], y = yc + dy, head = X0 + (lt - e.t0) * e.v * span
      const L = e.len * (0.6 + 0.5 * dep), h = 5 + 9 * dep
      if (head > X1) { spent += e.cost; return }
      if (head < X0) return
      const a = A * edge(head) * (0.35 + 0.65 * dep); if (a <= 0.01) return
      const col = e.risk ? TOK.bad : TOK.cyan, tail = Math.max(head - L, X0)
      const g = c.createLinearGradient(tail, 0, head, 0); g.addColorStop(0, rgba(col, 0)); g.addColorStop(0.85, rgba(col, 0.55 * a)); g.addColorStop(1, rgba(col, 0.95 * a))
      rrect(tail, y - h / 2, head - tail, h, h / 2); c.fillStyle = g; c.fill()
      dot(head, y, h * 0.42, e.risk ? TOK.bad : TOK.ink, a)
      if (e.risk) glow(head, y, 34 + 20 * dep, TOK.bad, a * (0.6 + 0.4 * Math.sin((lt - e.t0) * 9)) * 0.8)
    })
    if (cfg.counter !== false) {
      const [a2, d2] = appear(lt, cfg.counterAt ?? 1.0, T - 0.05)
      mono(cfg.counterLabel ?? 'AI spend · this month', W / 2, H / 2 - 150 + d2 * 10, 15, TOK.t2, a2, 'center')
      const val = (cfg.counterStart ?? 28400) + spent * 2.1
      text('$' + val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }), W / 2, H / 2 - 72 + d2 * 10, 64, { family: TOK.mono, weight: TOK.wMed, col: TOK.warn, a: a2, track: 0 })
    }
    headline(cfg.line1, W / 2, TITLE_Y, 72, lt, 0.15, q - 0.15)
    headline(cfg.line2, W / 2, TITLE_Y, 72, lt, q, T - 0.05)
  }

  // ---- intro: product logo revealed by a scan wipe, rule, one-line description
  BLOCKS.intro = (cfg, lt, T) => {
    const { W, H } = S, c = S.ctx, pw = cfg.logoWidth ?? 900, ph = imgH(cfg.logo, pw) || 120
    const x0 = W / 2 - pw / 2, y0 = H / 2 - ph / 2 - 60, k = eio(prog(lt, 0.1, 0.8)), out = 1 - prog(lt, T - 0.25, T)
    c.save(); c.beginPath(); c.rect(x0 - 10, y0 - 40, (pw + 20) * k, ph + 80); c.clip(); image(cfg.logo, x0, y0, pw, out); c.restore()
    if (k > 0 && k < 1) { const xe = x0 - 10 + (pw + 20) * k; c.fillStyle = rgba(TOK.cyan, 0.8 * out); c.fillRect(xe - 1.5, y0 - 50, 3, ph + 100); glow(xe, y0 + ph / 2, 120, TOK.cyan, 0.25 * out) }
    gradline(W / 2 - pw / 2, y0 + ph + 40, W / 2 - pw / 2 + pw * eo(prog(lt, 0.7, 1.3)) * out, 3)
    headline(cfg.line, W / 2, y0 + ph + 120, 44, lt, 0.9, T, { weight: TOK.wReg, col: TOK.t2, stagger: 0.035 })
  }

  // ---- claim · ID card: a new agent gets an identity; pull back to the verified fleet
  BLOCKS.idcard = (cfg, lt, T) => cam(lt, T, (A) => {
    const { W, H } = S, c = S.ctx
    title(cfg.title, lt, T)
    const vAt = cfg.verifyAt ?? 1.95, pull = cfg.pullAt ?? 2.5, zb = eio(prog(lt, pull, pull + 0.9))
    if (zb > 0) {
      const r = rng(3)
      for (let i = -5; i <= 5; i++) for (let j = -1; j <= 2; j++) {
        const jx = r.uniform(-30, 30), jy = r.uniform(-20, 20); if (i === 0 && j === 0) continue
        const x = W / 2 + i * 150 + jx, y = H / 2 + 80 + j * 120 + jy, d = Math.hypot(i, j * 1.3)
        const ap = A * zb * eo(prog(lt, pull + 0.2 + d * 0.06, pull + 0.5 + d * 0.06)); if (ap <= 0) continue
        dot(x, y, 7, TOK.ink, ap); glow(x, y, 22, TOK.ink, 0.25 * ap)
        const ka = prog(lt, pull + 0.5 + d * 0.09, pull + 0.9 + d * 0.09); if (ka > 0) ring(x, y, 17, TOK.ok, ap, 2, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * eo(ka))
      }
      const n = Math.round(1 + ((cfg.fleet ?? 2000) - 1) * eo(prog(lt, pull + 0.7, pull + 1.9)))
      mono(`${n.toLocaleString('en-US')} agents · ${n.toLocaleString('en-US')} verified`, W / 2, H - 150, 18, TOK.t2, A * eo(prog(lt, pull + 0.7, pull + 1.0)), 'center')
    }
    const s = 1 - 0.82 * zb, hx = W / 2 - 330, hy = H / 2 + 90
    c.save(); c.translate(W / 2, H / 2 + 80); c.scale(s, s); c.translate(-W / 2, -(H / 2 + 80))
    const ha = zb > 0 ? A * (1 - prog(lt, pull + 0.7, pull + 1.0)) : A, pin = eob(prog(lt, 0.15, 0.65))
    glow(hx, hy, 110, TOK.cyan, 0.35 * ha * pin); dot(hx, hy, 26 * clamp(pin, 0, 2), TOK.ink, ha); ring(hx, hy, 44 * Math.max(0, pin), TOK.hair2, ha, 1.5)
    mono(cfg.agentLabel ?? 'New agent', hx, hy + 86, 15, TOK.t2, ha * clamp(pin), 'center')
    const vk = prog(lt, vAt, vAt + 0.45)
    if (vk > 0) { ring(hx, hy, 58, TOK.ok, ha, 4, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * eo(vk)); glow(hx, hy, 130, TOK.ok, 0.3 * ha * eo(vk)) }
    const ck = eo(prog(lt, 0.5, 0.9)), px = W / 2 - 140, py = H / 2 - 110, pw = 780, ph = 400
    line(hx + 60, hy, lerp(hx + 60, px, ck), hy, TOK.hair2, ha, 1.5)
    const sl = eob(prog(lt, 0.6, 1.1))
    if (sl > 0) {
      const ox = (1 - sl) * 260
      panel(px + ox, py, pw, ph, ha * clamp(sl), TOK.hair2)
      mono(cfg.cardLabel ?? 'Agent identity', px + ox + 36, py + 48, 16, TOK.t3, ha)
      ;(cfg.fields ?? []).forEach(([k_, v_, to_], i) => {
        const tk = prog(lt, 1.0 + i * 0.32, 1.3 + i * 0.32); if (tk <= 0) return
        const yy = py + 112 + i * 60
        mono(k_, px + ox + 36, yy, 16, TOK.t3, ha * eo(tk))
        // Optional swap: [label, value, swapTo] with cfg.swapAt — the value scrambles, then
        // resolves left to right into its stand-in. Deterministic: the scramble is seeded by the
        // frame index, so a headless re-render draws the same glyphs.
        let shown = v_.slice(0, Math.round(v_.length * tk)), col = TOK.ink
        const sw = to_ != null && cfg.swapAt != null ? prog(lt, cfg.swapAt + i * 0.25, cfg.swapAt + i * 0.25 + 0.8) : 0
        if (sw >= 1) shown = to_
        else if (sw > 0) {
          const n = Math.round(lerp(v_.length, to_.length, eo(sw))), keep = Math.floor(n * sw), r = rng(Math.floor(lt * 30) * 7 + i)
          const glyphs = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&'
          shown = to_.slice(0, keep) + Array.from({ length: Math.max(0, n - keep) }, () => glyphs[Math.floor(r() * glyphs.length)]).join('')
          col = TOK.cyan
        }
        text(shown, px + ox + 260, yy + 3, 32, { weight: TOK.wMed, align: 'left', a: ha, track: 0, col })
      })
      const sk = prog(lt, vAt, vAt + 0.35)
      if (sk > 0) {
        const yy = py + 112 + (cfg.fields ?? []).length * 60
        mono('Status', px + ox + 36, yy, 16, TOK.t3, ha * eo(sk)); mono(cfg.status ?? 'Verified', px + ox + 296, yy, 22, TOK.ok, ha * eo(sk))
        check(px + ox + 272, yy - 8, 20, TOK.ok, ha, eo(sk), 3.5)
      }
    }
    c.restore()
  })

  // ---- claim · rules gate: requests are checked rule by rule; the last one is denied
  BLOCKS.gate = (cfg, lt, T) => cam(lt, T, (A) => {
    const { W, H } = S, c = S.ctx
    title(cfg.title, lt, T)
    const y = H / 2 + 10, ax = W / 2 - 620, gx = W / 2 - 40, tx = W / 2 + 560, rules = cfg.rules ?? []
    dot(ax, y, 20, TOK.ink, A); glow(ax, y, 60, TOK.cyan, 0.3 * A); ring(ax, y, 32, TOK.ok, A, 4)
    mono(cfg.agent ?? 'Agent', ax, y + 72, 14, TOK.t2, A, 'center')
    line(ax + 40, y, tx - 110, y, TOK.hair2, A, 1.2, [6, 6])
    rrect(tx - 110, y - 48, 220, 96, 4); c.fillStyle = rgba(TOK.canvas, A); c.fill(); c.strokeStyle = rgba(TOK.hair2, A); c.lineWidth = 1.5; c.stroke()
    mono(cfg.target ?? 'Target', tx, y + 6, 16, TOK.t2, A, 'center')
    const step = 0.18
    let state = null
    ;(cfg.requests ?? []).forEach((rq) => {
      const s0 = rq.at, res = rq.pass, allowed = res.every(Boolean), tg = s0 + 0.45, te = tg + 0.25 + rules.length * step
      if (lt < s0) return
      let x
      if (lt < tg) x = lerp(ax + 50, gx - 150, eio(prog(lt, s0, tg)))
      else if (lt < te) x = gx - 150
      else if (allowed) x = lerp(gx - 150, tx - 230, eio(prog(lt, te, te + 0.5)))
      else x = gx - 150 - eob(prog(lt, te, te + 0.45)) * 120
      const fade = allowed ? 1 - prog(lt, te + 0.9, te + 1.15) : 1 - prog(lt, te + 1.0, te + 1.4)
      if (fade > 0) {
        const col = lt >= te ? (allowed ? TOK.ok : TOK.bad) : TOK.ink, bord = lt >= te ? rgba(col, 0.6) : TOK.hair2
        pill(rq.label, x, y - 56, col, bord, A * fade * eo(prog(lt, s0, s0 + 0.2)), 15)
        dot(x, y, 7, col, A * fade); glow(x, y, 24, col, 0.5 * A * fade)
      }
      if (lt >= tg && lt < te + 1.2) state = { tg, te, res, allowed }
      if (lt >= te && allowed && lt > te + 0.45) {
        const fl = 1 - prog(lt, te + 0.5, te + 1.1)
        glow(tx, y, 140, TOK.ok, 0.35 * A * fl); mono(cfg.allowLabel ?? 'Allowed', tx, y + 86, 18, TOK.ok, A * eo(prog(lt, te + 0.45, te + 0.6)) * fl, 'center')
      }
    })
    let gcol = TOK.hair2, gl = 0
    if (state) { if (lt >= state.te) { gcol = state.allowed ? TOK.ok : TOK.bad; gl = 1 - prog(lt, state.te + 0.6, state.te + 1.2) } else { gcol = TOK.cyan; gl = 0.6 } }
    rrect(gx - 8, y - 150, 16, 300, 8); c.fillStyle = rgba(gcol, A); c.fill()
    if (gl > 0) glow(gx, y, 180, gcol, 0.4 * A * gl)
    if (state && lt >= state.tg && lt < state.te) { const sy = y - 150 + 300 * ((lt * 2.2) % 1); c.fillStyle = rgba(TOK.cyan, 0.7 * A); c.fillRect(gx - 60, sy - 1, 120, 2) }
    mono(cfg.gateLabel ?? 'Your rules', gx, y - 182, 15, TOK.t2, A, 'center')
    const px = gx - 230, py = y + 190
    rules.forEach((rl, i) => {
      const yy = py + i * 44
      text(rl, px + 44, yy + 8, 24, { weight: TOK.wReg, col: TOK.t2, a: A, align: 'left', track: 0 })
      rrect(px, yy - 14, 28, 28, 4); c.strokeStyle = rgba(TOK.hair2, A); c.lineWidth = 1.4; c.stroke()
      if (state) {
        const ki = prog(lt, state.tg + 0.25 + i * step, state.tg + 0.25 + i * step + 0.16), fade = 1 - prog(lt, state.te + 0.9, state.te + 1.2)
        if (state.res[i]) check(px + 14, yy, 16, TOK.ok, A * fade, eo(ki), 3); else cross(px + 14, yy, 14, TOK.bad, A * fade, eo(ki), 3)
      }
    })
    if (state && !state.allowed && lt >= state.te) {
      const k = eob(prog(lt, state.te, state.te + 0.3)), fo = 1 - prog(lt, state.te + 1.6, state.te + 1.9)
      pill(cfg.denyLabel ?? 'Denied', gx, y - 240 + (1 - k) * 20, TOK.bad, rgba(TOK.bad, 0.7), A * clamp(k) * fo, 18)
      c.fillStyle = rgba(TOK.bad, 0.06 * A * (1 - prog(lt, state.te, state.te + 0.35))); c.fillRect(0, 0, W, H)
    }
  })

  // ---- claim · audit log: rows stream in, each signed and chained; pauses on the highlighted row
  BLOCKS.log = (cfg, lt, T) => cam(lt, T, (A) => {
    const { W, H } = S
    title(cfg.title, lt, T)
    if (!cfg._rows) {
      const r = rng(cfg.seed ?? 9), rows = [], agents = cfg.agents ?? [['Support agent', 'drafts reply']]
      let tt = -1.2
      while (tt < T) { tt += r.uniform(0.12, 0.32); const [ag, ac] = r.pick(agents); rows.push({ t: tt, ag, ac, ok: r() > 0.12 }) }
      if (cfg.highlight) rows.push({ t: cfg.highlight.at, ag: cfg.highlight.agent, ac: cfg.highlight.action, ok: false, hi: true })
      rows.sort((p, q) => p.t - q.t); cfg._rows = rows
    }
    const PW = 1100, x = W / 2 - PW / 2 - 110, rh = 56, gap = 8, cap = 8, bottom = H / 2 + 330
    const freeze = cfg.highlight ? cfg.highlight.at + 0.3 : Infinity, resume = freeze + 1.0
    const tl = lt < freeze ? lt : lt < resume ? freeze : freeze + (lt - resume)
    const vis = cfg._rows.filter((z) => z.t <= tl)
    vis.forEach((z, idx) => {
      let pos = 0; for (let j = idx + 1; j < vis.length; j++) pos += eo(prog(tl, vis[j].t, vis[j].t + 0.18))
      if (pos > cap + 1) return
      const enter = eo(prog(tl, z.t, z.t + 0.18)), yy = bottom - pos * (rh + gap) + (1 - enter) * rh * 0.5
      const ra = A * enter * (1 - clamp(pos - (cap - 1))); if (ra <= 0.01) return
      if (z.hi) { const hl = prog(lt, freeze, freeze + 0.3); panel(x, yy, PW, rh, ra, rgba(TOK.bad, 0.4 + 0.4 * hl), rgba(TOK.bad, 0.05 + 0.12 * hl)) } else panel(x, yy, PW, rh, ra)
      const by = yy + rh / 2 + 6
      text(clock(z.t + (cfg.start ?? 0)), x + 20, by, 16, { family: TOK.mono, weight: TOK.wReg, col: TOK.t3, a: ra, align: 'left', track: 0.3 })
      text(z.ag, x + 150, by, 21, { weight: TOK.wMed, a: ra, align: 'left', track: 0 })
      text(z.ac, x + 400, by, 21, { weight: TOK.wReg, col: TOK.t2, a: ra, align: 'left', track: 0 })
      mono(z.ok ? 'Allowed' : 'Denied', x + PW - 20, by, 15, z.ok ? TOK.ok : TOK.bad, ra, 'right')
      const sx = x + PW + 46, sy = yy + rh / 2
      ring(sx, sy, 10, TOK.cyan, ra * 0.9, 1.6); dot(sx, sy, 3.5, TOK.cyan, ra)
      if (pos < cap - 1) line(sx, sy - 10, sx, sy - rh - gap + 10, TOK.cyan, ra * 0.45)
    })
    mono(cfg.sealLabel ?? 'Signed', x + PW + 46, bottom - cap * (rh + gap) + rh - 6, 13, TOK.cyan, A, 'center')
    const n = Math.round((cfg.counterStart ?? 10480) + vis.length * 37 + lt * 180)
    mono(cfg.counterLabel ?? 'Decisions recorded', W - 170, H / 2 - 150, 14, TOK.t3, A, 'right')
    text(n.toLocaleString('en-US'), W - 170, H / 2 - 90, 52, { family: TOK.mono, weight: TOK.wMed, a: A, align: 'right', track: 0 })
    if (cfg.highlight && lt > freeze) {
      const k = eo(prog(lt, freeze, freeze + 0.3)) * (1 - prog(lt, resume + 0.3, resume + 0.6))
      mono(cfg.highlight.note ?? '', W - 170, H / 2 - 30, 14, TOK.bad, A * k, 'right')
    }
  })

  // ---- claim · gate on traffic: calls stream through the product gate; risky ones are stopped and named
  BLOCKS['traffic-gate'] = (cfg, lt, T) => cam(lt, T, (A) => {
    const { W, H } = S, c = S.ctx
    title(cfg.title, lt, T)
    const lanes = [[-150, 0.4], [-90, 0.65], [-30, 0.9], [30, 1], [90, 0.8], [150, 0.55], [210, 0.35]]
    if (!cfg._ev) {
      const r = rng(cfg.seed ?? 21), ev = []
      lanes.forEach(([, dep], li) => { let tt = -0.6 + r() * 0.5; while (tt < T + 0.2) { tt += r.expo(2.2 + dep); ev.push({ lane: li, t0: tt, v: r.uniform(0.3, 0.52), len: r.uniform(50, 150), risk: false }) } })
      const cats = cfg.categories ?? ['Data leak', 'Hijack attempt', 'Harmful content']
      ;[[3, 0.1], [1, 0.9], [5, 1.7], [2, 2.5], [4, 3.2]].forEach(([li, t0], k) => ev.push({ lane: li, t0, v: 0.4, len: 120, risk: true, cat: cats[k % cats.length] }))
      cfg._ev = ev
    }
    const X0 = 100, X1 = W - 100, span = X1 - X0, gx = W / 2, yc = H / 2 + 80
    lanes.forEach(([dy, dep]) => line(X0, yc + dy, X1, yc + dy, TOK.hair, A * (0.5 + 0.5 * dep), 1))
    let blocked = 0
    cfg._ev.forEach((e) => {
      const [dy, dep] = lanes[e.lane], y = yc + dy, L = e.len * (0.6 + 0.5 * dep), h = 5 + 9 * dep
      let head = X0 + (lt - e.t0) * e.v * span
      if (e.risk) {
        const hit = e.t0 + (gx - 14 - X0) / (e.v * span)
        if (lt >= hit) {
          blocked++; head = gx - 14; const k = prog(lt, hit, hit + 0.9), rg = eo(prog(lt, hit, hit + 0.5))
          ring(head, y, 10 + 80 * rg, TOK.bad, A * (1 - rg), 2)
          for (let j = 0; j < 7; j++) { const ang = Math.PI / 2 + (j - 3) * 0.38 + Math.PI, d = eo(k) * 90; dot(head + Math.cos(ang) * d, y + Math.sin(ang) * d * 0.6, 2.2, TOK.bad, A * (1 - k)) }
          pill(e.cat, gx - 170, y - 36, TOK.bad, rgba(TOK.bad, 0.7), A * eo(prog(lt, hit, hit + 0.2)) * (1 - prog(lt, hit + 1.0, hit + 1.3)), 15)
          if (A * (1 - k) <= 0.02) return
        }
      }
      if (head < X0 || head - L > X1) return
      const a = A * clamp((head - X0) / 200) * clamp((X1 - (head - L)) / 200) * (0.35 + 0.65 * dep)
      const col = e.risk ? TOK.bad : head < gx ? TOK.cyan : TOK.ink, tail = Math.max(head - L, X0)
      const g = c.createLinearGradient(tail, 0, head, 0); g.addColorStop(0, rgba(col, 0)); g.addColorStop(1, rgba(col, 0.9 * a))
      rrect(tail, y - h / 2, Math.max(1, head - tail), h, h / 2); c.fillStyle = g; c.fill(); dot(head, y, h * 0.42, col, a)
      if (e.risk) glow(head, y, 40, TOK.bad, 0.6 * a)
    })
    rrect(gx - 7, yc - 200, 14, 440, 7); c.fillStyle = rgba(TOK.cyan, A * 0.85); c.fill(); glow(gx, yc + 20, 220, TOK.cyan, 0.12 * A)
    if (cfg.logo) image(cfg.logo, gx - 140, yc - 262, 280, A)
    mono(cfg.counterLabel ?? 'Blocked', W - 170, H / 2 - 150, 14, TOK.t3, A, 'right')
    text(String(blocked), W - 170, H / 2 - 92, 52, { family: TOK.mono, weight: TOK.wMed, col: blocked ? TOK.bad : TOK.ink, a: A, align: 'right', track: 0 })
  })

  // ---- claim · reviewers: an answer is reviewed by three models, rewritten once, then approved
  BLOCKS.reviewers = (cfg, lt, T) => cam(lt, T, (A) => {
    const { W, H } = S
    title(cfg.title, lt, T)
    const cy = H / 2 + 70, cx = W / 2 - 330, cw = 560, ch = 280, rx = W / 2 + 420, revs = [cy - 150, cy, cy + 150]
    const p1 = 0.9, regen = 2.0, p2 = 2.7, app = 3.4, out = 3.9
    const ka = eob(prog(lt, 0.2, 0.7)), sx = (1 - clamp(ka)) * -200 + eio(prog(lt, out, out + 0.5)) * 700, fo = 1 - prog(lt, out + 0.3, out + 0.6)
    const approved = lt >= app, flagged = lt >= p1 + 0.7 && lt < regen + 0.4
    const bcol = approved ? rgba(TOK.ok, 0.8) : flagged ? rgba(TOK.warn, 0.8) : TOK.hair2
    panel(cx - cw / 2 + sx, cy - ch / 2, cw, ch, A * clamp(ka) * fo, bcol)
    mono(cfg.cardLabel ?? 'AI answer', cx - cw / 2 + sx + 28, cy - ch / 2 + 42, 16, TOK.t3, A * fo)
    const r = rng(lt > regen ? 4 : 3), sh = prog(lt, regen, regen + 0.5), c = S.ctx
    for (let i = 0; i < 4; i++) {
      let wl = r.uniform(0.55, 0.95) * (cw - 56); if (sh > 0 && sh < 1) wl *= 0.4 + 0.6 * Math.abs(Math.sin(sh * 9 + i))
      rrect(cx - cw / 2 + sx + 28, cy - ch / 2 + 80 + i * 44, wl, 14, 7); c.fillStyle = rgba(TOK.ink, A * fo * (i ? 0.55 : 0.8)); c.fill()
    }
    if (flagged) mono(cfg.rewriteLabel ?? 'Rewriting', cx + sx + cw / 2 - 28, cy - ch / 2 + 42, 16, TOK.warn, A * fo * (0.6 + 0.4 * Math.sin(lt * 12)), 'right')
    if (approved) mono(cfg.approveLabel ?? 'Approved', cx + sx + cw / 2 - 28, cy - ch / 2 + 42, 16, TOK.ok, A * fo * eo(prog(lt, app, app + 0.2)), 'right')
    const labels = cfg.reviewers ?? ['Reviewer 1', 'Reviewer 2', 'Reviewer 3']
    revs.forEach((yv, i) => {
      dot(rx, yv, 16, TOK.ink, A); ring(rx, yv, 30, TOK.hair2, A, 1.4); mono(labels[i], rx + 56, yv + 7, 17, TOK.t2, A)
      c.save(); c.strokeStyle = rgba(TOK.hair, A); c.lineWidth = 1.2; c.beginPath(); c.moveTo(cx + cw / 2 + sx, cy)
      c.bezierCurveTo(cx + cw / 2 + 150 + sx, cy, rx - 150, yv, rx - 34, yv); c.stroke(); c.restore()
      ;[[p1, [true, true, false]], [p2, [true, true, true]]].forEach(([ps, res]) => {
        const tv = ps + 0.15 + i * 0.18
        if (lt >= ps && lt < tv + 0.15) { const q = prog(lt, ps, tv), bx = lerp(cx + cw / 2 + sx, rx - 34, q), by = lerp(cy, yv, q); dot(bx, by, 4, TOK.cyan, A); glow(bx, by, 16, TOK.cyan, 0.6 * A) }
        if (lt >= tv && (ps === p2 || lt < regen)) { const k = eo(prog(lt, tv, tv + 0.2)); if (res[i]) check(rx, yv, 16, TOK.ok, A, k, 3.5); else cross(rx, yv, 14, TOK.warn, A, k, 3.5) }
      })
    })
    if (approved && lt < out + 0.6) glow(cx + sx, cy, 260, TOK.ok, 0.18 * A * (1 - prog(lt, app, app + 0.8)))
  })

  // ---- claim · budgets: team bars grow in bursts; one hits its cap and pauses
  BLOCKS.budgets = (cfg, lt, T) => cam(lt, T, (A) => {
    const { W, H } = S, c = S.ctx
    title(cfg.title, lt, T)
    const x0 = W / 2 - 560, bw = 1000, top = H / 2 - 90, rh = 118
    let total = 0
    ;(cfg.teams ?? []).forEach(([name, cap, pts], i) => {
      const y = top + i * rh, k = prog(lt, 0.4 + i * 0.08, 3.6), seg = k * (pts.length - 1), j = Math.min(Math.floor(seg), pts.length - 2)
      let frac = lerp(pts[j], pts[j + 1], eio(seg - j)); if (frac < 1) frac = Math.min(1, frac + 0.01 * Math.sin(lt * 13 + i))
      const spent = cap * frac; total += spent
      const ia = A * eo(prog(lt, 0.1 + i * 0.08, 0.5 + i * 0.08))
      text(name, x0, y, 26, { weight: TOK.wMed, a: ia, align: 'left', track: 0 })
      text('$' + Math.round(spent).toLocaleString('en-US'), x0 + bw, y, 16, { family: TOK.mono, weight: TOK.wMed, col: TOK.t2, a: ia, align: 'right', track: 0 })
      const by = y + 28, capx = x0 + bw * 0.9, hit = frac >= 0.999
      rrect(x0, by, bw, 14, 7); c.fillStyle = rgba(TOK.hair, ia); c.fill()
      gradline(x0, by + 7, x0 + (capx - x0) * (hit ? 1 : frac), 14, ia)
      line(capx, by - 12, capx, by + 26, TOK.ink, ia, 2.5); mono('Cap $' + cap.toLocaleString('en-US'), capx + 16, by + 13, 13, TOK.t3, ia)
      if (hit) {
        const ht = lerp(0.4 + i * 0.08, 3.6, pts.indexOf(1) / (pts.length - 1)), kk = eo(prog(lt, ht, ht + 0.25))
        glow(capx, by + 7, 90, TOK.warn, 0.6 * ia * (1 - prog(lt, ht, ht + 0.8)))
        pill(cfg.capLabel ?? 'Cap reached · paused', capx - 150, y - 6, TOK.warn, rgba(TOK.warn, 0.7), ia * kk, 13)
      }
    })
    mono(cfg.totalLabel ?? 'Total AI spend', W / 2 + 560, top - 90, 14, TOK.t3, A, 'right')
    text('$' + Math.round(total).toLocaleString('en-US'), W / 2 + 560, top - 36, 40, { family: TOK.mono, weight: TOK.wMed, a: A, align: 'right', track: 0 })
  })

  // ---- outcome: two lines, the payoff carries the accent; optional proof strip
  BLOCKS.outcome = (cfg, lt, T) => {
    const { W, H } = S, up = cfg.proof ? -90 : 0
    headline(cfg.line1, W / 2, H / 2 - 40 + up, 96, lt, 0.05, T)
    headline(cfg.line2, W / 2, H / 2 + 70 + up, 96, lt, 0.35, T)
    gradline(W / 2 - 180, H / 2 + 140 + up, W / 2 - 180 + 360 * eo(prog(lt, 0.7, 1.3)) * (1 - prog(lt, T - 0.25, T)), 3)
    const [a, d] = appear(lt, 0.9, T)
    mono(cfg.sub ?? '', W / 2, H / 2 + 210 + up + d * 10, 18, TOK.t2, a, 'center')
    if (cfg.proof) {
      const [pa, pd] = appear(lt, 1.3, T, 0.5)
      mono(cfg.proof.label ?? '', W / 2, H - 274 + pd * 12, 14, TOK.t3, pa, 'center')
      const hts = cfg.proof.logos.map((l) => l.h ?? 44), ws = cfg.proof.logos.map((l, i) => { const im = S.imgs[l.src]; return im ? (im.naturalWidth / im.naturalHeight) * hts[i] : 0 })
      let x = W / 2 - (ws.reduce((p, q) => p + q, 0) + 72 * (ws.length - 1)) / 2
      cfg.proof.logos.forEach((l, i) => { image(l.src, x, H - 200 + pd * 12 - hts[i] / 2, ws[i], 0.72 * pa * eo(prog(lt, 1.45 + i * 0.06, 1.9 + i * 0.06))); x += ws[i] + 72 })
    }
  }

  // ---- end card: product lockup, call to action, URL; brand logo bottom-left
  BLOCKS.endcard = (cfg, lt, T) => {
    const { W, H } = S, pw = 760, ph = imgH(cfg.logo, pw) || 110
    const [a, d] = appear(lt, 0, T + 1, 0.5, 0.0001)
    image(cfg.logo, W / 2 - pw / 2, H / 2 - ph / 2 - 120 + d * 18, pw, a)
    const [a2, d2] = appear(lt, 0.4, T + 1, 0.5, 0.0001)
    text(cfg.cta ?? 'Try it now', W / 2, H / 2 + 80 + d2 * 14, 64, { a: a2 })
    const [a3, d3] = appear(lt, 0.65, T + 1, 0.5, 0.0001)
    const s = cfg.url ?? ''
    if (s) {
      const w = measure(s, 30, TOK.wMed, TOK.mono, 1.2), bw = w + 72, bh = 68, by = H / 2 + 130 + d3 * 10
      panel(W / 2 - bw / 2, by, bw, bh, a3, TOK.hair2)
      text(s, W / 2, by + bh / 2 + 11, 30, { family: TOK.mono, weight: TOK.wMed, a: a3, track: 1.2 })
    }
    const [a4] = appear(lt, 0.9, T + 1, 0.5, 0.0001), lw = 200, lh = imgH(cfg.brandLogo, lw) || 35
    image(cfg.brandLogo, 96, H - 96 - lh, lw, a4)
    if (cfg.signoff) mono(cfg.signoff, W - 96, H - 96 - lh * 0.15, 15, TOK.t2, a4, 'right')
  }

  // ================================================================== engine
  function build(cfg) {
    let t = 0
    cfg.scenes.forEach((sc) => {
      const def = sc.type.startsWith('hook') ? TOK.dur.hook : sc.type === 'intro' ? TOK.dur.intro : sc.type === 'outcome' ? TOK.dur.outcome : sc.type === 'endcard' ? TOK.dur.endcard : TOK.dur.claim
      sc.start = t; sc.dur = sc.dur ?? def; t += sc.dur
    })
    cfg.duration = cfg.duration ?? t
    return cfg
  }
  function renderAt(t) {
    const cfg = S.cfg, c = S.ctx
    c.setTransform(S.dpr, 0, 0, S.dpr, 0, 0)
    background(); hud(t)
    cfg.scenes.forEach((sc) => {
      if (t < sc.start - 0.01 || t >= sc.start + sc.dur + 0.01) return
      const fn = BLOCKS[sc.type]; if (!fn) return
      c.save(); fn(sc, clamp(t - sc.start, 0, sc.dur), sc.dur, t); c.restore()
    })
    cfg.scenes.forEach((sc, i) => { if (i > 0) scan(t, sc.start) })
  }

  async function boot() {
    const cfgEl = document.getElementById('mcv-script'), canvas = document.getElementById('mcv-canvas')
    if (!cfgEl || !canvas) return
    const cfg = build(JSON.parse(cfgEl.textContent))
    const params = new URLSearchParams(location.search), render = params.has('render')
    S.cfg = cfg; S.W = cfg.width ?? 1920; S.H = cfg.height ?? 1080
    S.dpr = Math.min(2, window.devicePixelRatio || 1)  // the renderer runs at deviceScaleFactor 2 → 4K backing, downscaled by ffmpeg
    canvas.width = S.W * S.dpr; canvas.height = S.H * S.dpr
    if (render) { document.documentElement.classList.add('mcv-render'); canvas.style.width = S.W + 'px'; canvas.style.height = S.H + 'px' }
    S.ctx = canvas.getContext('2d')
    // preload every image the script references
    const srcs = new Set()
    cfg.scenes.forEach((sc) => { ['logo', 'brandLogo'].forEach((k) => sc[k] && srcs.add(sc[k])); sc.proof && sc.proof.logos.forEach((l) => srcs.add(l.src)) })
    await Promise.all([...srcs].map((src) => new Promise((res) => { const im = new Image(); im.onload = () => { S.imgs[src] = im; res() }; im.onerror = () => { console.warn('[mc-video] missing image', src); res() }; im.src = src })))
    if (document.fonts && document.fonts.ready) await document.fonts.ready
    document.documentElement.classList.add('mcv-ready')

    const api = { renderAt, duration: cfg.duration, fps: cfg.fps ?? TOK.fps, width: S.W, height: S.H, playing: false, t: 0 }
    const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches
    const poster = cfg.poster ?? cfg.scenes[cfg.scenes.length - 1].start + 2.5
    if (!render) wireTransport(api, reduce ? poster : 0, !reduce)
    else renderAt(0)
    window.MCVideo = api
    window.MC = { seek: (t) => api.renderAt(t), renderAt: (t) => api.renderAt(t) }
    return api
  }

  function wireTransport(api, t0, autoplay) {
    const bar = document.getElementById('mcv-transport'); if (!bar) { renderAt(t0); return }
    const btn = bar.querySelector('[data-play]'), range = bar.querySelector('[data-scrub]'), out = bar.querySelector('[data-time]')
    range.max = String(api.duration); range.step = String(1 / api.fps)
    let last = 0
    const show = (t) => { api.t = t; renderAt(t); range.value = String(t); out.textContent = `${t.toFixed(2)}s / ${api.duration.toFixed(1)}s · frame ${Math.round(t * api.fps)}` }
    const tick = (now) => { if (!api.playing) return; const dt = (now - last) / 1000; last = now; let t = api.t + dt; if (t >= api.duration) t = 0; show(t); requestAnimationFrame(tick) }
    api.play = () => { if (api.playing) return; api.playing = true; btn.textContent = 'Pause'; last = performance.now(); requestAnimationFrame(tick) }
    api.pause = () => { api.playing = false; btn.textContent = 'Play' }
    btn.addEventListener('click', () => (api.playing ? api.pause() : api.play()))
    range.addEventListener('input', () => { api.pause(); show(parseFloat(range.value)) })
    document.addEventListener('keydown', (e) => {
      if (e.code === 'Space') { e.preventDefault(); api.playing ? api.pause() : api.play() }
      if (e.code === 'ArrowRight' || e.code === 'ArrowLeft') { api.pause(); show(clamp(api.t + (e.code === 'ArrowRight' ? 1 : -1) / api.fps, 0, api.duration)) }
    })
    show(t0); if (autoplay) api.play()
  }

  window.MCVideoKit = { BLOCKS, TOK, clamp, prog, lerp, eo, eio, rng, appear, text, mono, panel, glow, dot, ring, line, gradline, pill, check, cross, headline, cam }
  const readyP = new Promise((res) => { const go = () => boot().then(res); document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', go) : go() })
  window.MCVideoReady = readyP
})()
