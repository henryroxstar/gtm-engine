#!/usr/bin/env node
/*
 * render.mjs — headless renderer for Mission Control motion-graphics scripts.
 * Supports:
 *   1. Native CDP via installed Google Chrome / Chromium (zero npm dependencies)
 *   2. Mock mode for headless CI environments
 */
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../..')
const ASSETS_DIR = path.join(HERE, 'assets')

const args = process.argv.slice(2)
const opt = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d }
const flag = (k) => args.includes(`--${k}`)

const scriptPath = args.find((a) => a.endsWith('.json'))
if (!scriptPath) {
  console.error('usage: node render.mjs script.json --out dir [--scale 1|2] [--stills t1,t2,...] [--poster t] [--preview-only] [--frames-dir dir] [--mock]')
  process.exit(1)
}

const script = JSON.parse(fs.readFileSync(scriptPath, 'utf8'))
const outDir = path.resolve(opt('out', 'video-out'))
const scale = Number(opt('scale', 1))
const previewOnly = flag('preview-only')
const mockMode = flag('mock') || process.env.GTM_MOTION_RENDERER_MOCK === '1'
const framesDir = opt('frames-dir') ? path.resolve(opt('frames-dir')) : path.join(outDir, 'frames')
const safeSegment = (v, label) => {
  if (!v || v.includes('/') || v.includes('\\') || v.includes('..') || v === '.' || v.startsWith('$')) {
    throw new Error(`Unsafe ${label}: ${v}`)
  }
  return v
}
const profile = safeSegment(opt('profile', '_template'), 'profile')
const product = opt('product') ? safeSegment(opt('product'), 'product') : null

// Calculate beat times if not provided
const scenes = script.scenes || []
let curT = 0
scenes.forEach((sc) => {
  const d = sc.dur || (sc.type.startsWith('hook') ? 3.6 : sc.type === 'intro' ? 3.0 : sc.type === 'outcome' ? 4.5 : sc.type === 'endcard' ? 5.1 : 4.6)
  sc.start = curT
  curT += d
})
const totalDur = script.duration || curT
const fps = script.fps || 25
const totalFrames = Math.round(totalDur * fps)

// Default 4 beat stills times: early hook, mid scene, late scene, outcome/endcard
let stillsTimes = []
if (opt('stills')) {
  stillsTimes = opt('stills').split(',').map(Number).filter((n) => !isNaN(n))
} else {
  stillsTimes = [
    Math.min(1.5, totalDur * 0.1),
    totalDur * 0.35,
    totalDur * 0.65,
    Math.max(totalDur - 1.5, totalDur * 0.9),
  ]
}

const posterTime = opt('poster') !== undefined ? Number(opt('poster')) : (script.poster !== undefined ? Number(script.poster) : Math.max(0, totalDur - 1.5))

// ------------------------------------------------------------ 1. Setup Preview Directory
const prevDir = path.join(outDir, 'preview')
fs.mkdirSync(prevDir, { recursive: true })

// Copy core CSS and JS runtime
fs.copyFileSync(path.join(ASSETS_DIR, 'mc-video.css'), path.join(prevDir, 'mc-video.css'))
fs.copyFileSync(path.join(ASSETS_DIR, 'mc-video.js'), path.join(prevDir, 'mc-video.js'))

// Copy tokens.css from active profile or assets fallback if not already pre-compiled
const previewTokens = path.join(prevDir, 'tokens.css')
if (!fs.existsSync(previewTokens)) {
  const profileTokens = path.join(ROOT, `profiles/${profile}/knowledge/brand/tokens.css`)
  if (fs.existsSync(profileTokens)) {
    fs.copyFileSync(profileTokens, previewTokens)
  } else {
    fs.copyFileSync(path.join(ASSETS_DIR, 'tokens.css'), previewTokens)
  }
}

// Copy standin fonts into preview/fonts/
const fontsDir = path.join(prevDir, 'fonts')
fs.mkdirSync(fontsDir, { recursive: true })
const profileFonts = path.join(ROOT, `profiles/${profile}/knowledge/brand/fonts`)
if (fs.existsSync(profileFonts)) {
  for (const f of fs.readdirSync(profileFonts)) {
    if (f.endsWith('.ttf')) {
      fs.copyFileSync(path.join(profileFonts, f), path.join(fontsDir, f))
    }
  }
}

