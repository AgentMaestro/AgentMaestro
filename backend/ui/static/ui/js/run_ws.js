// Minimal run WebSocket client for early scaffolding.
// Connects to /ws/ui/run/<run_id>/
// Logs all inbound messages and exposes helpers on window.AgentMaestroWS.
(function () {
  function buildWsUrl(path) {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}${path}`;
  }

  function connectRunWS(runId) {
    if (!runId) {
      console.warn("[run_ws] runId is required.");
      return null;
    }
    const url = buildWsUrl(`/ws/ui/run/${encodeURIComponent(runId)}/`);
    const ws = new WebSocket(url);

    ws.onopen = () => {
      console.log("[run_ws] connected:", url);
      // ping
      ws.send(JSON.stringify({ type: "cmd", cmd: "ping", data: { hello: "run" } }));
      // request snapshot (stubbed server-side until DB is ready)
      ws.send(JSON.stringify({ type: "cmd", cmd: "request_snapshot", since_seq: 0 }));
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        console.log("[run_ws] message:", msg);
      } catch (e) {
        console.log("[run_ws] message (raw):", evt.data);
      }
    };

    ws.onclose = (evt) => {
      console.log("[run_ws] closed:", evt.code, evt.reason);
    };

    ws.onerror = (err) => {
      console.error("[run_ws] error:", err);
    };

    return ws;
  }

  // Helpers for command sending (approve/cancel/retry stubs for now).
  function sendApproveToolCall(ws, toolCallId) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn("[run_ws] WS not open.");
      return;
    }
    ws.send(JSON.stringify({ type: "cmd", cmd: "approve_tool_call", tool_call_id: toolCallId }));
  }

function sendCancelRun(ws, runId) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("[run_ws] WS not open.");
    return;
  }
  ws.send(JSON.stringify({ type: "cmd", cmd: "cancel_run", run_id: runId }));
}

function sendPauseRun(ws) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("[run_ws] WS not open.");
    return;
  }
  ws.send(JSON.stringify({ type: "cmd", cmd: "pause_run" }));
}

function sendResumeRun(ws) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("[run_ws] WS not open.");
    return;
  }
  ws.send(JSON.stringify({ type: "cmd", cmd: "resume_run" }));
}

function sendSpawnSubrun(ws, prompt, options = {}) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("[run_ws] WS not open.");
    return;
  }
  const payload = { type: "cmd", cmd: "spawn_subrun" };
  if (prompt) {
    payload.input_text = prompt;
  }
  if (options && typeof options === "object" && Object.keys(options).length) {
    payload.options = options;
  }
  ws.send(JSON.stringify(payload));
}

function sendRetryRun(ws, runId) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("[run_ws] WS not open.");
    return;
  }
  ws.send(JSON.stringify({ type: "cmd", cmd: "retry_run", run_id: runId }));
}

window.AgentMaestroWS = window.AgentMaestroWS || {};
window.AgentMaestroWS.connectRun = connectRunWS;
window.AgentMaestroWS.approveToolCall = sendApproveToolCall;
window.AgentMaestroWS.cancelRun = sendCancelRun;
window.AgentMaestroWS.pauseRun = sendPauseRun;
window.AgentMaestroWS.resumeRun = sendResumeRun;
window.AgentMaestroWS.spawnSubrun = sendSpawnSubrun;
window.AgentMaestroWS.retryRun = sendRetryRun;
})();
