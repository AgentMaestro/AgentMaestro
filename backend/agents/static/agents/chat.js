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
    const runStatusEl = shell.querySelector("[data-run-status]");
    const pauseRunBtn = shell.querySelector("[data-run-pause]");
    const resumeRunBtn = shell.querySelector("[data-run-resume]");
    const approvalGrantsListEl = shell.querySelector("[data-approval-grants-list]");
    const approvalGrantsEmptyEl = shell.querySelector("[data-approval-grants-empty]");
    const clearApprovalGrantsBtn = shell.querySelector("[data-clear-approval-grants]");
    const wsUrl = shell.dataset.wsUrl;
    const runPreallocUrl = shell.dataset.runPreallocUrl;
    const agentName = shell.dataset.agentName || "Maestro";
    const userName = shell.dataset.userName || "You";
    const agentSlug = shell.dataset.agentSlug || "";
    const toolCards = new Map();
    let activeApprovalGrants = [];
    const RUN_ID_STORAGE_KEY = "agentmaestro.active_run_id";
    const log = (...args) => console.log("[chat.js]", ...args);
    let activeRunId = null;
    const sessionRunStorage = (() => {
        try {
            if (typeof window === "undefined" || typeof window.sessionStorage === "undefined") {
                return null;
            }
            const testKey = "__agent_chat_run_storage_test__";
            window.sessionStorage.setItem(testKey, "1");
            window.sessionStorage.removeItem(testKey);
            return window.sessionStorage;
        } catch (error) {
            return null;
        }
    })();
    if (!wsUrl) {
        return;
    }

    const scrollToBottom = () => {
        if (messagesEl) {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
    };

    const renderApprovalGrants = () => {
        if (!approvalGrantsListEl || !approvalGrantsEmptyEl || !clearApprovalGrantsBtn) {
            return;
        }
        approvalGrantsListEl.textContent = "";
        const grants = Array.isArray(activeApprovalGrants) ? activeApprovalGrants : [];
        grants.forEach((grant) => {
            const item = document.createElement("div");
            item.className = "approval-grant-item";

            const header = document.createElement("div");
            header.className = "approval-grant-item-header";

            const tool = document.createElement("span");
            tool.className = "approval-grant-tool";
            tool.textContent = grant.tool_name || "tool";

            const revokeBtn = document.createElement("button");
            revokeBtn.type = "button";
            revokeBtn.className = "approval-grant-revoke";
            revokeBtn.textContent = "Revoke";
            revokeBtn.addEventListener("click", () => {
                sendToolControl("tool_revoke_grant", null, {grant_id: grant.id});
            });

            header.append(tool, revokeBtn);

            const scope = document.createElement("div");
            scope.className = "approval-grant-scope";
            scope.textContent = grant.label || grant.scope_display || grant.scope_path || "";

            const meta = document.createElement("div");
            meta.className = "approval-grant-meta";
            const createdBits = [];
            if (grant.created_by) {
                createdBits.push(`by ${grant.created_by}`);
            }
            if (grant.created_at) {
                createdBits.push(formatTimestamp(grant.created_at));
            }
            meta.textContent = createdBits.join(" · ");

            item.append(header, scope);
            if (meta.textContent) {
                item.append(meta);
            }
            approvalGrantsListEl.append(item);
        });
        const hasGrants = grants.length > 0;
        approvalGrantsEmptyEl.hidden = hasGrants;
        clearApprovalGrantsBtn.disabled = !hasGrants;
    };

    const setApprovalGrants = (grants) => {
        activeApprovalGrants = Array.isArray(grants) ? grants : [];
        renderApprovalGrants();
    };

    let isConnected = false;
    let activeRunStatus = "RUNNING";

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

    const appendSystemMessage = (text, kind = "connection") => {
        appendMessage({
            role: "system",
            direction: "in",
            text,
            timestamp: new Date().toISOString(),
            kind,
        });
    };

    const updateRunControls = () => {
        const paused = activeRunStatus === "PAUSED" || activeRunStatus === "WAITING_FOR_USER";
        if (pauseRunBtn) {
            pauseRunBtn.disabled = !isConnected || activeRunStatus !== "RUNNING";
        }
        if (resumeRunBtn) {
            resumeRunBtn.disabled = !isConnected || !paused;
        }
    };

    const setRunStatus = (status) => {
        activeRunStatus = String(status || "RUNNING").toUpperCase();
        if (runStatusEl) {
            runStatusEl.textContent = activeRunStatus;
            runStatusEl.classList.remove(
                "running",
                "paused",
                "waiting_for_user",
                "completed",
                "failed",
                "canceled"
            );
            runStatusEl.classList.add(activeRunStatus.toLowerCase());
        }
        updateRunControls();
    };

    const setStatus = (text) => {
        if (statusEl) {
            statusEl.textContent = text;
        }
        isConnected = text === "Connected";
        updateConnectionAction(isConnected);
        updateRunControls();
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
            JSON.stringify({slug: agentSlug, tabId, openedAt: Date.now()})
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

    const storeRunId = (runId) => {
        if (!sessionRunStorage || !runId) {
            return;
        }
        try {
            sessionRunStorage.setItem(RUN_ID_STORAGE_KEY, runId);
        } catch (error) {
            console.warn("Unable to persist run_id", error);
        }
    };

    const readStoredRunId = () => {
        if (!sessionRunStorage) {
            return null;
        }
        try {
            return sessionRunStorage.getItem(RUN_ID_STORAGE_KEY);
        } catch {
            return null;
        }
    };

    const ensureRunId = async () => {
        if (activeRunId) {
            log("ensureRunId: already have activeRunId =", activeRunId);
            return activeRunId;
        }
        const storedRun = readStoredRunId();
        if (storedRun) {
            activeRunId = storedRun;
            log("ensureRunId: restored run_id from sessionStorage =", activeRunId);
            return activeRunId;
        }
        if (!runPreallocUrl) {
            log("ensureRunId: no preallocUrl available, will rely on server to set run_id");
            return null;
        }
        log("ensureRunId: requesting preallocated run_id via", runPreallocUrl);
        const preallocated = await requestPreallocatedRunId();
        if (preallocated) {
            activeRunId = preallocated;
            storeRunId(preallocated);
            log("ensureRunId: obtained preallocated run_id =", activeRunId);
        }
        return activeRunId;
    };

    const requestPreallocatedRunId = async () => {
        if (!runPreallocUrl) {
            return null;
        }
        log("requestPreallocatedRunId: requesting", runPreallocUrl);
        try {
            const response = await fetch(runPreallocUrl, {
                credentials: "include",
                headers: {Accept: "application/json"},
            });
            if (!response.ok) {
                throw new Error(`Failed to preallocate run (${response.status})`);
            }
            const payload = await response.json();
            return payload.run_id || null;
        } catch (error) {
            console.warn("Unable to preallocate run", error);
            return null;
        }
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

    const isCollapsibleTransportSystemMessage = (payload) => {
        const text = typeof payload?.text === "string" ? payload.text.trim() : "";
        return (
            (payload?.role || "agent") === "system" &&
            (text.startsWith("[WS Send]") || text.startsWith("[WS Rcv]"))
        );
    };

    const updateTransportToggle = (button, expanded) => {
        if (!button) {
            return;
        }
        button.textContent = expanded ? "^" : "v";
        button.setAttribute(
            "aria-label",
            expanded ? "Collapse system message" : "Expand system message"
        );
        button.setAttribute("aria-expanded", expanded ? "true" : "false");
        button.title = expanded ? "Collapse" : "Expand";
    };

    const createMessageElement = (payload) => {
        const roleName = payload.role || "agent";
        const direction = payload.direction || (roleName === "operator" ? "out" : "in");
        const article = document.createElement("article");
        article.className = `chat-message chat-message-${direction} chat-message-author-${roleName}`;

        const meta = document.createElement("div");
        meta.className = "chat-message-meta";
        const metaLead = document.createElement("div");
        metaLead.className = "chat-message-meta-lead";
        const metaTrail = document.createElement("div");
        metaTrail.className = "chat-message-meta-trail";
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
        metaLead.append(authorLabel);
        if (showType) {
            const kind = document.createElement("span");
            kind.className = "chat-message-type";
            kind.textContent = kindLabelText;
            metaLead.append(kind);
        }
        metaTrail.append(timestamp);

        const body = document.createElement("p");
        body.className = "chat-message-text";
        body.textContent = payload.text || "";

        if (isCollapsibleTransportSystemMessage(payload)) {
            article.classList.add("chat-message-transport-log");
            body.classList.add("chat-message-text-collapsed");
            const toggle = document.createElement("button");
            toggle.type = "button";
            toggle.className = "chat-message-toggle";
            updateTransportToggle(toggle, false);
            toggle.addEventListener("click", () => {
                const expanded = body.classList.toggle("chat-message-text-expanded");
                body.classList.toggle("chat-message-text-collapsed", !expanded);
                article.classList.toggle("chat-message-expanded", expanded);
                updateTransportToggle(toggle, expanded);
                scrollToBottom();
            });
            metaTrail.append(toggle);
        }

        meta.append(metaLead, metaTrail);
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

    const sendToolControl = (type, toolCallId, extra = {}) => {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
        }
        const payload = {type, ...extra};
        if (toolCallId) {
            payload.tool_call_id = toolCallId;
        }
        socket.send(JSON.stringify(payload));
    };

    const sendRunControl = (type, extra = {}) => {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
        }
        socket.send(JSON.stringify({type, ...extra}));
    };

    const renderArgs = (args) => {
        try {
            return JSON.stringify(args, null, 2);
        } catch {
            return String(args);
        }
    };

    const renderDetail = (detail) => {
        if (detail === null || typeof detail === "undefined" || detail === "") {
            return "";
        }
        if (typeof detail === "string") {
            return detail;
        }
        try {
            return JSON.stringify(detail, null, 2);
        } catch {
            return String(detail);
        }
    };

    const updateDetailToggle = (button, expanded) => {
        if (!button) {
            return;
        }
        button.textContent = expanded ? "^" : "v";
        button.setAttribute(
            "aria-label",
            expanded ? "Collapse tool response" : "Expand tool response"
        );
        button.setAttribute("aria-expanded", expanded ? "true" : "false");
        button.title = expanded ? "Collapse response" : "Expand response";
    };

    const setToolCardDetail = (card, detail) => {
        if (!card?._detailEl || !card?._footerEl || !card?._detailToggle) {
            return;
        }
        const rendered = renderDetail(detail);
        if (!rendered) {
            card._detailEl.textContent = "";
            card._detailEl.classList.add("tool-request-detail-collapsed");
            card._detailEl.classList.remove("tool-request-detail-expanded");
            card._footerEl.hidden = true;
            card._detailToggle.hidden = true;
            updateDetailToggle(card._detailToggle, false);
            return;
        }
        card._detailEl.textContent = rendered;
        card._footerEl.hidden = false;
        card._detailToggle.hidden = false;
        card._detailEl.classList.add("tool-request-detail-collapsed");
        card._detailEl.classList.remove("tool-request-detail-expanded");
        updateDetailToggle(card._detailToggle, false);
    };

    const setApprovalButtonsState = (card, state) => {
        if (!card?._approveBtn || !card?._denyBtn) {
            return;
        }
        const approveBtn = card._approveBtn;
        const denyBtn = card._denyBtn;
        const grantSelect = card._grantSelect;
        approveBtn.classList.remove("selected");
        denyBtn.classList.remove("selected");
        approveBtn.disabled = state !== "pending";
        denyBtn.disabled = state !== "pending";
        if (grantSelect) {
            grantSelect.disabled = state !== "pending";
        }
        approveBtn.textContent = state === "approved" ? "Approve ✓" : "Approve";
        denyBtn.textContent = state === "denied" ? "Deny ✓" : "Deny";
        if (state === "approved") {
            approveBtn.classList.add("selected");
        } else if (state === "denied") {
            denyBtn.classList.add("selected");
        }
        card.dataset.approvalState = state;
    };

    const getToolStatusKey = (status) =>
        String(status || "QUEUED")
            .trim()
            .replace(/\s+/g, "_")
            .toUpperCase();

    const createToolCard = (payload) => {
        const article = document.createElement("article");
        article.className = "chat-message chat-message-system chat-message-author-system tool-request-entry";

        const meta = document.createElement("div");
        meta.className = "chat-message-meta";
        const authorLabel = document.createElement("span");
        authorLabel.className = "chat-message-author";
        authorLabel.textContent = "system";
        const kind = document.createElement("span");
        kind.className = "chat-message-type";
        kind.textContent = `tool_${payload.tool_name || "event"}`;
        const timestamp = document.createElement("time");
        timestamp.textContent = formatTimestamp(payload.timestamp);
        meta.append(authorLabel, kind, timestamp);

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
        if (payload.awaiting_approval) {
            if (Array.isArray(payload.approval_options) && payload.approval_options.length > 1) {
                const grantSelect = document.createElement("select");
                grantSelect.className = "tool-request-grant-select";
                payload.approval_options.forEach((option) => {
                    const el = document.createElement("option");
                    el.value = option.mode || "once";
                    el.textContent = option.label || option.mode || "Approve once";
                    grantSelect.append(el);
                });
                card._grantSelect = grantSelect;
                actions.append(grantSelect);
            }
            const approveBtn = document.createElement("button");
            approveBtn.className = "tool-approve-btn";
            approveBtn.textContent = "Approve";
            approveBtn.addEventListener("click", () => {
                sendToolControl("tool_approve", payload.tool_call_id, {
                    grant_mode: card._grantSelect?.value || "once",
                });
            });
            const denyBtn = document.createElement("button");
            denyBtn.className = "tool-deny-btn";
            denyBtn.textContent = "Deny";
            denyBtn.addEventListener("click", () => {
                sendToolControl("tool_deny", payload.tool_call_id);
            });
            card._approveBtn = approveBtn;
            card._denyBtn = denyBtn;
            actions.append(approveBtn, denyBtn);
            setApprovalButtonsState(card, "pending");
        }

        const footer = document.createElement("div");
        footer.className = "tool-request-footer";
        footer.hidden = true;
        const footerHeader = document.createElement("div");
        footerHeader.className = "tool-request-footer-header";
        const footerLabel = document.createElement("span");
        footerLabel.className = "tool-request-footer-label";
        footerLabel.textContent = "Response";
        const detailToggle = document.createElement("button");
        detailToggle.type = "button";
        detailToggle.className = "tool-request-detail-toggle";
        detailToggle.hidden = true;
        updateDetailToggle(detailToggle, false);
        const detail = document.createElement("div");
        detail.className = "tool-request-detail";
        detail.classList.add("tool-request-detail-collapsed");
        detailToggle.addEventListener("click", () => {
            const expanded = detail.classList.toggle("tool-request-detail-expanded");
            detail.classList.toggle("tool-request-detail-collapsed", !expanded);
            footer.classList.toggle("tool-request-footer-expanded", expanded);
            updateDetailToggle(detailToggle, expanded);
            scrollToBottom();
        });
        footerHeader.append(footerLabel, detailToggle);
        footer.append(footerHeader, detail);
        card._detailEl = detail;
        card._detailToggle = detailToggle;
        card._footerEl = footer;

        const bodyWrap = document.createElement("div");
        bodyWrap.className = "chat-message-text";
        bodyWrap.append(card);

        card.append(header, body);
        if (actions.childElementCount) {
            card.append(actions);
        }
        card.append(footer);
        article.append(meta, bodyWrap);
        card._articleEl = article;
        return card;
    };

    const getToolCard = (toolCallId) => toolCards.get(toolCallId);

    const updateToolCardStatus = (toolCallId, status, info) => {
        const card = getToolCard(toolCallId);
        if (!card) {
            return;
        }
        const statusText = status || "Queued";
        const statusKey = getToolStatusKey(statusText);
        if (card._statusEl) {
            card._statusEl.textContent = statusText;
            card._statusEl.dataset.status = statusKey;
        }
        card.dataset.status = statusKey;
        if (card._articleEl) {
            card._articleEl.dataset.status = statusKey;
        }
        if (card._detailEl && info) {
            setToolCardDetail(card, info);
        }
        if (card._approveBtn && card._denyBtn) {
            if (statusKey === "PENDING_APPROVAL") {
                setApprovalButtonsState(card, "pending");
            } else if (statusKey === "DENIED") {
                setApprovalButtonsState(card, "denied");
            } else {
                setApprovalButtonsState(card, "approved");
            }
        }
    };

    const handleToolRequest = (payload) => {
        console.log("tool_request run_id=", activeRunId);
        if (!messagesEl) {
            return;
        }
        let card = getToolCard(payload.tool_call_id);
        if (!card) {
            card = createToolCard(payload);
            messagesEl.appendChild(card._articleEl);
            toolCards.set(payload.tool_call_id, card);
            scrollToBottom();
        }
        updateToolCardStatus(payload.tool_call_id, payload.status || "Queued");
    };

    const handleToolStatus = (payload) => {
        updateToolCardStatus(payload.tool_call_id, payload.status || "Queued", payload.error);
    };

    const handleToolResult = (payload) => {
        const data = payload?.data ? payload.data : payload;
        console.log("[chat.js] tool_result DIAG:", {
            run_id: data?.run_id,
            tool_call_id: data?.tool_call_id,
            tool_name: data?.tool_name,
            status: data?.status,
            keys: data ? Object.keys(data) : null,
        });

        updateToolCardStatus(data.tool_call_id, data?.status || "COMPLETED", data.result);
    };

    const handleToolDenied = (payload) => {
        const data = payload.data || payload;
        appendMessage({
            role: "system",
            direction: "in",
            text: `Tool ${data.tool_name || payload.tool_name} denied: ${data.error || "Not allowed"}`,
            kind: "tool",
        });
        updateToolCardStatus(data.tool_call_id || payload.tool_call_id, "DENIED", data.error);
    };

    const handleSocketMessage = (event) => {
        let payload;
        try {
            payload = JSON.parse(event.data);
            console.log("[WS RCV] ****************  START WS MESSAGE ******************");
            console.log("[WS RCV] type=", payload?.type, "activeRunId=", activeRunId);

        } catch (error) {
            console.warn("Unable to parse agent chat payload", error);
            console.log("[WS RCV] ****************  END WS MESSAGE ******************");
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
                activeRunId = payload.run_id || activeRunId;
                console.log("[WS CONNECT] Connected with run_id:", activeRunId);
                if (activeRunId) {
                    storeRunId(activeRunId);
                }
                setApprovalGrants(payload.approval_grants || []);
                setRunStatus(payload.run_status || activeRunStatus);
                setStatus("Connected");
                if (textarea) {
                    textarea.removeAttribute("disabled");
                }
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "message":
                appendMessage({
                    role: payload.role || "assistant",
                    direction: payload.direction || "in",
                    author: payload.author,
                    text: payload.text || "",
                    timestamp: payload.timestamp,
                });
                console.log("[WS MESSAGE] Message appended");
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "tool_request":
                handleToolRequest(payload);
                setStatus("Tool requested");
                console.log("[WS TOOL_REQUEST] tool_request run_id=", activeRunId, payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;

            case "debug_group_echo":
                console.log("[WS] debug_group_echo received:", payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "tool_call_completed":
                console.log("[WS] tool_call_completed DIAG:", payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "tool_status":
                handleToolStatus(payload.data || payload);
                console.log("[WS] tool_status run_id=", activeRunId, payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "tool_result":
                handleToolResult(payload);
                setStatus("Connected");
                console.log("[WS] tool_result run_id=", activeRunId, payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "tool_denied":
                handleToolDenied(payload);
                setStatus("Connected");
                console.log("[WS] tool_denied run_id=", activeRunId, payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "approval_grants":
                setApprovalGrants(payload.grants || []);
                console.log("[WS] approval_grants run_id=", activeRunId, payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "pause_run_ack":
                setRunStatus(payload.status || "PAUSED");
                appendSystemMessage("Run paused. Messages will queue until you resume.", "run_control");
                console.log("[WS] pause_run_ack run_id=", activeRunId, payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "resume_run_ack":
                setRunStatus(payload.status || "RUNNING");
                appendSystemMessage("Run resumed.", "run_control");
                console.log("[WS] resume_run_ack run_id=", activeRunId, payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "cancel_run_ack":
                setRunStatus(payload.status || "CANCELED");
                appendSystemMessage("Run canceled.", "run_control");
                console.log("[WS] cancel_run_ack run_id=", activeRunId, payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case ["error", "tool_error"]:
                appendMessage({
                    role: "system",
                    direction: "in",
                    text: payload.message || "An error occurred.",
                    timestamp: payload.timestamp,
                    kind: "error",
                });
                setStatus("Error");
                console.log("[WS ERROR] Payload = ", payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            case "system":
                appendMessage(
                    {
                        role: "system",
                        direction: "in",
                        text: payload.text || "",
                        timestamp: payload.timestamp,
                    },
                    {keepThinking: isTransportLog}
                );
                console.log("[WS] System Payload = ", payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
                return;
            default:
                console.log("[WS] Unhandled WS message:", payload);
                console.log("[WS RCV] ****************  END WS MESSAGE ******************");
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
        focusChannel?.postMessage({type: "focus", slug, origin: tabId, requestedBy: agentSlug});
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
            console.log("[WS Close] WS was open. Closing it before connecting again.")
            socket.close();
        }
        setStatus("Connecting…");
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        const runQuery = activeRunId
            ? `${wsUrl.includes("?") ? "&" : "?"}run=${encodeURIComponent(activeRunId)}`
            : "";
        const url = `${protocol}://${window.location.host}${wsUrl}${runQuery}`;
        log("WS connecting url =", url, "activeRunId =", activeRunId);
        console.log("[WS CREATE] Creating new socket", {url, run_id: activeRunId});
        socket = new WebSocket(url);
        socket.onclose = (event) => {
            console.warn("[WS CLOSE]", {
                code: event.code,
                reason: event.reason,
                wasClean: event.wasClean,
                readyState: socket.readyState,
                url: socket.url,
                run_id: activeRunId,
            });
        };
        socket.onerror = (event) => {
            console.error("[WS ERROR]", {
                event,
                readyState: socket.readyState,
                url: socket.url,
                run_id: activeRunId,
            });
        };
        socket.addEventListener("open", () => {
            console.log("[WS OPEN]", {url: socket.url, run_id: activeRunId});
            setStatus("Connected");
            if (textarea) {
                textarea.removeAttribute("disabled");
            }
            log("WS open event activeRunId =", activeRunId);
        });
        socket.addEventListener("message", handleSocketMessage);
        socket.addEventListener("close", (event) => {
            const wasConnected = isConnected;
            setStatus("Disconnected");
            if (textarea) {
                textarea.setAttribute("disabled", "true");
            }
            if (wasConnected) {
                appendSystemMessage("Disconnected from the agent");
            }
            log("WS close event code=", event.code, "reason=", event.reason, "activeRunId =", activeRunId);
            console.warn("[WS CLOSE]", {
                code: event.code,
                reason: event.reason,
                wasClean: event.wasClean,
                readyState: socket.readyState,
                url: socket.url,
            });
        });
        socket.addEventListener("error", () => {
            setStatus("Connection error");
            log("WS error event activeRunId =", activeRunId);
            console.error("[WS ERROR]", {url: socket.url, readyState: socket.readyState, run_id: activeRunId});
        });
    };

    const initializeRunAndConnect = async () => {
        await ensureRunId();
        connect();
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
        socket.send(JSON.stringify({type: "chat.message", text}));
        if (activeRunStatus === "RUNNING") {
            renderThinkingPlaceholder("thinking");
        }
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
            console.log("[WS CLOSE] Closing WS.")
            socket?.close();
        } else {
            connect();
        }
    });

    pauseRunBtn?.addEventListener("click", () => {
        sendRunControl("pause_run");
    });

    resumeRunBtn?.addEventListener("click", () => {
        sendRunControl("resume_run");
    });

    textarea?.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });

    window.addEventListener("beforeunload", () => {
        console.warn("[PAGE beforeunload]");
        if (socket && socket.readyState === WebSocket.OPEN) {
            console.log("[WS CLOSE] Closing WS prior to unloading window.")
            socket.close();
        }
    });
    window.addEventListener("pagehide", () => {
        console.warn("[PAGE pagehide]");
    });
    document.addEventListener("visibilitychange", () => {
        console.warn("[VISIBILITY]", document.visibilityState);
    });

    clearApprovalGrantsBtn?.addEventListener("click", () => {
        sendToolControl("tool_clear_grants", null);
    });

    setRunStatus(activeRunStatus);
    initializeRunAndConnect();
})();
