"use strict";

// Everything shown here comes from LinkedIn profiles other people wrote, so the page
// never builds HTML from strings: every node is created with textContent.

const PAGE = 100;
const MAX_REFRESH = 500;
const FILTER_IDS = ["keyword", "title", "company", "location", "connected_after", "connected_before"];

const state = {
  contacts: [],
  total: 0,
  stats: null,
  tags: [],
  sort: "recent",
  tag: "",
  untagged: false,
  selected: new Set(),
  allMatching: false,
  managing: false,
  openId: null,
  loadSeq: 0,
  flushNotes: null,
};

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n || 0).toLocaleString();

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (["checked", "value", "hidden", "disabled"].includes(key)) node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false || child === "") continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Well-separated hues so neighboring tags don't look alike.
const HUES = [212, 145, 330, 32, 265, 178, 2, 52, 292, 100];

function hue(name) {
  let h = 7;
  for (const ch of name.toLowerCase()) h = (h * 31 + ch.codePointAt(0)) % 9973;
  return HUES[h % HUES.length];
}

function chip(name, onRemove) {
  const node = el(
    "span",
    { class: "chip", title: name },
    name,
    onRemove
      ? el("button", {
          type: "button",
          "aria-label": `Remove tag ${name}`,
          onclick: (event) => { event.stopPropagation(); onRemove(); },
        }, "×")
      : null,
  );
  node.style.setProperty("--h", hue(name));
  return node;
}

function fullName(c) {
  return `${c.first_name || ""} ${c.last_name || ""}`.trim() || "(no name)";
}

function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

function isLinkedInUrl(url) {
  return /^https:\/\/(www\.)?linkedin\.com\//.test(url || "");
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), isError ? 5000 : 2500);
}

