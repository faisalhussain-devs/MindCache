(function setupMindCacheCore(globalScope) {
  const LOAD_DEPTH = 3;
  const NODE_WIDTH = 160;
  const NODE_HEIGHT = 48;
  const NODE_RADIUS = 4;
  const COLUMN_STEP = NODE_WIDTH + 140;
  const ROW_STEP = 26;
  const GRAPH_PADDING_X = 32;
  const GRAPH_PADDING_Y = 40;
  const MIN_SCALE = 0.46;
  const MAX_SCALE = 1.8;
  const DEFAULT_VIEWPORT = { x: 0, y: 0, scale: 0.6 };
  const STORAGE_KEY = "mindcache_graph_ui_state_v1";
  const STAGGER_OFFSET = 32;
  const MAX_SIBLING_LANES = 7;
  const SIBLING_LANE_STEP = NODE_WIDTH + 100;
  const STICKY_CENTER_SNAP = 30;
  const PARENT_LANE_OFFSET = 80;
  const ROOT_SECTION_GAP = 50;
  const EXPLORER_STORAGE_KEY = "mindcache_explorer_ui_state_v1";
  const EXPLORER_PRELOAD_DEPTH = 3;

  const state = {
    nodes: new Map(),
    rootIds: [],
    expanded: new Set(),
    loadingNodes: new Set(),
    explicitSelected: new Set(),
    userSelected: new Set(),
    userRemoved: new Set(),
    llmSelected: new Set(),
    finalSelected: new Set(),
    activeNodeId: null,
    retrievalTrace: null,
    positions: new Map(),
    rootPositions: new Map(),
    currentRootIndex: 0,
    persistedSelectedNodesByLevel: {},
    viewport: { ...DEFAULT_VIEWPORT },
    dragging: null,
    panning: null,
  };

  const elements = {
    statusText: document.getElementById("statusText"),
    queryInput: document.getElementById("queryInput"),
    clearStatesButton: document.getElementById("clearStatesButton"),
    runQueryButton: document.getElementById("runQueryButton"),
    prevRootButton: document.getElementById("prevRootButton"),
    nextRootButton: document.getElementById("nextRootButton"),
    rootPagerTitle: document.getElementById("rootPagerTitle"),
    rootPagerLabel: document.getElementById("rootPagerLabel"),
    zoomOutButton: document.getElementById("zoomOutButton"),
    zoomInButton: document.getElementById("zoomInButton"),
    fitViewButton: document.getElementById("fitViewButton"),
    resetViewButton: document.getElementById("resetViewButton"),
    graphViewport: document.getElementById("graphViewport"),
    graphCanvas: document.getElementById("graphCanvas"),
    graphScene: document.getElementById("graphScene"),
    focusNodeBanner: document.getElementById("focusNodeBanner"),
    stickyNodeLayer: document.getElementById("stickyNodeLayer"),
    nodeInspector: document.getElementById("nodeInspector"),
    rootConstraintList: document.getElementById("rootConstraintList"),
    traceSummary: document.getElementById("traceSummary"),
    graphSelectedPathList: document.getElementById("graphSelectedPathList"),
    graphSelectedPathCount: document.getElementById("graphSelectedPathCount"),
    contextOutput: document.getElementById("contextOutput"),
    semanticSuggestionList: document.getElementById("semanticSuggestionList"),
    semanticSuggestionStatus: document.getElementById("semanticSuggestionStatus"),
    semanticSuggestionBadge: document.getElementById("semanticSuggestionBadge"),
    explorerGrid: document.getElementById("explorerGrid"),
    explorerBreadcrumbs: document.getElementById("explorerBreadcrumbs"),
    explorerLevelTitle: document.getElementById("explorerLevelTitle"),
    explorerStatus: document.getElementById("explorerStatus"),
    explorerBackLevelButton: document.getElementById("explorerBackLevelButton"),
    explorerSelectionList: document.getElementById("explorerSelectionList"),
    explorerStats: document.getElementById("explorerStats"),
    clearExplorerSelectionButton: document.getElementById("clearExplorerSelectionButton"),
    openSelectedGraphButton: document.getElementById("openSelectedGraphButton"),
  };

  const hasGraphUi = Boolean(elements.graphViewport && elements.graphCanvas && elements.graphScene);
  const hasExplorerUi = Boolean(elements.explorerGrid && elements.explorerBreadcrumbs);
  const hasWorkbenchUi = Boolean(elements.queryInput || elements.contextOutput);

  const explorerState = {
    currentNodeId: null,
    visibleNodeIds: [],
    trailIds: [],
    selectedNodeIds: new Set(),
  };

  function updateContextOutputState() {
    if (!elements.contextOutput) {
      return;
    }
    const text = (elements.contextOutput.textContent || "").trim();
    const isEmptyState = !text || text === "No context generated yet." || text === "(No context returned)";
    elements.contextOutput.classList.toggle("is-empty", isEmptyState);
  }

  function normalizeSelectedNodesByLevel(grouped) {
    if (!grouped || typeof grouped !== "object") {
      return {};
    }
    const normalized = {};
    for (const [levelKey, nodeIds] of Object.entries(grouped)) {
      const cleanIds = [];
      const seen = new Set();
      for (const nodeId of nodeIds || []) {
        const cleanId = Number(nodeId);
        if (!Number.isInteger(cleanId) || seen.has(cleanId)) {
          continue;
        }
        seen.add(cleanId);
        cleanIds.push(cleanId);
      }
      if (cleanIds.length) {
        normalized[String(Number(levelKey))] = cleanIds;
      }
    }
    return normalized;
  }

  function countSelectedNodes(grouped) {
    return Object.values(grouped || {}).reduce((total, nodeIds) => total + nodeIds.length, 0);
  }

  function normalizeIdList(values) {
    const result = [];
    const seen = new Set();
    for (const value of values || []) {
      const id = Number(value);
      if (!Number.isInteger(id) || seen.has(id)) {
        continue;
      }
      seen.add(id);
      result.push(id);
    }
    return result;
  }

  function buildSelectedNodesByLevelFromNodeIds(nodeIds) {
    const grouped = {};
    const orderedIds = normalizeIdList(nodeIds).sort((a, b) => {
      const nodeA = getNode(a);
      const nodeB = getNode(b);
      if (!nodeA || !nodeB) {
        return 0;
      }
      if (nodeA.level !== nodeB.level) {
        return nodeA.level - nodeB.level;
      }
      return (nodeA.name || "").localeCompare(nodeB.name || "", undefined, { sensitivity: "base" });
    });

    for (const nodeId of orderedIds) {
      const node = getNode(nodeId);
      if (!node) {
        continue;
      }
      const key = String(node.level);
      grouped[key] = grouped[key] || [];
      grouped[key].push(nodeId);
    }
    return grouped;
  }

  function syncExplorerPathToSharedSelection(nodeIds = [...explorerState.selectedNodeIds]) {
    const leafNodeIds = getLeafSelectedNodeIds(nodeIds);
    state.persistedSelectedNodesByLevel = normalizeSelectedNodesByLevel(buildSelectedNodesByLevelFromNodeIds(leafNodeIds));
    savePersistedState();
  }

  function getLeafSelectedNodeIds(nodeIds) {
    const normalized = normalizeIdList(nodeIds);
    return normalized.filter((nodeId) => !normalized.some((otherNodeId) => otherNodeId !== nodeId && isAncestor(nodeId, otherNodeId)));
  }

  function getSelectedPathNodeIds(grouped) {
    const flattened = [];
    for (const nodeIds of Object.values(grouped || {})) {
      flattened.push(...normalizeIdList(nodeIds));
    }
    return getLeafSelectedNodeIds(flattened);
  }

  function loadExplorerState() {
    try {
      const raw = localStorage.getItem(EXPLORER_STORAGE_KEY);
      if (!raw) {
        return;
      }
      const parsed = JSON.parse(raw);
      explorerState.currentNodeId = Number.isInteger(parsed.currentNodeId) ? parsed.currentNodeId : null;
      explorerState.visibleNodeIds = normalizeIdList(parsed.visibleNodeIds);
      explorerState.trailIds = normalizeIdList(parsed.trailIds);
      explorerState.selectedNodeIds = new Set(normalizeIdList(parsed.selectedNodeIds));
    } catch (error) {
      console.warn("Failed to restore explorer state", error);
    }
  }

  function saveExplorerState() {
    try {
      localStorage.setItem(EXPLORER_STORAGE_KEY, JSON.stringify({
        currentNodeId: explorerState.currentNodeId,
        visibleNodeIds: explorerState.visibleNodeIds,
        trailIds: explorerState.trailIds,
        selectedNodeIds: [...explorerState.selectedNodeIds],
      }));
    } catch (error) {
      console.warn("Failed to persist explorer state", error);
    }
  }

  function getPageMode() {
    if (hasExplorerUi) {
      return "explorer";
    }
    if (hasGraphUi) {
      return "graph";
    }
    if (hasWorkbenchUi) {
      return "workbench";
    }
    return "unknown";
  }

  function loadPersistedState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return;
      }
      const parsed = JSON.parse(raw);
      state.currentRootIndex = Number.isInteger(parsed.currentRootIndex) ? parsed.currentRootIndex : 0;
      state.explicitSelected = new Set((parsed.explicitSelected || []).filter(Number.isInteger));
      state.userRemoved = new Set((parsed.userRemoved || []).filter(Number.isInteger));
      state.persistedSelectedNodesByLevel = normalizeSelectedNodesByLevel(parsed.selectedNodesByLevel);
    } catch (error) {
      console.warn("Failed to restore graph state", error);
    }
  }

  function savePersistedState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        currentRootIndex: state.currentRootIndex,
        explicitSelected: [...state.explicitSelected],
        userRemoved: [...state.userRemoved],
        selectedNodesByLevel: state.persistedSelectedNodesByLevel,
      }));
    } catch (error) {
      console.warn("Failed to persist graph state", error);
    }
  }

  function svgEl(name, attrs = {}) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs).forEach(([key, value]) => {
      element.setAttribute(key, String(value));
    });
    return element;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function getNode(nodeId) {
    return state.nodes.get(nodeId) || null;
  }

  function getCss(variableName) {
    return getComputedStyle(document.documentElement).getPropertyValue(variableName).trim();
  }

  function getNodeColor(nodeId) {
    if (state.userRemoved.has(nodeId)) {
      return getCss("--removed-node");
    }
    if (state.finalSelected.has(nodeId)) {
      return getCss("--final-node");
    }
    if (state.userSelected.has(nodeId)) {
      return getCss("--user-node");
    }
    if (state.llmSelected.has(nodeId)) {
      return getCss("--llm-node");
    }
    return getCss("--default-node");
  }

  function getVisibleChildIds(nodeId) {
    const node = getNode(nodeId);
    if (!node || !state.expanded.has(nodeId)) {
      return [];
    }
    return (node.children || []).filter((childId) => state.nodes.has(childId));
  }

  function getLoadedChildIds(nodeId) {
    const node = getNode(nodeId);
    if (!node) {
      return [];
    }
    return (node.children || []).filter((childId) => state.nodes.has(childId));
  }

  function getSiblingLaneCount(childCount) {
    if (childCount >= 14) {
      return MAX_SIBLING_LANES;
    }
    if (childCount >= 8) {
      return Math.min(MAX_SIBLING_LANES, 3);
    }
    if (childCount >= 3) {
      return Math.min(MAX_SIBLING_LANES, 2);
    }
    return 1;
  }

  function getLaneOffset(depth, laneIndex, siblingIndex) {
    if (depth === 0) {
      return 0;
    }
    return laneIndex * SIBLING_LANE_STEP + ((siblingIndex % 2 === 1) ? STAGGER_OFFSET : 0);
  }

  function setStatus(message) {
    if (elements.statusText) {
      elements.statusText.textContent = message;
    }
  }

  function setBusy(isBusy) {
    if (elements.runQueryButton) {
      elements.runQueryButton.disabled = isBusy;
    }
  }

  function compareNodeIds(a, b) {
    const nodeA = getNode(a);
    const nodeB = getNode(b);
    return (nodeA?.name || "").localeCompare(nodeB?.name || "", undefined, { sensitivity: "base" });
  }

  function pushUnique(array, value) {
    if (!array.includes(value)) {
      array.push(value);
    }
  }

  function formatPath(nodeId) {
    const path = [];
    let current = getNode(nodeId);
    while (current) {
      path.unshift(current.name);
      current = current.parentId != null ? getNode(current.parentId) : null;
    }
    return path.join(" > ");
  }

  function formatPathChain(nodeId, separator = " -> ") {
    const chain = [];
    let current = getNode(nodeId);
    while (current) {
      chain.unshift(current.name || `Node ${current.id}`);
      current = current.parentId != null ? getNode(current.parentId) : null;
    }
    return chain.join(separator);
  }

  function getAncestorIds(nodeId) {
    const ancestors = [];
    let current = getNode(nodeId);
    while (current?.parentId != null) {
      ancestors.unshift(current.parentId);
      current = getNode(current.parentId);
    }
    return ancestors;
  }

  function getRootId(nodeId) {
    let current = getNode(nodeId);
    let rootId = current?.id ?? null;
    while (current?.parentId != null) {
      rootId = current.parentId;
      current = getNode(current.parentId);
    }
    return rootId;
  }

  function isAncestor(ancestorId, nodeId) {
    let current = getNode(nodeId);
    while (current?.parentId != null) {
      if (current.parentId === ancestorId) {
        return true;
      }
      current = getNode(current.parentId);
    }
    return false;
  }

  function expandAncestorChain(nodeId) {
    for (const ancestorId of getAncestorIds(nodeId)) {
      state.expanded.add(ancestorId);
    }
  }

  function clearRemovedOnPath(nodeId) {
    state.userRemoved.delete(nodeId);
    for (const ancestorId of getAncestorIds(nodeId)) {
      state.userRemoved.delete(ancestorId);
    }
  }

  function removeExplicitSelectionsInBranch(nodeId) {
    for (const explicitId of [...state.explicitSelected]) {
      if (explicitId === nodeId || isAncestor(nodeId, explicitId)) {
        state.explicitSelected.delete(explicitId);
      }
    }
  }

  function recomputeUserSelection() {
    for (const explicitId of [...state.explicitSelected]) {
      if (state.userRemoved.has(explicitId)) {
        state.explicitSelected.delete(explicitId);
        continue;
      }
      if (getAncestorIds(explicitId).some((ancestorId) => state.userRemoved.has(ancestorId))) {
        state.explicitSelected.delete(explicitId);
      }
    }

    state.userSelected = new Set();
    for (const explicitId of state.explicitSelected) {
      state.userSelected.add(explicitId);
      for (const ancestorId of getAncestorIds(explicitId)) {
        if (!state.userRemoved.has(ancestorId)) {
          state.userSelected.add(ancestorId);
        }
      }
    }
  }

  function buildSelectedNodesByLevel() {
    const grouped = {};
    const orderedSelected = [...state.userSelected].sort((a, b) => {
      const nodeA = getNode(a);
      const nodeB = getNode(b);
      if (!nodeA || !nodeB) {
        return 0;
      }
      if (nodeA.level !== nodeB.level) {
        return nodeA.level - nodeB.level;
      }
      return (nodeA.name || "").localeCompare(nodeB.name || "", undefined, { sensitivity: "base" });
    });

    for (const nodeId of orderedSelected) {
      const node = getNode(nodeId);
      if (!node) {
        continue;
      }
      const key = String(node.level);
      grouped[key] = grouped[key] || [];
      grouped[key].push(nodeId);
    }
    return grouped;
  }

  function getSelectedNodesByLevel() {
    const computed = buildSelectedNodesByLevel();
    return countSelectedNodes(computed) >= countSelectedNodes(state.persistedSelectedNodesByLevel)
      ? computed
      : state.persistedSelectedNodesByLevel;
  }

  function syncSelectedStateToStorage() {
    state.persistedSelectedNodesByLevel = normalizeSelectedNodesByLevel(buildSelectedNodesByLevel());
    savePersistedState();
  }

  function clearAutomatedSelections() {
    state.llmSelected.clear();
    state.finalSelected.clear();
    state.retrievalTrace = null;
  }

  function clearAllStates() {
    state.explicitSelected.clear();
    state.userSelected.clear();
    state.userRemoved.clear();
    state.persistedSelectedNodesByLevel = {};
    clearAutomatedSelections();
    if (elements.contextOutput) {
      elements.contextOutput.textContent = "No context generated yet.";
      updateContextOutputState();
    }
    savePersistedState();
    globalScope.MindCache.render();
  }

  function ingestSubtree(rawNode, fallbackParentId = null, autoExpand = false, forceRoot = false) {
    const existing = getNode(rawNode.id) || {
      id: rawNode.id,
      children: [],
      childrenLoaded: false,
    };

    const node = {
      ...existing,
      id: rawNode.id,
      name: rawNode.name,
      level: rawNode.level ?? existing.level ?? 0,
      description: rawNode.description || existing.description || "",
      parentId: forceRoot ? null : (rawNode.parent_id ?? fallbackParentId),
      hasChildren: Boolean(rawNode.has_children),
    };

    state.nodes.set(node.id, node);

    const nextChildren = [];
    for (const child of rawNode.children || []) {
      nextChildren.push(child.id);
      ingestSubtree(child, node.id, autoExpand, false);
    }

    if (nextChildren.length) {
      node.children = nextChildren.sort(compareNodeIds);
      node.childrenLoaded = true;
      if (autoExpand) {
        state.expanded.add(node.id);
      }
    } else if (!node.hasChildren) {
      node.children = [];
      node.childrenLoaded = true;
      state.expanded.delete(node.id);
    }

    if (node.parentId == null) {
      pushUnique(state.rootIds, node.id);
      state.rootIds.sort(compareNodeIds);
    } else {
      const parent = getNode(node.parentId);
      if (parent) {
        parent.hasChildren = true;
        parent.children = [...(parent.children || [])];
        pushUnique(parent.children, node.id);
        parent.children.sort(compareNodeIds);
      }
    }
  }

  function parseRootIdsFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const roots = params.get("roots");
    if (roots) {
      return normalizeIdList(roots.split(","));
    }
    const root = params.get("root") || params.get("node");
    if (root) {
      return normalizeIdList([root]);
    }
    return [];
  }

  function getContextNodeIdFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const node = params.get("node") || params.get("focus");
    return node ? Number(node) : null;
  }

  function getSelectedIdsFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const selected = params.get("selected");
    if (!selected) {
      return [];
    }
    return normalizeIdList(selected.split(","));
  }

  function getContextDepthFromQuery(defaultDepth = LOAD_DEPTH) {
    const params = new URLSearchParams(window.location.search);
    const depth = Number(params.get("depth"));
    if (!Number.isInteger(depth) || depth < 0) {
      return defaultDepth;
    }
    return Math.min(10, depth);
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Request failed with status ${response.status}`);
    }
    return response.json();
  }

  globalScope.MindCache = globalScope.MindCache || {};
  Object.assign(globalScope.MindCache, {
    LOAD_DEPTH,
    NODE_WIDTH,
    NODE_HEIGHT,
    NODE_RADIUS,
    COLUMN_STEP,
    ROW_STEP,
    GRAPH_PADDING_X,
    GRAPH_PADDING_Y,
    MIN_SCALE,
    MAX_SCALE,
    DEFAULT_VIEWPORT,
    STAGGER_OFFSET,
    STICKY_CENTER_SNAP,
    PARENT_LANE_OFFSET,
    ROOT_SECTION_GAP,
    EXPLORER_PRELOAD_DEPTH,
    state,
    elements,
    explorerState,
    hasGraphUi,
    hasExplorerUi,
    hasWorkbenchUi,
    updateContextOutputState,
    normalizeSelectedNodesByLevel,
    countSelectedNodes,
    normalizeIdList,
    buildSelectedNodesByLevelFromNodeIds,
    syncExplorerPathToSharedSelection,
    getLeafSelectedNodeIds,
    getSelectedPathNodeIds,
    loadExplorerState,
    saveExplorerState,
    getPageMode,
    loadPersistedState,
    savePersistedState,
    svgEl,
    escapeHtml,
    getNode,
    getCss,
    getNodeColor,
    getVisibleChildIds,
    getLoadedChildIds,
    getSiblingLaneCount,
    getLaneOffset,
    setStatus,
    setBusy,
    compareNodeIds,
    pushUnique,
    formatPath,
    formatPathChain,
    getAncestorIds,
    getRootId,
    isAncestor,
    expandAncestorChain,
    clearRemovedOnPath,
    removeExplicitSelectionsInBranch,
    recomputeUserSelection,
    buildSelectedNodesByLevel,
    getSelectedNodesByLevel,
    syncSelectedStateToStorage,
    clearAutomatedSelections,
    clearAllStates,
    ingestSubtree,
    parseRootIdsFromQuery,
    getContextNodeIdFromQuery,
    getSelectedIdsFromQuery,
    getContextDepthFromQuery,
    fetchJson,
  });
})(window);