// Copy logos
const logosDir = path.join(prevDir, 'logos')
fs.mkdirSync(logosDir, { recursive: true })
const copyDirRecursive = (src, dest) => {
  if (!fs.existsSync(src)) return
  fs.mkdirSync(dest, { recursive: true })
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name), d = path.join(dest, entry.name)
    if (entry.isDirectory()) copyDirRecursive(s, d)
    else fs.copyFileSync(s, d)
  }
}
const profileLogos = path.join(ROOT, `profiles/${profile}/knowledge/brand/logos`)
if (fs.existsSync(profileLogos)) {
  copyDirRecursive(profileLogos, logosDir)
}

// Build index.html from template.html
const endScene = scenes.find((s) => s.type === 'endcard') || {}
const outcomeScene = scenes.find((s) => s.type === 'outcome') || {}
const esc = (s) => String(s ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;')
const posterLine = esc([outcomeScene.line1, outcomeScene.line2].filter(Boolean).join(' '))
  .replace(/\[(.+?)\]/, '<span class="accent">$1</span>')
  .replace(/[[\]]/g, '')

const safeScriptJson = JSON.stringify(script, null, 2).replace(/</g, '\\u003c')
const rawTypekitUrl = String(opt('typekit-url', '') || '').trim()
const isOfflineRender = ['1', 'true', 'yes'].includes(String(process.env.GTM_OFFLINE_RENDER || '').trim().toLowerCase())
const validTypekitUrl = (!isOfflineRender && /^https:\/\/use\.typekit\.net\/[a-z0-9]{5,12}\.css$/.test(rawTypekitUrl))
  ? `<link rel="stylesheet" href="${rawTypekitUrl}">`
  : ''

const templateHtml = fs.readFileSync(path.join(ASSETS_DIR, 'template.html'), 'utf8')
  .replace('<!--__TYPEKIT_LINK__-->', validTypekitUrl)
  .replaceAll('__TITLE__', esc(script.title || `${script.product || 'Mission Control'} · video`))
  .replace('__POSTER_LOGO__', esc(endScene.logo || 'logos/brand/logo.svg'))
  .replace('__POSTER_LINE__', posterLine)
  .replace('__URL__', esc(endScene.url || ''))
  .replace('__SCRIPT_JSON__', safeScriptJson)

fs.writeFileSync(path.join(prevDir, 'index.html'), templateHtml, 'utf8')
console.error(`[mc-video] Generated interactive preview: ${path.join(prevDir, 'index.html')}`)

// ------------------------------------------------------------ 2. Rendering Engines (CDP / Mock)
const stillsDir = path.join(outDir, 'stills')
fs.mkdirSync(stillsDir, { recursive: true })
if (!previewOnly) {
  fs.mkdirSync(framesDir, { recursive: true })
}

const findChromeBinary = () => {
  const candidates = [
    process.env.CHROME_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ].filter(Boolean)
  for (const p of candidates) {
    if (fs.existsSync(p)) return p
  }
  return null
}

import zlib from 'node:zlib'

function createPng(width, height, r = 10, g = 24, b = 42) {
  const lineSize = 1 + width * 3
  const raw = Buffer.alloc(lineSize * height)
  for (let y = 0; y < height; y++) {
    const offset = y * lineSize
    raw[offset] = 0
    for (let x = 0; x < width; x++) {
      const px = offset + 1 + x * 3
      raw[px] = r; raw[px + 1] = g; raw[px + 2] = b
    }
  }
  const compressed = zlib.deflateSync(raw)
  const crcTable = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1)
    crcTable[n] = c
  }
  function crc32(buf) {
    let c = 0xffffffff
    for (let i = 0; i < buf.length; i++) c = crcTable[(c ^ buf[i]) & 0xff] ^ (c >>> 8)
    return (c ^ 0xffffffff) >>> 0
  }
  function chunk(type, data) {
    const len = Buffer.alloc(4)
    len.writeUInt32BE(data.length, 0)
    const typeBuf = Buffer.from(type, 'ascii')
    const toCrc = Buffer.concat([typeBuf, data])
    const crcBuf = Buffer.alloc(4)
    crcBuf.writeUInt32BE(crc32(toCrc), 0)
    return Buffer.concat([len, typeBuf, data, crcBuf])
  }
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  const ihdrData = Buffer.alloc(13)
  ihdrData.writeUInt32BE(width, 0); ihdrData.writeUInt32BE(height, 4)
  ihdrData[8] = 8; ihdrData[9] = 2; ihdrData[10] = 0; ihdrData[11] = 0; ihdrData[12] = 0
  return Buffer.concat([sig, chunk('IHDR', ihdrData), chunk('IDAT', compressed), chunk('IEND', Buffer.alloc(0))])
}