async function api(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function filterParams() {
  const params = new URLSearchParams();
  for (const id of FILTER_IDS) {
    const value = $(id).value.trim();
    if (value) params.set(id, value);
  }
  if ($("has_email").checked) params.set("has_email", "1");
  if (state.tag) params.set("tag", state.tag);
  if (state.untagged) params.set("untagged", "1");
  params.set("sort", state.sort);
  return params;
}

// ---- loading -------------------------------------------------------------------------

async function loadContacts({ append = false, keepCount = false } = {}) {
  const seq = ++state.loadSeq;
  const params = filterParams();
  const limit = keepCount ? Math.min(Math.max(state.contacts.length, PAGE), MAX_REFRESH) : PAGE;
  params.set("limit", limit);
  params.set("offset", append ? state.contacts.length : 0);

  let data;
  try {
    data = await api(`/api/contacts?${params}`);
  } catch (err) {
    toast(err.message, true);
    return;
  }
  if (seq !== state.loadSeq) return; // a newer search has started

  if (!append) {
    state.contacts = [];
    state.selected.clear();
    state.allMatching = false;
  }
  state.contacts.push(...data.contacts);
  state.total = data.total;
  $("export").href = `/export.csv?${filterParams()}`;
  renderContacts();
}

async function loadTags() {
  const data = await api("/api/tags");
  state.tags = data.tags;
  $("tag-options").replaceChildren(...state.tags.map((t) => el("option", { value: t.name })));
  renderTags();
}

async function loadStats() {
  state.stats = await api("/api/stats");
  $("stats").textContent = `${fmt(state.stats.total)} connections`;
  renderTags();
}

async function refreshAll() {
  try {
    await Promise.all([loadTags(), loadStats()]);
  } catch (err) {
    toast(err.message, true);
  }
  await loadContacts({ keepCount: true });
}

// ---- contact table -------------------------------------------------------------------

// A headline that just restates "position at company" adds nothing next to the
// Role column, so show where they are instead.
function subtitle(c, role) {
  const headline = (c.headline || "").toLowerCase();
  const echoesRole = c.position && c.company &&
    headline.includes(c.position.toLowerCase()) && headline.includes(c.company.toLowerCase());
  return (!echoesRole && c.headline) || c.location || (c.headline ? "" : role);
}

function contactRow(c) {
  const name = fullName(c);
  const role = [c.position, c.company].filter(Boolean).join(" · ");
  const checkbox = el("input", {
    type: "checkbox",
    "aria-label": `Select ${name}`,
    checked: state.allMatching || state.selected.has(c.id),
    onclick: (event) => event.stopPropagation(),
    onchange: (event) => toggleSelected(c.id, event.target.checked),
  });
  return el(
    "tr",
    { class: c.id === state.openId ? "active" : "", "data-id": c.id, onclick: () => openContact(c.id) },
    el("td", { class: "col-check" }, checkbox),
    el("td", {},
      el("span", { class: "name" }, name),
      el("span", { class: "sub role-inline" }, role),
      el("span", { class: "sub" }, subtitle(c, role))),
    el("td", { class: "col-role" }, el("span", { class: "role" }, role || "—")),
    el("td", { class: "col-connected" }, el("span", { class: "date" }, formatDate(c.connected_on))),
    el("td", {}, el("div", { class: "chips" }, c.tags.map((t) => chip(t)))),
  );
}

function renderContacts() {
  $("contacts").tBodies[0].replaceChildren(...state.contacts.map(contactRow));
  const shown = state.contacts.length;
  $("result-count").textContent = state.total
    ? `${fmt(state.total)} ${state.total === 1 ? "person" : "people"}` +
      (shown < state.total ? ` · showing ${fmt(shown)}` : "")
    : "";
  $("more").hidden = shown >= state.total;
  renderEmpty();
  renderSelection();
}

function rerenderRow(contact) {
  const row = $("contacts").querySelector(`tbody tr[data-id="${contact.id}"]`);
  if (row) row.replaceWith(contactRow(contact));
}

function highlightOpenRow() {
  for (const row of $("contacts").tBodies[0].rows) {
    row.classList.toggle("active", Number(row.dataset.id) === state.openId);
  }
}

function renderEmpty() {
  const hasRows = state.contacts.length > 0;
  $("empty").hidden = hasRows;
  document.querySelector(".table-wrap").hidden = !hasRows;
  if (hasRows) return;
  const noData = state.stats && state.stats.total === 0;
  $("empty").replaceChildren(
    ...(noData
      ? [
          el("h2", {}, "No contacts yet"),
          el("p", {}, "In your terminal, run ", el("code", {}, "netreach sync"),
            " to pull your connections straight from LinkedIn (a few minutes), or ",
            el("code", {}, "netreach import-csv Connections.csv"),
            " if you have LinkedIn's export. Then reload this page."),
        ]
      : [el("h2", {}, "No one matches"), el("p", {}, "Try fewer filters or a different search.")]),
  );
}

// ---- selection and bulk tagging ------------------------------------------------------

function toggleSelected(id, on) {
  if (state.allMatching) {
    state.allMatching = false;
    for (const c of state.contacts) state.selected.add(c.id);
  }
  if (on) state.selected.add(id);
  else state.selected.delete(id);
  renderSelection();
}

function clearSelection() {
  state.selected.clear();
  state.allMatching = false;
  renderSelection();
}

function renderSelection() {
  const count = state.allMatching ? state.total : state.selected.size;
  $("selection-bar").hidden = count === 0;
  $("selected-count").textContent = `${fmt(count)} selected`;

  const ids = state.contacts.map((c) => c.id);
  const allShown = ids.length > 0 && (state.allMatching || ids.every((id) => state.selected.has(id)));
  $("select-page").checked = allShown;
  $("select-page").indeterminate = !allShown && ids.some((id) => state.selected.has(id));

  const all = $("select-all-matching");
  all.hidden = state.allMatching || !allShown || state.total <= ids.length;
  all.textContent = `Select all ${fmt(state.total)} matching`;

  for (const row of $("contacts").tBodies[0].rows) {
    row.querySelector("input").checked = state.allMatching || state.selected.has(Number(row.dataset.id));
  }
}

async function bulkTag(action) {
  const input = $("bulk-tag");
  const name = input.value.trim();
  if (!name) {
    input.focus();
    toast("Type a tag name first", true);
    return;
  }
  let ids = [...state.selected];
  try {
    if (state.allMatching) ids = (await api(`/api/contact-ids?${filterParams()}`)).ids;
    await api("/api/bulk-tags", { contact_ids: ids, [action]: [name] });
  } catch (err) {
    toast(err.message, true);
    return;
  }
  const people = `${fmt(ids.length)} ${ids.length === 1 ? "person" : "people"}`;
  toast(action === "add" ? `Tagged ${people} “${name}”` : `Removed “${name}” from ${people}`);
  input.value = "";
  await refreshAll();
  if (state.openId) openContact(state.openId);
}

// ---- tag sidebar ---------------------------------------------------------------------

function tagItem({ label, count, active, onclick, tagName }) {
  const dot = el("span", { class: tagName ? "dot" : "dot none" });
  if (tagName) dot.style.setProperty("--h", hue(tagName));
  const tools = tagName && state.managing
    ? [
        el("button", { class: "tag-tool", type: "button", title: "Rename", "aria-label": `Rename ${tagName}`,
          onclick: () => renameTag(tagName) }, "✎"),
        el("button", { class: "tag-tool delete", type: "button", title: "Delete", "aria-label": `Delete ${tagName}`,
          onclick: () => deleteTag(tagName, count) }, "×"),
      ]
    : [];
  return el(
    "li",
    {},
    el("button", { class: active ? "tag-filter active" : "tag-filter", type: "button", onclick },
      dot, el("span", { class: "name" }, label), el("span", { class: "count" }, count === undefined ? "" : fmt(count))),
    tools,
  );
}

function renderTags() {
  const items = [
    tagItem({ label: "Everyone", count: state.stats?.total, active: !state.tag && !state.untagged,
      onclick: () => setTagFilter("", false) }),
    tagItem({ label: "Untagged", count: state.stats?.untagged, active: state.untagged,
      onclick: () => setTagFilter("", true) }),
    ...state.tags.map((t) => tagItem({
      label: t.name, count: t.count, tagName: t.name,
      active: state.tag.toLowerCase() === t.name.toLowerCase(),
      onclick: () => setTagFilter(t.name, false),
    })),
  ];
  if (!state.tags.length) {
    items.push(el("li", { class: "muted" },
      "No tags yet. Tick people in the list and use “Add tag”, or add one from a person's details."));
  }
  $("tag-list").replaceChildren(...items);
  $("manage-tags").hidden = state.tags.length === 0;
  $("manage-tags").textContent = state.managing ? "Done" : "Edit";
}

function setTagFilter(tag, untagged) {
  state.tag = tag;
  state.untagged = untagged;
  renderTags();
  loadContacts();
}

async function renameTag(name) {
  const next = (window.prompt(`Rename the tag “${name}” to:`, name) || "").trim();
  if (!next || next === name) return;
  try {
    await api("/api/tags/rename", { old: name, new: next });
  } catch (err) {
    toast(err.message, true);
    return;
  }
  if (state.tag.toLowerCase() === name.toLowerCase()) state.tag = next;
  toast(`Renamed to “${next}”`);
  await refreshAll();
  if (state.openId) openContact(state.openId);
}

async function deleteTag(name, count) {
  const ok = window.confirm(`Delete the tag “${name}”? It will be removed from ${fmt(count)} ${count === 1 ? "person" : "people"}.`);
  if (!ok) return;
  try {
    await api("/api/tags/delete", { name });
  } catch (err) {
    toast(err.message, true);
    return;
  }
  if (state.tag.toLowerCase() === name.toLowerCase()) state.tag = "";
  toast(`Deleted “${name}”`);
  await refreshAll();
  if (state.openId) openContact(state.openId);
}

// ---- detail drawer -------------------------------------------------------------------

async function openContact(id) {
  if (state.flushNotes) state.flushNotes();
  let contact;
  try {
    contact = await api(`/api/contacts/${id}`);
  } catch (err) {
    toast(err.message, true);
    return;
  }
  state.openId = id;
  highlightOpenRow();
  renderDrawer(contact);
}

function closeDrawer() {
  if (state.flushNotes) state.flushNotes();
  state.flushNotes = null;
  state.openId = null;
  $("drawer").hidden = true;
  highlightOpenRow();
}

function renderDrawerTags(contact) {
  $("drawer-tags").replaceChildren(
    ...contact.tags.map((t) => chip(t, () => changeContactTags(contact, { remove: [t] }))),
  );
}

async function changeContactTags(contact, change) {
  try {
    contact.tags = (await api(`/api/contacts/${contact.id}/tags`, change)).tags;
  } catch (err) {
    toast(err.message, true);
    return;
  }
  renderDrawerTags(contact);
  const listed = state.contacts.find((c) => c.id === contact.id);
  if (listed) {
    listed.tags = contact.tags;
    rerenderRow(listed);
  }
  try {
    await Promise.all([loadTags(), loadStats()]);
  } catch (err) {
    toast(err.message, true);
  }
}

function notesEditor(contact) {
  const status = el("span", { class: "save-state muted" });
  const box = el("textarea", {
    class: "notes",
    "aria-label": "Notes",
    placeholder: "How you know them, what they care about, what to follow up on…",
  });
  box.value = contact.notes || "";

  let timer = null;
  let chain = Promise.resolve();
  const save = () => {
    clearTimeout(timer);
    timer = null;
    const value = box.value;
    status.textContent = "Saving…";
    chain = chain
      .then(() => api(`/api/contacts/${contact.id}/notes`, { notes: value }))
      .then(() => { if (box.value === value) status.textContent = "Saved"; })
      .catch((err) => { status.textContent = "Not saved"; toast(err.message, true); });
  };
  box.addEventListener("input", () => {
    status.textContent = "Editing…";
    clearTimeout(timer);
    timer = setTimeout(save, 600);
  });
  box.addEventListener("blur", () => { if (timer) save(); });
  state.flushNotes = () => { if (timer) save(); };
  return { status, box };
}

function renderDrawer(contact) {
  const sources = (contact.sources || "").split(",").filter(Boolean)
    .map((s) => (s === "csv" ? "LinkedIn export" : s === "linkedin" ? "Live sync" : s)).join(", ");
  const facts = [
    ["Company", contact.company],
    ["Position", contact.position],
    ["Location", contact.location],
    ["Email", contact.email ? el("a", { href: `mailto:${contact.email}` }, contact.email) : ""],
    ["Phone", contact.phone ? el("a", { href: `tel:${contact.phone.replace(/[^\d+]/g, "")}` }, contact.phone) : ""],
    ["Connected", contact.connected_on ? formatDate(contact.connected_on) : ""],
    ["Source", sources],
  ].filter(([, value]) => value);

  const tagInput = el("input", { list: "tag-options", placeholder: "Add a tag", "aria-label": "Add a tag", autocomplete: "off" });
  const notes = notesEditor(contact);

  $("drawer").replaceChildren(
    el("div", { class: "drawer-head" },
      el("div", {}, el("h2", {}, fullName(contact)), contact.headline ? el("p", {}, contact.headline) : null),
      el("button", { class: "close", type: "button", "aria-label": "Close", onclick: closeDrawer }, "×")),
    isLinkedInUrl(contact.profile_url)
      ? el("a", { class: "btn primary", href: contact.profile_url, target: "_blank", rel: "noopener noreferrer" },
          "Open on LinkedIn ↗")
      : null,
    facts.length ? el("dl", { class: "facts" }, facts.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])) : null,
    el("h3", {}, "Tags"),
    el("div", { class: "chips", id: "drawer-tags" }),
    el("form", {
      class: "tag-add",
      onsubmit: (event) => {
        event.preventDefault();
        const name = tagInput.value.trim();
        if (!name) return;
        tagInput.value = "";
        changeContactTags(contact, { add: [name] });
      },
    }, tagInput, el("button", { class: "btn", type: "submit" }, "Add")),
    el("h3", {}, "Notes", notes.status),
    notes.box,
    el("h3", {}, "Outreach"),
    contact.outreach.length
      ? el("ul", { class: "outreach" }, contact.outreach.map((o) => el("li", {},
          el("div", { class: "meta" },
            el("span", { class: "campaign" }, o.campaign),
            el("span", {}, [o.status, o.relevance ? `${o.relevance} fit` : ""].filter(Boolean).join(" · "))),
          el("p", {}, o.message))))
      : el("p", { class: "muted" }, "No outreach yet."),
  );
  renderDrawerTags(contact);
  $("drawer").hidden = false;
}

