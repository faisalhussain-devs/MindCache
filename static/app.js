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
const ROOT_SECTION_GAP = 150;
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
  render();
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
  if (!Number.isInteger(depth) || depth < 1) {
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

function getUiRootIndex() {
  const displayNode = getFocusDisplayNode();
  if (displayNode?.id != null) {
    const visibleIndex = state.rootIds.indexOf(displayNode.id);
    if (visibleIndex >= 0) {
      return visibleIndex;
    }
  }
  return clampRootIndex(state.currentRootIndex);
}

function queueFitView() {
  if (!hasGraphUi) {
    return;
  }
  requestAnimationFrame(() => fitView());
}

function queueScrollToNode(nodeId, options = {}) {
  if (!hasGraphUi) {
    return;
  }
  requestAnimationFrame(() => scrollToNode(nodeId, options));
}

function homeViewport(options = {}) {
  if (!hasGraphUi) {
    return;
  }
  const {
    preserveScale = true,
    behavior = "smooth",
  } = options;
  if (!preserveScale) {
    state.viewport.scale = DEFAULT_VIEWPORT.scale;
  }
  applyViewportTransform();
  elements.graphViewport.scrollTo({ left: 0, top: 0, behavior });
}

function queueHomeViewport(options = {}) {
  if (!hasGraphUi) {
    return;
  }
  requestAnimationFrame(() => homeViewport(options));
}

function focusRootByIndex(index, options = {}) {
  if (!state.rootIds.length) {
    return;
  }
  const {
    fit = false,
    preserveScale = true,
    behavior = "smooth",
  } = options;
  state.currentRootIndex = clampRootIndex(index);
  const rootId = getVisibleRootId();
  if (rootId != null) {
    state.activeNodeId = rootId;
  }
  render();
  if (fit) {
    queueFitView();
  } else {
    queueScrollToNode(rootId, { behavior });
  }
  savePersistedState();
}

function focusRootById(rootId, options = {}) {
  const index = state.rootIds.indexOf(rootId);
  if (index >= 0) {
    focusRootByIndex(index, options);
  }
}

async function loadInitialTree() {
  return loadGraphContext();
}

async function loadGraphContext() {
  setBusy(true);
  setStatus("Loading tree...");

  try {
    const requestedRootIds = parseRootIdsFromQuery();
    const focusNodeId = getContextNodeIdFromQuery();
    const selectedIdsFromQuery = getSelectedIdsFromQuery();
    const depth = getContextDepthFromQuery(LOAD_DEPTH);
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
        ingestSubtree(root, null, true, true);
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
    setStatus(`Loaded ${state.rootIds.length} roots.`);
    render();
    if (state.activeNodeId != null) {
      queueScrollToNode(state.activeNodeId, { behavior: "auto" });
    }
  } catch (error) {
    setStatus(`Failed to load tree: ${error.message}`);
    throw error;
  } finally {
    setBusy(false);
  }
}

async function loadNodeBranch(nodeId) {
  const node = getNode(nodeId);
  if (!node || !node.hasChildren || state.loadingNodes.has(nodeId)) {
    return;
  }

  state.loadingNodes.add(nodeId);
  setStatus(`Loading ${node.name}...`);
  render();

  try {
    const payload = await fetchJson(`/node/${nodeId}/tree?depth=${LOAD_DEPTH}`);
    ingestSubtree(payload.node, node.parentId, true);
    state.expanded.add(nodeId);
    expandAncestorChain(nodeId);
    setStatus(`Expanded ${node.name}.`);
  } finally {
    state.loadingNodes.delete(nodeId);
    render();
  }
}

function getExplorerVisibleNodes() {
  return explorerState.visibleNodeIds
    .map((nodeId) => getNode(nodeId))
    .filter(Boolean);
}

function getExplorerTitle() {
  if (!explorerState.currentNodeId) {
    return "Root";
  }
  const node = getNode(explorerState.currentNodeId);
  return node ? formatPath(node.id) : "Subtree";
}

function syncExplorerSelectionToStorage() {
  saveExplorerState();
}

function setExplorerStatus(message) {
  if (elements.explorerStatus) {
    elements.explorerStatus.textContent = message;
  }
}

function setExplorerVisibleNodes(nodeIds, options = {}) {
  const { currentNodeId = null, trailIds = [] } = options;
  explorerState.currentNodeId = currentNodeId;
  explorerState.visibleNodeIds = normalizeIdList(nodeIds);
  explorerState.trailIds = normalizeIdList(trailIds);
  syncExplorerSelectionToStorage();
  render();
}

function toggleExplorerSelection(nodeId) {
  if (explorerState.selectedNodeIds.has(nodeId)) {
    explorerState.selectedNodeIds.delete(nodeId);
  } else {
    explorerState.selectedNodeIds.add(nodeId);
  }
  syncExplorerSelectionToStorage();
  syncExplorerPathToSharedSelection();
  render();
}

function clearExplorerSelection() {
  explorerState.selectedNodeIds.clear();
  syncExplorerSelectionToStorage();
  syncExplorerPathToSharedSelection([]);
  render();
}

function buildGraphTransferUrl(rootIds, focusNodeId = null, depth = LOAD_DEPTH) {
  const params = new URLSearchParams();
  const normalizedRootIds = normalizeIdList(rootIds);
  if (Number.isInteger(focusNodeId)) {
    params.set("focus", String(focusNodeId));
  } else if (normalizedRootIds.length) {
    params.set("focus", String(normalizedRootIds[0]));
  }
  const selectedIds = normalizedRootIds;
  if (selectedIds.length) {
    params.set("selected", selectedIds.join(","));
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
    render();
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
  if (!currentNode) {
    await loadExplorerRootLevel();
    return;
  }
  if (currentNode.parentId == null) {
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

  const selectedNodes = getLeafSelectedNodeIds([...explorerState.selectedNodeIds])
    .map((nodeId) => getNode(nodeId))
    .filter(Boolean);

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
  const currentNode = explorerState.currentNodeId != null ? getNode(explorerState.currentNodeId) : null;
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

  for (const node of visibleNodes) {
    const card = document.createElement("article");
    const isSelected = explorerState.selectedNodeIds.has(node.id);
    card.className = `panel-card explorer-node-card${isSelected ? " is-selected" : ""}`;

    const childCount = (node.children || []).filter((childId) => state.nodes.has(childId)).length;

    card.innerHTML = `
      <div class="explorer-card-head">
        <h3 class="explorer-card-title">${escapeHtml(node.name)}</h3>
        <span class="meta-pill">${childCount}${childCount === 1 ? " child" : " children"}</span>
      </div>
      <div class="button-row tight explorer-card-actions-top">
        <button class="ghost-button" type="button" data-explorer-action="select">${isSelected ? "Unselect" : "Select"}</button>
        <button class="primary-button" type="button" data-explorer-action="open">View graph</button>
      </div>
      <div class="button-row tight explorer-card-actions-bottom">
        <button class="ghost-button" type="button" data-explorer-action="children" ${node.hasChildren ? "" : "disabled"}>View Children</button>
      </div>
    `;

    card.addEventListener("click", (event) => {
      if (event.target instanceof Element && event.target.closest("button")) {
        return;
      }
      toggleExplorerSelection(node.id);
    });

    card.querySelectorAll("[data-explorer-action]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.stopPropagation();
        const action = button.getAttribute("data-explorer-action");
        if (action === "select") {
          toggleExplorerSelection(node.id);
        } else if (action === "open") {
          openGraphFromExplorer(node.id);
        } else if (action === "children") {
          await loadExplorerChildren(node.id);
        }
      });
    });

    elements.explorerGrid.appendChild(card);
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
  const llmIds = [
    ...(trace.root_ids || []),
    ...(trace.candidate_topic_ids || []),
  ];
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
  render();

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
    focusRootId = trace.root_ids?.[0] ?? getRootId(trace.selected_topic_ids?.[0]);
  } catch (error) {
    console.error(error);
    if (elements.contextOutput) {
      elements.contextOutput.textContent = "Retrieval failed.";
      updateContextOutputState();
    }
    setStatus(`Retrieval failed: ${error.message}`);
  } finally {
    setBusy(false);
    render();
  }

  if (focusRootId != null) {
    focusRootById(focusRootId, {
      fit: false,
      preserveScale: true,
    });
  }
}

