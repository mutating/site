export function tabNotifications(name: string, receive: (message: unknown) => void) {
  if (typeof window.BroadcastChannel === "function") {
    const channel = new window.BroadcastChannel(name);
    channel.addEventListener("message", (event) => receive(event.data));
    return (message: unknown) => channel.postMessage(message);
  }

  window.addEventListener?.("storage", (event) => {
    if (event.key !== name || !event.newValue) return;
    try { receive(JSON.parse(event.newValue)); } catch { /* Ignore invalid messages. */ }
  });
  return (message: unknown) => {
    try {
      localStorage.setItem(name, JSON.stringify(message));
      localStorage.removeItem(name);
    } catch { /* Cross-tab storage can be disabled by the browser. */ }
  };
}
