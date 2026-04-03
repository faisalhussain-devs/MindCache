(() => {
  // =========================
  // CONFIG
  // =========================
  const CONFIG = {
    STABILITY_DELAY_MS: 700,
    MAX_TEXT_LENGTH: 20000
  };

  // =========================
  // STATE
  // =========================
  const seenMessageIds = new Set();
  const pendingNodes = new Map();
  let currentConversationId = null;
  let observer = null;
  let scheduled = false;

  // =========================
  // UTIL: HASH (stable dedup ID)
  // =========================
  async function hashString(str) {
    const encoder = new TextEncoder();
    const data = encoder.encode(str);
    const hashBuffer = await crypto.subtle.digest("SHA-256", data);
    return Array.from(new Uint8Array(hashBuffer))
      .map(b => b.toString(16).padStart(2, "0"))
      .join("")
      .slice(0, 16);
  }

  // =========================
  // UTIL: SAFE MESSAGE SEND
  // =========================
  function safeSendMessage(msg) {
    try {
      if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) {
        return;
      }
      chrome.runtime.sendMessage(msg, () => {
        if (chrome.runtime.lastError) { /* swallow */ }
      });
    } catch (e) { /* extension context invalidated */ }
  }

  // =========================
  // UTIL: SESSION
  // =========================
  function getConversationId() {
    const match = window.location.pathname.match(/\/c\/([^/?#]+)/);
    return match ? match[1] : null;
  }

  function updateSession(reason) {
    const newId = getConversationId();
    if (newId === currentConversationId) return;

    currentConversationId = newId;
    seenMessageIds.clear();

    safeSendMessage({
      type: "SESSION_CHANGED",
      version: 1,
      request_id: crypto.randomUUID(),
      timestamp: Date.now(),
      payload: {
        conversation_id: newId,
        url: location.href,
        reason
      }
    });
  }

  // =========================
  // ROLE DETECTION (resilient)
  // =========================
  function detectRole(node) {
    const roleAttr =
      node.getAttribute("data-message-author-role") ||
      node.getAttribute("data-testid") ||
      "";

    if (roleAttr.includes("user")) return "user";
    if (roleAttr.includes("assistant")) return "assistant";

    // fallback: ancestor scan
    const isUser = node.closest("[data-message-author-role='user']");
    if (isUser) return "user";

    const isAssistant = node.closest("[data-message-author-role='assistant']");
    if (isAssistant) return "assistant";

    return null;
  }

  // =========================
  // CONTENT EXTRACTION
  // =========================
  function extractText(node) {
    const clone = node.cloneNode(true);
    clone.querySelectorAll("button, svg, script, style, noscript").forEach(el => el.remove());

    let text = (clone.innerText || "").replace(/\s+/g, " ").trim();

    if (text.length > CONFIG.MAX_TEXT_LENGTH) {
      text = text.slice(0, CONFIG.MAX_TEXT_LENGTH);
    }

    return text;
  }

  // =========================
  // STREAMING DETECTION
  // =========================
  function isStreaming(node) {
    return !!(
      node.querySelector("[data-testid*='stream']") ||
      node.querySelector(".result-streaming") ||
      node.querySelector(".animate-pulse")
    );
  }

  // =========================
  // NODE DISCOVERY
  // =========================
  function findMessageNodes(root = document) {
    const PRIMARY = "[data-message-author-role]";
    const nodes = new Set();

    // Search descendants
    root.querySelectorAll(PRIMARY).forEach(el => {
      if (el instanceof HTMLElement) nodes.add(el);
    });

    // Also check if the root element itself matches (critical for MutationObserver)
    if (root !== document && root instanceof HTMLElement && root.matches && root.matches(PRIMARY)) {
      nodes.add(root);
    }

    return Array.from(nodes);
  }


  // =========================
  // CAPTURE LOGIC
  // =========================
  function scheduleNodeProcessing(node) {
    if (pendingNodes.has(node)) return;

    const timeout = setTimeout(async () => {
      pendingNodes.delete(node);

      if (isStreaming(node)) {
        scheduleNodeProcessing(node);
        return;
      }

      const role = detectRole(node);
      if (!role) return;

      const content = extractText(node);
      if (!content) return;

      const messageId = await hashString(role + "::" + content);
      if (seenMessageIds.has(messageId)) return;

      seenMessageIds.add(messageId);

      safeSendMessage({
        type: "NEW_MESSAGE",
        version: 1,
        request_id: crypto.randomUUID(),
        timestamp: Date.now(),
        payload: {
          role,
          content,
          message_id: messageId,
          conversation_id: currentConversationId,
          url: location.href,
          timestamp: Date.now()
        }
      });

    }, CONFIG.STABILITY_DELAY_MS);

    pendingNodes.set(node, timeout);
  }

  // =========================
  // CAPTURE ENTRY
  // =========================
  function processNodes(roots = [document]) {
    for (const root of roots) {
      const nodes = findMessageNodes(root);
      for (const node of nodes) {
        scheduleNodeProcessing(node);
      }
    }
  }

  // =========================
  // THROTTLED OBSERVER
  // =========================
  function scheduleProcessing(mutations) {
    if (scheduled) return;
    scheduled = true;

    requestAnimationFrame(() => {
      const roots = [];

      for (const m of mutations) {
        m.addedNodes.forEach(n => {
          if (n instanceof HTMLElement) roots.push(n);
        });
      }

      updateSession("mutation");
      processNodes(roots.length ? roots : [document]);

      scheduled = false;
    });
  }

  function startObserver() {
    if (observer) return;

    observer = new MutationObserver(scheduleProcessing);
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true
    });
  }

  // =========================
  // NAVIGATION TRACKING
  // =========================
  function patchHistory(method) {
    const original = history[method];
    history[method] = function (...args) {
      const result = original.apply(this, args);
      window.dispatchEvent(new Event("mc-nav"));
      return result;
    };
  }

  function setupNavigation() {
    patchHistory("pushState");
    patchHistory("replaceState");

    window.addEventListener("popstate", () => {
      window.dispatchEvent(new Event("mc-nav"));
    });

    window.addEventListener("mc-nav", () => {
      updateSession("navigation");
      processNodes();
    });
  }

  // =========================
  // RECALL BUTTON (MindCache Retrieval)
  // =========================
  const CONTEXT_WRAPPER = `[MEMORY CONTEXT]
The following context was retrieved from your personal memory system (MindCache).
Use this information to inform your response if relevant to the user's query below.
Ignore this context if it is not applicable.

{context}
[END CONTEXT]

`;

  function getInputElement() {
    return document.querySelector("#prompt-textarea")
      || document.querySelector("textarea[placeholder]")
      || document.querySelector('[contenteditable="true"][data-placeholder]');
  }

  function getInputValue(el) {
    if (!el) return "";
    if (el.tagName === "TEXTAREA") return el.value;
    return el.innerText || el.textContent || "";
  }

  function setInputValue(el, text) {
    if (!el) return;
    if (el.tagName === "TEXTAREA") {
      el.value = text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.style.height = "auto";
      el.style.height = el.scrollHeight + "px";
    } else {
      el.innerText = text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function createRecallButton() {
    const wrapper = document.createElement("div");
    wrapper.id = "mc-recall-btn";
    Object.assign(wrapper.style, {
      position: "absolute",
      bottom: "100%",
      right: "0",
      marginBottom: "12px",
      zIndex: "99999",
      display: "flex",
      alignItems: "center"
    });

    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "🧠 Recall";
    btn.title = "Retrieve MindCache memories for this query";
    Object.assign(btn.style, {
      padding: "8px 16px",
      borderRadius: "999px",
      border: "none",
      background: "linear-gradient(135deg, #0f6f8f, #0b5972)",
      color: "white",
      fontSize: "13px",
      fontWeight: "600",
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      cursor: "pointer",
      boxShadow: "0 4px 16px rgba(15, 111, 143, 0.35)",
      transition: "all 180ms ease",
      lineHeight: "1",
      whiteSpace: "nowrap"
    });
    btn.addEventListener("mouseenter", () => {
      btn.style.transform = "translateY(-2px)";
      btn.style.boxShadow = "0 6px 20px rgba(15, 111, 143, 0.45)";
    });
    btn.addEventListener("mouseleave", () => {
      btn.style.transform = "";
      btn.style.boxShadow = "0 4px 16px rgba(15, 111, 143, 0.35)";
    });

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      handleRecallClick(btn);
    });

    wrapper.appendChild(btn);
    return wrapper;
  }

  async function handleRecallClick(btn) {
    const inputEl = getInputElement();
    const query = getInputValue(inputEl).trim();
    if (!query) return;

    const originalText = btn.textContent;
    btn.textContent = "⏳...";
    btn.disabled = true;
    btn.style.opacity = "0.7";

    try {
      const resp = await new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: "RETRIEVE", query }, resolve);
      });

      if (resp?.ok && resp.context) {
        const wrapped = CONTEXT_WRAPPER.replace("{context}", resp.context) + query;
        setInputValue(inputEl, wrapped);
        btn.textContent = "✅ Done";
        btn.style.background = "linear-gradient(135deg, #238636, #2ea043)";
        setTimeout(() => {
          btn.textContent = originalText;
          btn.style.background = "linear-gradient(135deg, #0f6f8f, #0b5972)";
        }, 2000);
      } else {
        btn.textContent = "❌ No context";
        setTimeout(() => { btn.textContent = originalText; }, 2000);
      }
    } catch (err) {
      btn.textContent = "❌ Error";
      setTimeout(() => { btn.textContent = originalText; }, 2000);
    } finally {
      btn.disabled = false;
      btn.style.opacity = "1";
    }
  }

  function injectRecallButton() {
    if (document.getElementById("mc-recall-btn")) return;

    const inputEl = getInputElement();
    if (!inputEl) return;

    const container = inputEl.closest("form") || inputEl.parentElement;
    if (!container) return;

    const wrapper = createRecallButton();
    container.style.position = "relative";
    container.appendChild(wrapper);
  }

  // Re-inject on DOM changes (ChatGPT is an SPA, input can be re-rendered)
  const recallObserver = new MutationObserver(() => {
    if (!document.getElementById("mc-recall-btn")) {
      injectRecallButton();
    }
  });
  recallObserver.observe(document.body, { childList: true, subtree: true });

  // =========================
  // BOOTSTRAP
  // =========================
  function init() {
    updateSession("init");
    processNodes();
    startObserver();
    setupNavigation();
    injectRecallButton();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();