// ---- wiring --------------------------------------------------------------------------

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function init() {
  const reload = debounce(() => loadContacts(), 250);
  for (const id of FILTER_IDS) $(id).addEventListener("input", reload);
  $("has_email").addEventListener("change", () => loadContacts());
  $("sort").addEventListener("change", (event) => { state.sort = event.target.value; loadContacts(); });
  $("clear-filters").addEventListener("click", () => {
    for (const id of FILTER_IDS) $(id).value = "";
    $("has_email").checked = false;
    setTagFilter("", false);
  });

  $("more").addEventListener("click", () => loadContacts({ append: true }));
  $("select-page").addEventListener("change", (event) => {
    if (event.target.checked) {
      for (const c of state.contacts) state.selected.add(c.id);
      renderSelection();
    } else {
      clearSelection();
    }
  });
  $("select-all-matching").addEventListener("click", () => { state.allMatching = true; renderSelection(); });
  $("clear-selection").addEventListener("click", clearSelection);
  $("bulk-add").addEventListener("click", () => bulkTag("add"));
  $("bulk-remove").addEventListener("click", () => bulkTag("remove"));
  $("bulk-tag").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); bulkTag("add"); }
  });
  $("manage-tags").addEventListener("click", () => { state.managing = !state.managing; renderTags(); });

  document.addEventListener("keydown", (event) => {
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (event.key === "Escape") {
      if (!$("drawer").hidden) closeDrawer();
      else if (state.selected.size || state.allMatching) clearSelection();
    } else if (event.key === "/" && !typing) {
      event.preventDefault();
      $("keyword").focus();
    }
  });
  window.addEventListener("beforeunload", () => { if (state.flushNotes) state.flushNotes(); });
  // On a phone, start with filters folded away so the list is visible.
  if (window.matchMedia("(max-width: 760px)").matches) document.querySelector(".filters").open = false;

  refreshAll();
}

init();
