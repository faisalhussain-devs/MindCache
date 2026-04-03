// =========================
// CONFIG
// =========================
const STORAGE_KEY = "mindcache_state_v2";
const QUEUE_KEY = "mindcache_queue_v2";
const AUTO_STORAGE_KEY = "mindcache_auto_mode";
const REORG_COUNTER_KEY = "mindcache_reorg_counter";
const API_BASE = "http://localhost:8000";

const CONFIG = {
  MAX_HISTORY: 10,
  MAX_RETRIES: 3,
  TIER1_THRESHOLD: 3,         // ≥3 pending → extract + decision
  TIER2_THRESHOLD: 30,        // ≥30 new since last reorg → full pipeline
  AUTO_CHECK_INTERVAL: 300000 // 5 minutes
};

// =========================
// STATE
// =========================
let state = {
  sessionId: null,
  conversationId: null,
  history: [],
  seenIds: new Set(),
  lastTriadId: null,
  stats: { triads_sent: 0, last_capture: null }
};

let queue = [];
let processing = false;

// Auto-mode state
let autoMode = false;
let autoTimer = null;
let memoriesSinceReorg = 0;

// =========================
// UTIL
// =========================
function normalizeConversationId(id) {
  return id || null;
}

function createSessionId(conversationId) {
  return conversationId
    ? `session:${conversationId}`
    : `session:pending:${Date.now()}`;
}

// =========================
// PERSISTENCE
// =========================
async function persistState() {
  await chrome.storage.local.set({
    [STORAGE_KEY]: {
      ...state,
      seenIds: Array.from(state.seenIds)
    }
  });
}

async function loadState() {
  const data = await chrome.storage.local.get(STORAGE_KEY);
  if (data[STORAGE_KEY]) {
    state = {
      ...data[STORAGE_KEY],
      seenIds: new Set(data[STORAGE_KEY].seenIds || []),
      lastTriadId: data[STORAGE_KEY].lastTriadId || null,
      stats: data[STORAGE_KEY].stats || { triads_sent: 0, last_capture: null }
    };
  }
}

// =========================
// QUEUE SYSTEM
// =========================
async function persistQueue() {
  await chrome.storage.local.set({ [QUEUE_KEY]: queue });
}

async function loadQueue() {
  const data = await chrome.storage.local.get(QUEUE_KEY);
  queue = data[QUEUE_KEY] || [];
}

async function enqueue(item) {
  queue.push({ ...item, retries: 0 });
  await persistQueue();
  await processQueue();
}

async function processQueue() {
  if (processing) return;
  processing = true;

  const item = queue[0];
  if (!item) {
    processing = false;
    return;
  }

  try {
    await handleMessage(item);
    queue.shift();
    await persistQueue();
  } catch (err) {
    item.retries++;

    if (item.retries > CONFIG.MAX_RETRIES) {
      console.error("[MindCache] Dropping message after max retries:", item);
      queue.shift();
    }

    await persistQueue();
  }

  processing = false;

  if (queue.length > 0) {
    setTimeout(processQueue, 0);
  }
}

// =========================
// VALIDATION
// =========================
function validateMessage(msg) {
  if (!msg || typeof msg !== "object") return false;
  if (!msg.payload) return false;
  if (!msg.payload.role || !msg.payload.content) return false;
  return true;
}

// =========================
// SESSION MANAGEMENT
// =========================
async function resetSession(conversationId, reason) {
  state.conversationId = normalizeConversationId(conversationId);
  state.sessionId = createSessionId(state.conversationId);
  state.history = [];
  state.seenIds.clear();

  await persistState();
  console.debug("[MindCache] Session reset:", reason, state.sessionId);
}

// =========================
// TRIAD LOGIC
// =========================
function isTriad(history) {
  if (history.length < 3) return false;
  const [a, b, c] = history.slice(-3);
  return a.role === "user" && b.role === "assistant" && c.role === "user";
}

function buildTriad() {
  const [userMsg, assistantMsg, nextUserMsg] = state.history.slice(-3);
  return {
    prompt: userMsg.content,
    response: assistantMsg.content,
    next_prompt: nextUserMsg.content
  };
}

// =========================
// BACKEND COMMUNICATION
// =========================
async function sendTriadToBackend(triad) {
  const response = await fetch(`${API_BASE}/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(triad)
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Backend error ${response.status}: ${text}`);
  }

  const data = await response.json();
  console.debug("[MindCache] Triad ingested:", data);
  return data;
}

// =========================
// CORE MESSAGE HANDLER
// =========================
async function handleMessage(msg) {
  if (!msg || !msg.payload) {
    throw new Error("Invalid message structure");
  }

  const payload = msg.payload;
  const conversationId = normalizeConversationId(payload.conversation_id);

  if (conversationId !== state.conversationId) {
    await resetSession(conversationId, "conversation_changed");
  }

  // Deduplication
  if (state.seenIds.has(payload.message_id)) {
    return;
  }

  state.seenIds.add(payload.message_id);

  const entry = {
    role: payload.role,
    content: payload.content,
    messageId: payload.message_id,
    createdAt: payload.timestamp
  };

  // Collapse consecutive same-role messages (prevents duplicates from breaking triad pattern)
  const last = state.history.length > 0 ? state.history[state.history.length - 1] : null;
  if (last && last.role === payload.role) {
    state.history[state.history.length - 1] = entry;
  } else {
    state.history.push(entry);
  }

  if (state.history.length > CONFIG.MAX_HISTORY) {
    state.history = state.history.slice(-CONFIG.MAX_HISTORY);
  }

  await persistState();

  // Check for complete triad and send to backend
  if (isTriad(state.history)) {
    const triggerMsg = state.history[state.history.length - 1];

    // Don't resend the same triad
    if (state.lastTriadId === triggerMsg.messageId) return;

    state.lastTriadId = triggerMsg.messageId;
    const triad = buildTriad();

    // Update stats immediately (triad detected regardless of backend)
    state.stats.triads_sent++;
    state.stats.last_capture = new Date().toISOString();
    await persistState();

    try {
      await sendTriadToBackend(triad);
      console.debug("[MindCache] Triad sent to backend successfully");
    } catch (err) {
      console.error("[MindCache] Backend unreachable:", err.message);
    }
  }
}

