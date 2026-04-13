(function setupMindCacheApp(globalScope) {
  const app = globalScope.MindCache;
  const {
    elements,
    hasExplorerUi,
    loadPersistedState,
    loadExplorerState,
    updateContextOutputState,
    setBusy,
    setStatus,
  } = app;

  function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  function render() {
    if (hasExplorerUi) {
      app.renderExplorerBreadcrumbs();
      app.renderExplorerSelection();
      app.renderExplorerGrid();
    }
    if (elements.semanticSuggestionList) {
      app.renderSemanticSuggestions();
    }
    app.renderRootPager();
    app.renderGraph();
    app.renderStickyNodes();
    app.renderFocusNodeBanner();
    app.renderRootConstraints();
    app.renderGraphSelectedPath();
    app.renderInspector();
    app.renderTraceSummary();
  }

  function installControls() {
    if (elements.runQueryButton) {
      elements.runQueryButton.addEventListener("click", app.runRetrieval);
    }
    if (elements.clearStatesButton) {
      elements.clearStatesButton.addEventListener("click", () => {
        app.clearAllStates();
        setStatus("Cleared.");
      });
    }
    if (elements.prevRootButton) {
      elements.prevRootButton.addEventListener("click", () => app.focusRootByIndex(app.getUiRootIndex() - 1, { fit: false }));
    }
    if (elements.nextRootButton) {
      elements.nextRootButton.addEventListener("click", () => app.focusRootByIndex(app.getUiRootIndex() + 1, { fit: false }));
    }
    if (elements.zoomOutButton) {
      elements.zoomOutButton.addEventListener("click", () => app.adjustZoom(0.86));
    }
    if (elements.zoomInButton) {
      elements.zoomInButton.addEventListener("click", () => app.adjustZoom(1.16));
    }
    if (elements.fitViewButton) {
      elements.fitViewButton.addEventListener("click", app.fitView);
    }
    if (elements.resetViewButton) {
      elements.resetViewButton.addEventListener("click", app.resetView);
    }
    if (elements.clearExplorerSelectionButton) {
      elements.clearExplorerSelectionButton.addEventListener("click", app.clearExplorerSelection);
    }
    if (elements.openSelectedGraphButton) {
      elements.openSelectedGraphButton.addEventListener("click", () => app.openGraphFromExplorer());
    }
    if (elements.explorerBackLevelButton) {
      elements.explorerBackLevelButton.addEventListener("click", app.loadExplorerParentLevel);
    }

    window.addEventListener("resize", () => {
      app.syncGraphCanvasSize();
      app.applyViewportTransform();
      app.renderFocusNodeBanner();
      app.renderStickyNodes();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        app.runRetrieval();
      }
    });
  }

  async function initialize() {
    updateContextOutputState();
    loadPersistedState();
    loadExplorerState();
    app.installViewportInteraction();
    installControls();
    app.installWorkbenchInteractions?.();

    try {
      if (hasExplorerUi) {
        await app.loadExplorerRootLevel();
      } else {
        await app.loadGraphContext();
      }
    } catch (error) {
      console.error(error);
      setBusy(false);
      setStatus(`Failed to load tree: ${error.message}`);
    }
  }

  Object.assign(app, {
    showToast,
    render,
    installControls,
    initialize,
  });

  initialize();
})(window);
