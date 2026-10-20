// StateJar memory for OpenCode: recall before the model answers, batched capture with secrets removed, and local
// status / pause / resume tools. One ES module with no dependencies (OpenCode runs it in Bun; the tests run it in
// Node 20+). The tools StateJar itself offers (search, remember, get, list, update, delete, history, audit, verify,
// export) come from its MCP server, which opencode.json adds (see README.md).
//
// What leaves this machine, and when: a recall sends the question (the user's message) to StateJar's query endpoint;
// a flush sends the queued messages, joined, to the ingest endpoint, where StateJar extracts typed facts and stores no
// transcript. Secrets are removed before a message is queued. Nothing here logs a message, a fact or a key; the
// state file keeps counts, times and the last status code only. Same queue, patterns and batching as the Claude Code
// and Codex plugin's statejar_hook.py.

import fs from "node:fs"
import os from "node:os"
import path from "node:path"

const VERSION = "0.1.0"
const DEFAULT_API = "https://api.statejar.com/api/v1"
const DEFAULT_SCOPE = "opencode"
const BATCH_SIZE = 3 // messages per flush when a session goes idle
const BATCH_AGE_S = 600 // or when the oldest queued message is this old
const MAX_BATCH_CHARS = 18_000 // one ingest carries at most this much (the MCP limit is 20,000)
const MAX_CONTEXT_CHARS = 9_000
const SESSION_QUERY = "Who is the user, what do they prefer, and what are they working on?"

// --- options --------------------------------------------------------------------------------------------------------

function env(name) {
  return (process.env[`STATEJAR_${name.toUpperCase()}`] || "").trim()
}

function configDir() {
  if (process.env.STATEJAR_CONFIG_DIR) return process.env.STATEJAR_CONFIG_DIR
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"), "statejar")
  }
  return path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"), "statejar")
}

/** Options from the plugin's entry in opencode.json, then STATEJAR_* variables, then defaults. */
function readOptions(given = {}) {
  const pick = (key, name, fallback) => {
    const v = given[key] ?? env(name)
    return v === undefined || v === "" ? fallback : v
  }
  const flag = (key, name, fallback) => {
    const v = pick(key, name, fallback)
    return typeof v === "boolean" ? v : ["1", "true", "yes", "on"].includes(String(v).toLowerCase())
  }
  let apiKey = String(pick("apiKey", "api_key", "")).trim()
  if (!apiKey) {
    try {
      apiKey = String(JSON.parse(fs.readFileSync(path.join(configDir(), "credentials.json"), "utf8")).api_key || "")
    } catch {
      apiKey = "" // no `statejar login` key either
    }
  }
  const scope = String(pick("scope", "scope", DEFAULT_SCOPE))
  return {
    apiKey: apiKey.trim(),
    apiUrl: String(pick("apiUrl", "api_url", process.env.STATEJAR_BASE_URL || DEFAULT_API)).replace(/\/+$/, ""),
    scope: /^[A-Za-z0-9._:-]{1,64}$/.test(scope) ? scope : DEFAULT_SCOPE,
    autoCapture: flag("autoCapture", "auto_capture", true),
    recallEachPrompt: flag("recallEachPrompt", "recall_each_prompt", true),
    dataDir: String(pick("dataDir", "data_dir", path.join(configDir(), "clients", "opencode"))),
  }
}

// --- secrets out before anything is queued --------------------------------------------------------------------------

const SECRET_PATTERNS = [
  ["private key", /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)/g],
  ["api key", /\bsj_live_[A-Za-z0-9_-]{16,}/g],
  ["api key", /\bsk-(?:proj-|ant-(?:api\d{2}-)?|or-v1-)?[A-Za-z0-9_-]{20,}/g],
  ["api key", /\bAIza[0-9A-Za-z_-]{35}\b/g],
  ["api key", /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b/g],
  ["api key", /\bgithub_pat_[A-Za-z0-9_]{50,}\b/g],
  ["api key", /\bglpat-[A-Za-z0-9_-]{20,}\b/g],
  ["api key", /\bxox[abposr]-[A-Za-z0-9-]{10,}\b/g],
  ["api key", /\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b/g],
  ["api key", /\bhf_[A-Za-z0-9]{30,}\b/g],
  ["api key", /\bnpm_[A-Za-z0-9]{36}\b/g],
  ["api key", /\bpypi-[A-Za-z0-9_-]{50,}\b/g],
  ["cloud key", /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g],
  ["token", /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g],
  ["token", /\bbearer\s+[A-Za-z0-9._~+/=-]{16,}/gi],
  ["password", /\b[a-z][a-z0-9+.-]*:\/\/[^\s:/@]+:[^\s@/]+@/gi],
  [
    "password",
    /\b(?:password|passwd|passcode|pass\s*code|pin|otp|cvv|cvc|secret|api[ _-]?key|access[ _-]?token|auth[ _-]?token|private[ _-]?key|client[ _-]?secret)\b(?:\s+(?:is|was|=|:))?\s*[:=]?\s*["']?(?=[^\s"']*[A-Za-z0-9])([^\s"',;]{4,})/dgi,
  ],
]
const CARD = /\b\d(?:[ -]?\d){12,18}\b/g

