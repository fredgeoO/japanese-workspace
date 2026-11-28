/**
 * Host half of @local/jp-graph（日語學習 · 知識圖譜）。
 *
 * 職責只有一件：把面板發來的查詢轉給工作區的 python 引擎
 * `scripts/jgraph.py --serve`，並把它算出的「語法 ↔ 單詞 ↔ 例句」關聯回給前端。
 *
 * 資料庫只讀，引擎常駐（一行一個 JSON），結果有短期快取。
 */
import path from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export const name = 'jp-graph'
export const inject = ['webServer']

const API_PREFIX = '/jp-graph'
const API_PATH = `${API_PREFIX}/api`
const here = path.dirname(fileURLToPath(import.meta.url))

const START_TIMEOUT_MS = 20_000
const REQUEST_TIMEOUT_MS = 20_000
const CACHE_TTL_MS = 5 * 60 * 1000
const CACHE_MAX = 400

function str(value, max = 2_000) {
  if (typeof value !== 'string') return ''
  const text = value.trim()
  return text.length > max ? text.slice(0, max) : text
}

function isLoopback(address) {
  return address === '127.0.0.1' || address === '::1' || address === '::ffff:127.0.0.1'
}

function trustedRequest(req) {
  if (!isLoopback(req.socket?.remoteAddress)) return false
  const host = req.headers?.host
  const origin = req.headers?.origin
  if (origin !== undefined && origin !== '') {
    try {
      if (new URL(origin).host !== host) return false
    } catch {
      return false
    }
  }
  return true
}

function sendJson(res, status, body) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  })
  res.end(JSON.stringify(body))
}

async function readBody(req, limit = 256 * 1024) {
  let body = ''
  let bytes = 0
  for await (const chunk of req) {
    bytes += chunk.length
    if (bytes > limit) {
      const error = new Error('request body too large')
      error.status = 413
      throw error
    }
    body += chunk
  }
  return body
}

/** 常駐 python worker：stdin/stdout 一行一個 JSON，請求序列化排隊。 */
class GraphWorker {
  constructor(options) {
    this.python = options.python
    this.script = options.script
    this.cwd = options.cwd
    this.log = options.log
    this.child = null
    this.buffer = ''
    this.pending = []
    this.tail = Promise.resolve()
    this.closed = false
  }

  start() {
    if (this.child) return this.child
    const child = spawn(this.python, ['-X', 'utf8', this.script, '--serve'], {
      cwd: this.cwd,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      this.buffer += chunk
      let index = this.buffer.indexOf('\n')
      while (index >= 0) {
        const line = this.buffer.slice(0, index)
        this.buffer = this.buffer.slice(index + 1)
        if (line.trim()) this.settle(line)
        index = this.buffer.indexOf('\n')
      }
    })
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk) => {
      const text = String(chunk).trim()
      if (text) this.log(`[jp-graph] engine: ${text.slice(0, 800)}`)
    })
    child.on('error', (error) => {
      this.log(`[jp-graph] engine spawn failed: ${error?.message ?? error}`)
      this.failAll(new Error(`無法啟動日語資料引擎：${error?.message ?? error}`))
    })
    child.on('exit', (code) => {
      if (this.child === child) this.child = null
      this.buffer = ''
      this.failAll(new Error(`日語資料引擎已停止（exit ${code ?? '?'}）`))
    })
    this.child = child
    return child
  }

  settle(line) {
    const request = this.pending.shift()
    if (!request) return
    clearTimeout(request.timer)
    try {
      request.resolve(JSON.parse(line))
    } catch {
      request.resolve({ ok: false, error: '引擎回傳了無法解析的內容' })
    }
  }

  failAll(error) {
    const pending = this.pending
    this.pending = []
    for (const request of pending) {
      clearTimeout(request.timer)
      request.resolve({ ok: false, error: error.message })
    }
  }

  stop() {
    this.closed = true
    if (this.child) {
      try {
        this.child.stdin.write('{"op":"shutdown"}\n')
        this.child.stdin.end()
      } catch {}
      const child = this.child
      setTimeout(() => {
        if (this.child === child) child.kill()
      }, 500).unref?.()
    }
    this.child = null
  }

  request(payload) {
    const run = () => new Promise((resolve) => {
      if (this.closed) {
        resolve({ ok: false, error: '引擎已關閉' })
        return
      }
      let child
      try {
        child = this.start()
      } catch (error) {
        resolve({ ok: false, error: `無法啟動引擎：${error?.message ?? error}` })
        return
      }
      const timer = setTimeout(() => {
        this.log('[jp-graph] engine request timed out; restarting')
        try {
          this.child?.kill()
        } catch {}
        this.child = null
        this.failAll(new Error('引擎回應逾時'))
        resolve({ ok: false, error: '引擎回應逾時' })
      }, REQUEST_TIMEOUT_MS)
      this.pending.push({ resolve, timer })
      try {
        child.stdin.write(`${JSON.stringify(payload)}\n`)
      } catch (error) {
        clearTimeout(timer)
        this.pending.pop()
        resolve({ ok: false, error: `寫入引擎失敗：${error?.message ?? error}` })
      }
    })
    const next = this.tail.then(run, run)
    this.tail = next.then(() => {}, () => {})
    return next
  }
}

