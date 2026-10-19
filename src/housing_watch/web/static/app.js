// Small glue around htmx. The URL is the list's state: every change below edits the query string and
// asks the server for the new list fragment (which also refreshes the chips, view bar and nav).

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const results = () => $("#results, #ev-results");
const shell = () => $(".shell").dataset;
let draft = null; // index of the filter (f param) the open builder is editing, or null for a new one
let timer;

function debounce(fn, ms = 250) {
  clearTimeout(timer);
  timer = setTimeout(fn, ms);
}

function go(params) {
  params.delete("offset");
  params.delete("before");
  const url = location.pathname + (params.toString() ? "?" + params : "");
  history.pushState({}, "", url);
  htmx.ajax("GET", url, { target: "#" + results().id, swap: "innerHTML" });
}

function setFilters(fs) {
  const p = new URLSearchParams(location.search);
  p.delete("f");
  fs.forEach((f) => p.append("f", f));
  go(p);
}

// --- filter builder ---------------------------------------------------------------------------------

function openBuilder(extra = "") {
  const b = $("#builder");
  const qs = new URLSearchParams(location.search);
  qs.set("page", shell().page);
  htmx.ajax("GET", "/filters/builder?" + qs + extra, { target: "#builder", swap: "innerHTML" }).then(() => {
    b.hidden = false;
    const first = $("input[type=search], select, input", b);
    if (first) first.focus();
    const form = $(".fb-form", b);
    if (form) compose(form);
  });
}

function closeBuilder(apply = true) {
  clearTimeout(timer); // flush a pending edit now, so nothing lands after the builder is gone
  const b = $("#builder"), form = b && $(".fb-form", b);
  if (form && apply) applyDraft(form);
  if (b) { b.hidden = true; b.innerHTML = ""; }
  draft = null;
}

function compose(form) {
  const field = form.dataset.field, op = form.elements.op.value;
  const blocks = $$("[data-ops]", form);
  const block = blocks.find((b) => b.dataset.ops.split(" ").includes(op));
  blocks.forEach((b) => (b.hidden = b !== block));
  if (op === "empty" || op === "notempty") return `${field}.${op}`;
  if (!block) return null;
  const vals = $$("input", block)
    .filter((i) => i.type !== "search" && (i.type === "checkbox" || i.type === "radio" ? i.checked : i.value.trim() !== ""))
    .map((i) => i.value.trim().replace(/,/g, "\\,"));
  if (!vals.length || (op === "between" && vals.length < 2)) return null;
  return `${field}.${op}.${vals.join(",")}`;
}

function applyDraft(form) {
  if (!form.isConnected) return;
  const fs = new URLSearchParams(location.search).getAll("f");
  const c = compose(form);
  if (draft === null) {
    if (!c) return;
    fs.push(c);
    draft = fs.length - 1;
  } else {
    if (!c || fs[draft] === c) return; // half-typed: keep the filter as it was until the value is complete
    fs[draft] = c;
  }
  setFilters(fs);
}

document.addEventListener("input", (e) => {
  const find = e.target.closest("[data-find]");
  if (find) {
    const q = find.value.toLowerCase();
    $$(find.dataset.find, find.closest(".fb")).forEach((el) => (el.hidden = !el.textContent.toLowerCase().includes(q)));
    return;
  }
  if (e.target.id === "f-q") {
    return debounce(() => {
      const p = new URLSearchParams(location.search);
      e.target.value.trim() ? p.set("q", e.target.value.trim()) : p.delete("q");
      go(p);
    });
  }
  const form = e.target.closest(".fb-form");
  if (form) { compose(form); debounce(() => applyDraft(form)); }
});

document.addEventListener("change", (e) => {
  const form = e.target.closest(".fb-form");
  if (form) { compose(form); debounce(() => applyDraft(form)); return; }
  if (e.target.name === "col") {
    const p = new URLSearchParams(location.search);
    p.set("cols", $$("#cols-pop input[name=col]:checked").map((i) => i.value).join(","));
    go(p);
  }
});

// --- clicks -------------------------------------------------------------------------------------------

function closeDrawer() {
  const d = $("#drawer");
  if (!d || !d.innerHTML.trim()) return;
  d.innerHTML = "";
  const u = new URL(location.href);
  if (u.searchParams.has("open")) { u.searchParams.delete("open"); history.replaceState(history.state, "", u); }
  $$("tr.sel").forEach((r) => r.classList.remove("sel"));
}

