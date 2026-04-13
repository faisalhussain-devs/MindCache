(function setupMindCacheWorkbench(globalScope) {
  const app = globalScope.MindCache;
  const {
    state,
    elements,
    LOAD_DEPTH,
    clearAutomatedSelections,
    getSelectedNodesByLevel,
    countSelectedNodes,
    fetchJson,
    ingestSubtree,
    expandAncestorChain,
    setBusy,
    setStatus,
    updateContextOutputState,
    parseRootIdsFromQuery,
    getContextNodeIdFromQuery,
    getSelectedIdsFromQuery,
    getContextDepthFromQuery,
    getPageMode,
    recomputeUserSelection,
    getNode,
    getRootId,
  } = app;

  const suggestionState = {
    items: [],
    loading: false,
    query: "",
    debounceTimer: null,
    requestToken: 0,
  };

  function clampRootIndex(index) {
    if (!state.rootIds.length) {
      return 0;
    }
    return Math.max(0, Math.min(state.rootIds.length - 1, index));
  }

  function getVisibleRootId() {
    if (!state.rootIds.length) {
      return null;
    }
    state.currentRootIndex = clampRootIndex(state.currentRootIndex);
    return state.rootIds[state.currentRootIndex] || null;
  }

  function focusRootById(rootId, options = {}) {
    const index = state.rootIds.indexOf(rootId);
    if (index >= 0) {
      app.focusRootByIndex(index, options);
    }
  }

  async function loadGraphContext() {
    setBusy(true);
    setStatus("Loading tree...");

    try {
      const requestedRootIds = parseRootIdsFromQuery();
      const focusNodeId = getContextNodeIdFromQuery();
      const selectedIdsFromQuery = getSelectedIdsFromQuery();
      const hasRequestedContext = requestedRootIds.length || Number.isInteger(focusNodeId) || selectedIdsFromQuery.length;
      const rootOnlyGraphMode = getPageMode() === "graph" && !hasRequestedContext;
      const depth = rootOnlyGraphMode ? 0 : getContextDepthFromQuery(LOAD_DEPTH);
      const requestedRootIndex = state.currentRootIndex;

      state.nodes.clear();
      state.rootIds = [];
      state.expanded.clear();
      state.rootPositions.clear();

      if (requestedRootIds.length) {
        for (const rootId of requestedRootIds) {
          const payload = await fetchJson(`/node/${rootId}/tree?depth=${depth}`);
          ingestSubtree(payload.node, null, true, true);
        }
      } else {
        const payload = await fetchJson(`/tree?depth=${depth}`);
        for (const root of payload.roots || []) {
          ingestSubtree(root, null, !rootOnlyGraphMode, true);
        }
      }

      const focusIndex = Number.isInteger(focusNodeId) ? state.rootIds.indexOf(focusNodeId) : -1;
      state.currentRootIndex = clampRootIndex(focusIndex >= 0 ? focusIndex : requestedRootIndex);
      state.activeNodeId = Number.isInteger(focusNodeId) && getNode(focusNodeId) ? focusNodeId : getVisibleRootId();
      if (selectedIdsFromQuery.length) {
        state.explicitSelected = new Set(selectedIdsFromQuery.filter((topicId) => getNode(topicId)));
        state.userRemoved.clear();
      }
      recomputeUserSelection();
      setStatus(rootOnlyGraphMode
        ? `Loaded ${state.rootIds.length} roots. Double-click a node to reveal its branch.`
        : `Loaded ${state.rootIds.length} roots.`);
      globalScope.MindCache.render();
      if (state.activeNodeId != null) {
        app.queueScrollToNode(state.activeNodeId, { behavior: "auto" });
      }
    } catch (error) {
      setStatus(`Failed to load tree: ${error.message}`);
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function loadInitialTree() {
    return loadGraphContext();
  }

  async function loadNodeBranch(nodeId) {
    const node = getNode(nodeId);
    if (!node || !node.hasChildren || state.loadingNodes.has(nodeId)) {
      return;
    }

    state.loadingNodes.add(nodeId);
    setStatus(`Loading ${node.name}...`);
    globalScope.MindCache.render();

    try {
      const payload = await fetchJson(`/node/${nodeId}/tree?depth=${LOAD_DEPTH}`);
      ingestSubtree(payload.node, node.parentId, true);
      state.expanded.add(nodeId);
      expandAncestorChain(nodeId);
      setStatus(`Expanded ${node.name}.`);
    } finally {
      state.loadingNodes.delete(nodeId);
      globalScope.MindCache.render();
    }
  }

  async function hydrateTraceNodes(topicIds) {
    const missing = [...new Set(topicIds)].filter((topicId) => !state.nodes.has(topicId));
    if (!missing.length) {
      return;
    }

    await Promise.all(missing.map(async (topicId) => {
      try {
        const payload = await fetchJson(`/node/${topicId}/tree?depth=1`);
        ingestSubtree(payload.node, payload.node.parent_id, false);
      } catch (error) {
        console.warn("Failed to hydrate trace node", topicId, error);
      }
    }));
  }

  function applyTrace(trace = {}) {
    const llmIds = [...(trace.root_ids || []), ...(trace.candidate_topic_ids || [])];
    state.llmSelected = new Set(llmIds.filter((topicId) => Number.isInteger(topicId)));
    state.finalSelected = new Set((trace.selected_topic_ids || []).filter((topicId) => Number.isInteger(topicId)));
    state.retrievalTrace = trace;

    for (const topicId of [
      ...(trace.constraint_path_ids || []),
      ...(trace.starting_node_ids || []),
      ...(trace.selected_topic_ids || []),
    ]) {
      if (Number.isInteger(topicId)) {
        expandAncestorChain(topicId);
      }
    }
  }

  function setSuggestionStatus(message) {
    if (elements.semanticSuggestionStatus) {
      elements.semanticSuggestionStatus.textContent = message;
    }
  }

  function renderSemanticSuggestions() {
    if (!elements.semanticSuggestionList) {
      return;
    }

    if (elements.semanticSuggestionBadge) {
      elements.semanticSuggestionBadge.textContent = suggestionState.items.length
        ? `${suggestionState.items.length} semantic suggestion${suggestionState.items.length === 1 ? "" : "s"}`
        : "3 semantic suggestions";
    }

    if (suggestionState.loading) {
      elements.semanticSuggestionList.className = "suggestion-list is-loading";
      elements.semanticSuggestionList.innerHTML = `
        <div class="suggestion-skeleton"></div>
        <div class="suggestion-skeleton"></div>
        <div class="suggestion-skeleton"></div>
      `;
      return;
    }

    if (!suggestionState.query.trim()) {
      elements.semanticSuggestionList.className = "suggestion-list empty-state";
      elements.semanticSuggestionList.textContent = "Type at least 3 characters to get suggestions.";
      return;
    }

    if (!suggestionState.items.length) {
      elements.semanticSuggestionList.className = "suggestion-list empty-state";
      elements.semanticSuggestionList.textContent = "No strong topic suggestions yet. Try a more specific query.";
      return;
    }

    elements.semanticSuggestionList.className = "suggestion-list";
    elements.semanticSuggestionList.replaceChildren();

    for (const item of suggestionState.items) {
      const card = document.createElement("article");
      const isSelected = state.userSelected.has(item.id);
      card.className = `suggestion-card${isSelected ? " is-selected" : ""}`;
      const scoreLabel = `${Math.max(0, Math.min(99, Math.round(item.score * 100)))}% match`;
      const description = (item.description || "").trim();
      card.innerHTML = `
        <div class="suggestion-card-head">
          <div>
            <p class="suggestion-path">${item.path}</p>
            <h3 class="suggestion-title">${item.name}</h3>
          </div>
          <span class="meta-pill">L${item.level}</span>
        </div>
        <p class="suggestion-score">${scoreLabel}</p>
        <p class="suggestion-description">${description || "High-level semantic match for this query."}</p>
        <div class="button-row tight suggestion-actions">
          <button class="${isSelected ? "ghost-button" : "primary-button"}" type="button" data-suggestion-action="toggle">
            ${isSelected ? "Unpin Filter" : "Use As Filter"}
          </button>
          <a class="page-link" href="${app.buildGraphTransferUrl(item.path_ids || [item.id], item.id, LOAD_DEPTH)}">Preview Graph</a>
        </div>
      `;

      card.querySelector('[data-suggestion-action="toggle"]').addEventListener("click", () => {
        if (!getNode(item.id)) {
          setSuggestionStatus("That suggestion is not hydrated locally yet. Open its graph preview or expand the branch first.");
          return;
        }
        app.toggleUserSelected(item.id);
        setSuggestionStatus(state.userSelected.has(item.id)
          ? `Pinned ${item.name} as a retrieval filter.`
          : `Removed ${item.name} from retrieval filters.`);
        globalScope.MindCache.render();
      });

      elements.semanticSuggestionList.appendChild(card);
    }
  }

  async function fetchSemanticSuggestions(query) {
    if (!elements.semanticSuggestionList) {
      return;
    }

    const cleanQuery = query.trim();
    suggestionState.query = cleanQuery;

    if (cleanQuery.length < 3) {
      suggestionState.items = [];
      suggestionState.loading = false;
      setSuggestionStatus("Suggestions search higher-level topic nodes for fast constraint picks.");
      renderSemanticSuggestions();
      return;
    }

    const requestToken = ++suggestionState.requestToken;
    suggestionState.loading = true;
    setSuggestionStatus("Finding likely branches...");
    renderSemanticSuggestions();

    try {
      const payload = await fetchJson(`/topic-suggestions?q=${encodeURIComponent(cleanQuery)}&limit=3&max_level=2`);
      if (requestToken !== suggestionState.requestToken) {
        return;
      }
      suggestionState.items = payload.suggestions || [];
      setSuggestionStatus(suggestionState.items.length
        ? "Pin one or more suggested branches to constrain retrieval."
        : "No strong semantic matches yet.");
    } catch (error) {
      if (requestToken !== suggestionState.requestToken) {
        return;
      }
      console.error(error);
      suggestionState.items = [];
      setSuggestionStatus(`Suggestion lookup failed: ${error.message}`);
    } finally {
      if (requestToken === suggestionState.requestToken) {
        suggestionState.loading = false;
        renderSemanticSuggestions();
      }
    }
  }

  function installWorkbenchInteractions() {
    if (!elements.queryInput) {
      return;
    }

    elements.queryInput.addEventListener("input", () => {
      const query = elements.queryInput.value;
      if (suggestionState.debounceTimer) {
        clearTimeout(suggestionState.debounceTimer);
      }
      suggestionState.debounceTimer = setTimeout(() => {
        fetchSemanticSuggestions(query);
      }, 260);
    });

    if (elements.queryInput.value.trim()) {
      fetchSemanticSuggestions(elements.queryInput.value);
    }
  }

  async function runRetrieval() {
    if (!elements.queryInput) {
      return;
    }

    const query = elements.queryInput.value.trim();
    if (!query) {
      setStatus("Enter a query.");
      elements.queryInput.focus();
      return;
    }

    clearAutomatedSelections();
    const selectedNodesByLevel = getSelectedNodesByLevel();
    const totalSelectedNodes = countSelectedNodes(selectedNodesByLevel);

    setBusy(true);
    setStatus(totalSelectedNodes ? "Running constrained retrieval..." : "Running retrieval...");
    globalScope.MindCache.render();

    let focusRootId = null;

    try {
      const payload = await fetchJson("/retrieve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          selected_nodes_by_level: selectedNodesByLevel,
        }),
      });

      const trace = payload.trace || {};
      await hydrateTraceNodes([
        ...(trace.candidate_topic_ids || []),
        ...(trace.selected_topic_ids || []),
        ...(trace.constraint_path_ids || []),
      ]);
      applyTrace(trace);
      if (elements.contextOutput) {
        elements.contextOutput.textContent = payload.context || "(No context returned)";
        updateContextOutputState();
      }
      setStatus("Retrieval finished.");
      globalScope.MindCache.showToast("Retrieval complete", "success");
      focusRootId = trace.root_ids?.[0] ?? getRootId(trace.selected_topic_ids?.[0]);
    } catch (error) {
      console.error(error);
      if (elements.contextOutput) {
        elements.contextOutput.textContent = "Retrieval failed.";
        updateContextOutputState();
      }
      setStatus(`Retrieval failed: ${error.message}`);
      globalScope.MindCache.showToast("Retrieval failed", "error");
    } finally {
      setBusy(false);
      globalScope.MindCache.render();
    }

    if (focusRootId != null) {
      focusRootById(focusRootId, {
        fit: false,
        preserveScale: true,
      });
    }
  }

  Object.assign(app, {
    clampRootIndex,
    getVisibleRootId,
    focusRootById,
    loadInitialTree,
    loadGraphContext,
    loadNodeBranch,
    hydrateTraceNodes,
    applyTrace,
    setSuggestionStatus,
    renderSemanticSuggestions,
    fetchSemanticSuggestions,
    installWorkbenchInteractions,
    runRetrieval,
  });
})(window);