function luhn(digits) {
  let total = 0
  let odd = false
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = Number(digits[i])
    if (odd) d = d > 4 ? d * 2 - 9 : d * 2
    total += d
    odd = !odd
  }
  return total % 10 === 0
}

/** Every pattern against the original text, the spans replaced in one pass (as statejar_hook.py and the server do). */
function scrub(text) {
  const spans = []
  for (const [kind, rx] of SECRET_PATTERNS) {
    for (const m of text.matchAll(rx)) {
      if (m.indices && m.indices[1]) spans.push([m.indices[1][0], m.indices[1][1], kind])
      else spans.push([m.index, m.index + m[0].length, kind])
    }
  }
  for (const m of text.matchAll(CARD)) {
    if (luhn(m[0].replace(/\D/g, ""))) spans.push([m.index, m.index + m[0].length, "card number"])
  }
  spans.sort((a, b) => a[0] - b[0] || b[1] - b[0] - (a[1] - a[0]))
  let out = ""
  let pos = 0
  let n = 0
  for (const [start, end, kind] of spans) {
    if (start < pos) continue
    out += text.slice(pos, start) + `[secret: ${kind}]`
    pos = end
    n++
  }
  return [out + text.slice(pos), n]
}

// --- the local queue and state --------------------------------------------------------------------------------------

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"))
  } catch {
    return {}
  }
}

function writeState(dir, changes) {
  const file = path.join(dir, "state.json")
  fs.writeFileSync(file + ".tmp", JSON.stringify({ ...readJson(file), ...changes }))
  fs.renameSync(file + ".tmp", file)
}

function queueLines(file) {
  try {
    return fs
      .readFileSync(file, "utf8")
      .split("\n")
      .filter((x) => x.trim())
      .map((x) => JSON.parse(x))
  } catch {
    return []
  }
}

function enqueue(dir, text) {
  const fd = fs.openSync(path.join(dir, "queue.jsonl"), "a", 0o600)
  try {
    fs.writeSync(fd, JSON.stringify({ t: Math.floor(Date.now() / 1000), text }) + "\n")
  } finally {
    fs.closeSync(fd)
  }
}

// --- the API --------------------------------------------------------------------------------------------------------