document.addEventListener("click", (e) => {
  const t = e.target;
  if (t.closest("[data-close]")) return closeDrawer();
  if (t.closest("[data-builder]")) { draft = null; return openBuilder(); }
  const pick = t.closest("[data-pick]");
  if (pick) return openBuilder("&field=" + encodeURIComponent(pick.dataset.pick) + (draft !== null ? "&edit=" + draft : ""));
  const edit = t.closest(".chip-body");
  if (edit) { draft = +edit.dataset.edit; return openBuilder("&field=" + encodeURIComponent(edit.dataset.field) + "&edit=" + draft); }
  if (t.closest("[data-builder-back]")) return openBuilder(draft !== null ? "&edit=" + draft : "");
  if (t.closest("[data-close-builder]")) return closeBuilder();
  if (t.closest("[data-remove]")) {
    const fs = new URLSearchParams(location.search).getAll("f");
    if (draft !== null) { fs.splice(draft, 1); setFilters(fs); }
    return closeBuilder(false);
  }
  const pop = t.closest("[data-pop]");
  if (pop) { const p = document.getElementById(pop.dataset.pop); p.hidden = !p.hidden; return; }
  if (t.closest("[data-save-open]")) {
    const d = $("#save-dialog");
    $(".dialog-err", d).hidden = true;
    return d.showModal();
  }
  if (t.closest("[data-dialog-close]")) return t.closest("dialog").close();
  const ren = t.closest("[data-rename]");
  if (ren) {
    const name = prompt("Rename view", ren.dataset.name);
    if (name && name.trim()) htmx.ajax("PATCH", "/views/" + ren.dataset.rename, { values: { name: name.trim() }, swap: "none" });
    return;
  }
  // a click outside a popover or menu closes it
  $$(".popover:not([hidden])").forEach((p) => {
    if (!p.parentElement.contains(t)) p.id === "builder" ? closeBuilder() : (p.hidden = true);
  });
  $$("details.menu[open]").forEach((d) => { if (!d.contains(t)) d.open = false; });
  const row = t.closest("tr.click");
  if (!row || t.closest("a, button, input, select") || getSelection().toString()) return;
  const a = $("a.t1", row);
  if (a) a.click();
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if ($("#builder:not([hidden])")) return closeBuilder();
  closeDrawer();
});

// --- htmx hooks ---------------------------------------------------------------------------------------

document.addEventListener("htmx:configRequest", (e) => {
  const fd = e.detail.formData;
  if (!fd) return;
  if (e.detail.verb === "get") {
    for (const [k, v] of [...fd.entries()]) if (v === "") fd.delete(k);
  } else if (!fd.has("back")) {
    fd.set("back", location.pathname + location.search); // saved-view writes: the state to save, where to land
  }
});

document.addEventListener("htmx:afterSwap", (e) => {
  const t = e.detail.target;
  if (t.id === "drawer") {
    const a = e.detail.requestConfig.elt;
    if (a && a.href) history.replaceState(history.state, "", a.href);
    $$("tr.sel").forEach((r) => r.classList.remove("sel"));
    const row = a && a.closest && a.closest("tr");
    if (row) row.classList.add("sel");
  } else if (t === results()) {
    t.loaded = new Set();
    if (t.getBoundingClientRect().top < 0) t.scrollIntoView();
  }
});

// The next batch is asked for by a row 40 from the end (to load early) and by the last row (in case a
// fast scroll skipped past the first): whichever fires first wins, the other is dropped.
document.addEventListener("htmx:beforeRequest", (e) => {
  const m = /[?&](offset|before)=(\d+)/.exec(e.detail.pathInfo.requestPath);
  const r = results();
  if (!m || !r) return;
  r.loaded = r.loaded || new Set();
  if (r.loaded.has(m[2])) e.preventDefault();
  else r.loaded.add(m[2]);
});

document.addEventListener("htmx:afterRequest", (e) => {
  const d = e.detail.elt.closest && e.detail.elt.closest("dialog");
  if (d && e.detail.successful) d.close();
});

document.addEventListener("htmx:responseError", (e) => {
  const d = e.detail.elt.closest && e.detail.elt.closest("dialog");
  const box = d ? $(".dialog-err", d) : $("#toast");
  box.textContent = e.detail.xhr.responseText || "Something went wrong.";
  box.hidden = false;
  if (!d) setTimeout(() => (box.hidden = true), 4000);
});

// A view opened from a bare URL (the page's default): show its full state in the address bar.
function canonical() {
  const c = shell().canonical;
  if (c && c !== location.pathname + location.search) history.replaceState(history.state, "", c);
}
document.addEventListener("DOMContentLoaded", canonical);
document.addEventListener("htmx:afterSettle", (e) => { if (e.detail.target === document.body) canonical(); });
window.addEventListener("popstate", () => location.reload());
