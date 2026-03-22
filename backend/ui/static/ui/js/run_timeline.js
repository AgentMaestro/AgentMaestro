(() => {
    const page = document.querySelector("[data-run-timeline-page]");
    if (!page) {
        return;
    }

    const config = window.AgentMaestroRunTimeline || {};
    const snapshotUrl = String(config.snapshotUrl || "").trim();
    const timelineStrip = document.getElementById("timelineStrip");
    const stepSlider = document.getElementById("stepSlider");
    const timelinePosition = document.getElementById("timelinePosition");
    const detailRail = document.getElementById("detailRail");
    const windowLabel = document.getElementById("windowLabel");

    const state = {
        run: null,
        steps: [],
        stepByIndex: new Map(),
        selectedIndex: 0,
    };

    const createEl = (tag, className = "", text = "") => {
        const el = document.createElement(tag);
        if (className) {
            el.className = className;
        }
        if (text !== undefined && text !== null && text !== "") {
            el.textContent = text;
        }
        return el;
    };

    const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

    const formatTimestamp = (value) => {
        if (!value) {
            return "—";
        }
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
    };

    const toPlainText = (value) => {
        if (value === null || value === undefined || value === "") {
            return "";
        }
        if (typeof value === "string") {
            return value.trim();
        }
        if (typeof value === "number" || typeof value === "boolean") {
            return String(value);
        }
        try {
            return JSON.stringify(value);
        } catch {
            return String(value);
        }
    };

    const truncate = (value, maxLength = 220) => {
        const text = toPlainText(value);
        if (!text) {
            return "";
        }
        return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
    };

    const setStatusLine = (text) => {
        if (timelinePosition) {
            timelinePosition.textContent = text;
        }
    };

    const setWindowLine = (text) => {
        if (windowLabel) {
            windowLabel.textContent = text;
        }
    };

    const normalizeStep = (step) => {
        const payload = step && typeof step.payload === "object" && step.payload !== null ? step.payload : {};
        return {
            ...step,
            step_index: Number(step?.step_index || 0),
            kind: String(step?.kind || "UNKNOWN").toUpperCase(),
            payload,
            created_at: step?.created_at || "",
            updated_at: step?.updated_at || "",
        };
    };

    const getRole = (step) => {
        const payload = step.payload || {};
        return String(payload.role || payload.author || payload.direction || "").trim().toLowerCase();
    };

    const getStatus = (step) => {
        const payload = step.payload || {};
        return String(payload.status || payload.state || payload.result?.status || "").trim().toUpperCase();
    };

    const isFailure = (step) => {
        const payload = step.payload || {};
        const status = getStatus(step);
        const result = payload.result || {};
        return Boolean(
            payload.error ||
            payload.error_summary ||
            payload.failure ||
            result.error ||
            result.error_summary ||
            result.failure ||
            status.includes("FAIL") ||
            status.includes("ERROR") ||
            status === "DENIED"
        );
    };

    const toneForStep = (step) => {
        const kind = step.kind;
        const role = getRole(step);
        if (kind === "TOOL_CALL") {
            return isFailure(step) ? "failure" : "success";
        }
        if (kind === "MESSAGE") {
            if (role === "user" || role === "operator" || role === "scott") {
                return "user";
            }
            if (role === "system") {
                return "system";
            }
            return "assistant";
        }
        if (kind === "MODEL_CALL" || kind === "OBSERVATION" || kind === "ACTION" || kind === "SUBRUN_SPAWN") {
            return "system";
        }
        if (kind === "FINAL") {
            return isFailure(step) ? "failure" : "success";
        }
        return "info";
    };

    const summarizeStep = (step) => {
        const payload = step.payload || {};
        const kind = step.kind;
        if (kind === "MESSAGE") {
            return truncate(
                payload.text ||
                    payload.content ||
                    payload.message ||
                    payload.body ||
                    payload.summary_text ||
                    "",
                320
            );
        }
        if (kind === "TOOL_CALL") {
            return truncate(
                payload.summary_text ||
                    payload.tool_name ||
                    payload.tool ||
                    payload.operation ||
                    payload.error ||
                    payload.result?.summary_text ||
                    "",
                320
            );
        }
        if (kind === "MODEL_CALL") {
            return truncate(
                payload.description ||
                    payload.summary_text ||
                    payload.model ||
                    payload.prompt ||
                    "",
                320
            );
        }
        if (kind === "OBSERVATION") {
            return truncate(
                payload.description ||
                    payload.summary_text ||
                    payload.result?.summary_text ||
                    payload.result ||
                    "",
                320
            );
        }
        if (kind === "ACTION") {
            return truncate(
                payload.summary_text ||
                    [payload.resource_kind, payload.action_kind, payload.operation].filter(Boolean).join(" "),
                320
            );
        }
        if (kind === "SUBRUN_SPAWN") {
            return truncate(payload.join_policy || payload.failure_policy || payload.child_run_id || "", 320);
        }
        if (kind === "FINAL") {
            return truncate(
                payload.final_text ||
                    payload.summary_text ||
                    payload.error_summary ||
                    payload.status ||
                    "",
                320
            );
        }
        return truncate(
            payload.summary_text ||
                payload.description ||
                payload.text ||
                payload.message ||
                payload.result ||
                "",
            320
        );
    };

    const collectMeta = (step) => {
        const payload = step.payload || {};
        const meta = [];
        const push = (label, value) => {
            const text = truncate(value, 120);
            if (text) {
                meta.push({label, value: text});
            }
        };

        push("Created", formatTimestamp(step.created_at));
        push("Correlation", step.correlation_id);
        push("Step ID", step.id);

        if (step.kind === "MESSAGE") {
            push("Role", getRole(step));
            push("Author", payload.author || payload.role || "");
            push("Direction", payload.direction || "");
        } else if (step.kind === "TOOL_CALL") {
            push("Tool", payload.tool_name || payload.tool || "");
            push("Call ID", payload.tool_call_id || payload.provider_call_id || "");
            push("Status", payload.status || payload.result?.status || "");
        } else if (step.kind === "MODEL_CALL") {
            push("Model", payload.model || "");
            push("Provider", payload.provider || "");
        } else if (step.kind === "ACTION") {
            push("Resource", payload.resource_kind || "");
            push("Action", payload.action_kind || "");
            push("Operation", payload.operation || "");
        } else if (step.kind === "SUBRUN_SPAWN") {
            push("Child", payload.child_run_id || "");
            push("Join", payload.join_policy || "");
            push("Failure", payload.failure_policy || "");
        } else if (step.kind === "FINAL") {
            push("Status", payload.status || "");
        }

        return meta;
    };

    const renderStepButton = (step) => {
        const button = createEl("button", "timeline-step");
        button.type = "button";
        button.dataset.stepIndex = String(step.step_index);
        button.dataset.tone = toneForStep(step);
        if (step.step_index === state.selectedIndex) {
            button.classList.add("is-active");
        }

        const label = createEl("span", "timeline-step-label", `Step ${step.step_index}`);
        const kind = createEl("span", "timeline-step-kind", step.kind);
        button.append(label, kind);
        button.title = `${step.kind} · ${summarizeStep(step) || "No summary"}`;
        button.addEventListener("click", () => {
            setSelectedIndex(step.step_index);
        });
        return button;
    };

    const renderTimelineStrip = () => {
        if (!timelineStrip) {
            return;
        }
        timelineStrip.textContent = "";
        state.steps.forEach((step) => {
            timelineStrip.append(renderStepButton(step));
        });
    };

    const renderDetailCard = (step, offset) => {
        const card = createEl("article", "detail-card");
        if (!step) {
            card.classList.add("empty");
            const header = createEl("div", "detail-card-header");
            const title = createEl("div", "detail-card-title", offset === 0 ? "Focus" : `Offset ${offset > 0 ? `+${offset}` : offset}`);
            const status = createEl("span", "detail-card-status", "No step");
            header.append(title, status);
            const body = createEl("div", "detail-card-body");
            body.append(createEl("p", "detail-card-summary", "No AgentStep exists at this position."));
            card.append(header, body);
            return card;
        }

        card.dataset.tone = toneForStep(step);
        if (offset === 0) {
            card.classList.add("current");
        }

        const header = createEl("div", "detail-card-header");
        const titleBlock = createEl("div");
        titleBlock.append(
            createEl("div", "detail-card-title", `${offset === 0 ? "Focus" : `Offset ${offset > 0 ? `+${offset}` : offset}`} · Step ${step.step_index}`),
            createEl("div", "detail-card-subtitle", step.kind)
        );

        const statusText = step.kind === "TOOL_CALL"
            ? (isFailure(step) ? "Failure" : "Success")
            : toneForStep(step);
        const status = createEl("span", "detail-card-status", statusText);
        header.append(titleBlock, status);

        const body = createEl("div", "detail-card-body");
        body.append(createEl("p", "detail-card-summary", summarizeStep(step) || "No summary available."));

        const meta = createEl("div", "detail-meta");
        collectMeta(step).forEach((entry) => {
            const row = createEl("div", "detail-meta-row");
            row.append(
                createEl("div", "detail-meta-key", entry.label),
                createEl("div", "detail-meta-value", entry.value)
            );
            meta.append(row);
        });
        body.append(meta);

        const payload = createEl("details", "payload-details");
        const payloadSummary = createEl("summary", "", "Payload JSON");
        const payloadPre = createEl("pre", "", JSON.stringify(step.payload || {}, null, 2));
        payload.append(payloadSummary, payloadPre);
        body.append(payload);

        card.append(header, body);
        return card;
    };

    const renderFocusedWindow = () => {
        if (!detailRail) {
            return;
        }
        detailRail.textContent = "";
        const selected = state.stepByIndex.get(state.selectedIndex) || null;
        const total = state.steps.length;
        if (!selected) {
            detailRail.append(createEl("div", "empty-state", "No AgentSteps were found for this run."));
            setWindowLine("No step selected.");
            return;
        }
        setWindowLine(`Focused step ${state.selectedIndex} of ${total}`);
        for (let offset = -3; offset <= 3; offset += 1) {
            detailRail.append(renderDetailCard(state.stepByIndex.get(state.selectedIndex + offset) || null, offset));
        }
    };

    const renderAll = () => {
        renderTimelineStrip();
        renderFocusedWindow();
        if (stepSlider) {
            stepSlider.disabled = state.steps.length === 0;
            if (state.steps.length > 0) {
                stepSlider.min = String(state.steps[0].step_index);
                stepSlider.max = String(state.steps[state.steps.length - 1].step_index);
                stepSlider.value = String(state.selectedIndex);
            }
        }
        setStatusLine(state.steps.length > 0 ? `Loaded ${state.steps.length} AgentSteps.` : "No AgentSteps recorded yet.");
    };

    const setSelectedIndex = (nextIndex) => {
        if (!state.steps.length) {
            state.selectedIndex = 0;
            renderAll();
            return;
        }
        const minIndex = state.steps[0].step_index;
        const maxIndex = state.steps[state.steps.length - 1].step_index;
        state.selectedIndex = clamp(Number(nextIndex) || minIndex, minIndex, maxIndex);
        renderAll();
    };

    const hydrate = (snapshot) => {
        state.run = snapshot?.run || {};
        state.steps = Array.isArray(snapshot?.steps)
            ? snapshot.steps.map(normalizeStep).sort((a, b) => a.step_index - b.step_index)
            : [];
        state.stepByIndex = new Map(state.steps.map((step) => [step.step_index, step]));

        const currentStep = Number(state.run.current_step_index || 0);
        if (state.steps.length) {
            const maxIndex = state.steps[state.steps.length - 1].step_index;
            const minIndex = state.steps[0].step_index;
            state.selectedIndex = clamp(currentStep || maxIndex, minIndex, maxIndex);
        } else {
            state.selectedIndex = 0;
        }
        renderAll();
    };

    const loadSnapshot = async () => {
        if (!snapshotUrl) {
            throw new Error("Missing snapshotUrl");
        }
        const response = await fetch(snapshotUrl, {
            credentials: "same-origin",
            headers: {Accept: "application/json"},
        });
        if (!response.ok) {
            throw new Error(`Snapshot request failed (${response.status})`);
        }
        return response.json();
    };

    const renderError = (error) => {
        if (detailRail) {
            detailRail.textContent = "";
            detailRail.append(
                createEl(
                    "div",
                    "error-state",
                    `Unable to load the run snapshot. ${error?.message || error || "Unknown error."}`
                )
            );
        }
        setStatusLine("Snapshot load failed.");
        setWindowLine("No data available.");
    };

    if (stepSlider) {
        stepSlider.addEventListener("input", (event) => {
            const value = Number(event.target.value);
            if (!Number.isNaN(value)) {
                setSelectedIndex(value);
            }
        });
    }

    loadSnapshot().then(hydrate).catch(renderError);
})();
