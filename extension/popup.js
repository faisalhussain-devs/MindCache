(async function () {
  const $ = (id) => document.getElementById(id);
  const API = "http://localhost:8000";

  // ── Fetch & render status ──
  async function refresh() {
    try {
      const response = await chrome.runtime.sendMessage({ type: "GET_STATUS" });
      if (!response || !response.ok) {
        $("statusDot").className = "status-dot disconnected";
        return;
      }

      // Connection
      $("statusDot").className = response.connected
        ? "status-dot connected"
        : "status-dot disconnected";

      // Stats
      const stats = response.stats || {};
      $("triadCount").textContent = stats.triads_sent ?? 0;
      $("queueCount").textContent = response.queueLength ?? 0;
      $("historyCount").textContent = response.historyLength ?? 0;

      // Session
      $("sessionId").textContent = response.sessionId || "No active session";

      // Last capture
      if (stats.last_capture) {
        const d = new Date(stats.last_capture);
        const now = new Date();
        const diffMin = Math.floor((now - d) / 60000);

        if (diffMin < 1) {
          $("lastCapture").textContent = "Just now";
        } else if (diffMin < 60) {
          $("lastCapture").textContent = `${diffMin}m ago`;
        } else {
          $("lastCapture").textContent = d.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          });
        }
      } else {
        $("lastCapture").textContent = "Never";
      }

      // Auto mode
      $("autoCheckbox").checked = !!response.autoMode;
      if (response.memoriesSinceReorg != null) {
        $("autoCounter").textContent = response.autoMode
          ? `${response.memoriesSinceReorg}/30 memories until next reorg`
          : "";
      }

      // Scheduler status
      await checkScheduler();
    } catch (err) {
      $("statusDot").className = "status-dot disconnected";
      console.error("[MindCache Popup]", err);
    }
  }

  // ── Check scheduler running state ──
  async function checkScheduler() {
    try {
      const resp = await fetch(`${API}/scheduler-status`);
      const data = await resp.json();

      if (data.running) {
        $("pipelineStatus").className = "pipeline-status is-running";
        $("pipelineStatus").innerHTML =
          `<div class="spinner"></div> Running (${data.mode || "..."})`;
        $("runPipelineBtn").disabled = true;
        $("runPipelineBtn").textContent = "⏳ Running...";
      } else {
        $("pipelineStatus").className = "pipeline-status";
        $("pipelineStatus").textContent = "";
        $("runPipelineBtn").disabled = false;
        $("runPipelineBtn").textContent = "▶ Run Full Pipeline";
      }
    } catch {
      $("pipelineStatus").className = "pipeline-status";
      $("pipelineStatus").textContent = "⚠ Server unreachable";
    }
  }

  // ── Run full pipeline ──
  async function runPipeline() {
    $("runPipelineBtn").disabled = true;
    $("runPipelineBtn").textContent = "⏳ Starting...";

    try {
      const resp = await fetch(`${API}/run-scheduler`, { method: "POST" });
      const data = await resp.json();

      if (data.status === "already_running") {
        $("runPipelineBtn").textContent = "⏳ Already running";
      } else {
        $("runPipelineBtn").textContent = "⏳ Running...";
      }

      $("pipelineStatus").className = "pipeline-status is-running";
      $("pipelineStatus").innerHTML = `<div class="spinner"></div> Running pipeline...`;
    } catch (err) {
      $("runPipelineBtn").disabled = false;
      $("runPipelineBtn").textContent = "▶ Run Full Pipeline";
      $("pipelineStatus").textContent = `❌ ${err.message}`;
    }
  }

  // ── Toggle auto mode ──
  async function toggleAutoMode() {
    const enabled = $("autoCheckbox").checked;
    try {
      await chrome.runtime.sendMessage({ type: "SET_AUTO_MODE", enabled });
    } catch (err) {
      console.error("[MindCache Popup] Auto mode error:", err);
      $("autoCheckbox").checked = !enabled; // Revert
    }
    refresh();
  }

  // ── Open pages ──
  function openPage(path) {
    chrome.tabs.create({ url: `${API}${path}` });
  }

  // ── Event listeners ──
  $("runPipelineBtn").addEventListener("click", runPipeline);
  $("refreshBtn").addEventListener("click", refresh);
  $("autoCheckbox").addEventListener("change", toggleAutoMode);
  $("linkDashboard").addEventListener("click", () => openPage("/"));
  $("linkExplorer").addEventListener("click", () => openPage("/explore"));
  $("linkGraph").addEventListener("click", () => openPage("/graph"));

  // ── Initial load + auto-refresh ──
  await refresh();
  setInterval(refresh, 5000);
})();