async function renderWithMock() {
  console.error('[mc-video] Using mock renderer')
  const validPng = createPng(1920 * scale, 1080 * scale)

  for (let idx = 0; idx < stillsTimes.length; idx++) {
    const fn = `still-${String(idx + 1).padStart(2, '0')}.png`
    fs.writeFileSync(path.join(stillsDir, fn), validPng)
  }
  fs.writeFileSync(path.join(outDir, 'poster.png'), validPng)

  if (!previewOnly) {
    for (let i = 0; i < totalFrames; i++) {
      const fn = `frame-${String(i).padStart(4, '0')}.png`
      fs.writeFileSync(path.join(framesDir, fn), validPng)
      if (i % Math.max(1, Math.floor(totalFrames / 10)) === 0 || i === totalFrames - 1) {
        const pct = Math.round(((i + 1) / totalFrames) * 100)
        console.error(`[mc-video] Frame ${i + 1}/${totalFrames} (${pct}%)`)
      }
    }
  }
}

async function renderWithChrome(chromePath) {
  const mimeTypes = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.png': 'image/png', '.ttf': 'font/ttf' }
  const server = http.createServer((req, res) => {
    const rawUrl = new URL(req.url, 'http://localhost')
    const filePath = path.join(prevDir, decodeURIComponent(rawUrl.pathname))
    if (!filePath.startsWith(prevDir) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404); res.end(); return
    }
    res.writeHead(200, { 'Content-Type': mimeTypes[path.extname(filePath)] || 'application/octet-stream' })
    fs.createReadStream(filePath).pipe(res)
  })

  await new Promise((res) => server.listen(0, '127.0.0.1', res))
  const port = server.address().port
  const targetUrl = `http://127.0.0.1:${port}/index.html?render`

  const proc = spawn(chromePath, [
    '--headless=new',
    '--remote-debugging-port=0',
    '--disable-gpu',
    '--no-sandbox',
    '--font-render-hinting=none',
    '--disable-lcd-text',
    '--window-size=1920,1080',
  ])

  let ws = null
  try {
    let wsEndpoint = null
    await new Promise((resolve, reject) => {
      const to = setTimeout(() => reject(new Error('Timeout launching Chrome')), 15000)
      proc.stderr.on('data', (chunk) => {
        const str = chunk.toString()
        const m = str.match(/DevTools listening on (ws:\/\/[^\s]+)/)
        if (m && !wsEndpoint) {
          wsEndpoint = m[1]
          clearTimeout(to)
          resolve()
        }
      })
      proc.on('error', reject)
    })

    ws = new WebSocket(wsEndpoint)
    let msgId = 1
    const pending = new Map()

    const failPending = (err) => {
      for (const [id, req] of pending.entries()) {
        pending.delete(id)
        req.reject(err)
      }
    }

    ws.onmessage = (e) => {
      const data = JSON.parse(e.data)
      if (data.id && pending.has(data.id)) {
        const { resolve, reject } = pending.get(data.id)
        pending.delete(data.id)
        if (data.error) reject(new Error(data.error.message))
        else resolve(data.result)
      }
    }

    ws.onclose = () => {
      failPending(new Error('CDP WebSocket closed unexpectedly'))
    }

    ws.onerror = (e) => {
      failPending(new Error(`CDP WebSocket error: ${e.message || e}`))
    }

    proc.on('exit', (code, signal) => {
      failPending(new Error(`Chrome process exited unexpectedly with code ${code}, signal ${signal}`))
    })

    const send = (method, params = {}, sessionId = undefined, timeoutMs = 30000) => new Promise((resolve, reject) => {
      const curId = msgId++
      const timer = setTimeout(() => {
        if (pending.has(curId)) {
          pending.delete(curId)
          reject(new Error(`CDP command ${method} timed out after ${timeoutMs}ms`))
        }
      }, timeoutMs)
      pending.set(curId, {
        resolve: (val) => { clearTimeout(timer); resolve(val); },
        reject: (err) => { clearTimeout(timer); reject(err); },
      })
      const msg = { id: curId, method, params }
      if (sessionId) msg.sessionId = sessionId
      ws.send(JSON.stringify(msg))
    })

    await new Promise((res) => { ws.onopen = res })

    // Create target and attach with flat protocol
    const target = await send('Target.createTarget', { url: targetUrl })
    const { sessionId } = await send('Target.attachToTarget', { targetId: target.targetId, flatten: true })

    // Enable Page and Runtime
    await send('Page.enable', {}, sessionId)
    await send('Runtime.enable', {}, sessionId)

    // Set viewport resolution
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1920,
      height: 1080,
      deviceScaleFactor: scale,
      mobile: false,
    }, sessionId)

    // Wait for document font loading and readiness
    await new Promise((r) => setTimeout(r, 600))
    await send('Runtime.evaluate', { expression: 'window.MCVideoReady', awaitPromise: true }, sessionId)

    const captureFrame = async (t) => {
      await send('Runtime.evaluate', { expression: `window.MCVideo.renderAt(${t})` }, sessionId)
      const shot = await send('Page.captureScreenshot', { format: 'png' }, sessionId)
      return Buffer.from(shot.data, 'base64')
    }

    // Capture stills
    for (let i = 0; i < stillsTimes.length; i++) {
      const t = stillsTimes[i]
      const buf = await captureFrame(t)
      const fn = `still-${String(i + 1).padStart(2, '0')}.png`
      fs.writeFileSync(path.join(stillsDir, fn), buf)
    }
    console.error(`[mc-video] Captured ${stillsTimes.length} stills in ${stillsDir}`)

    // Capture poster
    const posterBuf = await captureFrame(posterTime)
    fs.writeFileSync(path.join(outDir, 'poster.png'), posterBuf)

    // Capture full frame sequence if not preview-only
    if (!previewOnly) {
      const startTime = Date.now()
      for (let i = 0; i < totalFrames; i++) {
        const t = i / fps
        const buf = await captureFrame(t)
        const fn = `frame-${String(i).padStart(4, '0')}.png`
        fs.writeFileSync(path.join(framesDir, fn), buf)

        if (i % Math.max(1, Math.floor(totalFrames / 10)) === 0 || i === totalFrames - 1) {
          const pct = Math.round(((i + 1) / totalFrames) * 100)
          const elapsed = (Date.now() - startTime) / 1000
          const fpsRate = (i + 1) / (elapsed || 0.001)
          const rem = Math.max(0, (totalFrames - (i + 1)) / fpsRate)
          console.error(`[mc-video] Frame ${i + 1}/${totalFrames} (${pct}%) | ETA ${rem.toFixed(1)}s`)
        }
      }
    }
  } finally {
    if (ws) {
      try { ws.close() } catch {}
    }
    try { proc.kill() } catch {}
    try { server.close() } catch {}
  }
}

// ------------------------------------------------------------ 3. Main Runner
async function main() {
  const chromeBin = findChromeBinary()
  if (mockMode || !chromeBin) {
    if (!mockMode && !chromeBin) {
      console.warn('[mc-video] Chrome not detected on system, using deterministic mock renderer.')
    }
    await renderWithMock()
  } else {
    await renderWithChrome(chromeBin)
  }
  console.error('[mc-video] Render complete.')
}

main().then(() => {
  process.exit(0)
}).catch((err) => {
  console.error('[mc-video] Fatal render error:', err)
  process.exit(1)
})