function toggleUserSelected(nodeId) {
  if (state.userSelected.has(nodeId)) {
    removeExplicitSelectionsInBranch(nodeId);
  } else {
    state.explicitSelected.add(nodeId);
    clearRemovedOnPath(nodeId);
    expandAncestorChain(nodeId);
  }
  recomputeUserSelection();
  syncSelectedStateToStorage();
}

function toggleUserRemoved(nodeId) {
  if (state.userRemoved.has(nodeId)) {
    state.userRemoved.delete(nodeId);
  } else {
    state.userRemoved.add(nodeId);
    removeExplicitSelectionsInBranch(nodeId);
  }
  recomputeUserSelection();
  syncSelectedStateToStorage();
}

async function toggleExpand(nodeId) {
  const node = getNode(nodeId);
  if (!node || !node.hasChildren) {
    return;
  }

  if (state.expanded.has(nodeId)) {
    state.expanded.delete(nodeId);
    render();
    return;
  }

  if (!node.childrenLoaded) {
    await loadNodeBranch(nodeId);
    return;
  }

  state.expanded.add(nodeId);
  expandAncestorChain(nodeId);
  render();
}

/* ────────── Layout computation with staggered parent lanes ────────── */

function computeLayout() {
  const positions = new Map();
  const rootPositions = new Map();
  const links = [];
  const measureCache = new Map();

  function measureSubtreeHeight(nodeId) {
    if (measureCache.has(nodeId)) {
      return measureCache.get(nodeId);
    }

    const visibleChildren = getVisibleChildIds(nodeId);
    let height = NODE_HEIGHT;

    if (visibleChildren.length) {
      let totalHeight = 0;
      visibleChildren.forEach((childId, childIndex) => {
        totalHeight += measureSubtreeHeight(childId);
        if (childIndex < visibleChildren.length - 1) {
          totalHeight += ROW_STEP;
        }
      });

      height = Math.max(NODE_HEIGHT, totalHeight);
    }

    measureCache.set(nodeId, height);
    return height;
  }

  function placeNode(nodeId, depth, topY, siblingIndex, ancestryLaneShift = 0) {
    const node = getNode(nodeId);
    if (!node) {
      return {
        top: topY,
        height: NODE_HEIGHT,
        centerY: topY + NODE_HEIGHT / 2,
      };
    }

    const visibleChildren = getVisibleChildIds(nodeId);
    const subtreeHeight = measureSubtreeHeight(nodeId);
    let nodeTop = topY + Math.max(0, (subtreeHeight - NODE_HEIGHT) / 2);

    // Alternate sibling groups left/right and accumulate this offset down the ancestry chain.
    const direction = siblingIndex % 2 === 0 ? -1 : 1;
    const laneShift = depth === 0 ? 0 : direction * PARENT_LANE_OFFSET;
    const cumulativeLaneShift = ancestryLaneShift + laneShift;
    const nodeX = depth * COLUMN_STEP + cumulativeLaneShift;

    if (visibleChildren.length) {
      const childExtents = [];
      let childTopCursor = topY;

      visibleChildren.forEach((childId, childIdx) => {
        const childHeight = measureSubtreeHeight(childId);
        const childPlacement = placeNode(childId, depth + 1, childTopCursor, childIdx, cumulativeLaneShift);

        childExtents.push({
          top: childPlacement.top,
          bottom: childPlacement.top + childPlacement.height,
          centerY: childPlacement.centerY,
        });
        links.push({ from: nodeId, to: childId });
        childTopCursor += childHeight + ROW_STEP;
      });

      if (childExtents.length) {
        const childTop = Math.min(...childExtents.map((item) => item.top));
        const childBottom = Math.max(...childExtents.map((item) => item.bottom));
        nodeTop = childTop + Math.max(0, (childBottom - childTop - NODE_HEIGHT) / 2);
      }
    }

    positions.set(nodeId, {
      x: nodeX,
      y: nodeTop,
    });

    return {
      top: topY,
      height: subtreeHeight,
      centerY: nodeTop + NODE_HEIGHT / 2,
    };
  }

  let rootTopCursor = 0;
  const visibleRootIds = state.rootIds.length ? state.rootIds : [];
  visibleRootIds.forEach((rootId, rootIndex) => {
    const placement = placeNode(rootId, 0, rootTopCursor, rootIndex, 0);
    rootPositions.set(rootId, placement.top);
    rootTopCursor = placement.top + placement.height + ROOT_SECTION_GAP;
  });

  if (!visibleRootIds.length) {
    const rootId = getVisibleRootId();
    if (rootId != null) {
      const placement = placeNode(rootId, 0, 0, 0, 0);
      rootPositions.set(rootId, placement.top);
    }
  }

  state.positions = positions;
  state.rootPositions = rootPositions;
  return links;
}

