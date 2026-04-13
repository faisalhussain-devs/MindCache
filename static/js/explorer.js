(function setupMindCacheExplorer(globalScope) {
  const app = globalScope.MindCache;
  const {
    LOAD_DEPTH,
    EXPLORER_PRELOAD_DEPTH,
    state,
    elements,
    explorerState,
    hasExplorerUi,
    normalizeIdList,
    saveExplorerState,
    syncExplorerPathToSharedSelection,
    getLeafSelectedNodeIds,
    fetchJson,
    ingestSubtree,
    getNode,
    getLoadedChildIds,
    getAncestorIds,
    isAncestor,
    formatPath,
    escapeHtml,
    setBusy,
  } = app;

  function getExplorerVisibleNodes() {
    return explorerState.visibleNodeIds.map((nodeId) => getNode(nodeId)).filter(Boolean);
  }

  function getExplorerTitle() {
    if (!explorerState.currentNodeId) {
      return "Root";
    }
    const node = getNode(explorerState.currentNodeId);
    return node ? formatPath(node.id) : "Subtree";
  }

  function setExplorerStatus(message) {
    if (elements.explorerStatus) {
      elements.explorerStatus.textContent = message;
    }
  }

  function syncExplorerSelectionToStorage() {
    saveExplorerState();
  }

  function setExplorerVisibleNodes(nodeIds, options = {}) {
    const { currentNodeId = null, trailIds = [] } = options;
    explorerState.currentNodeId = currentNodeId;
    explorerState.visibleNodeIds = normalizeIdList(nodeIds);
    explorerState.trailIds = normalizeIdList(trailIds);
    syncExplorerSelectionToStorage();
    globalScope.MindCache.render();
  }

  function toggleExplorerSelection(nodeId) {
    if (explorerState.selectedNodeIds.has(nodeId)) {
      explorerState.selectedNodeIds.delete(nodeId);
    } else {
      explorerState.selectedNodeIds.add(nodeId);
    }
    syncExplorerSelectionToStorage();
    syncExplorerPathToSharedSelection();
    globalScope.MindCache.render();
  }

  function clearExplorerSelection() {
    explorerState.selectedNodeIds.clear();
    syncExplorerSelectionToStorage();
    syncExplorerPathToSharedSelection([]);
    globalScope.MindCache.render();
  }

  function buildGraphTransferUrl(rootIds, focusNodeId = null, depth = LOAD_DEPTH) {
    const params = new URLSearchParams();
    const normalizedRootIds = normalizeIdList(rootIds);
    const transferRootIds = normalizedRootIds.filter((candidateId) => !normalizedRootIds.some((otherId) => otherId !== candidateId && isAncestor(otherId, candidateId)));
    if (Number.isInteger(focusNodeId)) {
      params.set("focus", String(focusNodeId));
    } else if (normalizedRootIds.length) {
      params.set("focus", String(normalizedRootIds[0]));
    }
    if (transferRootIds.length) {
      params.set("roots", transferRootIds.join(","));
    }
    if (normalizedRootIds.length) {
      params.set("selected", normalizedRootIds.join(","));
    }
    if (Number.isInteger(depth)) {
      params.set("depth", String(depth));
    }
    const query = params.toString();
    return query ? `/graph?${query}` : "/graph";
  }

  function openGraphFromExplorer(nodeId = null) {
    const selectedNodeId = Number.isInteger(nodeId)
      ? nodeId
      : (explorerState.selectedNodeIds.values().next().value ?? explorerState.currentNodeId ?? null);
    const selectedIds = new Set();

    for (const nodeIdValue of explorerState.selectedNodeIds) {
      for (const ancestorId of [...getAncestorIds(nodeIdValue), nodeIdValue]) {
        selectedIds.add(ancestorId);
      }
    }

    if (Number.isInteger(selectedNodeId)) {
      for (const ancestorId of [...getAncestorIds(selectedNodeId), selectedNodeId]) {
        selectedIds.add(ancestorId);
      }
    }

    const focusNodeId = Number.isInteger(selectedNodeId) ? selectedNodeId : null;
    syncExplorerPathToSharedSelection(selectedIds);
    window.location.href = buildGraphTransferUrl([...selectedIds], focusNodeId, LOAD_DEPTH);
  }

  async function ensureExplorerContext(nodeId = null) {
    setBusy(true);
    if (!hasExplorerUi) {
      setBusy(false);
      return;
    }

    try {
      if (nodeId == null) {
        setExplorerStatus("Loading roots...");
        const payload = await fetchJson(`/tree?depth=${EXPLORER_PRELOAD_DEPTH}`);
        state.nodes.clear();
        state.rootIds = [];
        state.expanded.clear();
        for (const root of payload.roots || []) {
          ingestSubtree(root, null, true, true);
        }
        setExplorerVisibleNodes(state.rootIds, { currentNodeId: null, trailIds: [] });
        setExplorerStatus(`Loaded ${state.rootIds.length} roots.`);
        return;
      }

      const currentNode = getNode(nodeId);
      if (currentNode && currentNode.childrenLoaded) {
        setExplorerVisibleNodes(getLoadedChildIds(nodeId), {
          currentNodeId: nodeId,
          trailIds: [...getAncestorIds(nodeId), nodeId],
        });
        setExplorerStatus(`Exploring ${currentNode.name}.`);
        return;
      }

      setExplorerStatus("Loading children...");
      const payload = await fetchJson(`/node/${nodeId}/tree?depth=${EXPLORER_PRELOAD_DEPTH}`);
      ingestSubtree(payload.node, payload.node.parent_id, false, false);
      const hydratedNode = getNode(nodeId) || payload.node;
      setExplorerVisibleNodes(getLoadedChildIds(nodeId), {
        currentNodeId: nodeId,
        trailIds: [...getAncestorIds(nodeId), nodeId],
      });
      setExplorerStatus(`Exploring ${hydratedNode?.name || `Node ${nodeId}`}.`);
    } catch (error) {
      console.error(error);
      setExplorerStatus(`Failed to load explorer context: ${error.message}`);
    } finally {
      setBusy(false);
      globalScope.MindCache.render();
    }
  }

  async function loadExplorerRootLevel() {
    await ensureExplorerContext(null);
  }

  async function loadExplorerChildren(nodeId) {
    await ensureExplorerContext(nodeId);
  }

  async function loadExplorerParentLevel() {
    if (explorerState.currentNodeId == null) {
      return;
    }
    const currentNode = getNode(explorerState.currentNodeId);
    if (!currentNode || currentNode.parentId == null) {
      await loadExplorerRootLevel();
      return;
    }
    await loadExplorerChildren(currentNode.parentId);
  }

  function renderExplorerBreadcrumbs() {
    if (!elements.explorerBreadcrumbs) {
      return;
    }
    elements.explorerBreadcrumbs.replaceChildren();
  }

  function renderExplorerSelection() {
    if (!elements.explorerSelectionList || !elements.explorerStats) {
      return;
    }

    const selectedNodes = getLeafSelectedNodeIds([...explorerState.selectedNodeIds]).map((nodeId) => getNode(nodeId)).filter(Boolean);
    elements.explorerStats.textContent = `${selectedNodes.length} selected`;
    if (!selectedNodes.length) {
      elements.explorerSelectionList.className = "path-chain-list empty-state";
      elements.explorerSelectionList.textContent = "No nodes selected.";
      return;
    }

    elements.explorerSelectionList.className = "path-chain-list";
    elements.explorerSelectionList.replaceChildren();
    for (const node of selectedNodes) {
      const chip = document.createElement("button");
      chip.className = "path-chain-item explorer-path-button";
      chip.type = "button";
      chip.textContent = formatPath(node.id);
      chip.title = node.name;
      chip.addEventListener("click", () => openGraphFromExplorer(node.id));
      elements.explorerSelectionList.appendChild(chip);
    }
  }

  function renderExplorerGrid() {
    if (!elements.explorerGrid) {
      return;
    }

    const visibleNodes = getExplorerVisibleNodes();
    if (elements.explorerLevelTitle) {
      elements.explorerLevelTitle.textContent = getExplorerTitle();
    }
    if (elements.explorerBackLevelButton) {
      elements.explorerBackLevelButton.disabled = explorerState.currentNodeId == null;
    }
    if (elements.explorerStatus) {
      elements.explorerStatus.textContent = visibleNodes.length
        ? `${visibleNodes.length} ${explorerState.currentNodeId == null ? "root cards" : "child cards"} ready.`
        : "No visible nodes.";
    }

    elements.explorerGrid.replaceChildren();
    if (!visibleNodes.length) {
      const emptyCard = document.createElement("div");
      emptyCard.className = "explorer-empty-card panel-card";
      emptyCard.textContent = "No nodes available in this level.";
      elements.explorerGrid.appendChild(emptyCard);
      return;
    }

    for (const [index, node] of visibleNodes.entries()) {
      const card = document.createElement("article");
      const isSelected = explorerState.selectedNodeIds.has(node.id);
      card.className = `panel-card explorer-node-card${isSelected ? " is-selected" : ""}`;
      card.style.animationDelay = `${index * 40}ms`;
      card.title = node.hasChildren ? `Click to explore ${node.name}` : node.name;

      const childCount = (node.children || []).filter((childId) => state.nodes.has(childId)).length;
      const childLabel = childCount === 1 ? "1 child" : `${childCount} children`;
      card.innerHTML = `
        <div class="explorer-card-head">
          <h3 class="explorer-card-title">${escapeHtml(node.name)}</h3>
          <span class="meta-pill">${childLabel}</span>
        </div>
        <div class="button-row tight explorer-card-actions-top">
          <button class="ghost-button" type="button" data-explorer-action="select">${isSelected ? "✓ Selected" : "Select"}</button>
          <button class="primary-button" type="button" data-explorer-action="open">Graph</button>
        </div>
      `;

      // Card click = drill into children (primary interaction)
      card.addEventListener("click", async (event) => {
        if (event.target instanceof Element && event.target.closest("button")) {
          return;
        }
        if (node.hasChildren) {
          await loadExplorerChildren(node.id);
        } else {
          toggleExplorerSelection(node.id);
        }
      });

      card.querySelectorAll("[data-explorer-action]").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          const action = button.getAttribute("data-explorer-action");
          if (action === "select") {
            toggleExplorerSelection(node.id);
          } else if (action === "open") {
            openGraphFromExplorer(node.id);
          }
        });
      });

      elements.explorerGrid.appendChild(card);
    }
  }

  Object.assign(app, {
    getExplorerVisibleNodes,
    getExplorerTitle,
    setExplorerStatus,
    setExplorerVisibleNodes,
    toggleExplorerSelection,
    clearExplorerSelection,
    buildGraphTransferUrl,
    openGraphFromExplorer,
    ensureExplorerContext,
    loadExplorerRootLevel,
    loadExplorerChildren,
    loadExplorerParentLevel,
    renderExplorerBreadcrumbs,
    renderExplorerSelection,
    renderExplorerGrid,
  });
})(window);