async function call(opts, method, route, body, timeoutMs) {
  if (!opts.apiKey) return [0, {}]
  try {
    const r = await fetch(opts.apiUrl + route, {
      method,
      headers: {
        "X-API-Key": opts.apiKey,
        "Content-Type": "application/json",
        "User-Agent": `statejar-opencode/${VERSION}`,
        "X-StateJar-Client": "opencode",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    })
    let data = {}
    try {
      data = await r.json()
    } catch {
      data = {}
    }
    return [r.status, data]
  } catch {
    return [-1, {}]
  }
}

async function recall(opts, question, timeoutMs) {
  const [status, r] = await call(opts, "POST", "/memory/query", { query: question.slice(0, 2000), session_tag: opts.scope }, timeoutMs)
  if (status !== 200 || !r.handle_used || !r.memory_context) return ""
  return String(r.memory_context).slice(0, MAX_CONTEXT_CHARS)
}

/** Send the queue in batches of at most MAX_BATCH_CHARS; anything refused stays queued for the next flush. */
async function flush(opts, force) {
  const dir = opts.dataDir
  const file = path.join(dir, "queue.jsonl")
  let items = queueLines(file)
  if (!items.length) return "empty"
  if (!force && items.length < BATCH_SIZE && Date.now() / 1000 - items[0].t < BATCH_AGE_S) return "waiting"
  const sending = path.join(dir, `queue.sending.${process.pid}.${Date.now()}.jsonl`)
  try {
    fs.renameSync(file, sending) // new messages go to a fresh queue while this batch is sent
  } catch {
    return "busy"
  }
  items = queueLines(sending)
  const batches = []
  let cur = []
  for (const it of items) {
    const size = cur.reduce((s, x) => s + x.text.length + 2, 0)
    if (cur.length && size + it.text.length > MAX_BATCH_CHARS) {
      batches.push(cur)
      cur = []
    }
    cur.push(it)
  }
  if (cur.length) batches.push(cur)
  let left = []
  let last = 0
  for (let i = 0; i < batches.length; i++) {
    const text = batches[i].map((x) => x.text).join("\n\n").slice(0, MAX_BATCH_CHARS)
    const [status] = await call(opts, "POST", "/memory/ingest", { text, session_tag: opts.scope, async: true }, 15_000)
    last = status
    if (status !== 200 && status !== 202) {
      left = batches.slice(i).flat()
      break
    }
  }
  for (const it of left) enqueue(dir, it.text)
  fs.rmSync(sending, { force: true })
  const sent = items.length - left.length
  writeState(dir, { last_flush: Math.floor(Date.now() / 1000), last_status: last, sent: (readJson(path.join(dir, "state.json")).sent || 0) + sent })
  return `sent ${sent}, kept ${left.length} (status ${last})`
}

function status(opts) {
  const st = readJson(path.join(opts.dataDir, "state.json"))
  return {
    client: "opencode",
    paused: fs.existsSync(path.join(opts.dataDir, "paused")),
    scope: opts.scope,
    api: opts.apiUrl,
    key: opts.apiKey ? `…${opts.apiKey.slice(-4)}` : "not set (set STATEJAR_API_KEY or run `statejar login`)",
    queued_messages: queueLines(path.join(opts.dataDir, "queue.jsonl")).length,
    last_flush: st.last_flush ? new Date(st.last_flush * 1000).toISOString() : null,
    last_status: st.last_status ?? null,
    messages_sent: st.sent || 0,
  }
}

// --- the plugin -----------------------------------------------------------------------------------------------------
// OpenCode calls every export of a plugin module as a plugin, so this module exports the plugin function only; the
// tests reach the helpers through its properties (StateJar.scrub, StateJar.flush, ...).

function textOf(parts) {
  return (parts || [])
    .filter((p) => p && p.type === "text" && !p.synthetic && typeof p.text === "string")
    .map((p) => p.text)
    .join("\n")
}

export const StateJar = async (_input, given) => {
  const opts = readOptions(given || {})
  fs.mkdirSync(opts.dataDir, { recursive: true })
  const paused = () => fs.existsSync(path.join(opts.dataDir, "paused"))
  const memory = new Map() // sessionID -> what the model gets before it answers
  const started = new Set()
  const note =
    `StateJar memory scope for this session: ${opts.scope}. Its tools (statejar_search, statejar_remember, ...) ` +
    `take session="${opts.scope}". Every recall names the handle (shm_...) of the state it came from.`

  return {
    "chat.message": async (input, output) => {
      const text = textOf(output.parts)
      if (!text.trim() || paused() || /^\s*[/$]/.test(text)) return // a command, not a fact
      if (opts.autoCapture && text.split(/\s+/).filter(Boolean).length >= 3) {
        enqueue(opts.dataDir, scrub(text)[0].slice(0, MAX_BATCH_CHARS))
      }
      if (!opts.apiKey) return
      const id = input.sessionID
      let recalled = ""
      if (opts.recallEachPrompt) recalled = await recall(opts, text, 4_000)
      if (!recalled && !started.has(id)) recalled = await recall(opts, SESSION_QUERY, 8_000)
      started.add(id)
      if (recalled) memory.set(id, recalled)
    },
    "experimental.chat.system.transform": async (input, output) => {
      if (paused()) return
      if (!opts.apiKey) {
        output.system.push("StateJar: no developer key yet. Set STATEJAR_API_KEY or run `statejar login`; until then nothing is recalled or captured.")
        return
      }
      const recalled = input.sessionID ? memory.get(input.sessionID) : ""
      output.system.push(recalled ? `${note}\n\n${recalled}` : note)
    },
    "experimental.session.compacting": async (input, output) => {
      const recalled = memory.get(input.sessionID)
      if (recalled && !paused()) output.context.push(`StateJar memory (scope ${opts.scope}):\n${recalled}`)
      if (!paused()) await flush(opts, true)
    },
    event: async ({ event }) => {
      if (paused()) return
      if (event.type === "session.idle") await flush(opts, false)
      else if (event.type === "session.compacted" || event.type === "session.deleted") await flush(opts, true)
    },
    dispose: async () => {
      if (!paused()) await flush(opts, true)
    },
    tool: {
      statejar_status: {
        description: "Whether StateJar memory is on for OpenCode, its scope, what is queued and the last batch's status. Never shows a key.",
        args: {},
        async execute() {
          return JSON.stringify(status(opts), null, 1)
        },
      },
      statejar_pause: {
        description: "Pause StateJar memory for OpenCode: nothing is recalled or captured until statejar_resume. Use only when the user asks.",
        args: {},
        async execute() {
          fs.writeFileSync(path.join(opts.dataDir, "paused"), "")
          return "StateJar memory paused: nothing is recalled or captured until it is resumed."
        },
      },
      statejar_resume: {
        description: "Resume StateJar memory for OpenCode after a pause. Use only when the user asks.",
        args: {},
        async execute() {
          fs.rmSync(path.join(opts.dataDir, "paused"), { force: true })
          return "StateJar memory resumed."
        },
      },
    },
  }
}

Object.assign(StateJar, { VERSION, readOptions, scrub, flush, status })
