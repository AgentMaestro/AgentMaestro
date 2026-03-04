(() => {
  const shell = document.querySelector("[data-agent-chat]");
  if (!shell) {
    return;
  }

  const statusEl = shell.querySelector("[data-connection-status]");
  const messagesEl = shell.querySelector("[data-chat-messages]");
  const form = shell.querySelector("[data-chat-form]");
  const textarea = shell.querySelector("[data-chat-input]");
  const connectionActionBtn = shell.querySelector("[data-connection-action]");
  const toolPanel = shell.querySelector("[data-tool-requests]");
  const wsUrl = shell.dataset.wsUrl;
  const agentName = shell.dataset.agentName || "Maestro";
  const userName = shell.dataset.userName || "You";
  const agentSlug = shell.dataset.agentSlug || "";
  const toolPlaceholder = toolPanel?.querySelector(".tool-request-empty");
  const toolCards = new Map();

  if (!wsUrl) {
    return;
  }

  const scrollToBottom = () => {
    if (messagesEl) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  };

  let isConnected = false;

  const updateConnectionAction = (connected) => {
    if (!connectionActionBtn) {
      return;
    }
    connectionActionBtn.classList.remove("success", "danger", "hidden");
    if (connected) {
      connectionActionBtn.textContent = "Disconnect";
      connectionActionBtn.classList.add("danger");
    } else {
      connectionActionBtn.textContent = "Connect";
      connectionActionBtn.classList.add("success");
    }
  };

  const appendSystemMessage = (text) => {
    appendMessage({
      role: "system",
      direction: "in",
      text,
      timestamp: new Date().toISOString(),
    });
  };

  const setStatus = (text) => {
    if (statusEl) {
      statusEl.textContent = text;
    }
    isConnected = text === "Connected";
    updateConnectionAction(isConnected);
    if (statusEl) {
      statusEl.classList.toggle("connected", text === "Connected");
      statusEl.classList.toggle("disconnected", text === "Disconnected");
    }
  };

  const AGENT_TAB_STORAGE_PREFIX = "agent-chat-tab:";
  const FOCUS_CHANNEL_NAME = "agent-chat-focus";
  const tabId =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const storageAvailable = (() => {
    try {
      if (typeof window === "undefined" || typeof window.localStorage === "undefined") {
        return false;
      }
      const testKey = "__agent_chat_storage_test__";
      window.localStorage.setItem(testKey, "1");
      window.localStorage.removeItem(testKey);
      return true;
    } catch (error) {
      return false;
    }
  })();
  const storageRef = storageAvailable ? window.localStorage : null;
  let agentTabStorageKey = null;
  if (storageRef && agentSlug) {
    agentTabStorageKey = `${AGENT_TAB_STORAGE_PREFIX}${agentSlug}:${tabId}`;
    storageRef.setItem(
      agentTabStorageKey,
      JSON.stringify({ slug: agentSlug, tabId, openedAt: Date.now() })
    );
  }
  if (agentSlug) {
    const agentWindowName = `agent-chat-${agentSlug}`;
    if (window.name !== agentWindowName) {
      window.name = agentWindowName;
    }
  }
  const focusChannel =
    typeof BroadcastChannel === "function" ? new BroadcastChannel(FOCUS_CHANNEL_NAME) : null;
  focusChannel?.addEventListener("message", (event) => {
    const payload = event.data;
    if (!payload || payload.origin === tabId || payload.slug !== agentSlug) {
      return;
    }
    appendSystemMessage(`${agentName} received focus request from another tab.`);
    if (typeof window.focus === "function") {
      window.focus();
    }
  });
  const cleanupAgentTabEntry = () => {
    if (storageRef && agentTabStorageKey) {
      storageRef.removeItem(agentTabStorageKey);
      agentTabStorageKey = null;
    }
    focusChannel?.close();
  };
  window.addEventListener("beforeunload", cleanupAgentTabEntry);
  window.addEventListener("pagehide", cleanupAgentTabEntry);

  const formatTimestamp = (value) => {
    const emitted = value ? new Date(value) : new Date();
    return emitted.toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }) + " EST";
  };

  const createMessageElement = (payload) => {
    const roleName = payload.role || "agent";
    const direction = payload.direction || (roleName === "operator" ? "out" : "in");
    const article = document.createElement("article");
    article.className = `chat-message chat-message-${direction} chat-message-author-${roleName}`;

    const meta = document.createElement("div");
    meta.className = "chat-message-meta";
    const authorLabel = document.createElement("span");
    authorLabel.className = "chat-message-author";
    const defaultAuthor = roleName === "assistant" ? agentName.toLowerCase() : roleName;
    const authorLabelText = payload.author || defaultAuthor;
    authorLabel.textContent = authorLabelText;
    const timestamp = document.createElement("time");
    timestamp.textContent = formatTimestamp(payload.timestamp);
    const kindLabelText = payload.kind || payload.role || roleName;
    const showType =
      roleName === "system" && kindLabelText && kindLabelText !== authorLabelText;
    if (showType) {
      const kind = document.createElement("span");
      kind.className = "chat-message-type";
      kind.textContent = kindLabelText;
      meta.append(authorLabel, kind, timestamp);
    } else {
      meta.append(authorLabel, timestamp);
    }

    const body = document.createElement("p");
    body.className = "chat-message-text";
    body.textContent = payload.text || "";

    article.append(meta, body);
    return article;
  };

  let thinkingPlaceholder = null;
  let thinkingPhase = null;

  const thinkingLabel = (phase) => `${agentName} is ${phase}…`;

  const removeThinkingPlaceholder = () => {
    if (thinkingPlaceholder) {
      thinkingPlaceholder.remove();
      thinkingPlaceholder = null;
      thinkingPhase = null;
    }
  };

  const renderThinkingPlaceholder = (phase) => {
    removeThinkingPlaceholder();
    console.log("renderThinkingPlaceholder", phase);
    thinkingPhase = phase;
    thinkingPlaceholder = createMessageElement({
      role: "system",
      direction: "system",
      author: "system",
      text: thinkingLabel(phase),
      timestamp: new Date().toISOString(),
    });
    if (messagesEl) {
      messagesEl.appendChild(thinkingPlaceholder);
      scrollToBottom();
    }
  };

  const setThinkingPhase = (phase) => {
    console.log("setThinkingPhase", phase);
    if (!thinkingPlaceholder || thinkingPhase === phase) {
      return;
    }
    thinkingPhase = phase;
    const textEl = thinkingPlaceholder.querySelector(".chat-message-text");
    if (textEl) {
      textEl.textContent = thinkingLabel(phase);
    }
  };

  const appendMessage = (payload, options = {}) => {
    if (!options.keepThinking) {
      removeThinkingPlaceholder();
    }
    if (!messagesEl) {
      return;
    }
    const article = createMessageElement(payload);
    messagesEl.appendChild(article);
    scrollToBottom();
  };

  const ensureToolPlaceholder = () => {
    if (!toolPlaceholder) {
      return;
    }
    toolPlaceholder.style.display = toolCards.size ? "none" : "";
  };

  const sendToolControl = (type, toolCallId, extra = {}) => {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    socket.send(JSON.stringify({ type, tool_call_id: toolCallId, ...extra }));
  };

  const renderArgs = (args) => {
    try {
      return JSON.stringify(args, null, 2);
    } catch {
      return String(args);
    }
  };

  const createToolCard = (payload) => {
    const card = document.createElement("div");
    card.className = "tool-request-card";
    card.dataset.toolCallId = payload.tool_call_id;

    const header = document.createElement("div");
    header.className = "tool-request-card-header";
    const title = document.createElement("strong");
    title.textContent = payload.tool_name;
    const status = document.createElement("span");
    status.className = "tool-request-status";
    status.textContent = payload.status || "Queued";
    card._statusEl = status;
    header.append(title, status);

    const body = document.createElement("pre");
    body.className = "tool-request-args";
    body.textContent = `Args: ${renderArgs(payload.args || {})}`;

    const actions = document.createElement("div");
    actions.className = "tool-request-card-actions";
    if (payload.requires_approval) {
      const approveBtn = document.createElement("button");
      approveBtn.className = "primary";
      approveBtn.textContent = "Approve";
      approveBtn.addEventListener("click", () => {
        sendToolControl("tool_approve", payload.tool_call_id);
      });
      const denyBtn = document.createElement("button");
      denyBtn.className = "secondary";
      denyBtn.textContent = "Deny";
      denyBtn.addEventListener("click", () => {
        sendToolControl("tool_deny", payload.tool_call_id);
      });
      actions.append(approveBtn, denyBtn);
    } else {
      const queued = document.createElement("span");
      queued.textContent = "Queued";
      queued.className = "tool-request-status";
      actions.append(queued);
    }

    const footer = document.createElement("div");
    footer.className = "tool-request-footer";
    const detail = document.createElement("div");
    detail.className = "tool-request-detail";
    footer.append(detail);
    card._detailEl = detail;

    card.append(header, body, actions, footer);
    ensureToolPlaceholder();
    return card;
  };

  const getToolCard = (toolCallId) => toolCards.get(toolCallId);

  const updateToolCardStatus = (toolCallId, status, info) => {
    const card = getToolCard(toolCallId);
    if (!card) {
      return;
    }
    if (card._statusEl) {
      card._statusEl.textContent = status;
    }
    if (card._detailEl && info) {
      card._detailEl.textContent = info;
    }
    const disableButtons = status !== "PENDING_APPROVAL";
    card.querySelectorAll("button").forEach((btn) => {
      btn.disabled = disableButtons;
    });
  };

  const handleToolRequest = (payload) => {
    if (!toolPanel) {
      return;
    }
    let card = getToolCard(payload.tool_call_id);
    if (!card) {
      card = createToolCard(payload);
      toolPanel.appendChild(card);
      toolCards.set(payload.tool_call_id, card);
    }
    updateToolCardStatus(payload.tool_call_id, payload.status || "Queued");
    ensureToolPlaceholder();
  };

  const handleToolStatus = (payload) => {
    updateToolCardStatus(payload.tool_call_id, payload.status || "Queued", payload.error);
  };

  const handleToolResult = (payload) => {
    appendMessage({
      role: "system",
      direction: "in",
      text: `Tool ${payload.tool_name} completed with status ${payload.status || "completed"}.`,
    });
    updateToolCardStatus(payload.tool_call_id, payload.status || "COMPLETED", payload.result && JSON.stringify(payload.result));
  };

  const handleToolDenied = (payload) => {
    const data = payload.data || payload;
    appendMessage({
      role: "system",
      direction: "in",
      text: `Tool ${data.tool_name || payload.tool_name} denied: ${data.error || "Not allowed"}`,
    });
    updateToolCardStatus(data.tool_call_id || payload.tool_call_id, "DENIED", data.error);
  };

  const handleSocketMessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
      console.log(payload);
    } catch (error) {
      console.warn("Unable to parse agent chat payload", error);
      return;
    }
    const isTransportLog =
      payload.type === "system" &&
      typeof payload.text === "string" &&
      (payload.text.includes("[WS") || payload.text.includes("[HTTP"));
    if (isTransportLog && payload.text.includes("[WS Rcv]")) {
      setThinkingPhase("typing");
    }

    switch (payload.type) {
      case "connected":
        setStatus("Connected");
        if (textarea) {
          textarea.removeAttribute("disabled");
        }
        return;
      case "message":
        appendMessage({
          role: payload.role || "assistant",
          direction: "in",
          text: payload.text || "",
          timestamp: payload.timestamp,
        });
        return;
      case "tool_request":
        handleToolRequest(payload);
        setStatus("Tool requested");
        return;
      case "tool_status":
        handleToolStatus(payload.data || payload);
        return;
      case "tool_result":
        handleToolResult(payload);
        setStatus("Connected");
        return;
      case "tool_denied":
        handleToolDenied(payload);
        setStatus("Connected");
        return;
      case "error":
        appendMessage({
          role: "system",
          direction: "in",
          text: payload.message || "An error occurred.",
          timestamp: payload.timestamp,
        });
        setStatus("Error");
        return;
      case "system":
        appendMessage(
          {
            role: "system",
            direction: "in",
            text: payload.text || "",
            timestamp: payload.timestamp,
          },
          { keepThinking: isTransportLog }
        );
      return;
    default:
      return;
    }
  };

  let socket = null;

  const hasOpenAgentTab = (slug) => {
    if (!storageRef || !slug) {
      return false;
    }
    const prefix = `${AGENT_TAB_STORAGE_PREFIX}${slug}:`;
    for (let index = 0; index < storageRef.length; index += 1) {
      const key = storageRef.key(index);
      if (key && key.startsWith(prefix)) {
        return true;
      }
    }
    return false;
  };

  const broadcastFocusRequest = (slug) => {
    focusChannel?.postMessage({ type: "focus", slug, origin: tabId, requestedBy: agentSlug });
  };

  const focusExistingAgentTab = (slug) => {
    broadcastFocusRequest(slug);
  };

  const openNewAgentTab = (url, label) => {
    const newTab = window.open(url, "_blank");
    if (newTab) {
      newTab.focus();
    }
    if (label) {
      appendSystemMessage(`Opening ${label} in a new tab.`);
    }
  };

  const resetAgentSwitcherSelection = (select) => {
    if (!select) {
      return;
    }
    const current = Array.from(select.options).find(
      (option) => option.dataset?.agentSlug === agentSlug
    );
    if (current) {
      current.selected = true;
      select.value = current.value;
    }
  };

  const handleAgentSwitcherChange = (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    const select = event.currentTarget;
    const option = select.selectedOptions?.[0];
    const targetSlug = option?.dataset?.agentSlug;
    const targetUrl = option?.value;
    const label = option?.textContent?.trim() || targetSlug;
    if (!targetSlug || !targetUrl) {
      resetAgentSwitcherSelection(select);
      return;
    }
    if (targetSlug === agentSlug) {
      resetAgentSwitcherSelection(select);
      return;
    }
    if (hasOpenAgentTab(targetSlug)) {
      appendSystemMessage(`${label} is already open in another tab. Click on that tab.`);
      focusExistingAgentTab(targetSlug);
    } else {
      openNewAgentTab(targetUrl, label);
    }
    resetAgentSwitcherSelection(select);
  };

  const agentSwitcher = shell.querySelector("[data-agent-switcher]");
  if (agentSwitcher) {
    agentSwitcher.removeAttribute("onchange");
    agentSwitcher.addEventListener("change", handleAgentSwitcherChange);
    resetAgentSwitcherSelection(agentSwitcher);
  }

  const connect = () => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.close();
    }
    setStatus("Connecting…");
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}${wsUrl}`;
    console.log("WS url = ", url);
    socket = new WebSocket(url);
    socket.addEventListener("open", () => {
      setStatus("Connected");
      if (textarea) {
        textarea.removeAttribute("disabled");
      }
    });
    socket.addEventListener("message", handleSocketMessage);
      socket.addEventListener("close", () => {
        const wasConnected = isConnected;
        setStatus("Disconnected");
        if (textarea) {
          textarea.setAttribute("disabled", "true");
        }
        if (wasConnected) {
          appendSystemMessage("Disconnected from the agent");
        }
      });
    socket.addEventListener("error", () => {
      setStatus("Connection error");
    });
  };

  const sendMessage = () => {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    const text = textarea?.value?.trim();
    if (!text) {
      return;
    }
    appendMessage({
      role: "operator",
      direction: "out",
      kind: userName,
      text,
      timestamp: new Date().toISOString(),
      author: userName,
    });
    socket.send(JSON.stringify({ type: "chat.message", text }));
    renderThinkingPlaceholder("thinking");
    if (textarea) {
      textarea.value = "";
    }
  };

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  connectionActionBtn?.addEventListener("click", () => {
    if (isConnected) {
      socket?.close();
    } else {
      connect();
    }
  });

  textarea?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  window.addEventListener("beforeunload", () => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.close();
    }
  });

  connect();
})();
