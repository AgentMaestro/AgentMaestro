(() => {
    const shell = document.querySelector("[data-agent-chat]");
    if (!shell) {
        return;
    }

    const statusEl = shell.querySelector("[data-connection-status]");
    const messagesEl = shell.querySelector("[data-chat-messages]");
    const elapsedPromptEl = shell.querySelector("[data-elapsed-prompt]");
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
    const approvalChimePlayed = new Set();
    const approvalChimeQueued = new Set();
    let approvalAudioContext = null;
    let activeApprovalGrants = [];
    const RUN_ID_STORAGE_KEY_BASE = "agentmaestro.active_run_id";
    let runtimeProvider = (shell.dataset.llmProvider || "openai").trim() || "openai";
    let runtimeProviderLabel = (shell.dataset.llmProviderLabel || "LLM").trim() || "LLM";
    let runtimeModel = (shell.dataset.llmModel || "unknown").trim() || "unknown";
    let runtimeTransport = (shell.dataset.llmTransport || "ws").trim() || "ws";
    let runtimeTransportLabel =
        (shell.dataset.llmTransportLabel || "").trim() || (runtimeTransport.toLowerCase() === "ws" ? "WS" : "HTTP");
    const normalizeTransportLabel = (value, fallbackValue = "") => {
        const candidate = String(value || fallbackValue || "").trim().toUpperCase();
        if (candidate.includes("HTTP")) {
            return "HTTP";
        }
        if (candidate.includes("WS")) {
            return "WS";
        }
        return "HTTP";
    };
    const runtimeLogPrefix = () => 
        `[chat.js][${runtimeProviderLabel}:${runtimeModel}:${normalizeTransportLabel(runtimeTransportLabel, runtimeTransport)}]`;
    const log = (...args) => console.log(runtimeLogPrefix(), ...args);
    const warn = (...args) => console.warn(runtimeLogPrefix(), ...args);
    const error = (...args) => console.error(runtimeLogPrefix(), ...args);
    const debug = (...args) => console.debug(runtimeLogPrefix(), ...args);
    const syncRuntimeMetadata = (payload = {}) => {
        const nextProvider = String(payload.provider || runtimeProvider).trim();
        const nextProviderLabel = String(payload.provider_label || runtimeProviderLabel).trim();
        const nextModel = String(payload.model || runtimeModel).trim();
        const nextTransport = String(payload.transport || runtimeTransport).trim();
        const nextTransportLabel = String(
            payload.transport_label ||
                normalizeTransportLabel(nextTransport, runtimeTransportLabel)
        ).trim();

        runtimeProvider = nextProvider || runtimeProvider;
        runtimeProviderLabel = nextProviderLabel || runtimeProviderLabel;
        runtimeModel = nextModel || runtimeModel;
        runtimeTransport = nextTransport || runtimeTransport;
        runtimeTransportLabel = nextTransportLabel || runtimeTransportLabel;
    };
    let activeRunId = null;
    let queuedMessageAfterRunRotate = null;
    let lastHandoffNoticeKey = null;
    let lastPromptSentAt = null;
    let elapsedPromptTimerId = null;
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

    const formatElapsedPrompt = (elapsedMs) => {
        const totalSeconds = Math.max(0, Math.floor((elapsedMs || 0) / 1000));
        const hours = String(Math.floor(totalSeconds / 3600)).padStart(2, "0");
        const minutes = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, "0");
        const seconds = String(totalSeconds % 60).padStart(2, "0");
        return `${hours}:${minutes}:${seconds}`;
    };

    const renderElapsedPrompt = () => {
        if (!elapsedPromptEl) {
            return;
        }
        if (!lastPromptSentAt) {
            elapsedPromptEl.textContent = "";
            return;
        }
        const elapsedText = formatElapsedPrompt(Date.now() - lastPromptSentAt);
        elapsedPromptEl.textContent = `Elapsed since your last prompt: ${elapsedText}`;
    };

    const ensureElapsedPromptTimer = () => {
        if (elapsedPromptTimerId !== null) {
            return;
        }
        elapsedPromptTimerId = window.setInterval(() => {
            renderElapsedPrompt();
        }, 1000);
    };

    const markPromptSentNow = () => {
        lastPromptSentAt = Date.now();
        renderElapsedPrompt();
        ensureElapsedPromptTimer();
    };

    const ensureApprovalAudioContext = () => {
        if (approvalAudioContext) {
            return approvalAudioContext;
        }
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) {
            return null;
        }
        approvalAudioContext = new AudioContextClass();
        return approvalAudioContext;
    };

    const playApprovalChime = () => {
        const context = ensureApprovalAudioContext();
        if (!context || context.state !== "running") {
            return false;
        }
        const notes = [
            {frequency: 880, start: 0, duration: 0.09, gain: 0.035},
            {frequency: 1320, start: 0.11, duration: 0.14, gain: 0.03},
        ];
        notes.forEach((note) => {
            const oscillator = context.createOscillator();
            const gainNode = context.createGain();
            oscillator.type = "sine";
            oscillator.frequency.value = note.frequency;
            gainNode.gain.setValueAtTime(0.0001, context.currentTime + note.start);
            gainNode.gain.exponentialRampToValueAtTime(
                note.gain,
                context.currentTime + note.start + 0.01
            );
            gainNode.gain.exponentialRampToValueAtTime(
                0.0001,
                context.currentTime + note.start + note.duration
            );
            oscillator.connect(gainNode);
            gainNode.connect(context.destination);
            oscillator.start(context.currentTime + note.start);
            oscillator.stop(context.currentTime + note.start + note.duration);
        });
        return true;
    };

    const flushQueuedApprovalChimes = () => {
        if (!approvalChimeQueued.size) {
            return;
        }
        const queuedToolCallIds = Array.from(approvalChimeQueued);
        queuedToolCallIds.forEach((toolCallId) => {
            if (!playApprovalChime()) {
                return;
            }
            approvalChimeQueued.delete(toolCallId);
            approvalChimePlayed.add(toolCallId);
        });
    };

    const unlockApprovalAudio = () => {
        const context = ensureApprovalAudioContext();
        if (!context) {
            return;
        }
        if (context.state === "running") {
            flushQueuedApprovalChimes();
            return;
        }
        const resumeResult = context.resume();
        if (resumeResult && typeof resumeResult.then === "function") {
            resumeResult
                .then(() => {
                    flushQueuedApprovalChimes();
                })
                .catch((error) => {
                    console.debug("[chat.js] approval audio resume failed", error);
                });
            return;
        }
        flushQueuedApprovalChimes();
    };

    const queueApprovalChime = (toolCallId) => {
        if (!toolCallId || approvalChimePlayed.has(toolCallId) || approvalChimeQueued.has(toolCallId)) {
            return;
        }
        if (playApprovalChime()) {
            approvalChimePlayed.add(toolCallId);
            return;
        }
        approvalChimeQueued.add(toolCallId);
    };

    ["pointerdown", "keydown", "touchstart"].forEach((eventName) => {
        document.addEventListener(eventName, unlockApprovalAudio, {passive: true});
    });

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

    const TERMINAL_RUN_STATUSES = new Set(["FAILED", "COMPLETED", "CANCELED"]);

    const shouldRotateRunForNextPrompt = () =>
        activeRunStatus === "WAITING_FOR_SUBRUN" || TERMINAL_RUN_STATUSES.has(activeRunStatus);

    const nextPromptRunRotationReason = () => {
        if (activeRunStatus === "WAITING_FOR_SUBRUN") {
            return "The previous run is still waiting on a subrun, so Maestro is starting a fresh run for this prompt.";
        }
        if (TERMINAL_RUN_STATUSES.has(activeRunStatus)) {
            return `The previous run is ${activeRunStatus.toLowerCase()}, so Maestro is starting a fresh run for this prompt.`;
        }
        return "";
    };

    const nextPromptRunRotationCode = () => {
        if (activeRunStatus === "WAITING_FOR_SUBRUN") {
            return "waiting_for_subrun";
        }
        if (activeRunStatus === "FAILED") {
            return "failed_run";
        }
        if (activeRunStatus === "COMPLETED") {
            return "completed_run";
        }
        if (activeRunStatus === "CANCELED") {
            return "canceled_run";
        }
        return "unexpected_result";
    };

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

    const runIdStorageKey = agentSlug ? `${RUN_ID_STORAGE_KEY_BASE}:${agentSlug}` : RUN_ID_STORAGE_KEY_BASE;

    const storeRunId = (runId) => {
        if (!sessionRunStorage || !runId) {
            return;
        }
        try {
            sessionRunStorage.setItem(runIdStorageKey, runId);
        } catch (error) {
            console.warn("Unable to persist run_id", error);
        }
    };

    const readStoredRunId = () => {
        if (!sessionRunStorage) {
            return null;
        }
        try {
            return sessionRunStorage.getItem(runIdStorageKey);
        } catch {
            return null;
        }
    };

    const clearStoredRunId = () => {
        if (!sessionRunStorage) {
            return;
        }
        try {
            sessionRunStorage.removeItem(runIdStorageKey);
        } catch (error) {
            console.warn("Unable to clear stored run_id", error);
        }
    };

    const ensureRunId = async ({fromRunId = "", rotationReason = ""} = {}) => {
        if (activeRunId) {
            log("ensureRunId: already have activeRunId =", activeRunId);
            return activeRunId;
        }
        const storedRun = readStoredRunId();
        if (storedRun && !fromRunId) {
            activeRunId = storedRun;
            log("ensureRunId: restored run_id from sessionStorage =", activeRunId);
            return activeRunId;
        }
        if (!runPreallocUrl) {
            log("ensureRunId: no preallocUrl available, will rely on server to set run_id");
            return null;
        }
        log("ensureRunId: requesting preallocated run_id via", runPreallocUrl);
        const preallocated = await requestPreallocatedRunId({fromRunId, rotationReason});
        if (preallocated) {
            activeRunId = preallocated.run_id || preallocated;
            storeRunId(activeRunId);
            log("ensureRunId: obtained preallocated run_id =", activeRunId);
        }
        return activeRunId;
    };

    const requestPreallocatedRunId = async ({fromRunId = "", rotationReason = ""} = {}) => {
        if (!runPreallocUrl) {
            return null;
        }
        const requestUrl = new URL(runPreallocUrl, window.location.origin);
        if (fromRunId) {
            requestUrl.searchParams.set("from_run_id", fromRunId);
        }
        if (rotationReason) {
            requestUrl.searchParams.set("rotation_reason", rotationReason);
        }
        log("requestPreallocatedRunId: requesting", requestUrl.toString());
        try {
            const response = await fetch(requestUrl.toString(), {
                credentials: "include",
                headers: {Accept: "application/json"},
            });
            if (!response.ok) {
                throw new Error(`Failed to preallocate run (${response.status})`);
            }
            const payload = await response.json();
            return payload || null;
        } catch (error) {
            console.warn("Unable to preallocate run", error);
            return null;
        }
    };

    const sendChatText = (text) => {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return false;
        }
        appendMessage({
            role: "operator",
            direction: "out",
            kind: userName,
            text,
            timestamp: new Date().toISOString(),
            author: userName,
        });
        markPromptSentNow();
        socket.send(JSON.stringify({type: "chat.message", text}));
        if (activeRunStatus === "RUNNING") {
            renderThinkingPlaceholder("thinking");
        }
        return true;
    };

    const rotateToFreshRun = async ({queuedText = null, reason = "", rotationCode = "unexpected_result"} = {}) => {
        const priorRunId = activeRunId;
        if (reason) {
            appendSystemMessage(reason, "run_control");
        }
        clearStoredRunId();
        activeRunId = null;
        queuedMessageAfterRunRotate = queuedText;
        const runId = await ensureRunId({fromRunId: priorRunId, rotationReason: rotationCode});
        if (!runId) {
            queuedMessageAfterRunRotate = null;
            appendSystemMessage("Unable to start a fresh run right now.", "error");
            return false;
        }
        connect();
        return true;
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
        const text = typeof payload?.text === "string" ? payload.text.toUpperCase().trim() : "";
        return (
            (payload?.role || "agent") === "system" &&
            (
                text.includes("[WS SEND]") ||
                text.includes("[WS RCV]") ||
                text.includes("[HTTP SEND]") ||
                text.includes("[HTTP RCV]")
            )
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

    let markdownRenderCounter = 0;

    const createMarkdownPrefix = () => `chat-md-${++markdownRenderCounter}`;

    const appendPlainText = (container, text, preserveLineBreaks = true) => {
        const value = String(text || "");
        if (!preserveLineBreaks || !value.includes("\n")) {
            container.append(document.createTextNode(value));
            return;
        }
        value.split("\n").forEach((part, index) => {
            if (index > 0) {
                container.append(document.createElement("br"));
            }
            if (part) {
                container.append(document.createTextNode(part));
            }
        });
    };

    const appendInlineMarkdown = (container, text, options = {}) => {
        const value = String(text || "");
        const tokenPattern = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\*([^*]+)\*)|(\[([^\]]+)\]\(([^)\s]+)\))|(\[\^([^\]]+)\])/g;
        let lastIndex = 0;
        let match = tokenPattern.exec(value);
        while (match) {
            if (match.index > lastIndex) {
                appendPlainText(container, value.slice(lastIndex, match.index), options.preserveLineBreaks !== false);
            }
            if (match[1]) {
                const strong = document.createElement("strong");
                appendInlineMarkdown(strong, match[2], options);
                container.append(strong);
            } else if (match[3]) {
                const code = document.createElement("code");
                code.textContent = match[4] || "";
                container.append(code);
            } else if (match[5]) {
                const em = document.createElement("em");
                appendInlineMarkdown(em, match[6], options);
                container.append(em);
            } else if (match[7]) {
                const href = String(match[9] || "").trim();
                const label = match[8] || href;
                if (/^(https?:|mailto:)/i.test(href)) {
                    const link = document.createElement("a");
                    link.href = href;
                    link.target = "_blank";
                    link.rel = "noopener noreferrer";
                    appendInlineMarkdown(link, label, options);
                    container.append(link);
                } else {
                    appendPlainText(container, match[0], options.preserveLineBreaks !== false);
                }
            } else if (match[10]) {
                const footnoteId = match[11] || "note";
                const sup = document.createElement("sup");
                sup.className = "chat-md-footnote-ref";
                const anchor = document.createElement("a");
                anchor.href = `#${options.footnotePrefix || "chat-md"}-footnote-${footnoteId}`;
                anchor.textContent = footnoteId;
                sup.append(anchor);
                container.append(sup);
            }
            lastIndex = tokenPattern.lastIndex;
            match = tokenPattern.exec(value);
        }
        if (lastIndex < value.length) {
            appendPlainText(container, value.slice(lastIndex), options.preserveLineBreaks !== false);
        }
    };

    const isTableSeparator = (line) => /^\s*\|?(\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/.test(line || "");

    const splitTableRow = (line) => {
        const normalized = String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "");
        return normalized.split("|").map((part) => part.trim());
    };

    const isBulletLine = (line) => /^\s*[-*+]\s+/.test(line || "");
    const isOrderedLine = (line) => /^\s*\d+\.\s+/.test(line || "");
    const isFootnoteLine = (line) => /^\[\^([^\]]+)\]:\s*(.*)$/.test(line || "");
    const isHeadingLine = (line) => /^\s{0,3}#{1,6}\s+/.test(line || "");
    const isFenceLine = (line) => /^```/.test(line || "");
    const isQuoteLine = (line) => /^\s*>\s?/.test(line || "");

    const isBlockStart = (line, nextLine) => {
        if (!line || !line.trim()) {
            return true;
        }
        return (
            isHeadingLine(line) ||
            isFenceLine(line) ||
            isBulletLine(line) ||
            isOrderedLine(line) ||
            isQuoteLine(line) ||
            isFootnoteLine(line) ||
            (line.includes("|") && isTableSeparator(nextLine || ""))
        );
    };

    const renderMarkdownInto = (container, text) => {
        container.textContent = "";
        container.classList.add("chat-markdown");
        const prefix = createMarkdownPrefix();
        const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
        const footnotes = [];
        let index = 0;

        const renderParagraph = (paragraphLines) => {
            const paragraph = document.createElement("p");
            appendInlineMarkdown(paragraph, paragraphLines.join("\n"), {footnotePrefix: prefix});
            container.append(paragraph);
        };

        while (index < lines.length) {
            const line = lines[index];
            const nextLine = index + 1 < lines.length ? lines[index + 1] : "";
            if (!line.trim()) {
                index += 1;
                continue;
            }
            if (isFootnoteLine(line)) {
                const match = line.match(/^\[\^([^\]]+)\]:\s*(.*)$/);
                const contentLines = [match?.[2] || ""];
                index += 1;
                while (index < lines.length) {
                    const continuation = lines[index];
                    if (!continuation.trim()) {
                        index += 1;
                        break;
                    }
                    if (/^\s{2,}|^\t/.test(continuation)) {
                        contentLines.push(continuation.trim());
                        index += 1;
                        continue;
                    }
                    break;
                }
                footnotes.push({id: match?.[1] || String(footnotes.length + 1), text: contentLines.join(" ")});
                continue;
            }
            if (isFenceLine(line)) {
                const language = line.replace(/^```/, "").trim();
                index += 1;
                const codeLines = [];
                while (index < lines.length && !isFenceLine(lines[index])) {
                    codeLines.push(lines[index]);
                    index += 1;
                }
                if (index < lines.length && isFenceLine(lines[index])) {
                    index += 1;
                }
                const pre = document.createElement("pre");
                const code = document.createElement("code");
                if (language) {
                    code.dataset.language = language;
                }
                code.textContent = codeLines.join("\n");
                pre.append(code);
                container.append(pre);
                continue;
            }
            if (isHeadingLine(line)) {
                const match = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
                const level = Math.min((match?.[1] || "#").length, 6);
                const heading = document.createElement(`h${level}`);
                appendInlineMarkdown(heading, match?.[2] || "", {footnotePrefix: prefix, preserveLineBreaks: false});
                container.append(heading);
                index += 1;
                continue;
            }
            if (line.includes("|") && isTableSeparator(nextLine)) {
                const table = document.createElement("table");
                const thead = document.createElement("thead");
                const tbody = document.createElement("tbody");
                const headerCells = splitTableRow(line);
                const headerRow = document.createElement("tr");
                headerCells.forEach((cellText) => {
                    const th = document.createElement("th");
                    appendInlineMarkdown(th, cellText, {footnotePrefix: prefix, preserveLineBreaks: false});
                    headerRow.append(th);
                });
                thead.append(headerRow);
                table.append(thead);
                index += 2;
                while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
                    const row = document.createElement("tr");
                    splitTableRow(lines[index]).forEach((cellText) => {
                        const td = document.createElement("td");
                        appendInlineMarkdown(td, cellText, {footnotePrefix: prefix, preserveLineBreaks: false});
                        row.append(td);
                    });
                    tbody.append(row);
                    index += 1;
                }
                table.append(tbody);
                container.append(table);
                continue;
            }
            if (isBulletLine(line) || isOrderedLine(line)) {
                const ordered = isOrderedLine(line);
                const list = document.createElement(ordered ? "ol" : "ul");
                while (index < lines.length) {
                    const current = lines[index];
                    if (!current.trim()) {
                        index += 1;
                        break;
                    }
                    if (ordered ? !isOrderedLine(current) : !isBulletLine(current)) {
                        break;
                    }
                    const item = document.createElement("li");
                    const markerPattern = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*+]\s+/;
                    const itemLines = [current.replace(markerPattern, "")];
                    index += 1;
                    while (index < lines.length) {
                        const continuation = lines[index];
                        if (!continuation.trim()) {
                            break;
                        }
                        if (ordered ? isOrderedLine(continuation) : isBulletLine(continuation)) {
                            break;
                        }
                        if (isBlockStart(continuation, index + 1 < lines.length ? lines[index + 1] : "")) {
                            break;
                        }
                        itemLines.push(continuation.trim());
                        index += 1;
                    }
                    appendInlineMarkdown(item, itemLines.join("\n"), {footnotePrefix: prefix});
                    list.append(item);
                }
                container.append(list);
                continue;
            }
            if (isQuoteLine(line)) {
                const quoteLines = [];
                while (index < lines.length && isQuoteLine(lines[index])) {
                    quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
                    index += 1;
                }
                const blockquote = document.createElement("blockquote");
                renderMarkdownInto(blockquote, quoteLines.join("\n"));
                container.append(blockquote);
                continue;
            }
            const paragraphLines = [line];
            index += 1;
            while (index < lines.length) {
                const current = lines[index];
                const lookahead = index + 1 < lines.length ? lines[index + 1] : "";
                if (!current.trim()) {
                    break;
                }
                if (isBlockStart(current, lookahead)) {
                    break;
                }
                paragraphLines.push(current);
                index += 1;
            }
            renderParagraph(paragraphLines);
        }

        if (footnotes.length) {
            const footnotesSection = document.createElement("section");
            footnotesSection.className = "chat-md-footnotes";
            const divider = document.createElement("hr");
            const list = document.createElement("ol");
            footnotes.forEach((footnote) => {
                const item = document.createElement("li");
                item.id = `${prefix}-footnote-${footnote.id}`;
                appendInlineMarkdown(item, footnote.text, {footnotePrefix: prefix});
                list.append(item);
            });
            footnotesSection.append(divider, list);
            container.append(footnotesSection);
        }
    };

    const setMessageBodyContent = (body, text, {markdown = true} = {}) => {
        if (!markdown) {
            body.classList.remove("chat-markdown");
            body.textContent = text || "";
            return;
        }
        renderMarkdownInto(body, text || "");
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

        const body = document.createElement("div");
        body.className = "chat-message-text";
        setMessageBodyContent(body, payload.text || "", {
            markdown: !isCollapsibleTransportSystemMessage(payload),
        });

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
            setMessageBodyContent(textEl, thinkingLabel(phase), {markdown: false});
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

    const buildToolFailureDetail = (payload, result) => {
        const parts = [];
        const rawStatus = String(payload?.status || "").trim();
        const exitCode = payload?.exit_code;
        const stderr = typeof payload?.stderr === "string" ? payload.stderr.trim() : "";
        const resultChildStatus = String(result?.child_status || "").trim();
        const childSummary = typeof result?.child_error_summary === "string" ? result.child_error_summary.trim() : "";
        const failureSummary = typeof result?.child_failure?.summary === "string" ? result.child_failure.summary.trim() : "";
        const recommendedAction = typeof result?.child_recommended_action === "string"
            ? result.child_recommended_action.trim()
            : "";

        if (rawStatus) {
            parts.push(`Tool status: ${rawStatus}`);
        }
        if (exitCode !== null && exitCode !== undefined && exitCode !== "") {
            parts.push(`Exit code: ${exitCode}`);
        }
        if (resultChildStatus) {
            parts.push(`Child status: ${resultChildStatus}`);
        }
        if (stderr) {
            parts.push(`Error: ${stderr}`);
        } else if (childSummary) {
            parts.push(`Error: ${childSummary}`);
        } else if (failureSummary) {
            parts.push(`Error: ${failureSummary}`);
        }
        if (recommendedAction) {
            parts.push(`Recommended action: ${recommendedAction}`);
        }
        return parts.join("\n");
    };

    const resolveToolDisplayState = (payload) => {
        const data = payload || {};
        const result = data.result && typeof data.result === "object" ? data.result : {};
        const rawStatus = String(data.status || "COMPLETED").trim();
        const normalizedStatus = getToolStatusKey(rawStatus);
        const exitCode = data.exit_code;
        const childStatus = getToolStatusKey(result.child_status || "");
        const childFailed = Boolean(result.child_failed) || childStatus === "FAILED" || childStatus === "CANCELED";
        const hasFailureCode = exitCode !== null && exitCode !== undefined && Number(exitCode) !== 0;
        const hasFailureStatus = normalizedStatus === "FAILED";
        const hasWarningStatus = normalizedStatus === "COMPLETED_WITH_WARNING";
        const failureDetail = buildToolFailureDetail(data, result);

        if (childFailed || hasFailureStatus || hasFailureCode) {
            return {
                statusText: "COMPLETED_WITH_FAILURE",
                detail: failureDetail || result || data.stderr || data.error || data.detail || "",
            };
        }
        if (hasWarningStatus) {
            return {
                statusText: "COMPLETED_WITH_WARNING",
                detail: failureDetail || result || data.stderr || data.error || data.detail || "",
            };
        }
        return {
            statusText: rawStatus || "COMPLETED",
            detail: result || data.stderr || data.error || data.detail || "",
        };
    };

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
        if (statusKey === "PENDING_APPROVAL") {
            queueApprovalChime(toolCallId);
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
        debug("tool_request run_id=", activeRunId);
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
        const display = resolveToolDisplayState(payload);
        updateToolCardStatus(
            payload.tool_call_id,
            display.statusText || payload.status || "Queued",
            display.detail || payload.error || payload.detail || payload.approval_note,
        );
    };

    const handleToolResult = (payload) => {
        const data = payload?.data ? payload.data : payload;
        debug("[tool_result DIAG]", {
            run_id: data?.run_id,
            tool_call_id: data?.tool_call_id,
            tool_name: data?.tool_name,
            status: data?.status,
            keys: data ? Object.keys(data) : null,
        });

        const display = resolveToolDisplayState(data);
        updateToolCardStatus(
            data.tool_call_id,
            display.statusText || data?.status || "COMPLETED",
            display.detail,
        );
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
            log("[RCV] type=", payload?.type, "activeRunId=", activeRunId);

        } catch (error) {
            warn("Unable to parse agent chat payload", error);
            return;
        }
        const isTransportLog =
            payload.type === "system" &&
            typeof payload.text === "string" &&
            (payload.text.includes("[WS") || payload.text.includes("[HTTP"));
        const transportTextUpper =
            typeof payload.text === "string" ? payload.text.toUpperCase() : "";
        if (isTransportLog && (
            transportTextUpper.includes("[WS RCV]") ||
            transportTextUpper.includes("[HTTP RCV]")
        )) {
            setThinkingPhase("typing");
        }

        switch (payload.type) {
            case "connected":
                syncRuntimeMetadata(payload);
                activeRunId = payload.run_id || activeRunId;
                log("Connected with run_id:", activeRunId);
                if (activeRunId) {
                    storeRunId(activeRunId);
                }
                setApprovalGrants(payload.approval_grants || []);
                setRunStatus(payload.run_status || activeRunStatus);
                if (payload.handoff) {
                    const handoffKey = `${payload.handoff.predecessor_run_id || ""}:${payload.handoff.rotation_reason || ""}`;
                    if (handoffKey && handoffKey !== lastHandoffNoticeKey) {
                        appendSystemMessage(payload.handoff.notice || "Continuing prior work in a successor run.", "run_control");
                        lastHandoffNoticeKey = handoffKey;
                    }
                }
                if (shouldRotateRunForNextPrompt()) {
                    clearStoredRunId();
                    const statusMessage = activeRunStatus === "WAITING_FOR_SUBRUN"
                        ? "This run is still waiting on a child run. Your next prompt will start a fresh run automatically."
                        : `This run is ${activeRunStatus.toLowerCase()}. Your next prompt will start a fresh run automatically.`;
                    appendSystemMessage(statusMessage, "run_control");
                } else if (queuedMessageAfterRunRotate) {
                    const queuedText = queuedMessageAfterRunRotate;
                    queuedMessageAfterRunRotate = null;
                    sendChatText(queuedText);
                }
                setStatus("Connected");
                if (textarea) {
                    textarea.removeAttribute("disabled");
                }
                return;
            case "message":
                appendMessage({
                    role: payload.role || "assistant",
                    direction: payload.direction || "in",
                    author: payload.author,
                    text: payload.text || "",
                    timestamp: payload.timestamp,
                });
                log("[MESSAGE] Message appended");
                return;
            case "tool_request":
                handleToolRequest(payload);
                setStatus("Tool requested");
                log("[TOOL_REQUEST] tool_request run_id=", activeRunId, payload);
                return;

            case "debug_group_echo":
                log("[debug_group_echo] received:", payload);
                return;
            case "tool_call_completed":
                log("[tool_call_completed] DIAG:", payload);
                return;
            case "tool_status":
                handleToolStatus(payload.data || payload);
                log("[tool_status] run_id=", activeRunId, payload);
                return;
            case "tool_result":
                handleToolResult(payload);
                setStatus("Connected");
                log("[tool_result] run_id=", activeRunId, payload);
                return;
            case "tool_denied":
                handleToolDenied(payload);
                setStatus("Connected");
                log("[tool_denied] run_id=", activeRunId, payload);
                return;
            case "approval_grants":
                setApprovalGrants(payload.grants || []);
                log("[approval_grants] run_id=", activeRunId, payload);
                return;
            case "pause_run_ack":
                setRunStatus(payload.status || "PAUSED");
                appendSystemMessage("Run paused. Messages will queue until you resume.", "run_control");
                log("[pause_run_ack] run_id=", activeRunId, payload);
                return;
            case "resume_run_ack":
                setRunStatus(payload.status || "RUNNING");
                appendSystemMessage("Run resumed.", "run_control");
                log("[resume_run_ack] run_id=", activeRunId, payload);
                return;
            case "cancel_run_ack":
                setRunStatus(payload.status || "CANCELED");
                appendSystemMessage("Run canceled.", "run_control");
                log("[cancel_run_ack] run_id=", activeRunId, payload);
                return;
            case "state_changed": {
                const nextStatus = payload.data?.to || payload.to || payload.status;
                if (nextStatus) {
                    setRunStatus(nextStatus);
                    if (shouldRotateRunForNextPrompt()) {
                        clearStoredRunId();
                    }
                }
                log("[state_changed] run_id=", activeRunId, payload);
                return;
            }
            case "error":
            case "tool_error":
                appendMessage({
                    role: "system",
                    direction: "in",
                    text: payload.message || "An error occurred.",
                    timestamp: payload.timestamp,
                    kind: "error",
                });
                setRunStatus("FAILED");
                clearStoredRunId();
                setStatus("Connected");
                log("[ERROR payload]", payload);
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
                log("[SYSTEM payload]", payload);
                return;
            default:
                log("[UNHANDLED message]", payload);
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
            log("[CLOSE] Previous transport was open. Closing before reconnect.")
            socket.close();
        }
        setStatus("Connecting…");
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        const runQuery = activeRunId
            ? `${wsUrl.includes("?") ? "&" : "?"}run=${encodeURIComponent(activeRunId)}`
            : "";
        const url = `${protocol}://${window.location.host}${wsUrl}${runQuery}`;
        log("Transport connecting url =", url, "activeRunId =", activeRunId);
        log("[CREATE] Creating new socket", {url, run_id: activeRunId});
        socket = new WebSocket(url);
        socket.onclose = (event) => {
            warn("[CLOSE]", {
                code: event.code,
                reason: event.reason,
                wasClean: event.wasClean,
                readyState: socket.readyState,
                url: socket.url,
                run_id: activeRunId,
            });
        };
        socket.onerror = (event) => {
            error("[ERROR]", {
                event,
                readyState: socket.readyState,
                url: socket.url,
                run_id: activeRunId,
            });
        };
        socket.addEventListener("open", () => {
            log("[OPEN]", {url: socket.url, run_id: activeRunId});
            setStatus("Connected");
            if (textarea) {
                textarea.removeAttribute("disabled");
            }
            log("Transport open event activeRunId =", activeRunId);
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
            log("Transport close event code=", event.code, "reason=", event.reason, "activeRunId =", activeRunId);
            warn("[CLOSE]", {
                code: event.code,
                reason: event.reason,
                wasClean: event.wasClean,
                readyState: socket.readyState,
                url: socket.url,
            });
        });
        socket.addEventListener("error", () => {
            setStatus("Connection error");
            log("Transport error event activeRunId =", activeRunId);
            error("[ERROR]", {url: socket.url, readyState: socket.readyState, run_id: activeRunId});
        });
    };

    const initializeRunAndConnect = async () => {
        await ensureRunId();
        connect();
    };

    const sendMessage = async () => {
        const text = textarea?.value?.trim();
        if (!text) {
            return;
        }
        if (shouldRotateRunForNextPrompt()) {
            if (textarea) {
                textarea.value = "";
            }
            await rotateToFreshRun({
                queuedText: text,
                reason: nextPromptRunRotationReason(),
                rotationCode: nextPromptRunRotationCode(),
            });
            return;
        }
        if (!sendChatText(text)) {
            return;
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
            warn("[CLOSE] Closing socket.")
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
        warn("[PAGE beforeunload]");
        if (socket && socket.readyState === WebSocket.OPEN) {
            warn("[CLOSE] Closing socket prior to unloading window.")
            socket.close();
        }
    });
    window.addEventListener("pagehide", () => {
        warn("[PAGE pagehide]");
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