function resolvePaths(config) {
  const workspace = path.resolve(str(config?.workspace, 4_000) || path.join(here, '..', '..'))
  const python = str(config?.python, 4_000) || path.join(workspace, '.venv', 'Scripts', 'python.exe')
  const script = path.join(workspace, 'scripts', 'jgraph.py')
  return { workspace, python, script }
}

function createApiHandler(worker) {
  const cache = new Map()

  function cacheKey(payload) {
    return JSON.stringify(payload)
  }

  function fromCache(key) {
    const hit = cache.get(key)
    if (!hit) return null
    if (Date.now() - hit.at > CACHE_TTL_MS) {
      cache.delete(key)
      return null
    }
    return hit.value
  }

  function toCache(key, value) {
    if (cache.size >= CACHE_MAX) {
      const oldest = cache.keys().next().value
      cache.delete(oldest)
    }
    cache.set(key, { value, at: Date.now() })
  }

  return async function handle(req, res) {
    let url
    try {
      url = new URL(req.url ?? '/', 'http://127.0.0.1')
    } catch {
      sendJson(res, 400, { ok: false, error: 'bad url' })
      return
    }
    if (url.pathname !== API_PATH && url.pathname !== `${API_PATH}/`) {
      sendJson(res, 404, { ok: false, error: 'not found' })
      return
    }
    if (!trustedRequest(req)) {
      sendJson(res, 403, { ok: false, error: 'forbidden' })
      return
    }
    if (req.method === 'GET') {
      sendJson(res, 200, await worker.request({ op: 'hello' }))
      return
    }
    if (req.method !== 'POST') {
      sendJson(res, 405, { ok: false, error: 'method not allowed' })
      return
    }
    if (!/^application\/json\b/i.test(String(req.headers?.['content-type'] ?? ''))) {
      sendJson(res, 415, { ok: false, error: 'content-type must be application/json' })
      return
    }
    let payload
    try {
      const body = await readBody(req)
      payload = JSON.parse(body === '' ? '{}' : body)
    } catch (error) {
      sendJson(res, error?.status === 413 ? 413 : 400, {
        ok: false,
        error: error?.status === 413 ? 'request body too large' : 'invalid JSON body',
      })
      return
    }
    if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
      sendJson(res, 400, { ok: false, error: 'body must be a JSON object' })
      return
    }
    const allowed = new Set(['hello', 'home', 'search', 'facets', 'facet', 'node'])
    if (!allowed.has(payload.op)) {
      sendJson(res, 400, { ok: false, error: `unsupported op: ${String(payload.op)}` })
      return
    }
    const key = cacheKey(payload)
    const hit = fromCache(key)
    if (hit) {
      sendJson(res, 200, hit)
      return
    }
    const value = await worker.request(payload)
    // 首頁與分類面向很便宜，而且會隨資料／引擎改動而變；不快取，避免舊結果卡住。
    if (value?.ok && payload.op !== 'home' && payload.op !== 'facets') toCache(key, value)
    sendJson(res, 200, value)
  }
}

export function apply(ctx, config = {}) {
  const paths = resolvePaths(config)
  const worker = new GraphWorker({
    python: paths.python,
    script: paths.script,
    cwd: paths.workspace,
    log: (message) => console.log(message),
  })
  ctx.effect(() => ctx.webServer.register({
    kind: 'prefix',
    path: API_PREFIX,
    handler: createApiHandler(worker),
  }))
  ctx.effect(() => () => worker.stop())
  console.log(`[jp-graph] engine: ${paths.python} ${paths.script}`)
}
