// Reddit Library frontend. Plain JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const params = new URLSearchParams(location.search);
const store = {
  get(k, d) { try { const v = localStorage.getItem("rl." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("rl." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const state = { tree: null, route: null, chatTab: null, sending: false, saving: false, status: null, notice: null, chats: {}, bookCache: {}, sort: store.get("sort", "heat") };

// ---------- small utils ----------
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (t) => DOMPurify.sanitize(marked.parse(t || ""));
const ktok = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n || 0));
const ICON = {
  copy: '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><rect x="8.5" y="8.5" width="11" height="11" rx="2.2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M15.5 8.5V6.7a2.2 2.2 0 0 0-2.2-2.2H6.7a2.2 2.2 0 0 0-2.2 2.2v6.6a2.2 2.2 0 0 0 2.2 2.2h1.8" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  book: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M5 4.5h9.5a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3Z M5 17a3 3 0 0 1 3-3h9.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
  ext: '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M14 5h5v5M19 5l-8 8M17 14v4a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 5 18V8.5A1.5 1.5 0 0 1 6.5 7H10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch { const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }
  if (btn) { btn.innerHTML = ICON.check; btn.classList.add("copied"); setTimeout(() => { btn.innerHTML = ICON.copy; btn.classList.remove("copied"); }, 1400); }
}

// Citations like [T1, S2] -> small link chips (Reddit, new tab).
function citeHtml(text, refs) {
  return esc(text).replace(/\[((?:[TS]\d+)(?:\s*,\s*[TS]\d+)*)\]/g, (m, inner) => {
    const ids = inner.split(/\s*,\s*/);
    if (!ids.every((r) => refs[r])) return m;
    return `<span class="cite">${ids.map((r) => `<a href="${esc(refs[r].url)}" target="_blank" rel="noopener noreferrer" title="${esc(refs[r].title)}">${r}</a>`).join("")}</span>`;
  });
}
const citesFor = (list, refs) => {
  const ids = (list || []).filter((r) => refs[r]);
  return ids.length ? citeHtml(`[${ids.join(", ")}]`, refs) : "";
};

function externalize(container) {
  $$("a[href]", container).forEach((a) => {
    if (/^https?:/.test(a.getAttribute("href"))) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
  });
}
function lightbox(src) {
  const box = document.createElement("div");
  box.className = "lightbox";
  box.innerHTML = `<img src="${esc(src)}" alt="Post image, enlarged">`;
  box.addEventListener("click", () => box.remove());
  document.addEventListener("keydown", function onKey(e) { if (e.key === "Escape") { box.remove(); document.removeEventListener("keydown", onKey); } });
  document.body.appendChild(box);
}

// ---------- theme ----------
function applyTheme(mode) {
  if (mode === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
  $("#theme-toggle").dataset.mode = mode;
  $("#theme-toggle").title = `Theme: ${mode}`;
}
function initTheme() {
  let mode = params.get("theme") || store.get("theme", "system");
  applyTheme(mode);
  $("#theme-toggle").addEventListener("click", () => {
    mode = { system: "light", light: "dark", dark: "system" }[mode];
    store.set("theme", mode);
    applyTheme(mode);
  });
}

// ---------- view mode + split divider ----------
function setView(view, persist = true) {
  $("#workspace").dataset.view = view;
  $$("#view-switch button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.view === view)));
  if (persist) store.set("view", view);
}
function currentView() { return $("#workspace").dataset.view; }
function applySplit() {
  const frac = store.get("split." + (state.chatTab || "default"), 0.56);
  $("#workspace").style.setProperty("--read-frac", frac);
}
function initDivider() {
  const ws = $("#workspace"), div = $("#divider");
  const setFrac = (f) => {
    f = Math.min(0.75, Math.max(0.3, f));
    ws.style.setProperty("--read-frac", f);
    store.set("split." + (state.chatTab || "default"), Number(f.toFixed(3)));
  };
  div.addEventListener("pointerdown", (e) => {
    div.setPointerCapture(e.pointerId);
    div.classList.add("dragging"); ws.classList.add("dragging");
    const rect = ws.getBoundingClientRect();
    const move = (ev) => setFrac((ev.clientX - rect.left) / rect.width);
    const up = () => { div.classList.remove("dragging"); ws.classList.remove("dragging"); div.removeEventListener("pointermove", move); div.removeEventListener("pointerup", up); };
    div.addEventListener("pointermove", move);
    div.addEventListener("pointerup", up);
  });
  div.addEventListener("keydown", (e) => {
    const cur = parseFloat(getComputedStyle(ws).getPropertyValue("--read-frac")) || 0.56;
    if (e.key === "ArrowLeft") setFrac(cur - 0.03);
    if (e.key === "ArrowRight") setFrac(cur + 0.03);
  });
  div.addEventListener("dblclick", () => setFrac(0.56));
}

// ---------- sidebar ----------
function isMobile() { return matchMedia("(max-width: 900px)").matches; }
function initSidebar() {
  const shell = $("#shell");
  if (params.get("sidebar") === "closed" || store.get("sidebarClosed", false)) shell.classList.add("sidebar-closed");
  $("#sidebar-toggle").addEventListener("click", () => {
    if (isMobile()) { shell.classList.toggle("sidebar-open-mobile"); return; }
    shell.classList.toggle("sidebar-closed");
    store.set("sidebarClosed", shell.classList.contains("sidebar-closed"));
  });
  $("#scrim").addEventListener("click", () => shell.classList.remove("sidebar-open-mobile"));
}
async function loadTree() {
  state.tree = await api("/api/tree");
  $("#games").innerHTML = state.tree.games.map((g) =>
    `<li><a class="nav-item" href="#/game/${g.id}" data-tab="game:${g.id}">${esc(g.title)}</a></li>`).join("");
  const ready = state.tree.library.filter((b) => b.status === "ready");
  const later = state.tree.library.filter((b) => b.status === "not built");
  let html = `<li><a class="nav-item muted" href="#/master" data-tab="master">Master Book <span class="pill">soon</span></a></li>`;
  html += ready.map((b) => `<li><a class="nav-item" href="#/book/${b.id}" data-tab="book:${b.id}">${esc(b.title)}</a></li>`).join("");
  if (later.length) {
    html += `<li><details class="soon-group" ${store.get("soonOpen", false) ? "open" : ""}><summary class="nav-item muted"><span>Coming soon (${later.length})</span>${ICON.chev}</summary><ul>` +
      later.map((b) => `<li><a class="nav-item muted" href="#/book/${b.id}" data-tab="book:${b.id}">${esc(b.title)}</a></li>`).join("") + "</ul></details></li>";
  }
  $("#library").innerHTML = html;
  $("details.soon-group")?.addEventListener("toggle", (e) => store.set("soonOpen", e.target.open));
}
function markActive(tab) {
  $$(".sidebar a[data-tab]").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
}

// ---------- routing ----------
function parseRoute() {
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (p[0] === "book" && p[1]) return { kind: "book", book: p[1], chapter: p[2] || null };
  if (p[0] === "game" && p[1]) return { kind: "game", game: p[1] };
  if (p[0] === "master") return { kind: "master" };
  if (p[0] === "ideas") return { kind: "ideas" };
  return null;
}
async function route() {
  const r = parseRoute();
  if (!r) {
    const first = state.tree.library.find((b) => b.status === "ready");
    location.replace(first ? `#/book/${first.id}` : "#/master");
    return;
  }
  state.route = r;
  $("#shell").classList.remove("sidebar-open-mobile");
  const tab = r.kind === "book" ? `book:${r.book}` : r.kind === "game" ? `game:${r.game}` : r.kind;
  markActive(tab);
  if (r.kind === "book") await (r.chapter ? showChapter(r.book, r.chapter) : showBook(r.book));
  else if (r.kind === "game") await showGame(r.game);
  else showSoon(r.kind);
  $("#read-scroll").scrollTop = 0;
  if (tab !== state.chatTab) await openChat(tab);
}

// ---------- read: book index ----------
async function showBook(book) {
  const reader = $("#reader");
  const status = state.tree.library.find((b) => b.id === book);
  if (!status || status.status !== "ready") {
    reader.innerHTML = `<div class="soon-page"><h1>r/${esc(book)}</h1><p class="notice">Coming soon. This book appears once its subreddit has been collected and written.</p></div>`;
    return;
  }
  const data = state.bookCache[book] || (state.bookCache[book] = await api(`/api/book/${book}`));
  const sorted = [...data.chapters].sort((a, b) => state.sort === "debate"
    ? b.comments_per_thread - a.comments_per_thread : b.heat - a.heat);
  reader.innerHTML = `
    <div class="book-head">
      <div>
        <h1 class="page-title">r/${esc(book)}</h1>
        <p class="page-sub">${data.thread_count} of the most engaging threads, grouped into ${data.chapters.length} chapters.</p>
      </div>
      <div class="sort">Sort by
        <div class="segmented" role="radiogroup" aria-label="Sort chapters">
          <button role="radio" data-sort="heat" aria-checked="${state.sort === "heat"}">Heat</button>
          <button role="radio" data-sort="debate" aria-checked="${state.sort === "debate"}">Debate</button>
        </div>
      </div>
    </div>
    <div class="cards">${sorted.map((c) => `
      <a class="card" href="#/book/${book}/${c.slug}">
        <div class="card-top"><h3>${esc(c.title)}</h3>${c.hot_debate ? '<span class="badge-hot">Hot debate</span>' : ""}</div>
        <p>${esc(c.tldr || c.one_line || c.description)}</p>
        <div class="chips">
          <span class="chip" title="Sum of the threads' engagement scores">Heat <b>${Math.round(c.heat)}</b></span>
          <span class="chip" title="Average comments per main thread"><b>${Math.round(c.comments_per_thread)}</b> comments / thread</span>
          <span class="chip"><b>${c.threads}</b> threads${c.secondary ? ` +${c.secondary}` : ""}</span>
        </div>
      </a>`).join("")}
    </div>`;
  $$("[data-sort]", reader).forEach((b) => b.addEventListener("click", () => {
    state.sort = b.dataset.sort; store.set("sort", state.sort); showBook(book);
  }));
}

// ---------- read: chapter ----------
async function showChapter(book, slug) {
  const reader = $("#reader");
  let c;
  try { c = await api(`/api/book/${book}/${slug}`); }
  catch (e) { reader.innerHTML = `<p class="notice">${esc(e.message)}</p>`; return; }
  const R = c.refs, s = c.stats;
  const points = (list, key) => list.map((x) => `<li>${citeHtml(x[key], R)}${citesFor(x.threads, R)}</li>`).join("");
  const toc = [["tldr", "TL;DR"], ["consensus", "Consensus"], ["splits", "Splits"], ["lessons", "Lessons"], ["ideas", "Content ideas"], ["threads", "Threads"]];
  const splitHtml = (sp) => {
    const sup = sp.support;
    let bar = "";
    if (sup && (sup.a_points + sup.b_points) > 0) {
      const total = sup.a_points + sup.b_points;
      const pa = Math.max(3, Math.round((sup.a_points / total) * 100));
      const who = (n) => (n ? ` · ${n} commenter${n === 1 ? "" : "s"}` : "");
      bar = `<div class="bar" role="img" aria-label="Support: ${sup.a_points} points vs ${sup.b_points} points"><div class="a" style="width:${pa}%"></div><div class="b" style="width:${100 - pa}%"></div></div>
             <div class="bar-labels"><span>Side A · <b>${sup.a_points}</b> pts${who(sup.a_commenters)}</span><span>Side B · <b>${sup.b_points}</b> pts${who(sup.b_commenters)}</span></div>`;
    }
    return `<div class="split"><h3>${esc(sp.issue)}</h3>
      <div class="sides"><div class="side a"><div class="who">Side A</div><p>${citeHtml(sp.side_a, R)}</p></div>
      <div class="side b"><div class="who">Side B</div><p>${citeHtml(sp.side_b, R)}</p></div></div>
      ${bar}<p class="weight">${citeHtml(sp.weight, R)}${citesFor(sp.threads, R)}</p></div>`;
  };
  const ideaHtml = (i, kind) => `<div class="idea"><div class="kind">${kind}</div><div class="hook">“${esc(i.hook)}”</div>
      <p class="why">${citeHtml(i.why, R)}${citesFor(i.threads, R)}</p>
      <button class="copy-btn" data-copy="${esc(i.hook)}" aria-label="Copy hook" title="Copy hook">${ICON.copy}</button></div>`;
  const threadHtml = (t) => `<div class="thread"><span class="ref">${t.ref}</span>
      <a class="t-title" href="${esc(t.url)}" target="_blank" rel="noopener noreferrer">${esc(t.title)}</a>
      <div class="t-meta">${t.upvotes} upvotes · ${t.comments} comments · ${t.date}${t.flair ? ` · ${esc(t.flair)}` : ""}</div>
      ${t.image_note ? `<div class="t-note">${esc(t.image_note)}</div>` : ""}
      ${t.images.length ? `<div class="thumbs">${t.images.map((f) => `<img loading="lazy" src="/data/images/${esc(f.replace(/^data\/images\//, ""))}" alt="Image from: ${esc(t.title)}">`).join("")}</div>` : ""}</div>`;

  reader.innerHTML = `
  <div class="chapter">
    <nav class="toc" aria-label="On this page"><div class="toc-label">On this page</div>
      ${toc.map(([id, label]) => `<a href="#${id}" data-target="${id}">${label}</a>`).join("")}</nav>
    <article class="chapter-main">
      <div class="eyebrow"><a href="#/book/${book}">r/${esc(book)}</a> › Chapter</div>
      <h1 class="page-title">${esc(c.title)}</h1>
      <p class="page-sub">${esc(c.description)}</p>
      <div class="chips">
        <span class="chip">Heat <b>${Math.round(s.heat)}</b></span>
        <span class="chip"><b>${Math.round(s.comments_per_thread)}</b> comments / thread</span>
        <span class="chip"><b>${s.threads}</b> threads${s.secondary ? ` +${s.secondary} supporting` : ""}</span>
        <span class="chip"><b>${s.upvotes.toLocaleString()}</b> upvotes · <b>${s.comments.toLocaleString()}</b> comments</span>
        <span class="chip">${esc(s.date_range)}</span>
      </div>

      <h2 class="section" id="tldr">TL;DR</h2>
      <div class="tldr">${citeHtml(c.tldr, R)}</div>

      <h2 class="section" id="consensus">Consensus</h2>
      ${c.consensus.length ? `<ul class="points">${points(c.consensus, "point")}</ul>` : `<p class="notice">No point is backed by more than one thread yet.</p>`}
      ${c.single_thread_agreement.length ? `<div class="single-thread"><div class="label">Agreed within a single thread</div><ul class="points">${points(c.single_thread_agreement, "point")}</ul></div>` : ""}

      <h2 class="section" id="splits">Hot takes and splits</h2>
      ${c.splits.length ? c.splits.map(splitHtml).join("") : `<p class="notice">No real splits in these threads.</p>`}

      <h2 class="section" id="lessons">Lessons</h2>
      <h3 class="sub">Design</h3>
      <ul class="points">${points(c.design_lessons, "lesson")}</ul>
      ${c.business_lessons.length ? `<h3 class="sub">Business and Kickstarter</h3><ul class="points">${points(c.business_lessons, "lesson")}</ul>` : ""}

      <h2 class="section" id="ideas">Content ideas</h2>
      <h3 class="sub">Debates to weigh in on</h3>
      <div class="idea-grid">${c.debates.map((i) => ideaHtml(i, "Debate")).join("") || '<p class="notice">None.</p>'}</div>
      <h3 class="sub">Recurring questions to answer</h3>
      <div class="idea-grid">${c.questions.map((i) => ideaHtml(i, "Question")).join("") || '<p class="notice">None.</p>'}</div>

      <h2 class="section" id="threads">Threads</h2>
      <div class="thread-list">${c.threads.map(threadHtml).join("")}</div>
      ${c.supporting_threads.length ? `<details class="more"><summary>Also relevant: ${c.supporting_threads.length} threads whose main chapter is elsewhere</summary><div class="thread-list">${c.supporting_threads.map(threadHtml).join("")}</div></details>` : ""}
    </article>
  </div>`;

  $$(".copy-btn", reader).forEach((b) => b.addEventListener("click", () => copyText(b.dataset.copy, b)));
  $$(".thumbs img", reader).forEach((img) => img.addEventListener("click", () => lightbox(img.src)));
  $$(".toc a", reader).forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    document.getElementById(a.dataset.target)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  watchToc();
}
function watchToc() {
  const scroller = $("#read-scroll");
  const links = $$(".toc a");
  if (!links.length) return;
  const update = () => {
    let current = links[0].dataset.target;
    for (const a of links) {
      const el = document.getElementById(a.dataset.target);
      if (el && el.getBoundingClientRect().top - scroller.getBoundingClientRect().top < 120) current = a.dataset.target;
    }
    links.forEach((a) => a.classList.toggle("current", a.dataset.target === current));
  };
  scroller.onscroll = update;
  update();
}

// ---------- read: game ----------
async function showGame(slug) {
  const reader = $("#reader");
  const data = await api(`/api/game/${slug}`);
  $("#read-scroll").onscroll = null;
  const render = (text) => {
    reader.innerHTML = `<div class="eyebrow">My Games</div><div class="page-actions"><button class="btn" id="edit-game">Edit game page</button></div>
      <div class="game-text md">${md(text)}</div>`;
    externalize(reader);
    $("#edit-game").onclick = () => {
      reader.innerHTML = `<div class="eyebrow">My Games › Editing game.md</div>
        <div class="page-actions"><button class="btn" id="cancel-game">Cancel</button><button class="btn primary" id="save-game">Save</button></div>
        <textarea class="game-editor" id="game-text" aria-label="game.md" spellcheck="false"></textarea>`;
      $("#game-text").value = text;
      $("#game-text").focus();
      $("#cancel-game").onclick = () => render(text);
      $("#save-game").onclick = async () => {
        const next = $("#game-text").value;
        await api(`/api/game/${slug}`, { method: "PUT", body: JSON.stringify({ markdown: next }) });
        await loadTree(); markActive(`game:${slug}`);
        render(next);
      };
    };
  };
  render(data.markdown);
}
function showSoon(kind) {
  $("#read-scroll").onscroll = null;
  const name = kind === "master" ? "Master Book" : "Ideas Board";
  const text = kind === "master"
    ? "Topics that show up across several subreddit books, with where the communities agree and differ. Built once more than one book exists."
    : "Every content idea from every chapter, de-duplicated and sorted by how hot it is.";
  $("#reader").innerHTML = `<div class="soon-page"><h1>${name}</h1><p class="notice">Coming soon. ${text}</p></div>`;
}

// ---------- chat ----------
// Chats are temporary: each tab's live chat lives only in this page's memory (state.chats) until you
// press Save. Every answer runs on your Claude Code subscription, never the API.
const STARTERS = {
  book: [
    "Give me five video hooks from the hottest debates in this book.",
    "What do designers here agree makes a card easy to read?",
    "Where is this community most split, and which side has more support?",
    "What should I know about printing and Kickstarter before launching?",
  ],
  game: [
    "Based on these communities, what's most likely to break or confuse players in my game?",
    "Which card layout lessons apply to my cards?",
    "Give me three build-in-public video ideas for this game, backed by the debates here.",
    "What do players dislike that my rules might be doing?",
  ],
};

const freshChat = () => ({ messages: [], savedId: null, savedCount: 0, savedTitle: null });
function chatState(tab = state.chatTab) { return state.chats[tab] || (state.chats[tab] = freshChat()); }
function unsavedCount(c) { return c.messages.length - (c.savedId ? c.savedCount : 0); }
function confirmLimit() { return state.status?.unsaved_confirm_after ?? 4; }
function okToDiscard(c, what) {
  const n = unsavedCount(c);
  if (n <= confirmLimit()) return true;
  return confirm(`This chat has ${n} unsaved messages. ${what} anyway? They will be lost (use Save to keep them).`);
}
function chatEnabled(tab) {
  return tab.startsWith("game:") || (tab.startsWith("book:") && state.tree.library.some((b) => `book:${b.id}` === tab && b.status === "ready"));
}

async function loadStatus(force = false) {
  if (state.status && state.status.ok && !force) return state.status;
  try { state.status = await api(`/api/chat/status${force ? "?refresh=true" : ""}`); }
  catch (e) { state.status = { ok: false, message: `Couldn't check the Claude Code login: ${e.message}` }; }
  return state.status;
}

async function openChat(tab) {
  state.chatTab = tab;
  applySplit();
  closeSaved();
  const pane = $("#chat-pane");
  const enabled = chatEnabled(tab);
  pane.classList.toggle("disabled", !enabled);
  const gameTitle = tab.startsWith("game:") ? (state.tree.games.find((g) => `game:${g.id}` === tab)?.title || "this game") : "";
  $("#chat-input").placeholder = tab.startsWith("game:") ? `Ask how the communities' lessons apply to ${gameTitle}…` : `Ask r/${tab.slice(5)}…`;
  if (!enabled) {
    $("#chat-title").textContent = "Chat";
    $("#messages").innerHTML = `<p class="disabled-note">Chat for this tab arrives with the ${tab === "master" ? "Master Book" : tab === "ideas" ? "Ideas Board" : "book"}.</p>`;
    return;
  }
  renderChat();
  const s = await loadStatus();
  if (state.chatTab === tab) renderChat();
  return s;
}

function chatTitleText() {
  const tab = state.chatTab, c = chatState();
  const base = tab.startsWith("game:") ? `Chat · ${state.tree.games.find((g) => `game:${g.id}` === tab)?.title || "game"}` : `Chat · r/${tab.slice(5)}`;
  return c.savedTitle ? `${base} — ${c.savedTitle}` : base;
}

function renderChat() {
  const c = chatState();
  $("#chat-title").textContent = chatTitleText();
  const box = $("#messages");
  box.innerHTML = "";
  const s = state.status;
  const blocked = s && !s.ok;
  $("#chat-input").disabled = !!blocked;
  $("#send-btn").disabled = !!blocked || state.sending;
  if (blocked) {
    box.innerHTML = `<div class="notice-box"><b>Chat runs on your Claude Code subscription.</b><br>${md(s.message)}
      <p><button class="btn" id="recheck-login">I've logged in — check again</button></p></div>`;
    $("#recheck-login").onclick = async () => { await loadStatus(true); renderChat(); };
  } else if (!c.messages.length) {
    const kind = state.chatTab.split(":")[0];
    const name = kind === "game" ? (state.tree.games.find((g) => `game:${g.id}` === state.chatTab)?.title || "your game") : `r/${state.chatTab.slice(5)}`;
    box.innerHTML = `<div class="empty"><h2>${kind === "game" ? `Test ${esc(name)} against the library` : `Ask ${esc(name)}`}</h2>
      <p>${kind === "game" ? "Paste your rules and card lists into the game page (Edit) first, so answers are specific." : "Answers cite the chapters and threads they use."}
      Chats are temporary until you press Save.</p>
      <div class="starters">${STARTERS[kind].map((q) => `<button class="starter">${esc(q)}</button>`).join("")}</div></div>`;
    $$(".starter", box).forEach((b) => b.addEventListener("click", () => { $("#chat-input").value = b.textContent; autoGrow(); send(); }));
  }
  if (!blocked) c.messages.forEach((m) => box.appendChild(messageEl(m)));
  updateChatChrome();
  scrollChat();
}

function updateChatChrome() {
  if (!state.chatTab || !chatEnabled(state.chatTab)) return;
  const c = chatState();
  const tokens = c.messages.reduce((n, m) => n + (m.tokens || 0), 0);
  $("#chat-usage").textContent = tokens ? `via subscription · ${ktok(tokens)} tokens` : "via subscription";
  const save = $("#save-chat"), unsaved = unsavedCount(c);
  save.textContent = state.saving ? "Saving…" : c.savedId && !unsaved ? "Saved" : c.savedId ? "Save changes" : "Save";
  save.classList.toggle("is-saved", !!c.savedId && !unsaved);
  save.disabled = state.saving || state.sending || !c.messages.length || (!!c.savedId && !unsaved) || !(state.status?.ok);
  $("#new-chat").disabled = state.sending || !c.messages.length;
  const chars = c.messages.reduce((n, m) => n + (m.text || "").length, 0);
  const banner = $("#chat-banner");
  if (state.notice) {
    banner.hidden = false; banner.className = "chat-banner warn"; banner.textContent = state.notice;
  } else if (chars / 3.8 > (state.status?.long_chat_hint_tokens ?? 60000)) {
    banner.hidden = false; banner.className = "chat-banner";
    banner.textContent = "This chat is getting long. Save it, then start a New chat (or Continue the saved one) to keep answers fast — Continue sends Claude a summary plus the last few messages.";
  } else banner.hidden = true;
}
function scrollChat() { const s = $("#messages-scroll"); s.scrollTop = s.scrollHeight; }

function sourcesHtml(sources) {
  if (!sources || !sources.length) return "";
  return `<div class="sources">${sources.map((s) => s.type === "chapter"
    ? `<a class="source" href="#/book/${esc(s.book)}/${esc(s.slug)}" data-chapter title="Open chapter">${ICON.book}<span>${esc(s.title)}</span></a>`
    : `<a class="source" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" title="Open on Reddit">${ICON.ext}<span>${esc(s.title)}</span></a>`).join("")}</div>`;
}
function wireAnswerLinks(el) {
  $$("a[href]", el).forEach((a) => {
    const href = a.getAttribute("href");
    if (/^https?:/.test(href)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
    else if (href.startsWith("#/book/")) a.addEventListener("click", () => { if (currentView() === "chat") setView("split"); });
  });
}
function messageEl(m) {
  const el = document.createElement("div");
  el.className = `msg ${m.role}`;
  if (m.role === "user") { el.innerHTML = `<div class="bubble">${esc(m.text)}</div>`; return el; }
  const tokens = m.tokens ?? Object.values(m.usage || {}).reduce((a, b) => a + (b || 0), 0);
  el.innerHTML = `<div class="md">${md(m.text)}</div>${sourcesHtml(m.sources)}
    <div class="msg-foot"><span>${esc(m.model_label || m.model)} · via subscription${tokens ? ` · ${ktok(tokens)} tokens` : ""}</span>
    <button class="copy-btn" aria-label="Copy answer" title="Copy answer">${ICON.copy}</button></div>`;
  $(".copy-btn", el).addEventListener("click", (e) => copyText(m.text, e.currentTarget));
  wireAnswerLinks(el);
  return el;
}

function autoGrow() {
  const t = $("#chat-input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 220) + "px";
}

async function send(e) {
  e?.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || state.sending || !(state.status?.ok)) return;
  const tab = state.chatTab, c = chatState(tab);
  state.sending = true; state.notice = null; $("#send-btn").disabled = true; closeSaved(); updateChatChrome();
  const history = c.messages.slice();
  const box = $("#messages");
  $(".empty", box)?.remove();
  const userMsg = { role: "user", text, ts: new Date().toISOString() };
  box.appendChild(messageEl(userMsg));
  input.value = ""; autoGrow();
  const live = document.createElement("div");
  live.className = "msg assistant";
  live.innerHTML = `<div class="status"><span class="dot"></span><span class="status-text">Thinking…</span></div><div class="md"></div>`;
  box.appendChild(live);
  scrollChat();
  const status = (t) => { const s = $(".status", live); if (s) { s.hidden = !t; $(".status-text", live).textContent = t || ""; } };
  const fail = (msg, kind) => {
    status(""); live.classList.add(kind === "usage_limit" ? "limit" : "error");
    $(".md", live).innerHTML = md((kind === "usage_limit" ? "" : "Error: ") + msg);
    // Not added to the chat: your message goes back into the box so you can resend it.
    if (state.chatTab === tab && !$("#chat-input").value) { $("#chat-input").value = text; autoGrow(); }
    if (kind === "login") { state.status = null; loadStatus(true).then(() => state.chatTab === tab && renderChat()); }
  };
  let raw = "";
  try {
    const res = await fetch(`/api/chat/${encodeURIComponent(tab)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, model: $("#model-select").value, history, saved_id: c.savedId }),
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
          raw += ev.delta; status("");
          $(".md", live).innerHTML = md(raw);
        } else if (ev.type === "tool") {
          status(`Reading: ${ev.label || ev.name}…`);
        } else if (ev.type === "notice") {
          state.notice = ev.message;
        } else if (ev.type === "done") {
          c.messages.push(userMsg, ev.message);
          live.replaceWith(messageEl(ev.message));
        } else if (ev.type === "error") {
          fail(ev.message, ev.kind);
        }
        if (state.chatTab === tab) scrollChat();
      }
    }
  } catch (err) {
    fail(err.message);
  } finally {
    state.sending = false; $("#send-btn").disabled = false;
    if (state.chatTab === tab) updateChatChrome();
  }
}

// ---------- save / saved list ----------
async function saveChat() {
  const tab = state.chatTab, c = chatState(tab);
  if (!c.messages.length || state.saving) return;
  state.saving = true; updateChatChrome();
  try {
    const meta = await api(`/api/chat/${encodeURIComponent(tab)}/save`, {
      method: "POST", body: JSON.stringify({ messages: c.messages, saved_id: c.savedId }),
    });
    c.savedId = meta.id; c.savedCount = meta.messages; c.savedTitle = meta.title;
    if (!$("#saved-panel").hidden) renderSavedList();
  } catch (err) {
    alert(`Not saved: ${err.message}`);
  } finally {
    state.saving = false;
    if (state.chatTab === tab) { $("#chat-title").textContent = chatTitleText(); updateChatChrome(); }
  }
}

function newChat() {
  const c = chatState();
  if (!okToDiscard(c, "Start a new chat")) return;
  state.chats[state.chatTab] = freshChat();
  state.notice = null;
  renderChat();
}

function closeSaved() { $("#saved-panel").hidden = true; $("#saved-toggle").setAttribute("aria-expanded", "false"); }
async function toggleSaved() {
  if (!$("#saved-panel").hidden) return closeSaved();
  $("#saved-panel").hidden = false; $("#saved-toggle").setAttribute("aria-expanded", "true");
  await renderSavedList();
}
const fmtDate = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

async function renderSavedList() {
  const tab = state.chatTab, list = $("#saved-list");
  list.innerHTML = `<li class="saved-empty">Loading…</li>`;
  let items;
  try { items = (await api(`/api/chat/${encodeURIComponent(tab)}/saved`)).saved; }
  catch (e) { list.innerHTML = `<li class="saved-empty">${esc(e.message)}</li>`; return; }
  if (state.chatTab !== tab) return;
  if (!items.length) { list.innerHTML = `<li class="saved-empty">No saved chats for this tab yet. Press Save in a chat to keep it.</li>`; return; }
  const cur = chatState(tab).savedId;
  list.innerHTML = items.map((s) => `<li class="${s.id === cur ? "current" : ""}" data-id="${esc(s.id)}">
      <div class="saved-title">${esc(s.title)}</div>
      <div class="saved-meta">${fmtDate(s.updated_at)} · ${esc(s.model_label || s.model || "")} · ${s.messages} messages${s.id === cur ? " · open now" : ""}</div>
      <div class="saved-actions"><button class="text-btn" data-act="continue">Continue</button><button class="text-btn" data-act="rename">Rename</button><button class="text-btn" data-act="delete">Delete</button></div>
    </li>`).join("");
  $$("button[data-act]", list).forEach((b) => b.addEventListener("click", () => {
    const li = b.closest("li"), item = items.find((x) => x.id === li.dataset.id);
    ({ continue: continueSaved, rename: renameSaved, delete: deleteSaved })[b.dataset.act](item);
  }));
}

async function continueSaved(item) {
  const tab = state.chatTab, c = chatState(tab);
  if (c.savedId !== item.id && !okToDiscard(c, "Open this saved chat")) return;
  let d;
  try { d = await api(`/api/chat/${encodeURIComponent(tab)}/saved/${encodeURIComponent(item.id)}`); }
  catch (e) { alert(e.message); return; }
  state.chats[tab] = { messages: d.messages, savedId: d.id, savedCount: d.messages.length, savedTitle: d.title };
  state.notice = null;
  closeSaved();
  if (state.chatTab === tab) renderChat();
}

async function renameSaved(item) {
  const title = prompt("Rename saved chat:", item.title);
  if (!title || title.trim() === item.title) return;
  const tab = state.chatTab;
  try {
    const meta = await api(`/api/chat/${encodeURIComponent(tab)}/saved/${encodeURIComponent(item.id)}`, { method: "PATCH", body: JSON.stringify({ title }) });
    const c = chatState(tab);
    if (c.savedId === item.id) { c.savedId = meta.id; c.savedTitle = meta.title; $("#chat-title").textContent = chatTitleText(); }
  } catch (e) { alert(e.message); }
  renderSavedList();
}

async function deleteSaved(item) {
  if (!confirm(`Delete the saved chat "${item.title}"? This removes its file from chats/saved/.`)) return;
  const tab = state.chatTab;
  try { await api(`/api/chat/${encodeURIComponent(tab)}/saved/${encodeURIComponent(item.id)}`, { method: "DELETE" }); }
  catch (e) { alert(e.message); }
  const c = chatState(tab);
  if (c.savedId === item.id) { c.savedId = null; c.savedCount = 0; c.savedTitle = null; renderChat(); }
  renderSavedList();
}

// ---------- init ----------
async function init() {
  initTheme();
  initSidebar();
  initDivider();
  setView(params.get("view") || store.get("view", "split"), !params.get("view"));
  $$("#view-switch button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));

  const m = await api("/api/models");
  $("#model-select").innerHTML = m.models.map((x) => `<option value="${x.id}">${esc(x.label)}</option>`).join("");
  const savedModel = store.get("chatModel", m.default);
  $("#model-select").value = m.models.some((x) => x.id === savedModel) ? savedModel : m.default;
  await loadTree();

  $("#chat-form").addEventListener("submit", send);
  const input = $("#chat-input");
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  $("#model-select").addEventListener("change", (e) => store.set("chatModel", e.target.value));
  $("#save-chat").addEventListener("click", saveChat);
  $("#new-chat").addEventListener("click", newChat);
  $("#saved-toggle").addEventListener("click", toggleSaved);
  $("#saved-close").addEventListener("click", closeSaved);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSaved(); });
  window.addEventListener("beforeunload", (e) => {
    if (Object.values(state.chats).some((c) => unsavedCount(c) > confirmLimit())) { e.preventDefault(); e.returnValue = ""; }
  });
  $("#add-game").addEventListener("click", async () => {
    const name = prompt("Name of the new game:");
    if (!name) return;
    try {
      const g = await api("/api/games", { method: "POST", body: JSON.stringify({ name }) });
      await loadTree();
      location.hash = `#/game/${g.id}`;
    } catch (err) { alert(err.message); }
  });
  window.addEventListener("hashchange", route);
  await route();
}
init();
