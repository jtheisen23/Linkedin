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

// replaceChildren() would print a null or false as text, so drop those first.
function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false && c !== ""));
}

// Grow a textarea to fit its text, so a whole message is visible without scrolling.
function autosize(box) {
  box.style.height = "auto";
  box.style.height = `${box.scrollHeight + 2}px`;
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

function titleOf(c) {
  return c.position || c.headline || "";
}

// Under the name: the headline when it says more than the Title and Company columns
// already do, otherwise where they are. Never a repeat of what's beside it.
function subtitle(c) {
  const headline = (c.headline || "").trim();
  if (headline && c.position) {
    const h = headline.toLowerCase();
    const echoes = h.includes(c.position.toLowerCase()) && (!c.company || h.includes(c.company.toLowerCase()));
    if (!echoes) return headline;
  }
  return c.location || "";
}

function contactRow(c) {
  const name = fullName(c);
  const title = titleOf(c);
  const role = [title, c.company].filter(Boolean).join(" · ");
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
      el("span", { class: "sub" }, subtitle(c))),
    el("td", { class: "col-role" }, el("span", { class: "role" }, title || "—")),
    el("td", { class: "col-company" }, el("span", { class: "role" }, c.company || "—")),
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
  renderDraftForTag();
  loadContacts();
}

