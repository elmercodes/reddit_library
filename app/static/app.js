// Reddit Library frontend: hash routing, Markdown reading, per-tab chat with model picker.
const $ = (sel) => document.querySelector(sel);
const state = { tree: null, models: [], route: null, chatTab: null, sending: false };

// ---------- helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
function md(text) {
  return DOMPurify.sanitize(marked.parse(text || ""), { ADD_ATTR: ["target"] });
}
function money(x) { return "$" + (x || 0).toFixed(x < 0.01 ? 4 : 3); }
function tokens(n) { return n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n || 0); }
function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

// Rewrite links and images inside rendered Markdown for the app.
function fixLinks(container, book) {
  container.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href");
    const ch = href.match(/^chapters\/([\w-]+)\.md$/);
    if (ch && book) a.setAttribute("href", `#/book/${book}/${ch[1]}`);
    else if (/^https?:/.test(href)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
  });
  container.querySelectorAll("img[src]").forEach((img) => {
    const src = img.getAttribute("src");
    const m = src.match(/data\/images\/(.+)$/);
    if (m) img.src = "/data/images/" + m[1];
    img.loading = "lazy";
    img.alt = img.alt || "Post image";
    img.addEventListener("click", () => img.classList.toggle("big"));
  });
}

// ---------- sidebar ----------
async function loadTree() {
  state.tree = await api("/api/tree");
  const games = $("#games");
  games.innerHTML = state.tree.games.map((g) =>
    `<li><a href="#/game/${g.id}" data-tab="game:${g.id}">${escapeHtml(g.title)}</a></li>`).join("");
  $("#library").innerHTML = state.tree.library.map((b) => {
    if (b.id === "master") return `<li><a href="#/master" data-tab="master" class="soon">Master Book <span class="badge">soon</span></a></li>`;
    if (b.status === "ready") return `<li><a href="#/book/${b.id}" data-tab="book:${b.id}">${escapeHtml(b.title)}</a></li>`;
    return `<li><a href="#/book/${b.id}" data-tab="book:${b.id}" class="disabled">${escapeHtml(b.title)} <span class="badge">not built</span></a></li>`;
  }).join("");
}
function markActive(tab) {
  document.querySelectorAll(".sidebar a[data-tab]").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
}

// ---------- routing ----------
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "book" && parts[1]) return { kind: "book", book: parts[1], chapter: parts[2] || null };
  if (parts[0] === "game" && parts[1]) return { kind: "game", game: parts[1] };
  if (parts[0] === "master") return { kind: "master" };
  if (parts[0] === "ideas") return { kind: "ideas" };
  return null;
}
async function route() {
  let r = parseRoute();
  if (!r) {
    const first = state.tree.library.find((b) => b.status === "ready");
    location.hash = first ? `#/book/${first.id}` : "#/master";
    return;
  }
  state.route = r;
  $("#sidebar").classList.remove("open");
  const tab = r.kind === "book" ? `book:${r.book}` : r.kind === "game" ? `game:${r.game}` : r.kind;
  markActive(tab);
  $("#read-actions").innerHTML = "";
  if (r.kind === "book") await showBook(r);
  else if (r.kind === "game") await showGame(r);
  else showSoon(r.kind);
  await openChat(tab);
}

async function showBook(r) {
  const reader = $("#reader");
  const status = state.tree.library.find((b) => b.id === r.book);
  if (!status || status.status !== "ready") {
    $("#crumbs").textContent = `r/${r.book}`;
    reader.innerHTML = `<p class="notice">This book isn't built yet. It will appear here once its subreddit has been collected and written.</p>`;
    return;
  }
  try {
    const data = await api(r.chapter ? `/api/book/${r.book}/${r.chapter}` : `/api/book/${r.book}`);
    reader.innerHTML = md(data.markdown);
    fixLinks(reader, r.book);
    $("#crumbs").innerHTML = r.chapter
      ? `<a href="#/book/${r.book}">r/${r.book}</a> › ${escapeHtml(reader.querySelector("h1")?.textContent || r.chapter)}`
      : `r/${r.book}`;
    reader.scrollTop = 0;
  } catch (e) {
    reader.innerHTML = `<p class="notice">${escapeHtml(e.message)}</p>`;
  }
}

