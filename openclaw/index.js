// StateJar memory for OpenClaw: a memory-slot plugin (plugins.slots.memory = "statejar"). ES module, no dependencies
// beyond OpenClaw itself.
//
// - autoRecall (before_prompt_build): the memory the turn's message needs, prepended as context with the handle
//   (shm_...) of the state it came from. Each recall is an audited disclosure in the user's StateJar account.
// - autoCapture (agent_end): the user's message of the turn (never the reply, never tool output) is queued on this
//   machine with secrets removed, and sent in batches: three messages, or the oldest ten minutes old, or at
//   session end, compaction, reset and gateway stop. StateJar extracts typed facts and stores no transcript.
// - memory_search and memory_get, the tools the memory slot's owner provides, plus statejar_remember and
//   statejar_forget.
// - `openclaw statejar init | status | search | add | list | forget | export | pause | resume`.
//
// The key comes from STATEJAR_API_KEY or `statejar login` (the statejar package on PyPI); `init` never writes it to
// OpenClaw's config. Same queue format, secret patterns and batching as the Claude Code, Codex and OpenCode plugins.

import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry"

const VERSION = "0.1.0"
const DEFAULT_API = "https://api.statejar.com/api/v1"
const DEFAULT_SCOPE = "openclaw"
const BATCH_SIZE = 3
const BATCH_AGE_S = 600
const MAX_BATCH_CHARS = 18_000
const MAX_CONTEXT_CHARS = 9_000
const SESSION_QUERY = "Who is the user, what do they prefer, and what are they working on?"

// --- options --------------------------------------------------------------------------------------------------------

function configDir() {
  if (process.env.STATEJAR_CONFIG_DIR) return process.env.STATEJAR_CONFIG_DIR
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"), "statejar")
  }
  return path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"), "statejar")
}

function apiKey() {
  const key = (process.env.STATEJAR_API_KEY || "").trim()
  if (key) return key
  try {
    return String(JSON.parse(fs.readFileSync(path.join(configDir(), "credentials.json"), "utf8")).api_key || "").trim()
  } catch {
    return ""
  }
}

function readOptions(given = {}) {
  const scope = String(given.scope || process.env.STATEJAR_SCOPE || DEFAULT_SCOPE)
  const flag = (v, fallback) => (v === undefined || v === null || v === "" ? fallback : v === true || ["1", "true", "yes", "on"].includes(String(v).toLowerCase()))
  return {
    apiKey: apiKey(),
    apiUrl: String(given.apiUrl || process.env.STATEJAR_API_URL || DEFAULT_API).replace(/\/+$/, ""),
    scope: /^[A-Za-z0-9._:-]{1,64}$/.test(scope) ? scope : DEFAULT_SCOPE,
    autoRecall: flag(given.autoRecall ?? process.env.STATEJAR_AUTO_RECALL, true),
    autoCapture: flag(given.autoCapture ?? process.env.STATEJAR_AUTO_CAPTURE, true),
    dataDir: String(given.dataDir || process.env.STATEJAR_DATA_DIR || path.join(configDir(), "clients", "openclaw")),
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

// --- the local queue, the API ---------------------------------------------------------------------------------------

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"))
  } catch {
    return {}
  }
}

