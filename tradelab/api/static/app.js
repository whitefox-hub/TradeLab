/* Shared navigation and session handling. Plan.md §20.3.
   The sidebar is built here rather than pasted into each page, so a screen that
   ships later appears in the menu by editing one list. */

const SCREENS = [
  { group: "Research" },
  { href: "/home", label: "Home" },
  { href: "/algorithm", label: "Algorithm" },
  { href: "/market-sentiment", label: "Market Sentiment" },
  { href: "/macro", label: "Macro" },
  { href: "/parameters", label: "Parameters" },
  { group: "Lifecycle" },
  { href: "/strategies", label: "Strategies" },
  { href: "/experiments", label: "Experiments" },
  { label: "Approvals", soon: true },
  { group: "Operations" },
  { href: "/data-quality", label: "Data quality" },
  { href: "/audit", label: "Audit" },
  { label: "Runs", soon: true },
  { label: "Risk limits", soon: true },
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

/* Every screen calls this. It draws the menu, resolves the session, and sends an
   unauthenticated visitor to sign in with the path they wanted — rather than
   showing a page whose every request is about to 401. */
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
    throw new Error((await response.json()).detail || response.statusText);
  }
  return response.json();
}

const $ = (id) => document.getElementById(id);