async function showGame(r) {
  const reader = $("#reader");
  const data = await api(`/api/game/${r.game}`);
  $("#crumbs").textContent = `My Games › ${data.title}`;
  const render = (text) => {
    reader.innerHTML = md(text);
    fixLinks(reader, null);
    $("#read-actions").innerHTML = `<button id="edit-game">Edit</button>`;
    $("#edit-game").onclick = () => {
      reader.innerHTML = `<textarea class="game-editor" id="game-text" aria-label="game.md"></textarea>`;
      $("#game-text").value = text;
      $("#read-actions").innerHTML = `<button id="cancel-game">Cancel</button> <button id="save-game">Save</button>`;
      $("#cancel-game").onclick = () => render(text);
      $("#save-game").onclick = async () => {
        const newText = $("#game-text").value;
        await api(`/api/game/${r.game}`, { method: "PUT", body: JSON.stringify({ markdown: newText }) });
        await loadTree(); markActive(`game:${r.game}`);
        render(newText);
      };
    };
  };
  render(data.markdown);
  reader.scrollTop = 0;
}

function showSoon(kind) {
  const name = kind === "master" ? "Master Book" : "Ideas Board";
  $("#crumbs").textContent = name;
  $("#reader").innerHTML = `<h1>${name}</h1><p class="notice">Coming soon. ${kind === "master"
    ? "The Master Book is built once more than one subreddit book exists. It covers topics that show up across communities."
    : "The Ideas Board collects every content idea from every chapter, de-duplicated and sorted by how hot it is."}</p>`;
}

// ---------- chat ----------
async function openChat(tab) {
  state.chatTab = tab;
  const pane = $(".chat-pane");
  const enabled = tab.startsWith("book:")
    ? state.tree.library.some((b) => `book:${b.id}` === tab && b.status === "ready")
    : tab.startsWith("game:");
  pane.classList.toggle("disabled", !enabled);
  const titles = { book: "Chat with this book", game: "Chat about this game" };
  $("#chat-title").textContent = enabled ? titles[tab.split(":")[0]] : "Chat";
  $("#chat-input").placeholder = tab.startsWith("game:")
    ? "Ask how the communities' lessons apply to this game…" : "Ask this book…";
  if (!enabled) {
    $("#messages").innerHTML = `<p class="empty-chat">Chat for this tab arrives with the ${tab === "master" ? "Master Book" : tab === "ideas" ? "Ideas Board" : "book"}.</p>`;
    $("#session-cost").textContent = "";
    return;
  }
  const data = await api(`/api/chat/${encodeURIComponent(tab)}`);
  if (state.chatTab !== tab) return;
  $("#model-select").value = data.selected_model;
  renderMessages(data);
}

function renderMessages(data) {
  const box = $("#messages");
  box.innerHTML = "";
  if (!data.messages.length) {
    box.innerHTML = `<p class="empty-chat">${state.chatTab.startsWith("game:")
      ? "Paste your rules and card lists into this game's page (Edit), then ask things like “what do these communities say would break my save cards?”"
      : "Ask about this book, e.g. “What do people agree makes a card readable?” or “Give me five video hooks from the hottest debates.”"}</p>`;
  }
  data.messages.forEach((m) => box.appendChild(messageEl(m)));
  setCost(data.session_cost_usd, data.session_tokens);
  box.scrollTop = box.scrollHeight;
}

