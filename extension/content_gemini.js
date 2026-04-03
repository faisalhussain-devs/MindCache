(() => {
  // =========================
  // CONFIG
  // =========================
  const CONFIG = {
    STABILITY_DELAY_MS: 1200,
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
  // UTIL: HASH
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
      if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) return;
      chrome.runtime.sendMessage(msg, () => {
        if (chrome.runtime.lastError) { }
      });
    } catch (e) { }
  }

  // =========================
  // SESSION
  // =========================
  function getConversationId() {
    // Gemini URL: gemini.google.com/app/<id> or gemini.google.com/chat/<id>
    const match = window.location.pathname.match(/\/(?:app|chat)\/([^/?#]+)/);
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
        conversation_id: newId ? `gemini:${newId}` : null,
        url: location.href,
        reason
      }
    });
  }

  // =========================
  // ROLE DETECTION (Gemini custom elements)
  // =========================
  function detectRole(node) {
    const tag = node.tagName.toLowerCase();

    // Gemini uses custom element tags
    if (tag === "user-query") return "user";
    if (tag === "model-response") return "assistant";

    // Ancestor scan
    if (node.closest("user-query")) return "user";
    if (node.closest("model-response")) return "assistant";

    return null;
  }

  // =========================
  // CONTENT EXTRACTION
  // =========================
  function extractText(node) {
    const clone = node.cloneNode(true);
    clone.querySelectorAll("button, svg, script, style, noscript, mat-icon, [aria-hidden='true']").forEach(el => el.remove());

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
      node.querySelector(".loading-indicator") ||
      node.querySelector("[data-loading]") ||
      node.querySelector(".animate-pulse") ||
      node.querySelector("mat-progress-bar") ||
      node.querySelector(".response-streaming")
    );
  }

  // =========================
  // NODE DISCOVERY (Gemini custom elements)
  // =========================
  function findMessageNodes(root = document) {
    const selectors = ["user-query", "model-response"];
    const nodes = new Set();

    for (const sel of selectors) {
      root.querySelectorAll(sel).forEach(el => {
        if (el instanceof HTMLElement) nodes.add(el);
      });
      // Check root itself (critical for MutationObserver)
      if (root !== document && root instanceof HTMLElement && root.matches && root.matches(sel)) {
        nodes.add(root);
      }
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
      if (!content || content.length < 2) return;

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
          conversation_id: currentConversationId ? `gemini:${currentConversationId}` : null,
          url: location.href,
          timestamp: Date.now()
        }
      });

    }, CONFIG.STABILITY_DELAY_MS);

    pendingNodes.set(node, timeout);
  }

  function processNodes(roots = [document]) {
    for (const root of roots) {
      for (const node of findMessageNodes(root)) {
        scheduleNodeProcessing(node);
      }
    }
  }

  // =========================
  // OBSERVER
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
  // NAVIGATION
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
    window.addEventListener("popstate", () => window.dispatchEvent(new Event("mc-nav")));
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
    return document.querySelector(".ql-editor")
      || document.querySelector('div[contenteditable="true"]')
      || document.querySelector("textarea");
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

    // Gemini often wraps input deeply and hides overflow.
    // section.input-area-container is the outermost wrapper that won't clip our button.
    const container = document.querySelector("section.input-area-container")
      || document.querySelector(".input-area")
      || inputEl.parentElement;

    if (!container) return;

    const wrapper = createRecallButton();
    container.style.position = "relative";
    container.appendChild(wrapper);
  }

  // Re-inject on DOM changes (Gemini is an SPA)
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
    console.debug("[MindCache-Gemini] Content script initialized");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
