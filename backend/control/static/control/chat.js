(() => {
  const messageContainer = document.getElementById("chatMessages");
  if (!messageContainer) return;
  const conversationUuid = messageContainer.dataset.conversationUuid;
  if (!conversationUuid) return;

  const renderMessage = (payload) => {
    if (!payload?.direction) return;
    const authorType = payload.author_type || "agent";
    const direction = payload.direction || "in";
    const article = document.createElement("article");
    article.className = `chat-message chat-message-${direction} chat-message-author-${authorType}`;

    const meta = document.createElement("div");
    meta.className = "chat-message-meta";
    const author = document.createElement("span");
    author.className = "chat-message-author";
    author.textContent = payload.author_label || "agent";
    const kind = document.createElement("span");
    kind.className = "chat-message-type";
    kind.textContent = payload.author_type || "agent";
    const timestamp = document.createElement("time");
    if (payload.created_at) {
      const timeString = new Date(payload.created_at).toLocaleTimeString("en-US", {
        timeZone: "America/New_York",
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
      timestamp.textContent = `${timeString} EST`;
    } else {
      timestamp.textContent = "now";
    }
    meta.append(author, kind, timestamp);

    const body = document.createElement("p");
    body.className = "chat-message-text";
    body.textContent = payload.text || "";

    article.append(meta, body);
    messageContainer.appendChild(article);
    messageContainer.scrollTop = messageContainer.scrollHeight;
  };

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socketUrl = `${protocol}://${window.location.host}/ws/ui/chat/${conversationUuid}/`;
  const socket = new WebSocket(socketUrl);
  socket.addEventListener("message", (event) => {
    try {
      const payload = JSON.parse(event.data);
      renderMessage(payload);
    } catch (error) {
      console.warn("Unable to parse control chat message", error);
    }
  });

  window.addEventListener("beforeunload", () => {
    if (socket.readyState === WebSocket.OPEN) {
      socket.close();
    }
  });
})();