function queueLines(file) {
  try {
    return fs.readFileSync(file, "utf8").split("\n").filter((x) => x.trim()).map((x) => JSON.parse(x))
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

async function call(opts, method, route, body, timeoutMs = 8_000) {
  if (!opts.apiKey) return [0, {}]
  try {
    const r = await fetch(opts.apiUrl + route, {
      method,
      headers: {
        "X-API-Key": opts.apiKey,
        "Content-Type": "application/json",
        "User-Agent": `statejar-openclaw/${VERSION}`,
        "X-StateJar-Client": "openclaw",
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

async function flush(opts, force) {
  const dir = opts.dataDir
  if (fs.existsSync(path.join(dir, "paused"))) return "paused"
  const file = path.join(dir, "queue.jsonl")
  let items = queueLines(file)
  if (!items.length) return "empty"
  if (!force && items.length < BATCH_SIZE && Date.now() / 1000 - items[0].t < BATCH_AGE_S) return "waiting"
  const sending = path.join(dir, `queue.sending.${process.pid}.${Date.now()}.jsonl`)
  try {
    fs.renameSync(file, sending)
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
  const stateFile = path.join(dir, "state.json")
  const st = readJson(stateFile)
  fs.writeFileSync(stateFile, JSON.stringify({ ...st, last_flush: Math.floor(Date.now() / 1000), last_status: last, sent: (st.sent || 0) + items.length - left.length }))
  return `sent ${items.length - left.length}, kept ${left.length} (status ${last})`
}

function status(opts) {
  const st = readJson(path.join(opts.dataDir, "state.json"))
  return {
    plugin: "statejar",
    paused: fs.existsSync(path.join(opts.dataDir, "paused")),
    scope: opts.scope,
    api: opts.apiUrl,
    key: opts.apiKey ? `…${opts.apiKey.slice(-4)}` : "not set (set STATEJAR_API_KEY or run `statejar login`)",
    auto_recall: opts.autoRecall,
    auto_capture: opts.autoCapture,
    queued_messages: queueLines(path.join(opts.dataDir, "queue.jsonl")).length,
    last_flush: st.last_flush ? new Date(st.last_flush * 1000).toISOString() : null,
    last_status: st.last_status ?? null,
    messages_sent: st.sent || 0,
  }
}

/** The text of the newest user message in a run's messages (OpenClaw keeps provider-shaped messages). */
function lastUserText(messages) {
  for (let i = (messages || []).length - 1; i >= 0; i--) {
    const m = messages[i]
    if (!m || m.role !== "user") continue
    if (typeof m.content === "string") return m.content
    if (Array.isArray(m.content)) {
      return m.content.filter((p) => p && p.type === "text" && typeof p.text === "string").map((p) => p.text).join("\n")
    }
  }
  return ""
}

const text = (t, details) => ({ content: [{ type: "text", text: t }], details: details || {} })

// --- the plugin -----------------------------------------------------------------------------------------------------

export default definePluginEntry({
  id: "statejar",
  name: "StateJar memory",
  description: "Verifiable memory: recall of only what a turn needs, with its handle; batched capture with secrets removed.",
  kind: "memory",
  register(api) {
    const opts = readOptions(api.pluginConfig || {})
    fs.mkdirSync(opts.dataDir, { recursive: true })
    const paused = () => fs.existsSync(path.join(opts.dataDir, "paused"))
    let sessionMemory = ""
    let gateway = false // inside a Gateway, session end and gateway stop send what is left; a one-shot run has neither
    api.on("gateway_start", () => {
      gateway = true
    })

    api.registerMemoryCapability({ recallToolNames: ["memory_search"] })

    api.registerTool({
      name: "memory_search",
      description: "Search the user's StateJar memory for what a question needs. Returns the memory and the handle (shm_...) it came from. Use before answering a question that depends on the user's facts.",
      parameters: { type: "object", properties: { query: { type: "string", description: "The user's question." } }, required: ["query"], additionalProperties: false },
      async execute(_id, params) {
        const [st, r] = await call(opts, "POST", "/memory/query", { query: String(params.query || "").slice(0, 2000), session_tag: opts.scope, audit: true })
        if (st !== 200) return text(`StateJar answered HTTP ${st}.`)
        return text(r.memory_context || "No memory yet.", { handle: r.handle_used || null })
      },
    })
    api.registerTool({
      name: "memory_get",
      description: "The user's current StateJar memory: every fact as path and value, with the handle of the state.",
      parameters: { type: "object", properties: {}, additionalProperties: false },
      async execute() {
        const [st, r] = await call(opts, "POST", "/memory/query", { query: "Everything you know about me.", session_tag: opts.scope, audit: true })
        if (st !== 200) return text(`StateJar answered HTTP ${st}.`)
        return text(r.memory_context || "No memory yet.", { handle: r.handle_used || null })
      },
    })
    api.registerTool({
      name: "statejar_remember",
      description: "Store facts the user states, now. StateJar keeps typed facts, refuses a value that fails its type and stores no transcript. Use only when the user asks you to remember something.",
      parameters: { type: "object", properties: { text: { type: "string", description: "The user's words." } }, required: ["text"], additionalProperties: false },
      async execute(_id, params) {
        const [st, r] = await call(opts, "POST", "/memory/ingest", { text: scrub(String(params.text || ""))[0].slice(0, MAX_BATCH_CHARS), session_tag: opts.scope }, 60_000)
        return text(st === 200 ? JSON.stringify({ stored: r.stored, handle: r.handle, extraction: r.extraction }) : `StateJar answered HTTP ${st}.`)
      },
    })
    api.registerTool({
      name: "statejar_forget",
      description: "Remove one fact from the user's current StateJar memory as a new version (history keeps it). Use only when the user asks you to forget something.",
      parameters: { type: "object", properties: { fact: { type: "string", description: "The fact to remove, e.g. budget." } }, required: ["fact"], additionalProperties: false },
      async execute(_id, params) {
        const [st, r] = await call(opts, "POST", "/memory/ingest", { text: `Forget the ${String(params.fact || "").trim()}.`, session_tag: opts.scope }, 60_000)
        return text(st === 200 ? JSON.stringify({ stored: r.stored, handle: r.handle }) : `StateJar answered HTTP ${st}.`)
      },
    })

    api.on("before_prompt_build", async (event) => {
      if (!opts.autoRecall || paused() || !opts.apiKey) return
      const question = (event.currentUserMessage ?? "").trim() || lastUserText(event.messages).trim()
      let memory = question ? await recall(opts, question, 4_000) : ""
      if (!memory) {
        if (!sessionMemory) sessionMemory = await recall(opts, SESSION_QUERY, 8_000)
        memory = sessionMemory
      }
      if (memory) return { prependContext: `StateJar memory (scope ${opts.scope}):\n${memory}` }
    })
    api.on("agent_end", async (event) => {
      if (!opts.autoCapture || paused()) return
      const said = lastUserText(event.messages).trim()
      if (said && said.split(/\s+/).length >= 3 && !/^[/$]/.test(said)) enqueue(opts.dataDir, scrub(said)[0].slice(0, MAX_BATCH_CHARS))
      await flush(opts, !gateway)
    })
    for (const boundary of ["session_end", "before_compaction", "before_reset", "gateway_stop"]) {
      api.on(boundary, async () => {
        await flush(opts, true)
      })
    }

    api.registerCli(
      ({ program }) => {
        const root = program.command("statejar").description("StateJar memory: init, status, search, add, list, forget, export")
        const print = (v) => console.log(typeof v === "string" ? v : JSON.stringify(v, null, 1))
        root
          .command("init")
          .description("Make StateJar this agent's memory: the memory slot, auto-recall and auto-capture, and conversation access for its hooks")
          .option("--scope <scope>", "memory scope in your StateJar account", opts.scope)
          .action(async (o) => {
            await api.runtime.config.mutateConfigFile({
              afterWrite: { mode: "auto" },
              mutate(draft) {
                draft.plugins ??= {}
                draft.plugins.slots ??= {}
                draft.plugins.slots.memory = "statejar"
                draft.plugins.entries ??= {}
                const entry = (draft.plugins.entries.statejar ??= {})
                entry.enabled = true
                entry.hooks = { ...(entry.hooks || {}), allowConversationAccess: true }
                entry.config = { autoRecall: true, autoCapture: true, ...(entry.config || {}), scope: o.scope }
              },
            })
            print(`StateJar is this agent's memory (scope ${o.scope}).`)
            if (!apiKey()) print("Set STATEJAR_API_KEY where OpenClaw runs, or run `statejar login`, to connect it.")
          })
        root.command("status").description("Scope, options, what is queued and the last batch's status (never the key)").action(() => print(status(opts)))
        root
          .command("search")
          .argument("<question...>")
          .description("The memory a question needs, with its handle")
          .action(async (q) => {
            const [, r] = await call(opts, "POST", "/memory/query", { query: q.join(" "), session_tag: opts.scope, audit: true })
            print(r.memory_context ? `${r.memory_context}\n\nhandle: ${r.handle_used}` : "No memory yet.")
          })
        root
          .command("add")
          .argument("<text...>")
          .description("Store the facts in a message now")
          .action(async (t) => {
            const [, r] = await call(opts, "POST", "/memory/ingest", { text: scrub(t.join(" "))[0], session_tag: opts.scope }, 60_000)
            print({ stored: r.stored, handle: r.handle })
          })
        root.command("list").description("Every fact in the current memory").action(async () => {
          const [, r] = await call(opts, "POST", "/memory/query", { query: "Everything you know about me.", session_tag: opts.scope, audit: true })
          print(r.memory_context || "No memory yet.")
        })
        root
          .command("forget")
          .argument("<fact...>")
          .description("Remove one fact from the current memory (history keeps it)")
          .action(async (f) => {
            const [, r] = await call(opts, "POST", "/memory/ingest", { text: `Forget the ${f.join(" ")}.`, session_tag: opts.scope }, 60_000)
            print({ stored: r.stored, handle: r.handle })
          })
        root.command("export").description("Every version of the scope as JSON, oldest first, with its handle").action(async () => {
          const [, v] = await call(opts, "GET", `/memory/versions?session_tag=${encodeURIComponent(opts.scope)}`)
          const versions = []
          for (const h of v.versions || []) {
            const [, s] = await call(opts, "GET", `/memory/state/${h}`)
            versions.push({ handle: h, parent_handle: s.parent_handle, state: s.state, created_at: s.created_at })
          }
          print({ scope: opts.scope, versions })
        })
        root.command("pause").description("Stop recall and capture until resume").action(() => {
          fs.writeFileSync(path.join(opts.dataDir, "paused"), "")
          print("StateJar memory paused.")
        })
        root.command("resume").description("Turn recall and capture back on").action(() => {
          fs.rmSync(path.join(opts.dataDir, "paused"), { force: true })
          print("StateJar memory resumed.")
        })
      },
      { descriptors: [{ name: "statejar", description: "StateJar memory: init, status, search, add, list, forget, export", hasSubcommands: true }] },
    )
  },
})