function buildLinkPath(fromPosition, toPosition) {
  const startX = fromPosition.x + NODE_WIDTH;
  const startY = fromPosition.y + NODE_HEIGHT / 2;
  const endX = toPosition.x;
  const endY = toPosition.y + NODE_HEIGHT / 2;
  const dx = endX - startX;
  // Use 0.45 of dx for a smooth, organic S-curve
  const cpOffset = dx * 0.45;
  return `M ${startX} ${startY} C ${startX + cpOffset} ${startY}, ${endX - cpOffset} ${endY}, ${endX} ${endY}`;
}

function shortenLabel(value, maxLength = 16) {
  if (!value || value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength - 1)}…`;
}

function getGraphBounds() {
  const positions = [...state.positions.values()];
  if (!positions.length) {
    return { width: 320, height: 220 };
  }

  const maxX = Math.max(...positions.map((pos) => pos.x)) + NODE_WIDTH;
  const maxY = Math.max(...positions.map((pos) => pos.y)) + NODE_HEIGHT;
  return { width: maxX, height: maxY };
}

function getViewportBounds() {
  return {
    left: elements.graphViewport.scrollLeft,
    top: elements.graphViewport.scrollTop,
    right: elements.graphViewport.scrollLeft + elements.graphViewport.clientWidth,
    bottom: elements.graphViewport.scrollTop + elements.graphViewport.clientHeight,
  };
}

function getNodeScreenRect(position) {
  const left = GRAPH_PADDING_X + position.x * state.viewport.scale;
  const top = GRAPH_PADDING_Y + position.y * state.viewport.scale;
  const width = NODE_WIDTH * state.viewport.scale;
  const height = NODE_HEIGHT * state.viewport.scale;

  return {
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
    centerY: top + height / 2,
  };
}

function isRectVisible(rect, viewportBounds) {
  return !(
    rect.left > viewportBounds.right
    || rect.right < viewportBounds.left
    || rect.top > viewportBounds.bottom
    || rect.bottom < viewportBounds.top
  );
}

function getVisibleContextNodeId() {
  const rootId = getVisibleRootId();
  if (!hasGraphUi || rootId == null) {
    return rootId;
  }

  const viewportBounds = getViewportBounds();
  const viewportCenterY = viewportBounds.top + elements.graphViewport.clientHeight / 2;
  const visibleNodes = [];

  for (const [nodeId, position] of state.positions.entries()) {
    const node = getNode(nodeId);
    if (!node) {
      continue;
    }

    const rect = getNodeScreenRect(position);
    if (!isRectVisible(rect, viewportBounds)) {
      continue;
    }

    visibleNodes.push({
      id: nodeId,
      node,
      rect,
    });
  }

  if (!visibleNodes.length) {
    return rootId;
  }

  const leadLeft = Math.min(...visibleNodes.map((item) => item.rect.left));
  const leadColumn = visibleNodes
    .filter((item) => item.rect.left <= leadLeft + 34)
    .sort((a, b) => (
      Math.abs(a.rect.centerY - viewportCenterY) - Math.abs(b.rect.centerY - viewportCenterY)
    ));

  const leadNode = leadColumn[0]?.node || visibleNodes[0].node;
  if (leadNode.parentId != null) {
    return leadNode.parentId;
  }

  return leadNode.id;
}

function getVisibleLeadNodeId() {
  const rootId = getVisibleRootId();
  if (!hasGraphUi || rootId == null) {
    return rootId;
  }

  const viewportBounds = getViewportBounds();
  const viewportCenterY = viewportBounds.top + elements.graphViewport.clientHeight / 2;
  const visibleNodes = [];

  for (const [nodeId, position] of state.positions.entries()) {
    const node = getNode(nodeId);
    if (!node) {
      continue;
    }

    const rect = getNodeScreenRect(position);
    if (!isRectVisible(rect, viewportBounds)) {
      continue;
    }

    visibleNodes.push({
      id: nodeId,
      node,
      rect,
    });
  }

  if (!visibleNodes.length) {
    return rootId;
  }

  const leadLeft = Math.min(...visibleNodes.map((item) => item.rect.left));
  const leadColumn = visibleNodes
    .filter((item) => item.rect.left <= leadLeft + 34)
    .sort((a, b) => (
      Math.abs(a.rect.centerY - viewportCenterY) - Math.abs(b.rect.centerY - viewportCenterY)
    ));

  return leadColumn[0]?.node?.id || visibleNodes[0].node.id;
}

function getFocusDisplayNode() {
  const leadNodeId = getVisibleLeadNodeId();
  const leadNode = getNode(leadNodeId);
  if (!leadNode) {
    return null;
  }

  if (leadNode.hasChildren) {
    return leadNode;
  }

  return getNode(leadNode.parentId) || leadNode;
}

function getStickyPathNodeIds() {
  const displayNode = getFocusDisplayNode();
  if (!displayNode) {
    return [];
  }

  return [displayNode.id];
}

function computeStickyNodePlacements() {
  if (!hasGraphUi || !elements.stickyNodeLayer) {
    return [];
  }

  const viewportBounds = getViewportBounds();
  const viewportWidth = elements.graphViewport.clientWidth;
  const viewportHeight = elements.graphViewport.clientHeight;
  const viewportCenterY = viewportBounds.top + viewportHeight / 2;

  if (!viewportWidth || !viewportHeight) {
    return [];
  }

  const maxLeft = Math.max(12, viewportWidth - NODE_WIDTH - 12);

  return getStickyPathNodeIds()
    .map((nodeId) => {
      const node = getNode(nodeId);
      const position = state.positions.get(nodeId);
      if (!node || !position) {
        return null;
      }

      const rect = getNodeScreenRect(position);
      const shouldPin = !isRectVisible(rect, viewportBounds)
        || Math.abs(rect.centerY - viewportCenterY) > STICKY_CENTER_SNAP;

      if (!shouldPin) {
        return null;
      }

      const viewportLeft = GRAPH_PADDING_X + position.x * state.viewport.scale - elements.graphViewport.scrollLeft;
      return {
        id: nodeId,
        left: Math.max(12, Math.min(maxLeft, viewportLeft)),
        top: viewportHeight / 2,
      };
    })
    .filter(Boolean);
}

function syncGraphCanvasSize() {
  if (!hasGraphUi) {
    return;
  }
  const bounds = getGraphBounds();
  const width = Math.max(
    elements.graphViewport.clientWidth || 1000,
    Math.ceil(bounds.width * state.viewport.scale + GRAPH_PADDING_X * 2)
  );
  const height = Math.max(
    elements.graphViewport.clientHeight || 700,
    Math.ceil(bounds.height * state.viewport.scale + GRAPH_PADDING_Y * 2)
  );
  elements.graphCanvas.setAttribute("width", String(width));
  elements.graphCanvas.setAttribute("height", String(height));
  elements.graphCanvas.setAttribute("viewBox", `0 0 ${width} ${height}`);
  elements.graphCanvas.style.width = `${width}px`;
  elements.graphCanvas.style.height = `${height}px`;
}

function applyViewportTransform() {
  if (!hasGraphUi) {
    return;
  }
  syncGraphCanvasSize();
  elements.graphScene.setAttribute(
    "transform",
    `translate(${GRAPH_PADDING_X} ${GRAPH_PADDING_Y}) scale(${state.viewport.scale})`
  );
}

function adjustZoom(factor) {
  if (!hasGraphUi) {
    return;
  }
  const previousScale = state.viewport.scale;
  const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, state.viewport.scale * factor));
  if (nextScale === previousScale) {
    return;
  }

  state.viewport.scale = nextScale;
  applyViewportTransform();
  const scaleRatio = nextScale / previousScale;
  elements.graphViewport.scrollLeft *= scaleRatio;
  elements.graphViewport.scrollTop *= scaleRatio;
}

function fitView() {
  if (!hasGraphUi) {
    return;
  }
  syncGraphCanvasSize();

  const bounds = getGraphBounds();
  const rect = elements.graphViewport.getBoundingClientRect();
  if (!bounds.width || !bounds.height || !rect.width || !rect.height) {
    return;
  }

  const horizontalPadding = GRAPH_PADDING_X + 48;
  const verticalPadding = GRAPH_PADDING_Y + 36;
  const scale = Math.max(
    MIN_SCALE,
    Math.min(
      1,
      (rect.width - horizontalPadding) / bounds.width,
      (rect.height - verticalPadding) / bounds.height
    )
  );

  state.viewport.scale = Math.min(scale, MAX_SCALE);
  applyViewportTransform();
  elements.graphViewport.scrollTo({ left: 0, top: 0, behavior: "smooth" });
}

function resetView() {
  state.viewport = { ...DEFAULT_VIEWPORT };
  applyViewportTransform();
  if (elements.graphViewport) {
    elements.graphViewport.scrollTo({ left: 0, top: 0, behavior: "smooth" });
  }
}

/* ────────── Scroll to a node position ────────── */

function scrollToNode(nodeId, options = {}) {
  if (!hasGraphUi) {
    return;
  }
  const position = state.positions.get(nodeId);
  if (!position) {
    return;
  }
  const screenRect = getNodeScreenRect(position);
  const vpRect = elements.graphViewport.getBoundingClientRect();
  const targetLeft = screenRect.left - vpRect.width / 2 + screenRect.width / 2;
  const targetTop = screenRect.top - vpRect.height / 2 + screenRect.height / 2;
  const { behavior = "smooth" } = options;
  elements.graphViewport.scrollTo({
    left: Math.max(0, targetLeft),
    top: Math.max(0, targetTop),
    behavior,
  });
}

/* ────────── Rendering ────────── */

function renderGraph() {
  if (!hasGraphUi) {
    return;
  }

  const scene = elements.graphScene;
  scene.replaceChildren();

  if (!state.rootIds.length) {
    syncGraphCanvasSize();
    applyViewportTransform();
    return;
  }

  const links = computeLayout();
  const highlightedPathIds = new Set([...state.userSelected, ...state.finalSelected]);
  for (const topicId of state.finalSelected) {
    for (const ancestorId of getAncestorIds(topicId)) {
      highlightedPathIds.add(ancestorId);
    }
  }

  // Create SVG defs for link gradient
  const defs = svgEl("defs");
  const gradient = svgEl("linearGradient", { id: "linkGrad", x1: "0%", y1: "0%", x2: "100%", y2: "0%" });
  gradient.appendChild(svgEl("stop", { offset: "0%", "stop-color": "rgba(23,107,135,0.45)" }));
  gradient.appendChild(svgEl("stop", { offset: "100%", "stop-color": "rgba(23,107,135,0.18)" }));
  defs.appendChild(gradient);
  scene.appendChild(defs);

  // Render all links (no aggressive culling — let SVG handle it)
  for (const link of links) {
    const fromPosition = state.positions.get(link.from);
    const toPosition = state.positions.get(link.to);
    if (!fromPosition || !toPosition) {
      continue;
    }

    const pathD = buildLinkPath(fromPosition, toPosition);
    const selectedPath = highlightedPathIds.has(link.from)
      && highlightedPathIds.has(link.to);

    // Glow layer
    scene.appendChild(svgEl("path", {
      class: `graph-link-glow${selectedPath ? " graph-link-selected-glow" : ""}`,
      d: pathD,
    }));

    // Main link
    scene.appendChild(svgEl("path", {
      class: `graph-link${selectedPath ? " graph-link-selected" : ""}`,
      d: pathD,
      stroke: "url(#linkGrad)",
    }));
  }

  const orderedNodes = [...state.positions.keys()].sort((a, b) => {
    const posA = state.positions.get(a);
    const posB = state.positions.get(b);
    if (!posA || !posB) {
      return 0;
    }
    if (posA.y !== posB.y) {
      return posA.y - posB.y;
    }
    return posA.x - posB.x;
  });

  for (const nodeId of orderedNodes) {
    const node = getNode(nodeId);
    const position = state.positions.get(nodeId);
    if (!node || !position) {
      continue;
    }

    const isPathSelected = highlightedPathIds.has(nodeId);
    const isPinnedFocus = state.activeNodeId === nodeId;

    const nodeGroup = svgEl("g", {
      class: [
        "node-group",
        isPinnedFocus ? "is-active" : "",
        isPathSelected ? "is-path-selected" : "",
      ].filter(Boolean).join(" "),
      transform: `translate(${position.x} ${position.y})`,
    });

    const tooltip = svgEl("title");
    tooltip.textContent = formatPath(nodeId);
    nodeGroup.appendChild(tooltip);

    const color = getNodeColor(nodeId);
    nodeGroup.appendChild(svgEl("rect", {
      class: "node-halo",
      x: -4,
      y: -4,
      rx: 12,
      ry: 12,
      width: NODE_WIDTH + 8,
      height: NODE_HEIGHT + 8,
      fill: color,
    }));
    nodeGroup.appendChild(svgEl("rect", {
      class: "node-card",
      x: 0,
      y: 0,
      rx: 11,
      ry: 11,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    }));
    nodeGroup.appendChild(svgEl("circle", {
      class: "node-dot",
      cx: 14,
      cy: 20,
      r: NODE_RADIUS,
      fill: color,
    }));

    const title = svgEl("text", {
      class: "node-label",
      x: 28,
      y: 21,
    });
    title.textContent = shortenLabel(node.name);
    nodeGroup.appendChild(title);

    if (node.hasChildren) {
      const expander = svgEl("g", {
        class: "node-expander",
        transform: `translate(${NODE_WIDTH - 12} 9)`,
      });
      expander.appendChild(svgEl("circle", {
        class: "node-expander-bg",
        cx: 0,
        cy: 0,
        r: 9,
      }));
      const expanderText = svgEl("text", {
        class: "node-expander-text",
        x: 0,
        y: 0,
      });
      if (state.loadingNodes.has(nodeId)) {
        expanderText.textContent = "…";
      } else if (state.expanded.has(nodeId)) {
        expanderText.textContent = "−";
      } else {
        expanderText.textContent = "+";
      }
      expander.appendChild(expanderText);
      expander.addEventListener("click", async (event) => {
        event.stopPropagation();
        await toggleExpand(nodeId);
      });
      nodeGroup.appendChild(expander);
    }

    nodeGroup.addEventListener("click", async (event) => {
      state.activeNodeId = nodeId;
      if (event.shiftKey) {
        toggleUserRemoved(nodeId);
      } else {
        toggleUserSelected(nodeId);
        if (node.hasChildren && !state.expanded.has(nodeId)) {
          if (node.childrenLoaded) {
            state.expanded.add(nodeId);
          } else {
            await loadNodeBranch(nodeId);
          }
        }
      }
      render();
    });

    nodeGroup.addEventListener("dblclick", async (event) => {
      event.stopPropagation();
      state.activeNodeId = nodeId;
      await toggleExpand(nodeId);
    });

    scene.appendChild(nodeGroup);
  }

  syncGraphCanvasSize();
  applyViewportTransform();
}

function renderFocusNodeBanner() {
  if (!elements.focusNodeBanner) {
    return;
  }

  const displayNode = getFocusDisplayNode();
  if (!displayNode) {
    elements.focusNodeBanner.textContent = "No focused node";
    elements.focusNodeBanner.onclick = null;
    return;
  }

  const fullPath = formatPath(displayNode.id);
  const color = getNodeColor(displayNode.id);
  elements.focusNodeBanner.className = "graph-parent-banner pinned-node-card";
  elements.focusNodeBanner.style.setProperty("--pinned-node-color", color);
  elements.focusNodeBanner.innerHTML = `
    <span class="pinned-node-dot"></span>
    <span class="pinned-node-copy">
      <span class="pinned-node-title">${escapeHtml(displayNode.name)}</span>
      <span class="pinned-node-path">${escapeHtml(fullPath || displayNode.name)}</span>
    </span>
    <span class="pinned-node-meta">L${displayNode.level}</span>
  `;
  elements.focusNodeBanner.title = `Click to scroll to: ${displayNode.name}`;

  // Click to scroll to the focused node
  elements.focusNodeBanner.onclick = () => {
    scrollToNode(displayNode.id);
    state.activeNodeId = displayNode.id;
    render();
  };
}

function renderStickyNodes() {
  if (!elements.stickyNodeLayer) {
    return;
  }

  elements.stickyNodeLayer.replaceChildren();
}

function renderRootPager() {
  if (!elements.rootPagerTitle || !elements.rootPagerLabel || !elements.prevRootButton || !elements.nextRootButton) {
    return;
  }

  if (!state.rootIds.length) {
    elements.rootPagerTitle.textContent = "No roots";
    elements.rootPagerLabel.textContent = "No roots";
    elements.prevRootButton.disabled = true;
    elements.nextRootButton.disabled = true;
    return;
  }

  const uiRootIndex = getUiRootIndex();
  const rootId = state.rootIds[uiRootIndex] || getVisibleRootId();
  const root = getNode(rootId);
  elements.rootPagerTitle.textContent = root?.name ? shortenLabel(root.name, 24) : "Root";
  elements.rootPagerLabel.textContent = `${uiRootIndex + 1} / ${state.rootIds.length}`;
  elements.rootPagerTitle.title = root ? formatPath(root.id) : "Unknown";
  elements.rootPagerLabel.title = root ? formatPath(root.id) : "Unknown";
  elements.prevRootButton.disabled = uiRootIndex <= 0;
  elements.nextRootButton.disabled = uiRootIndex >= state.rootIds.length - 1;
}

function renderRootConstraints() {
  if (!elements.rootConstraintList) {
    return;
  }

  const selectedNodesByLevel = getSelectedNodesByLevel();
  const displayNodeIds = getSelectedPathNodeIds(selectedNodesByLevel);
  const totalSelected = displayNodeIds.length;

  if (!totalSelected) {
    elements.rootConstraintList.className = "path-chain-list empty-state";
    elements.rootConstraintList.textContent = "No path selected.";
    return;
  }

  elements.rootConstraintList.className = "path-chain-list";
  elements.rootConstraintList.replaceChildren();

  for (const nodeId of displayNodeIds) {
    const item = document.createElement("div");
    item.className = "path-chain-item";
    item.textContent = formatPath(nodeId);
    item.title = formatPathChain(nodeId);
    elements.rootConstraintList.appendChild(item);
  }
}

function renderGraphSelectedPath() {
  if (!elements.graphSelectedPathList) {
    return;
  }

  const selectedNodesByLevel = getSelectedNodesByLevel();
  const displayNodeIds = getSelectedPathNodeIds(selectedNodesByLevel);

  if (elements.graphSelectedPathCount) {
    elements.graphSelectedPathCount.textContent = `${displayNodeIds.length} selected`;
  }

  if (!displayNodeIds.length) {
    elements.graphSelectedPathList.className = "path-chain-list empty-state";
    elements.graphSelectedPathList.textContent = "No path selected.";
    return;
  }

  elements.graphSelectedPathList.className = "path-chain-list";
  elements.graphSelectedPathList.replaceChildren();

  for (const nodeId of displayNodeIds) {
    const item = document.createElement("div");
    item.className = "path-chain-item";
    item.textContent = formatPath(nodeId);
    item.title = formatPathChain(nodeId);
    elements.graphSelectedPathList.appendChild(item);
  }
}

function renderInspector() {
  if (!elements.nodeInspector) {
    return;
  }

  const node = getNode(state.activeNodeId);
  if (!node) {
    elements.nodeInspector.className = "inspector-body empty-state";
    elements.nodeInspector.textContent = "Select a node.";
    return;
  }

  const description = (node.description || "").trim();
  elements.nodeInspector.className = "inspector-body";
  elements.nodeInspector.innerHTML = `
    <div>
      <h3 class="inspector-title">${escapeHtml(node.name)}</h3>
      <p class="inspector-path">${escapeHtml(formatPath(node.id) || node.name)}</p>
    </div>
    <div class="inspector-meta">
      <span class="meta-pill">L${node.level}</span>
      <span class="meta-pill">${node.hasChildren ? "branch" : "leaf"}</span>
    </div>
    ${description ? `<p class="inspector-copy">${escapeHtml(description)}</p>` : ""}
    <div class="button-row">
      <button class="primary-button" type="button" data-inspector-action="select">${state.userSelected.has(node.id) ? "Deselect" : "Select"}</button>
      <button class="ghost-button" type="button" data-inspector-action="remove">${state.userRemoved.has(node.id) ? "Undo Remove" : "Remove"}</button>
      <button class="ghost-button" type="button" data-inspector-action="expand" ${node.hasChildren ? "" : "disabled"}>${node.hasChildren ? "Expand" : "No Children"}</button>
    </div>
  `;

  elements.nodeInspector.querySelectorAll("[data-inspector-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.getAttribute("data-inspector-action");
      if (action === "select") {
        toggleUserSelected(node.id);
        render();
      } else if (action === "remove") {
        toggleUserRemoved(node.id);
        render();
      } else if (action === "expand") {
        await toggleExpand(node.id);
      }
    });
  });
}

function renderTraceSummary() {
  if (!elements.traceSummary) {
    return;
  }

  const trace = state.retrievalTrace;
  if (!trace) {
    elements.traceSummary.className = "trace-summary empty-state";
    elements.traceSummary.textContent = "Query result will appear here.";
    return;
  }

  const selectedByLevel = trace.selected_nodes_by_level || {};
  const selectedPathIds = getSelectedPathNodeIds(selectedByLevel);
  const selectedTopics = (trace.selected_topics || []).map((item) => item.chain?.join(" -> ") || `Topic ${item.id}`);

  const pills = [];
  if (selectedPathIds.length) {
    pills.push(`Path: ${selectedPathIds.map((nodeId) => formatPath(nodeId)).join(" || ")}`);
  }
  if (selectedTopics.length) {
    pills.push(`Final: ${selectedTopics.join(" | ")}`);
  }

  if (!pills.length) {
    elements.traceSummary.className = "trace-summary empty-state";
    elements.traceSummary.textContent = "No trace details.";
    return;
  }

  elements.traceSummary.className = "trace-summary";
  elements.traceSummary.replaceChildren();
  for (const label of pills) {
    const pill = document.createElement("span");
    pill.className = "trace-pill";
    pill.textContent = label;
    elements.traceSummary.appendChild(pill);
  }
}

function render() {
  if (hasExplorerUi) {
    renderExplorerBreadcrumbs();
    renderExplorerSelection();
    renderExplorerGrid();
  }
  renderRootPager();
  renderGraph();
  renderStickyNodes();
  renderFocusNodeBanner();
  renderRootConstraints();
  renderGraphSelectedPath();
  renderInspector();
  renderTraceSummary();
}

/* ────────── Pointer-based pan (single finger / mouse drag) ────────── */

function installViewportInteraction() {
  if (!hasGraphUi) {
    return;
  }

  const vp = elements.graphViewport;

  // --- Ctrl+Wheel zoom ---
  vp.addEventListener("wheel", (e) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.08 : 0.92;
      adjustZoom(factor);
      renderStickyNodes();
      renderFocusNodeBanner();
      return;
    }

    if (state.rootIds.length > 1) {
      const nearTop = vp.scrollTop <= 18;
      const nearBottom = vp.scrollTop + vp.clientHeight >= vp.scrollHeight - 18;
      if ((e.deltaY < 0 && nearTop) || (e.deltaY > 0 && nearBottom)) {
        e.preventDefault();
        const nextIndex = getUiRootIndex() + (e.deltaY > 0 ? 1 : -1);
        focusRootByIndex(nextIndex, {
          fit: false,
          preserveScale: true,
          behavior: "smooth",
        });
      }
    }
  }, { passive: false });

  // --- Single-finger / mouse drag panning ---
  let panState = null;

  function isNodeTarget(el) {
    while (el && el !== vp) {
      if (el.classList && (el.classList.contains("node-group") || el.classList.contains("node-expander"))) {
        return true;
      }
      el = el.parentElement;
    }
    return false;
  }

  function canStartPanning(target) {
    if (!target || !(target instanceof Element) || isNodeTarget(target)) {
      return false;
    }
    if (target === vp || target === elements.graphCanvas || target === elements.graphScene) {
      return true;
    }
    return Boolean(target.closest("#graphCanvas"));
  }

  vp.addEventListener("pointerdown", (e) => {
    // Only primary button on viewport/canvas background (not nodes).
    if (e.button !== 0 || !canStartPanning(e.target)) {
      return;
    }
    panState = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      scrollLeft: vp.scrollLeft,
      scrollTop: vp.scrollTop,
    };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add("is-panning");
    e.preventDefault();
  });

  vp.addEventListener("pointermove", (e) => {
    if (!panState || e.pointerId !== panState.pointerId) {
      return;
    }
    const dx = e.clientX - panState.startX;
    const dy = e.clientY - panState.startY;
    vp.scrollLeft = panState.scrollLeft - dx;
    vp.scrollTop = panState.scrollTop - dy;
  });

  vp.addEventListener("pointerup", (e) => {
    if (!panState || e.pointerId !== panState.pointerId) {
      return;
    }
    vp.releasePointerCapture(e.pointerId);
    vp.classList.remove("is-panning");
    panState = null;
  });

  vp.addEventListener("pointercancel", (e) => {
    if (!panState || e.pointerId !== panState.pointerId) {
      return;
    }
    vp.classList.remove("is-panning");
    panState = null;
  });

  // --- Update banner on scroll ---
  vp.addEventListener("scroll", () => {
    renderRootPager();
    renderFocusNodeBanner();
    renderStickyNodes();
  }, { passive: true });
}

function installControls() {
  if (elements.runQueryButton) {
    elements.runQueryButton.addEventListener("click", runRetrieval);
  }

  if (elements.clearStatesButton) {
    elements.clearStatesButton.addEventListener("click", () => {
      clearAllStates();
      setStatus("Cleared.");
    });
  }

  if (elements.prevRootButton) {
    elements.prevRootButton.addEventListener("click", () => focusRootByIndex(getUiRootIndex() - 1, {
      fit: false,
      preserveScale: true,
    }));
  }

  if (elements.nextRootButton) {
    elements.nextRootButton.addEventListener("click", () => focusRootByIndex(getUiRootIndex() + 1, {
      fit: false,
      preserveScale: true,
    }));
  }

  if (elements.zoomOutButton) {
    elements.zoomOutButton.addEventListener("click", () => adjustZoom(0.86));
  }

  if (elements.zoomInButton) {
    elements.zoomInButton.addEventListener("click", () => adjustZoom(1.16));
  }

  if (elements.fitViewButton) {
    elements.fitViewButton.addEventListener("click", fitView);
  }

  if (elements.resetViewButton) {
    elements.resetViewButton.addEventListener("click", resetView);
  }

  if (elements.clearExplorerSelectionButton) {
    elements.clearExplorerSelectionButton.addEventListener("click", clearExplorerSelection);
  }

  if (elements.openSelectedGraphButton) {
    elements.openSelectedGraphButton.addEventListener("click", () => openGraphFromExplorer());
  }

  if (elements.explorerBackLevelButton) {
    elements.explorerBackLevelButton.addEventListener("click", loadExplorerParentLevel);
  }

  window.addEventListener("resize", () => {
    syncGraphCanvasSize();
    applyViewportTransform();
    renderFocusNodeBanner();
    renderStickyNodes();
  });
}

async function initialize() {
  updateContextOutputState();
  loadPersistedState();
  loadExplorerState();
  installViewportInteraction();
  installControls();
  try {
    if (hasExplorerUi) {
      await loadExplorerRootLevel();
    } else {
      await loadGraphContext();
    }
  } catch (error) {
    console.error(error);
    setBusy(false);
    setStatus(`Failed to load tree: ${error.message}`);
  }
}

initialize();
