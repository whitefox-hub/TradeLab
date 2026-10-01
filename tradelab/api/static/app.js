
const SCREENS = [
  { href: "/home", label: "Home" },
  { href: "/algorithm", label: "Algorithm" },
  { href: "/market-sentiment", label: "Market Sentiment" },
  { href: "/parameters", label: "Parameters" },
  { group: "Operations" },
  { href: "/audit", label: "Audit" },
  { href: "/risk-limits", label: "Risk limits" },
];

function renderSidebar(active) {
  const items = SCREENS.map((entry) => {
    if (entry.group) return `<div class="group">${entry.group}</div>`;
    if (entry.soon) {
      return `<span class="soon">${entry.label}<span class="tag">soon</span></span>`;
    }
    const on = entry.href === active ? ' class="on"' : "";
    return `<a href="${entry.href}"${on}>${entry.label}</a>`;
  }).join("");

  return `
    <aside class="side">
      <a href="/home" class="brand">TradeLab</a>
      <nav>${items}</nav>
      <div class="foot">
        <span class="email" id="who">—</span>
        <button class="ghost" id="bye" hidden>Sign out</button>
      </div>
    </aside>`;
}

async function mount(active, { onReady } = {}) {
  document.querySelector(".layout")
    .insertAdjacentHTML("afterbegin", renderSidebar(active));

  document.getElementById("bye").onclick = async () => {
    await fetch("/api/session", { method: "DELETE", credentials: "same-origin" });
    location.href = "/login";
  };

  const response = await fetch("/api/me", { credentials: "same-origin" });
  if (!response.ok) {
    location.replace("/login?next=" + encodeURIComponent(active));
    return null;
  }
  const me = await response.json();
  document.getElementById("who").textContent = me.email;
  document.getElementById("bye").hidden = false;
  if (onReady) onReady(me);
  return me;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, ...options,
  });
  if (response.status === 401) {
    location.replace("/login?next=" + encodeURIComponent(location.pathname));
    throw new Error("unauthenticated");
  }
  if (!response.ok) {
    const detail = (await response.json()).detail;
    const text = Array.isArray(detail)
      ? detail.map((d) => `${(d.loc || []).filter((p) => p !== "body").join(".")}: ${d.msg}`)
          .join("; ")
      : detail;
    throw new Error(text || response.statusText);
  }
  return response.json();
}

const $ = (id) => document.getElementById(id);

const PER_PAGE = 100;

async function apiPage(path) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (response.status === 401) {
    location.replace("/login?next=" + encodeURIComponent(location.pathname));
    throw new Error("unauthenticated");
  }
  if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
  return { items: await response.json(),
           total: Number(response.headers.get("X-Total-Count") || 0) };
}

function pager(id, page, total, onPage) {
  const el = $(id);
  if (!el) return;
  const pages = Math.max(1, Math.ceil(total / PER_PAGE));
  if (total <= PER_PAGE) { el.innerHTML = total ? `<span class="pages">${total} records</span>` : ""; return; }
  el.innerHTML = `
    <button class="ghost" ${page <= 1 ? "disabled" : ""} data-go="${page - 1}">‹ Previous</button>
    <span class="pages">Page ${page} of ${pages} · ${total.toLocaleString()} records</span>
    <button class="ghost" ${page >= pages ? "disabled" : ""} data-go="${page + 1}">Next ›</button>`;
  el.querySelectorAll("[data-go]").forEach((b) => { b.onclick = () => onPage(Number(b.dataset.go)); });
}
