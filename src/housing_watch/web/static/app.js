// Small glue around htmx: drop empty filters from URLs, row clicks, closing the detail drawer.
document.addEventListener("htmx:configRequest", (e) => {
  const fd = e.detail.formData;
  if (fd) for (const [k, v] of [...fd.entries()]) if (v === "") fd.delete(k);
});

function closeDrawer() {
  const d = document.getElementById("drawer");
  if (!d || !d.innerHTML.trim()) return;
  d.innerHTML = "";
  const u = new URL(location.href);
  if (u.searchParams.has("open")) { u.searchParams.delete("open"); history.replaceState(history.state, "", u); }
  document.querySelectorAll("tr.sel").forEach((r) => r.classList.remove("sel"));
}

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]")) return closeDrawer();
  const row = e.target.closest("tr.click");
  if (!row || e.target.closest("a, button, input, select") || getSelection().toString()) return;
  const a = row.querySelector("a.t1");
  if (a) a.click();
});

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// Mark the opened row; back/forward restores the drawer from htmx's history cache.
document.addEventListener("htmx:afterRequest", (e) => {
  const row = e.detail.elt.closest && e.detail.elt.closest("tr.click");
  if (!row) return;
  document.querySelectorAll("tr.sel").forEach((r) => r.classList.remove("sel"));
  row.classList.add("sel");
});