function renderDraftForTag() {
  const button = $("draft-for-tag");
  button.hidden = !state.tag;
  button.textContent = `Write messages to “${state.tag}”`;
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

  fill($("drawer"),
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

// ---- views ---------------------------------------------------------------------------

function parseHash() {
  const raw = location.hash.slice(1);
  const slash = raw.indexOf("/");
  const view = slash === -1 ? raw : raw.slice(0, slash);
  return { view: view === "campaigns" ? "campaigns" : "people",
           campaign: slash === -1 ? null : decodeURIComponent(raw.slice(slash + 1)) };
}

function route() {
  const { view, campaign } = parseHash();
  const onCampaigns = view === "campaigns";
  $("people-view").hidden = onCampaigns;
  $("campaigns-view").hidden = !onCampaigns;
  $("export").hidden = onCampaigns;
  for (const [id, active] of [["tab-people", !onCampaigns], ["tab-campaigns", onCampaigns]]) {
    $(id).classList.toggle("active", active);
    if (active) $(id).setAttribute("aria-current", "page");
    else $(id).removeAttribute("aria-current");
  }
  if (onCampaigns) {
    if (campaign && campaign !== camp.selected) {
      camp.selected = campaign;
      camp.status = "draft";
    }
    loadCampaigns();
  }
}

function showCampaign(name) {
  location.hash = `#campaigns/${encodeURIComponent(name)}`;
}

// ---- campaigns -----------------------------------------------------------------------

const STATUS_TABS = [["draft", "To send"], ["sent", "Sent"], ["replied", "Replied"], ["skipped", "Skipped"]];

const camp = {
  list: [],
  selected: null,
  status: "draft",
  items: [],
  total: 0,
  sentToday: 0,
  hasKey: true,
  job: null,
  poll: null,
  dismissedJob: null,
  lastBrief: "",
};

function currentCampaign() {
  return camp.list.find((c) => c.name === camp.selected) || null;
}

async function loadCampaigns() {
  let data;
  try {
    data = await api("/api/campaigns");
  } catch (err) {
    toast(err.message, true);
    return;
  }
  camp.list = data.campaigns;
  camp.sentToday = data.sent_today;
  camp.hasKey = data.has_api_key;
  if (!currentCampaign()) camp.selected = camp.list.length ? camp.list[0].name : null;
  setJob(data.job);
  renderCampaignList();
  renderPace();
  await loadOutreach();
}

function campaignSummary(c) {
  const parts = [`${fmt(c.draft)} to send`, `${fmt(c.sent)} sent`];
  if (c.replied) parts.push(`${fmt(c.replied)} replied`);
  return parts.join(" · ");
}

function renderCampaignList() {
  $("campaign-list").replaceChildren(...camp.list.map((c) => el("li", {},
    el("button", {
      class: c.name === camp.selected ? "campaign-item active" : "campaign-item",
      type: "button",
      onclick: () => showCampaign(c.name),
    }, el("span", { class: "name" }, c.name), el("span", { class: "meta" }, campaignSummary(c))))));
}

function renderPace() {
  const n = camp.sentToday;
  const node = $("sent-today");
  node.classList.toggle("warn", n > 30);
  node.replaceChildren(
    el("strong", {}, `${fmt(n)} sent today`),
    el("span", { class: "muted" }, n > 30
      ? ". That's a lot for one day; LinkedIn may flag it."
      : ". About 20–30 a day is a safe pace."),
  );
}

async function loadOutreach({ append = false } = {}) {
  const c = currentCampaign();
  if (!c) {
    renderNoCampaigns();
    return;
  }
  const params = new URLSearchParams({
    campaign: c.name, status: camp.status, limit: 50, offset: append ? camp.items.length : 0,
  });
  let data;
  try {
    data = await api(`/api/outreach?${params}`);
  } catch (err) {
    toast(err.message, true);
    return;
  }
  camp.items = append ? camp.items.concat(data.items) : data.items;
  camp.total = data.total;
  renderCampaignDetail(c);
}

function renderNoCampaigns() {
  $("campaign-detail").replaceChildren(el("div", { class: "empty" },
    el("h2", {}, "No campaigns yet"),
    el("p", {}, "Tag the people you want to reach on the People tab, then start a campaign. " +
      "Claude writes a personal message for each person, and you send them from here."),
    el("button", { class: "btn primary", type: "button", onclick: () => openCampaignDialog(null) }, "+ New campaign")));
}

const EMPTY_TEXT = {
  draft: "Nothing left to send here. Use “Write more drafts” to reach more people.",
  sent: "Nothing marked as sent yet.",
  replied: "No replies yet. When someone answers, find them under Sent and click “Got a reply”.",
  skipped: "Nothing skipped.",
};

function renderStatusTabs(c) {
  return STATUS_TABS.map(([key, label]) => el("button", {
    class: key === camp.status ? "status-tab active" : "status-tab",
    type: "button",
    role: "tab",
    "aria-selected": key === camp.status ? "true" : "false",
    onclick: () => { camp.status = key; loadOutreach(); },
  }, label, el("span", { class: "count" }, fmt(c[key]))));
}

function renderCampaignDetail(c) {
  fill($("campaign-detail"),
    el("div", { class: "campaign-head" },
      el("div", {}, el("h1", {}, c.name), el("p", { class: "muted" }, campaignSummary(c))),
      el("button", { class: "btn", type: "button", onclick: () => openCampaignDialog(c) }, "Write more drafts")),
    el("div", { class: "status-tabs", role: "tablist", id: "status-tabs" }, renderStatusTabs(c)),
    c.brief ? el("details", { class: "brief" }, el("summary", {}, "Brief"), el("p", {}, c.brief)) : null,
    el("div", { class: "cards", id: "cards" }, camp.items.map(card)),
    el("div", { class: "empty", id: "cards-empty", hidden: camp.items.length > 0 }, emptyText(c)),
    camp.items.length < camp.total
      ? el("button", { class: "btn more", type: "button", onclick: () => loadOutreach({ append: true }) }, "Show more")
      : null,
  );
  document.querySelectorAll("#cards .message").forEach(autosize);
}

function emptyText(c) {
  const writing = camp.job?.running && camp.job.campaign === c.name;
  if (camp.status === "draft" && writing) return "Claude is writing drafts. They'll appear here as they're ready.";
  return EMPTY_TEXT[camp.status];
}

function fitBadge(relevance) {
  return el("span", { class: `fit ${relevance}`, title: "How well your offer fits them, according to Claude" },
    `${relevance[0].toUpperCase()}${relevance.slice(1)} fit`);
}

function formatDateTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function card(item) {
  const name = fullName(item);
  const role = [titleOf(item), item.company].filter(Boolean).join(" · ");
  const canOpen = isLinkedInUrl(item.profile_url);
  let flush = async () => {};
  let body;
  let foot;

  if (item.status === "draft") {
    const box = el("textarea", { class: "message", "aria-label": `Message to ${name}`, rows: 6 });
    box.value = item.message;
    const count = el("span", { class: "chars muted" });
    const saveState = el("span", { class: "save-state muted" });
    const updateCount = () => { count.textContent = `${fmt(box.value.length)} characters`; };
    updateCount();

    let timer = null;
    const save = async () => {
      clearTimeout(timer);
      timer = null;
      const value = box.value;
      if (!value.trim()) {
        saveState.textContent = "A message can't be empty";
        return;
      }
      saveState.textContent = "Saving…";
      try {
        await api(`/api/outreach/${item.id}`, { message: value });
        item.message = value;
        if (box.value === value) saveState.textContent = "Saved";
      } catch (err) {
        saveState.textContent = "Not saved";
        toast(err.message, true);
      }
    };
    flush = async () => { if (timer) await save(); };
    box.addEventListener("input", () => {
      updateCount();
      autosize(box);
      saveState.textContent = "";
      clearTimeout(timer);
      timer = setTimeout(save, 700);
    });
    box.addEventListener("blur", () => { if (timer) save(); });

    body = box;
    foot = [
      count, saveState, el("span", { class: "spacer" }),
      el("button", { class: "btn primary", type: "button", onclick: (event) => copyAndOpen(item, box, event.currentTarget) },
        canOpen ? "Copy & open LinkedIn" : "Copy message"),
      el("button", { class: "btn", type: "button", onclick: () => setStatus(item, "sent", flush) }, "Mark sent"),
      el("button", { class: "link", type: "button", onclick: () => setStatus(item, "skipped", flush) }, "Skip"),
    ];
  } else {
    body = el("p", { class: "sent-message" }, item.message);
    const moves = {
      sent: [["Got a reply", "replied", "btn primary"], ["Back to drafts", "draft", "link"]],
      replied: [["Back to sent", "sent", "link"]],
      skipped: [["Back to drafts", "draft", "btn"]],
    }[item.status] || [];
    foot = [
      el("span", { class: "muted" }, item.sent_at ? `Sent ${formatDateTime(item.sent_at)}` : ""),
      el("span", { class: "spacer" }),
      canOpen ? el("a", { class: "btn", href: item.profile_url, target: "_blank", rel: "noopener noreferrer" }, "Open LinkedIn ↗") : null,
      moves.map(([label, status, cls]) =>
        el("button", { class: cls, type: "button", onclick: () => setStatus(item, status, flush) }, label)),
    ];
  }

  return el("article", { class: "card", "data-id": item.id },
    el("div", { class: "card-head" },
      el("div", { class: "who" },
        el("button", { class: "name", type: "button", title: "See their details, tags and notes",
          onclick: () => openContact(item.contact_id) }, name),
        role ? el("span", { class: "muted" }, role) : null),
      item.relevance ? fitBadge(item.relevance) : null),
    item.reason ? el("p", { class: "reason muted" }, item.reason) : null,
    body,
    el("div", { class: "card-foot" }, foot),
  );
}

async function copyAndOpen(item, box, button) {
  try {
    await navigator.clipboard.writeText(box.value);
  } catch {
    box.select();
    document.execCommand("copy");
  }
  button.textContent = "Copied ✓";
  if (isLinkedInUrl(item.profile_url)) window.open(item.profile_url, "_blank", "noopener");
  toast("Copied. On their profile, click Message, paste and send. Then come back and click “Mark sent”.");
}

const STATUS_TOASTS = {
  sent: () => `Marked sent · ${fmt(camp.sentToday)} sent today`,
  skipped: () => "Skipped",
  replied: () => "Marked as replied",
  draft: () => "Moved back to drafts",
};

async function setStatus(item, status, flush) {
  await flush();
  try {
    camp.sentToday = (await api(`/api/outreach/${item.id}`, { status })).sent_today;
  } catch (err) {
    toast(err.message, true);
    return;
  }
  const c = currentCampaign();
  if (c) {
    c[item.status] -= 1;
    c[status] += 1;
  }
  camp.items = camp.items.filter((x) => x.id !== item.id);
  camp.total -= 1;
  document.querySelector(`.card[data-id="${item.id}"]`)?.remove();
  if (c) {
    $("status-tabs").replaceChildren(...renderStatusTabs(c));
    document.querySelector(".campaign-head p").textContent = campaignSummary(c);
  }
  $("cards-empty").hidden = camp.items.length > 0;
  renderCampaignList();
  renderPace();
  toast(STATUS_TOASTS[status]());
}

// ---- writing drafts ------------------------------------------------------------------

function campaignFormBody() {
  return {
    campaign: $("cf-name").value.trim(),
    tag: $("cf-tag").value,
    title: $("cf-title").value.trim(),
    limit: Number($("cf-limit").value || 0),
    effort: $("cf-effort").value,
    brief: $("cf-brief").value,
  };
}

function openCampaignDialog(existing, tag = "") {
  $("campaign-dialog-title").textContent = existing ? `Write more drafts: ${existing.name}` : "New campaign";
  $("cf-name").value = existing ? existing.name : "";
  $("cf-name").readOnly = Boolean(existing);
  $("cf-brief").value = existing?.brief || camp.lastBrief || "";
  $("cf-tag").replaceChildren(
    el("option", { value: "" }, "Everyone"),
    ...state.tags.map((t) => el("option", { value: t.name }, `${t.name} (${fmt(t.count)})`)),
  );
  $("cf-tag").value = state.tags.some((t) => t.name === tag) ? tag : "";
  $("cf-title").value = "";
  $("cf-key-warning").hidden = camp.hasKey;
  $("cf-submit").disabled = false;
  $("campaign-dialog").showModal();
  (existing ? $("cf-tag") : $("cf-name")).focus();
  updatePlan();
}

let planSeq = 0;
async function updatePlan() {
  const seq = ++planSeq;
  const body = campaignFormBody();
  const out = $("cf-plan");
  if (!body.campaign) {
    out.textContent = "Name the campaign to see who it will include.";
    return;
  }
  try {
    const plan = await api("/api/campaigns/plan", body);
    if (seq !== planSeq) return;
    const people = `${fmt(plan.count)} ${plan.count === 1 ? "person" : "people"}`;
    const skipped = plan.already ? ` · ${fmt(plan.already)} already in this campaign will be skipped` : "";
    out.textContent = plan.count
      ? `${people} · roughly $${plan.cost[0].toFixed(2)}–$${plan.cost[1].toFixed(2)} of Claude usage${skipped}`
      : `No one new to write to${skipped}.`;
    $("cf-submit").disabled = plan.count === 0;
  } catch (err) {
    if (seq === planSeq) out.textContent = err.message;
  }
}

async function submitCampaign(event) {
  event.preventDefault();
  const body = campaignFormBody();
  if (!body.brief.trim()) {
    toast("Write a brief first, so Claude knows what you're reaching out about.", true);
    $("cf-brief").focus();
    return;
  }
  $("cf-submit").disabled = true;
  let result;
  try {
    result = await api("/api/campaigns/draft", body);
  } catch (err) {
    $("cf-submit").disabled = false;
    toast(err.message, true);
    return;
  }
  camp.lastBrief = body.brief;
  $("campaign-dialog").close();
  camp.selected = result.job.campaign;
  camp.status = "draft";
  setJob(result.job);
  if (parseHash().campaign === result.job.campaign) loadCampaigns();
  else showCampaign(result.job.campaign);
}

function setJob(job) {
  camp.job = job;
  renderJob();
  if (job?.running && !camp.poll) camp.poll = setInterval(pollJob, 1500);
  if (!job?.running && camp.poll) {
    clearInterval(camp.poll);
    camp.poll = null;
  }
}

async function pollJob() {
  let data;
  try {
    data = await api("/api/drafting");
  } catch {
    return;
  }
  const finished = camp.job?.running && !data.job.running;
  setJob(data.job);
  // Refresh when it's done, unless you're in the middle of editing a message.
  if (finished && !document.activeElement?.classList.contains("message")) loadCampaigns();
}

async function stopJob() {
  try {
    setJob((await api("/api/drafting/stop", {})).job);
    toast("Stopping after the drafts already in progress…");
  } catch (err) {
    toast(err.message, true);
  }
}

function renderJob() {
  const job = camp.job;
  const banner = $("job-banner");
  if (!job || !job.started_at || camp.dismissedJob === job.started_at) {
    banner.hidden = true;
    return;
  }
  const failedNote = job.failed ? ` · ${fmt(job.failed)} couldn't be written` : "";
  if (job.running) {
    banner.className = "job-banner";
    fill(banner,
      el("div", { class: "job-text" },
        el("strong", {}, `Claude is writing drafts for “${job.campaign}”`),
        el("span", { class: "muted" }, `${fmt(job.done)} of ${fmt(job.total)} written${failedNote}`)),
      el("div", { class: "job-actions" },
        job.done ? el("button", { class: "btn", type: "button", onclick: loadCampaigns }, "Show new drafts") : null,
        el("button", { class: "link", type: "button", onclick: stopJob }, "Stop")),
      el("progress", { max: job.total, value: job.done + job.failed }),
    );
  } else {
    banner.className = job.error ? "job-banner failed" : "job-banner done";
    const summary = job.error
      ? job.error
      : `${fmt(job.done)} drafts written for “${job.campaign}”${failedNote}` +
        (job.failed ? ". Use “Write more drafts” to retry them." : ".");
    fill(banner,
      el("div", { class: "job-text" },
        el("strong", {}, job.error ? "Drafting stopped" : job.stopped ? "Stopped" : "Drafts ready"),
        el("span", {}, summary)),
      el("button", { class: "close", type: "button", "aria-label": "Dismiss",
        onclick: () => { camp.dismissedJob = job.started_at; renderJob(); } }, "×"),
      job.errors.length
        ? el("details", {}, el("summary", {}, "What went wrong"), el("ul", {}, job.errors.map((e) => el("li", {}, e))))
        : null,
    );
  }
  banner.hidden = false;
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
  $("draft-for-tag").addEventListener("click", () => {
    location.hash = "#campaigns";
    openCampaignDialog(null, state.tag);
  });

  $("new-campaign").addEventListener("click", () => openCampaignDialog(null));
  $("campaign-form").addEventListener("submit", submitCampaign);
  $("cf-cancel").addEventListener("click", () => $("campaign-dialog").close());
  const replan = debounce(updatePlan, 300);
  for (const id of ["cf-name", "cf-tag", "cf-title", "cf-limit", "cf-effort"]) {
    $(id).addEventListener("input", replan);
  }
  window.addEventListener("hashchange", route);

  document.addEventListener("keydown", (event) => {
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (event.key === "Escape") {
      if ($("campaign-dialog").open) return; // the dialog closes itself
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
  route();
}

init();