// =========================
// AUTO-MODE SCHEDULER
// =========================
async function autoCheck() {
  try {
    // Skip if scheduler already running
    const statusResp = await fetch(`${API_BASE}/scheduler-status`);
    const statusData = await statusResp.json();
    if (statusData.running) return;

    // Check pending queue
    const queueResp = await fetch(`${API_BASE}/queue/status`);
    const queueData = await queueResp.json();
    const pending = (queueData.queue?.pending || 0) + (queueData.queue?.failed || 0);

    if (pending < CONFIG.TIER1_THRESHOLD) return; // Nothing to do

    if (memoriesSinceReorg >= CONFIG.TIER2_THRESHOLD) {
      // TIER 2: Full pipeline — reorg + summaries + embeddings
      await fetch(`${API_BASE}/run-tier2`, { method: "POST" });
      memoriesSinceReorg = 0;
      await chrome.storage.local.set({ [REORG_COUNTER_KEY]: 0 });
      console.debug("[MindCache] Auto: Tier 2 — full pipeline triggered");
    } else {
      // TIER 1: Extract + Decision Analyzer only
      await fetch(`${API_BASE}/run-tier1`, { method: "POST" });
      memoriesSinceReorg += pending;
      await chrome.storage.local.set({ [REORG_COUNTER_KEY]: memoriesSinceReorg });
      console.debug(`[MindCache] Auto: Tier 1 — ${memoriesSinceReorg}/${CONFIG.TIER2_THRESHOLD} until reorg`);
    }
  } catch (err) {
    console.debug("[MindCache] Auto-check failed:", err.message);
  }
}

function startAutoScheduler() {
  if (autoTimer) return;
  autoTimer = setInterval(autoCheck, CONFIG.AUTO_CHECK_INTERVAL);
  console.debug("[MindCache] Auto mode ON — checking every 5 min");
}

function stopAutoScheduler() {
  if (autoTimer) {
    clearInterval(autoTimer);
    autoTimer = null;
  }
  console.debug("[MindCache] Auto mode OFF");
}

// =========================
// MESSAGE ROUTER
// =========================
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    await initDone; // Wait for state to be loaded from storage
    try {
      if (msg.type === "NEW_MESSAGE") {
        if (!validateMessage(msg)) {
          return sendResponse({ ok: false, error: "Invalid message" });
        }
        await enqueue(msg);
        sendResponse({ ok: true });

      } else if (msg.type === "SESSION_CHANGED") {
        const payload = msg.payload || {};
        await resetSession(payload.conversation_id, payload.reason || "session_changed");
        sendResponse({ ok: true });

      } else if (msg.type === "GET_STATUS") {
        sendResponse({
          ok: true,
          connected: state.sessionId !== null,
          sessionId: state.sessionId,
          conversationId: state.conversationId,
          historyLength: state.history.length,
          queueLength: queue.length,
          stats: state.stats,
          autoMode,
          memoriesSinceReorg
        });

      } else if (msg.type === "RETRIEVE") {
        const query = (msg.query || "").trim();
        if (!query) {
          return sendResponse({ ok: false, error: "Empty query" });
        }

        try {
          const resp = await fetch(`${API_BASE}/retrieve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query })
          });
          const data = await resp.json();
          sendResponse({ ok: true, context: data.context || "" });
        } catch (err) {
          sendResponse({ ok: false, error: err.message });
        }

      } else if (msg.type === "SET_AUTO_MODE") {
        autoMode = !!msg.enabled;
        await chrome.storage.local.set({ [AUTO_STORAGE_KEY]: autoMode });
        if (autoMode) startAutoScheduler();
        else stopAutoScheduler();
        sendResponse({ ok: true, autoMode });

      } else {
        sendResponse({ ok: false, error: "Unknown message type" });
      }

    } catch (err) {
      console.error("[MindCache] Handler error:", err);
      sendResponse({ ok: false, error: String(err) });
    }
  })();

  return true;
});

// =========================
// INIT
// =========================
let initDone = null;

initDone = (async function init() {
  await loadState();
  await loadQueue();

  // Restore auto-mode state
  const autoData = await chrome.storage.local.get([AUTO_STORAGE_KEY, REORG_COUNTER_KEY]);
  autoMode = autoData[AUTO_STORAGE_KEY] || false;
  memoriesSinceReorg = autoData[REORG_COUNTER_KEY] || 0;
  if (autoMode) startAutoScheduler();

  await processQueue();
  console.debug("[MindCache] Background ready. Triads:", state.stats.triads_sent, "Auto:", autoMode);
})();
