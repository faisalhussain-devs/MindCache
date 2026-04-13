(function setupMindCacheGraph(globalScope) {
  const app = globalScope.MindCache;
  const {
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
    STICKY_CENTER_SNAP,
    PARENT_LANE_OFFSET,
    ROOT_SECTION_GAP,
    state,
    elements,
    hasGraphUi,
    svgEl,
    escapeHtml,
    getNode,
    getNodeColor,
    getVisibleChildIds,
    formatPath,
    formatPathChain,
    getAncestorIds,
    getSelectedPathNodeIds,
    getSelectedNodesByLevel,
    savePersistedState,
    expandAncestorChain,
    clearRemovedOnPath,
    removeExplicitSelectionsInBranch,
    recomputeUserSelection,
    syncSelectedStateToStorage,
  } = app;

  function getUiRootIndex() {
    const displayNode = getFocusDisplayNode();
    if (displayNode?.id != null) {
      const visibleIndex = state.rootIds.indexOf(displayNode.id);
      if (visibleIndex >= 0) {
        return visibleIndex;
      }
    }
    return app.clampRootIndex(state.currentRootIndex);
  }

  function queueFitView() {
    if (hasGraphUi) {
      requestAnimationFrame(() => fitView());
    }
  }

  function queueScrollToNode(nodeId, options = {}) {
    if (hasGraphUi) {
      requestAnimationFrame(() => scrollToNode(nodeId, options));
    }
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
    const { fit = false, preserveScale = true, behavior = "smooth" } = options;
    state.currentRootIndex = app.clampRootIndex(index);
    const rootId = app.getVisibleRootId();
    if (rootId != null) {
      state.activeNodeId = rootId;
    }
    globalScope.MindCache.render();
    if (fit) {
      queueFitView();
    } else {
      queueScrollToNode(rootId, { behavior, preserveScale });
    }
    savePersistedState();
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
      globalScope.MindCache.render();
      return;
    }
    if (!node.childrenLoaded) {
      await app.loadNodeBranch(nodeId);
      return;
    }
    state.expanded.add(nodeId);
    expandAncestorChain(nodeId);
    globalScope.MindCache.render();
  }

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
        return { top: topY, height: NODE_HEIGHT, centerY: topY + NODE_HEIGHT / 2 };
      }

      const visibleChildren = getVisibleChildIds(nodeId);
      const subtreeHeight = measureSubtreeHeight(nodeId);
      let nodeTop = topY + Math.max(0, (subtreeHeight - NODE_HEIGHT) / 2);
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

      positions.set(nodeId, { x: nodeX, y: nodeTop });
      return { top: topY, height: subtreeHeight, centerY: nodeTop + NODE_HEIGHT / 2 };
    }

    let rootTopCursor = 0;
    (state.rootIds.length ? state.rootIds : []).forEach((rootId, rootIndex) => {
      const placement = placeNode(rootId, 0, rootTopCursor, rootIndex, 0);
      rootPositions.set(rootId, placement.top);
      rootTopCursor = placement.top + placement.height + ROOT_SECTION_GAP;
    });

    if (!state.rootIds.length) {
      const rootId = app.getVisibleRootId();
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
    const cpOffset = dx * 0.45;
    return `M ${startX} ${startY} C ${startX + cpOffset} ${startY}, ${endX - cpOffset} ${endY}, ${endX} ${endY}`;
  }

  function shortenLabel(value, maxLength = 16) {
    if (!value || value.length <= maxLength) {
      return value;
    }
    return `${value.slice(0, maxLength - 1)}...`;
  }

  function getGraphBounds() {
    const positions = [...state.positions.values()];
    if (!positions.length) {
      return { width: 320, height: 220 };
    }
    return {
      width: Math.max(...positions.map((pos) => pos.x)) + NODE_WIDTH,
      height: Math.max(...positions.map((pos) => pos.y)) + NODE_HEIGHT,
    };
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
    return !(rect.left > viewportBounds.right || rect.right < viewportBounds.left || rect.top > viewportBounds.bottom || rect.bottom < viewportBounds.top);
  }

  function getVisibleContextNodeId() {
    const rootId = app.getVisibleRootId();
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
      visibleNodes.push({ id: nodeId, node, rect });
    }
    if (!visibleNodes.length) {
      return rootId;
    }
    const leadLeft = Math.min(...visibleNodes.map((item) => item.rect.left));
    const leadColumn = visibleNodes
      .filter((item) => item.rect.left <= leadLeft + 34)
      .sort((a, b) => Math.abs(a.rect.centerY - viewportCenterY) - Math.abs(b.rect.centerY - viewportCenterY));
    const leadNode = leadColumn[0]?.node || visibleNodes[0].node;
    if (leadNode.parentId != null) {
      return leadNode.parentId;
    }
    return leadNode.id;
  }

  function getVisibleLeadNodeId() {
    const rootId = app.getVisibleRootId();
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
      visibleNodes.push({ node, rect });
    }
    if (!visibleNodes.length) {
      return rootId;
    }
    const leadLeft = Math.min(...visibleNodes.map((item) => item.rect.left));
    const leadColumn = visibleNodes
      .filter((item) => item.rect.left <= leadLeft + 34)
      .sort((a, b) => Math.abs(a.rect.centerY - viewportCenterY) - Math.abs(b.rect.centerY - viewportCenterY));
    return leadColumn[0]?.node?.id || visibleNodes[0].node.id;
  }

  function getFocusDisplayNode() {
    const leadNodeId = getVisibleLeadNodeId();
    const leadNode = getNode(leadNodeId);
    if (!leadNode) {
      return null;
    }
    return leadNode.hasChildren ? leadNode : (getNode(leadNode.parentId) || leadNode);
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
    const width = Math.max(elements.graphViewport.clientWidth || 1000, Math.ceil(bounds.width * state.viewport.scale + GRAPH_PADDING_X * 2));
    const height = Math.max(elements.graphViewport.clientHeight || 700, Math.ceil(bounds.height * state.viewport.scale + GRAPH_PADDING_Y * 2));
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
    elements.graphScene.setAttribute("transform", `translate(${GRAPH_PADDING_X} ${GRAPH_PADDING_Y}) scale(${state.viewport.scale})`);
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
    const scale = Math.max(MIN_SCALE, Math.min(1, (rect.width - horizontalPadding) / bounds.width, (rect.height - verticalPadding) / bounds.height));
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
    elements.graphViewport.scrollTo({
      left: Math.max(0, targetLeft),
      top: Math.max(0, targetTop),
      behavior: options.behavior || "smooth",
    });
  }

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

    const defs = svgEl("defs");
    const gradient = svgEl("linearGradient", { id: "linkGrad", x1: "0%", y1: "0%", x2: "100%", y2: "0%" });
    gradient.appendChild(svgEl("stop", { offset: "0%", "stop-color": "rgba(23,107,135,0.45)" }));
    gradient.appendChild(svgEl("stop", { offset: "100%", "stop-color": "rgba(23,107,135,0.18)" }));
    defs.appendChild(gradient);
    scene.appendChild(defs);

    for (const link of links) {
      const fromPosition = state.positions.get(link.from);
      const toPosition = state.positions.get(link.to);
      if (!fromPosition || !toPosition) {
        continue;
      }
      const pathD = buildLinkPath(fromPosition, toPosition);
      const selectedPath = highlightedPathIds.has(link.from) && highlightedPathIds.has(link.to);
      scene.appendChild(svgEl("path", { class: `graph-link-glow${selectedPath ? " graph-link-selected-glow" : ""}`, d: pathD }));
      scene.appendChild(svgEl("path", { class: `graph-link${selectedPath ? " graph-link-selected" : ""}`, d: pathD, stroke: "url(#linkGrad)" }));
    }

    const orderedNodes = [...state.positions.keys()].sort((a, b) => {
      const posA = state.positions.get(a);
      const posB = state.positions.get(b);
      if (!posA || !posB) {
        return 0;
      }
      return posA.y !== posB.y ? posA.y - posB.y : posA.x - posB.x;
    });

    for (const nodeId of orderedNodes) {
      const node = getNode(nodeId);
      const position = state.positions.get(nodeId);
      if (!node || !position) {
        continue;
      }
      const isPathSelected = highlightedPathIds.has(nodeId);
      const isPinnedFocus = state.activeNodeId === nodeId;
      const isRoot = state.rootIds.includes(nodeId);
      const nodeGroup = svgEl("g", {
        class: [
          "node-group",
          isRoot ? "is-root" : "",
          isPinnedFocus ? "is-active" : "",
          isPathSelected ? "is-path-selected" : "",
          node.hasChildren && !node.childrenLoaded ? "is-unhydrated" : "",
        ].filter(Boolean).join(" "),
        transform: `translate(${position.x} ${position.y})`,
      });

      const tooltip = svgEl("title");
      tooltip.textContent = formatPath(nodeId);
      nodeGroup.appendChild(tooltip);

      const color = getNodeColor(nodeId);
      nodeGroup.appendChild(svgEl("rect", { class: "node-halo", x: -4, y: -4, rx: 12, ry: 12, width: NODE_WIDTH + 8, height: NODE_HEIGHT + 8, fill: color }));
      nodeGroup.appendChild(svgEl("rect", { class: "node-card", x: 0, y: 0, rx: 11, ry: 11, width: NODE_WIDTH, height: NODE_HEIGHT }));
      nodeGroup.appendChild(svgEl("circle", { class: "node-dot", cx: 14, cy: 20, r: NODE_RADIUS, fill: color }));
      const title = svgEl("text", { class: "node-label", x: 28, y: 21 });
      title.textContent = shortenLabel(node.name);
      nodeGroup.appendChild(title);

      if (node.hasChildren) {
        const expander = svgEl("g", { class: "node-expander", transform: `translate(${NODE_WIDTH - 12} 9)` });
        expander.appendChild(svgEl("circle", { class: "node-expander-bg", cx: 0, cy: 0, r: 9 }));
        const expanderText = svgEl("text", { class: "node-expander-text", x: 0, y: 0 });
        expanderText.textContent = state.loadingNodes.has(nodeId) ? "..." : (state.expanded.has(nodeId) ? "-" : "+");
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
        }
        globalScope.MindCache.render();
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
    elements.focusNodeBanner.className = "graph-parent-banner pinned-node-card";
    elements.focusNodeBanner.style.setProperty("--pinned-node-color", getNodeColor(displayNode.id));
    elements.focusNodeBanner.innerHTML = `
      <span class="pinned-node-dot"></span>
      <span class="pinned-node-copy">
        <span class="pinned-node-title">${escapeHtml(displayNode.name)}</span>
        <span class="pinned-node-path">${escapeHtml(formatPath(displayNode.id) || displayNode.name)}</span>
      </span>
      <span class="pinned-node-meta">L${displayNode.level}</span>
    `;
    elements.focusNodeBanner.title = `Click to scroll to: ${displayNode.name}`;
    elements.focusNodeBanner.onclick = () => {
      scrollToNode(displayNode.id);
      state.activeNodeId = displayNode.id;
      globalScope.MindCache.render();
    };
  }

  function renderStickyNodes() {
    if (elements.stickyNodeLayer) {
      elements.stickyNodeLayer.replaceChildren();
    }
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
    const rootId = state.rootIds[uiRootIndex] || app.getVisibleRootId();
    const root = getNode(rootId);
    elements.rootPagerTitle.textContent = root?.name ? shortenLabel(root.name, 24) : "Root";
    elements.rootPagerLabel.textContent = `${uiRootIndex + 1} / ${state.rootIds.length}`;
    elements.rootPagerTitle.title = root ? formatPath(root.id) : "Unknown";
    elements.rootPagerLabel.title = root ? formatPath(root.id) : "Unknown";
    elements.prevRootButton.disabled = uiRootIndex <= 0;
    elements.nextRootButton.disabled = uiRootIndex >= state.rootIds.length - 1;
  }

  function renderPathList(target, nodeIds, emptyMessage) {
    if (!target) {
      return;
    }
    if (!nodeIds.length) {
      target.className = "path-chain-list empty-state";
      target.textContent = emptyMessage;
      return;
    }
    target.className = "path-chain-list";
    target.replaceChildren();
    for (const nodeId of nodeIds) {
      const item = document.createElement("div");
      item.className = "path-chain-item";
      item.textContent = formatPath(nodeId);
      item.title = formatPathChain(nodeId);
      target.appendChild(item);
    }
  }

  function renderRootConstraints() {
    const displayNodeIds = getSelectedPathNodeIds(getSelectedNodesByLevel());
    renderPathList(elements.rootConstraintList, displayNodeIds, "No path selected.");
  }

  function renderGraphSelectedPath() {
    const displayNodeIds = getSelectedPathNodeIds(getSelectedNodesByLevel());
    if (elements.graphSelectedPathCount) {
      elements.graphSelectedPathCount.textContent = `${displayNodeIds.length} selected`;
    }
    renderPathList(elements.graphSelectedPathList, displayNodeIds, "No path selected.");
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
    const expandLabel = !node.hasChildren
      ? "No Children"
      : state.loadingNodes.has(node.id)
        ? "Loading..."
        : (!node.childrenLoaded
          ? "Reveal Branch"
          : (state.expanded.has(node.id) ? "Collapse" : "Expand"));
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
        <button class="ghost-button" type="button" data-inspector-action="expand" ${node.hasChildren && !state.loadingNodes.has(node.id) ? "" : "disabled"}>${expandLabel}</button>
      </div>
    `;
    elements.nodeInspector.querySelectorAll("[data-inspector-action]").forEach((button) => {
      button.addEventListener("click", async () => {
        const action = button.getAttribute("data-inspector-action");
        if (action === "select") {
          toggleUserSelected(node.id);
          globalScope.MindCache.render();
        } else if (action === "remove") {
          toggleUserRemoved(node.id);
          globalScope.MindCache.render();
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

    const selectedPathIds = getSelectedPathNodeIds(trace.selected_nodes_by_level || {});
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

  function installViewportInteraction() {
    if (!hasGraphUi) {
      return;
    }
    const viewport = elements.graphViewport;
    viewport.addEventListener("wheel", (event) => {
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        adjustZoom(event.deltaY < 0 ? 1.08 : 0.92);
        renderStickyNodes();
        renderFocusNodeBanner();
        return;
      }
      if (state.rootIds.length > 1) {
        const nearTop = viewport.scrollTop <= 18;
        const nearBottom = viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 18;
        if ((event.deltaY < 0 && nearTop) || (event.deltaY > 0 && nearBottom)) {
          event.preventDefault();
          focusRootByIndex(getUiRootIndex() + (event.deltaY > 0 ? 1 : -1), { fit: false, behavior: "smooth" });
        }
      }
    }, { passive: false });

    let panState = null;

    function isNodeTarget(element) {
      let current = element;
      while (current && current !== viewport) {
        if (current.classList && (current.classList.contains("node-group") || current.classList.contains("node-expander"))) {
          return true;
        }
        current = current.parentElement;
      }
      return false;
    }

    function canStartPanning(target) {
      if (!target || !(target instanceof Element)) {
        return false;
      }
      if (isNodeTarget(target)) {
        return false;
      }
      return target === viewport || target === elements.graphCanvas || target === elements.graphScene || Boolean(target.closest("#graphCanvas"));
    }

    viewport.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !canStartPanning(event.target)) {
        return;
      }
      panState = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        scrollLeft: viewport.scrollLeft,
        scrollTop: viewport.scrollTop,
      };
      viewport.setPointerCapture(event.pointerId);
      viewport.classList.add("is-panning");
      event.preventDefault();
    });

    viewport.addEventListener("pointermove", (event) => {
      if (!panState || event.pointerId !== panState.pointerId) {
        return;
      }
      viewport.scrollLeft = panState.scrollLeft - (event.clientX - panState.startX);
      viewport.scrollTop = panState.scrollTop - (event.clientY - panState.startY);
    });

    const endPan = (event) => {
      if (!panState || event.pointerId !== panState.pointerId) {
        return;
      }
      if (viewport.hasPointerCapture(event.pointerId)) {
        viewport.releasePointerCapture(event.pointerId);
      }
      viewport.classList.remove("is-panning");
      panState = null;
    };
    viewport.addEventListener("pointerup", endPan);
    viewport.addEventListener("pointercancel", endPan);
    viewport.addEventListener("scroll", () => {
      renderRootPager();
      renderFocusNodeBanner();
      renderStickyNodes();
    }, { passive: true });
  }

  Object.assign(app, {
    getUiRootIndex,
    queueFitView,
    queueScrollToNode,
    homeViewport,
    queueHomeViewport,
    focusRootByIndex,
    toggleUserSelected,
    toggleUserRemoved,
    toggleExpand,
    computeLayout,
    buildLinkPath,
    getGraphBounds,
    getViewportBounds,
    getNodeScreenRect,
    isRectVisible,
    getVisibleContextNodeId,
    getVisibleLeadNodeId,
    getFocusDisplayNode,
    getStickyPathNodeIds,
    computeStickyNodePlacements,
    syncGraphCanvasSize,
    applyViewportTransform,
    adjustZoom,
    fitView,
    resetView,
    scrollToNode,
    renderGraph,
    renderFocusNodeBanner,
    renderStickyNodes,
    renderRootPager,
    renderRootConstraints,
    renderGraphSelectedPath,
    renderInspector,
    renderTraceSummary,
    installViewportInteraction,
  });
})(window);
