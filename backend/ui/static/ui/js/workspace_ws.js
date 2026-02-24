// Minimal workspace WebSocket client for early scaffolding.
// Connects to /ws/ui/workspace/?workspace_id=<uuid>
// Logs all inbound messages and exposes helpers on window.AgentMaestroWS.
(function () {
  function buildWsUrl(pathWithQuery) {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}${pathWithQuery}`;
  }

  function connectWorkspaceWS(workspaceId, { autoSubscribeApprovals = false } = {}) {
    if (!workspaceId) {
      console.warn("[workspace_ws] workspaceId is required.");
      return null;
    }
    const url = buildWsUrl(`/ws/ui/workspace/?workspace_id=${encodeURIComponent(workspaceId)}`);
    const ws = new WebSocket(url);

    ws.onopen = () => {
      console.log("[workspace_ws] connected:", url);
      if (autoSubscribeApprovals) {
        ws.send(JSON.stringify({ type: "cmd", cmd: "subscribe_approvals" }));
      }
      // basic ping
      ws.send(JSON.stringify({ type: "cmd", cmd: "ping", data: { hello: "workspace" } }));
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        console.log("[workspace_ws] message:", msg);
      } catch (e) {
        console.log("[workspace_ws] message (raw):", evt.data);
      }
    };

    ws.onclose = (evt) => {
      console.log("[workspace_ws] closed:", evt.code, evt.reason);
    };

    ws.onerror = (err) => {
      console.error("[workspace_ws] error:", err);
    };

    return ws;
  }

  // Expose helpers globally for quick testing in browser console.
  window.AgentMaestroWS = window.AgentMaestroWS || {};
  window.AgentMaestroWS.connectWorkspace = connectWorkspaceWS;
})();