function messageEl(m) {
  const el = document.createElement("div");
  el.className = `msg ${m.role}`;
  if (m.role === "user") { el.textContent = m.text; return el; }
  const tools = (m.tools_used || []).map(toolLabel).join(" · ");
  el.innerHTML = (tools ? `<div class="tools">Read: ${escapeHtml(tools)}</div>` : "") +
    `<div class="body markdown">${md(m.text)}</div>` +
    `<div class="meta">answered by ${escapeHtml(m.model_label || m.model)} · ${money(m.cost_usd)}</div>`;
  fixLinks(el, null);
  return el;
}
function toolLabel(t) {
  return t.name === "read_chapter" ? `${t.input.chapter}` : `thread ${t.input.id}`;
}
function setCost(usd, tok) {
  $("#session-cost").textContent = `Session ${money(usd)} · ${tokens(tok)} tokens`;
}

async function send(e) {
  e.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || state.sending) return;
  state.sending = true; $("#send-btn").disabled = true;
  const tab = state.chatTab;
  const box = $("#messages");
  box.querySelector(".empty-chat")?.remove();
  box.appendChild(messageEl({ role: "user", text }));
  input.value = "";
  const live = document.createElement("div");
  live.className = "msg assistant";
  live.innerHTML = `<div class="tools"></div><div class="body markdown"><p class="notice">Thinking…</p></div><div class="meta"></div>`;
  box.appendChild(live);
  box.scrollTop = box.scrollHeight;
  let raw = "", toolsSeen = [];
  try {
    const res = await fetch(`/api/chat/${encodeURIComponent(tab)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, model: $("#model-select").value }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, ""); buf = buf.slice(i + 2);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "text") {
          raw += ev.delta;
          live.querySelector(".body").innerHTML = md(raw);
        } else if (ev.type === "tool") {
          toolsSeen.push(ev);
          live.querySelector(".tools").textContent = "Read: " + toolsSeen.map(toolLabel).join(" · ");
        } else if (ev.type === "done") {
          const el = messageEl(ev.message);
          live.replaceWith(el);
          setCost(ev.session_cost_usd, ev.session_tokens);
        } else if (ev.type === "error") {
          live.classList.add("error");
          live.querySelector(".body").textContent = "Error: " + ev.message;
          if (ev.session_cost_usd !== undefined) setCost(ev.session_cost_usd, null);
        }
        box.scrollTop = box.scrollHeight;
      }
    }
  } catch (err) {
    live.classList.add("error");
    live.querySelector(".body").textContent = "Error: " + err.message;
  } finally {
    state.sending = false; $("#send-btn").disabled = false;
  }
}

// ---------- wiring ----------
async function init() {
  const m = await api("/api/models");
  state.models = m.models;
  $("#model-select").innerHTML = m.models.map((x) => `<option value="${x.id}">${escapeHtml(x.label)}</option>`).join("");
  await loadTree();
  $("#chat-form").addEventListener("submit", send);
  $("#chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
  });
  $("#model-select").addEventListener("change", async (e) => {
    if (state.chatTab && !$(".chat-pane").classList.contains("disabled")) {
      await api(`/api/chat/${encodeURIComponent(state.chatTab)}/model`, { method: "PUT", body: JSON.stringify({ model: e.target.value }) });
    }
  });
  $("#clear-chat").addEventListener("click", async () => {
    if (!confirm("Clear this tab's conversation?")) return;
    renderMessages(await api(`/api/chat/${encodeURIComponent(state.chatTab)}/clear`, { method: "POST" }));
  });
  $("#add-game").addEventListener("click", async () => {
    const name = prompt("Game name:");
    if (!name) return;
    try {
      const g = await api("/api/games", { method: "POST", body: JSON.stringify({ name }) });
      await loadTree();
      location.hash = `#/game/${g.id}`;
    } catch (err) { alert(err.message); }
  });
  $("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  document.querySelectorAll(".view-toggle button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".view-toggle button").forEach((x) => x.classList.toggle("active", x === b));
    $(".panes").dataset.view = b.dataset.view;
  }));
  window.addEventListener("hashchange", route);
  await route();
}
init();
