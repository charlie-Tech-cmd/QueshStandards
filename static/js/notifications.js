(() => {
  const badge = document.querySelector(".notification-pill");
  if (!badge || !("WebSocket" in window)) return;
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  let socket;
  let delay = 1000;

  const showToast = item => {
    let stack = document.querySelector(".live-toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "live-toast-stack";
      stack.setAttribute("aria-live", "polite");
      document.body.append(stack);
    }
    const toast = document.createElement("div");
    toast.className = "live-toast";
    const title = document.createElement("strong");
    title.textContent = item.title;
    const body = document.createElement("p");
    body.textContent = item.body;
    toast.append(title, body);
    const target = new URL(item.url || "/notifications/", window.location.origin);
    if (target.origin === window.location.origin) {
      const link = document.createElement("a");
      link.href = `${target.pathname}${target.search}${target.hash}`;
      link.textContent = "Open notification →";
      toast.append(link);
    }
    stack.append(toast);
    window.setTimeout(() => toast.remove(), 8000);
    const count = Number((badge.textContent.match(/\d+/) || ["0"])[0]) + 1;
    badge.textContent = `● ${count}`;
  };

  const connect = () => {
    socket = new WebSocket(`${scheme}//${window.location.host}/ws/notifications/`);
    socket.addEventListener("open", () => { delay = 1000; });
    socket.addEventListener("message", event => {
      const data = JSON.parse(event.data);
      if (data.type === "notification.created") showToast(data);
    });
    socket.addEventListener("close", () => {
      window.setTimeout(connect, delay);
      delay = Math.min(delay * 2, 30000);
    });
  };
  connect();
})();
