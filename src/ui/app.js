/* VMBox dashboard */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATE_LABEL = { running: "Läuft", paused: "Pausiert", saved: "Gespeichert", stopped: "Aus", busy: "Beschäftigt" };
const CLIPBOARD = [["off", "Aus"], ["in", "Empfangen"], ["out", "Senden"], ["both", "Beidseitig"]];
const PAGES = [
  ["vms", "grid", "Maschinen"],
  ["usb", "usb", "USB-Geräte"],
  ["network", "network", "Netzwerk"],
  ["isos", "disc", "ISO-Abbilder"],
  ["system", "system", "System"],
];

const S = {
  state: null,
  page: "vms",
  host: null,
  templates: null,
  colors: [],
  isos: [],
  networks: [],
  usb: [],
  media: { devices: [], jobs: [] },
  mediaIsos: {},
  wifi: null,
  showHidden: false,
  drawer: null,
  drawerTab: "general",
  draft: null,
  snapshots: [],
  rendered: {},
};

/* ------------------------------------------------------------------ */
/* API                                                                 */
/* ------------------------------------------------------------------ */
async function api(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (method !== "GET") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body === undefined ? {} : body);
  }
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (res.status === 401) {
    poll();
  }
  if (!res.ok) {
    const err = new Error(data.error || "Fehler " + res.status);
    err.status = res.status;
    throw err;
  }
  return data;
}

async function run(promise, okMessage) {
  try {
    const result = await promise;
    if (okMessage) toast(okMessage);
    refreshSoon();
    return result;
  } catch (err) {
    toast(err.message, true);
    throw err;
  }
}

function toast(message, error) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.innerHTML = icon(error ? "alert" : "check") + "<span>" + esc(message) + "</span>";
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), error ? 6000 : 3500);
}

/* ------------------------------------------------------------------ */
/* Formatting                                                          */
/* ------------------------------------------------------------------ */
function fmtBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return (bytes >= 100 || i === 0 ? Math.round(bytes) : bytes.toFixed(1)).toString().replace(".", ",") + " " + units[i];
}
const fmtMB = (mb) => fmtBytes(mb * 1024 * 1024);
function fmtUptime(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return (d ? d + " T " : "") + h + " Std " + m + " Min";
}
function networkLabel(id) {
  if (!id || id === "nat") return "NAT";
  if (id === "none") return "Kein Netz";
  const [kind, arg] = id.split(":");
  return { bridged: "Brücke ", intnet: "Intern ", hostonly: "Host-only " }[kind] + arg;
}
const vmById = (uuid) => (S.state && S.state.vms || []).find((v) => v.uuid === uuid);
const isLegacy = (vm) => !!((S.templates || []).find((t) => t.id === vm.template) || {}).legacy;
const templateLabel = (vm) => {
  const t = (S.templates || []).find((t) => t.id === vm.template);
  return t ? t.label : vm.ostype;
};

/* ------------------------------------------------------------------ */
/* Polling                                                             */
/* ------------------------------------------------------------------ */
let pollTimer = null;
async function poll() {
  clearTimeout(pollTimer);
  try {
    const st = await fetch("/api/state", { credentials: "same-origin" }).then((r) => r.json());
    const wasAuth = S.state && S.state.authenticated;
    S.state = st;
    if (st.authenticated) {
      if (!wasAuth) await onLogin();
      renderApp();
    } else {
      closeOverlay();
      renderAuth();
    }
  } catch (e) {
    /* vmboxd restarting - try again */
  }
  pollTimer = setTimeout(poll, 2000);
}
function refreshSoon() { setTimeout(poll, 250); setTimeout(loadPage, 400); }

async function onLogin() {
  S.rendered = {};
  const t = await api("GET", "/api/templates");
  S.templates = t.templates;
  S.colors = t.colors;
  await loadPage();
  loadHost();
}

async function loadHost() {
  try { S.host = await api("GET", "/api/host"); } catch (e) { /* ignore */ }
  if (S.page === "vms") renderStats();
  if (S.page === "system") renderPage(true);
}
setInterval(() => { if (S.state && S.state.authenticated) loadHost(); }, 15000);

async function loadPage() {
  if (!S.state || !S.state.authenticated) return;
  try {
    if (S.page === "usb") S.usb = (await api("GET", "/api/usb")).devices;
    if (S.page === "network") {
      S.networks = (await api("GET", "/api/networks")).networks;
      try { S.wifi = await api("GET", "/api/wifi"); } catch (e) { S.wifi = { networks: [], devices: [], error: e.message }; }
    }
    if (S.page === "isos") {
      S.isos = (await api("GET", "/api/isos")).isos;
      try { S.media = await api("GET", "/api/media"); } catch (e) { S.media = { devices: [], jobs: [], error: e.message }; }
      const present = S.media.devices.map((d) => d.device);
      Object.keys(S.mediaIsos).forEach((dev) => { if (!present.includes(dev)) delete S.mediaIsos[dev]; });
    }
  } catch (e) { /* handled by poll */ }
  renderPage();
}
setInterval(() => { if (S.page === "usb" || S.page === "isos") loadPage(); }, 2000);

/* ------------------------------------------------------------------ */
/* Auth screen                                                         */
/* ------------------------------------------------------------------ */
function tickClock() {
  const now = new Date();
  $("#auth-clock").textContent = now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  $("#auth-date").textContent = now.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}
setInterval(tickClock, 1000);

let authMode = null;
function renderAuth() {
  $("#app").hidden = true;
  $("#auth").hidden = false;
  tickClock();
  const st = S.state;
  const mode = st.setup_required ? "setup" : "login";
  if (mode === authMode) return;
  authMode = mode;
  $("#auth-error").textContent = "";
  $("#auth-password").value = "";
  $("#auth-password2").value = "";
  if (mode === "setup") {
    $("#auth-title").textContent = "Willkommen bei VMBox";
    $("#auth-text").textContent = "Lege ein Passwort fest, das die Verwaltung und den Sperrbildschirm schützt.";
    $("#auth-password2").hidden = false;
    $("#auth-submit").textContent = "Einrichten";
  } else {
    $("#auth-title").textContent = "VMBox ist gesperrt";
    $("#auth-text").textContent = "Gib dein Passwort ein, um fortzufahren.";
    $("#auth-password2").hidden = true;
    $("#auth-submit").textContent = "Entsperren";
  }
  $("#auth-password").focus();
}

$("#auth-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const pw = $("#auth-password").value;
  const err = $("#auth-error");
  err.textContent = "";
  try {
    if (authMode === "setup") {
      if (pw !== $("#auth-password2").value) { err.textContent = "Die Passwörter stimmen nicht überein."; return; }
      await api("POST", "/api/setup", { password: pw });
    } else {
      await api("POST", "/api/login", { password: pw });
    }
    authMode = null;
    $("#auth-password").value = "";
    $("#auth-password2").value = "";
    poll();
  } catch (e) {
    err.textContent = e.message;
    $("#auth-password").select();
  }
});

/* ------------------------------------------------------------------ */
/* Main shell                                                          */
/* ------------------------------------------------------------------ */
function renderApp() {
  $("#auth").hidden = true;
  $("#app").hidden = false;
  authMode = null;
  const vms = S.state.vms || [];
  const running = vms.filter((v) => v.running && v.type === "user").length;
  $("#nav").innerHTML = PAGES.map(([id, ic, label]) =>
    '<button data-page="' + id + '" class="' + (S.page === id ? "active" : "") + '">' + icon(ic) + "<span>" + label + "</span>" +
    (id === "vms" && running ? '<span class="count">' + running + "</span>" : "") + "</button>").join("");
  $("#live-hint").hidden = !S.state.live;
  $("#btn-lock").innerHTML = icon("lock") + "<span>Sperren</span>";
  if (S.page === "vms") renderPage();
  if (S.drawer) updateDrawer();
}

$("#nav").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-page]");
  if (!btn) return;
  S.page = btn.dataset.page;
  S.rendered = {};
  renderApp();
  renderPage(true);
  loadPage();
  $("#main").scrollTop = 0;
});

$("#btn-lock").addEventListener("click", async () => {
  await api("POST", "/api/lock").catch(() => {});
  poll();
});

function renderPage(force) {
  const renderers = { vms: renderVMs, usb: renderUSB, network: renderNetwork, isos: renderISOs, system: renderSystem };
  const html = renderers[S.page]();
  if (!force && S.rendered[S.page] === html) return;
  S.rendered[S.page] = html;
  const main = $("#main");
  const scroll = main.scrollTop;
  main.innerHTML = html;
  main.scrollTop = scroll;
  if (S.page === "vms") renderStats();
}

function pageHead(title, sub, actions) {
  return '<div class="page-head"><div><h1>' + title + "</h1><p>" + sub + '</p></div><div class="actions">' + (actions || "") + "</div></div>";
}

/* ------------------------------------------------------------------ */
/* VM overview                                                         */
/* ------------------------------------------------------------------ */
function renderStats() {
  const el = $("#stats");
  if (!el) return;
  const h = S.host;
  const vms = (S.state.vms || []).filter((v) => v.type === "user");
  const running = vms.filter((v) => v.running).length;
  const memUsed = h ? h.memory_total - h.memory_free : 0;
  const stoUsed = h ? h.storage.total - h.storage.free : 0;
  const stat = (ic, value, label, pct) => '<div class="stat glass"><div class="icon-wrap">' + icon(ic) + '</div><div style="flex:1;min-width:0">' +
    '<div class="value">' + value + '</div><div class="label">' + label + "</div>" +
    (pct !== undefined ? '<div class="meter"><div style="width:' + Math.min(100, pct).toFixed(0) + '%"></div></div>' : "") + "</div></div>";
  el.innerHTML =
    stat("monitor", running + " / " + vms.length, "VMs aktiv") +
    stat("memory", h ? fmtBytes(memUsed) : "–", h ? "von " + fmtBytes(h.memory_total) + " Arbeitsspeicher" : "Arbeitsspeicher", h ? (memUsed / h.memory_total) * 100 : 0) +
    stat("storage", h ? fmtBytes(h.storage.free) : "–", "frei für VMs", h && h.storage.total ? (stoUsed / h.storage.total) * 100 : 0) +
    stat("shield", h ? (h.encrypted ? "Verschlüsselt" : "Unverschlüsselt") : "–", h ? (h.tpm ? "TPM 2.0 vorhanden" : "Kein TPM") + (h.secure_boot ? " · Secure Boot" : "") : "Sicherheit");
}

function vmCard(vm, focus) {
  const st = vm.state;
  let actions = "";
  if (st === "stopped" || st === "saved") {
    actions = '<button class="btn primary small" data-act="start">' + icon("play") + (st === "saved" ? "Fortsetzen" : "Starten") + "</button>";
  } else if (st === "running") {
    actions = (vm.type === "user" ? '<button class="btn primary small" data-act="switch">' + icon("monitor") + "Anzeigen</button>" : "") +
      '<button class="btn small icon-only" title="Pausieren" data-act="pause">' + icon("pause") + "</button>" +
      '<button class="btn small icon-only" title="Ausschalten …" data-act="power">' + icon("power") + "</button>";
  } else if (st === "paused") {
    actions = '<button class="btn primary small" data-act="resume">' + icon("play") + "Fortsetzen</button>" +
      '<button class="btn small icon-only" title="Ausschalten …" data-act="power">' + icon("power") + "</button>";
  } else {
    actions = '<button class="btn small" disabled>Bitte warten …</button>';
  }
  const badges = [];
  if (vm.disposable) badges.push('<span class="badge warn">Wegwerf</span>');
  if (vm.start_on_boot) badges.push('<span class="badge blue">Autostart</span>');
  if (vm.type === "service") badges.push('<span class="badge">Service</span>');
  return '<div class="vm-card glass' + (focus ? " focused" : "") + '" data-uuid="' + vm.uuid + '" style="--vm-color:' + esc(vm.color) + '">' +
    '<div class="band" data-act="details"><div class="os">' + osIcon(vm) + '</div><div class="title"><h3>' + esc(vm.name) + "</h3>" +
    '<div class="os-name">' + esc(templateLabel(vm)) + "</div></div>" +
    (vm.slot ? '<span class="slot" title="Strg+Alt+' + vm.slot + '">Slot ' + vm.slot + "</span>" : "") + "</div>" +
    '<div class="body"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><span class="status ' + st + '">' + STATE_LABEL[st] + "</span>" + badges.join("") + "</div>" +
    '<div class="meta"><span>' + icon("memory") + fmtMB(vm.memory) + "</span><span>" + icon("cpu") + vm.cpus + " CPU</span><span>" +
    icon("network") + esc(networkLabel(vm.network)) + "</span>" + (vm.accelerate3d ? "<span>" + icon("monitor") + "3D</span>" : "") + (vm.usb_attached.length ? "<span>" + icon("usb") + vm.usb_attached.length + "</span>" : "") + "</div>" +
    '<div class="actions">' + actions + '<span class="grow"></span><button class="btn small icon-only ghost" title="Einstellungen" data-act="details">' + icon("more") + "</button></div></div></div>";
}

function renderVMs() {
  const vms = S.state.vms || [];
  const user = vms.filter((v) => v.type === "user" && (!v.hidden || S.showHidden));
  const service = vms.filter((v) => v.type === "service");
  const focus = S.state.focus;
  let html = pageHead("Virtuelle Maschinen", "Jede Aufgabe in ihrer eigenen, isolierten Umgebung.",
    '<button class="btn" data-act="toggle-hidden">' + (S.showHidden ? "Ausgeblendete verbergen" : "Ausgeblendete zeigen") + "</button>" +
    '<button class="btn primary" data-act="new">' + icon("plus") + "Neue VM</button>");
  html += '<div class="stats" id="stats"></div>';
  html += '<div class="section-title">Arbeitsumgebungen</div><div class="vm-grid">' + user.map((v) => vmCard(v, v.uuid === focus)).join("") +
    '<div class="vm-card glass new" data-act="new"><div class="plus">' + icon("plus") + "</div><strong>Neue virtuelle Maschine</strong></div></div>";
  if (service.length) {
    html += '<div class="section-title">Service-VMs</div><div class="vm-grid">' + service.map((v) => vmCard(v, false)).join("") + "</div>";
  }
  return html;
}

$("#main").addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-act]");
  if (!el) return;
  const act = el.dataset.act;
  const card = el.closest("[data-uuid]");
  const uuid = card && card.dataset.uuid;
  const vm = uuid && vmById(uuid);
  switch (act) {
    case "new": return openCreate();
    case "toggle-hidden": S.showHidden = !S.showHidden; return renderPage(true);
    case "details": return openDrawer(uuid);
    case "start": case "resume": case "pause": case "switch":
      el.disabled = true;
      return run(api("POST", "/api/vms/" + uuid + "/" + act)).catch(() => {}).finally(() => { el.disabled = false; });
    case "power": return powerDialog(vm);
    default: return pageAction(act, el);
  }
});

async function powerDialog(vm) {
  const choice = await choose("„" + vm.name + "“ beenden", "Wie soll die VM beendet werden?", [
    ["shutdown", "Herunterfahren", "primary"],
    ["save", "Zustand speichern", ""],
    ["poweroff", "Ausschalten erzwingen", "danger"],
  ]);
  if (choice) run(api("POST", "/api/vms/" + vm.uuid + "/" + choice), {
    shutdown: "Herunterfahren angefordert", save: "Zustand wird gespeichert", poweroff: "VM ausgeschaltet" }[choice]).catch(() => {});
}

/* ------------------------------------------------------------------ */
/* Modals                                                              */
/* ------------------------------------------------------------------ */
function openOverlay(html, cls) {
  $("#overlay").innerHTML = '<div class="backdrop" data-close></div><div class="' + cls + '">' + html + "</div>";
  const first = $("#overlay input, #overlay select");
  if (first) first.focus();
}
function closeOverlay() {
  $("#overlay").innerHTML = "";
  S.drawer = null;
  S.draft = null;
}
$("#overlay").addEventListener("click", (ev) => { if (ev.target.hasAttribute("data-close")) closeOverlay(); });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && $("#overlay").innerHTML) closeOverlay(); });

function choose(title, text, options) {
  return new Promise((resolve) => {
    openOverlay('<div class="modal-head"><h2>' + esc(title) + "</h2><p>" + esc(text) + '</p></div><div class="modal-foot">' +
      '<button class="btn ghost" data-choice="">Abbrechen</button>' +
      options.map(([id, label, cls]) => '<button class="btn ' + cls + '" data-choice="' + id + '">' + esc(label) + "</button>").join("") + "</div>", "modal small");
    $("#overlay").addEventListener("click", function handler(ev) {
      const btn = ev.target.closest("[data-choice]");
      if (!btn && !ev.target.hasAttribute("data-close")) return;
      $("#overlay").removeEventListener("click", handler);
      const value = btn ? btn.dataset.choice : "";
      closeOverlay();
      resolve(value || null);
    });
  });
}
const confirmBox = (title, text, label, danger) => choose(title, text, [["ok", label, danger ? "danger" : "primary"]]).then((c) => c === "ok");

function prompt2(title, text, fields, okLabel) {
  return new Promise((resolve) => {
    openOverlay('<form id="prompt-form"><div class="modal-head"><h2>' + esc(title) + "</h2><p>" + esc(text) + '</p></div><div class="modal-body">' +
      fields.map((f) => '<div class="field"><label>' + esc(f.label) + '</label><input class="input" name="' + f.name + '" type="' + (f.type || "text") + '" value="' + esc(f.value || "") + '"></div>').join("") +
      '</div><div class="modal-foot"><button type="button" class="btn ghost" data-close>Abbrechen</button><button class="btn primary">' + esc(okLabel) + "</button></div></form>", "modal small");
    const form = $("#prompt-form");
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const data = Object.fromEntries(new FormData(form).entries());
      closeOverlay();
      resolve(data);
    });
    $("#overlay").addEventListener("click", (ev) => { if (ev.target.hasAttribute("data-close")) resolve(null); });
  });
}

/* ------------------------------------------------------------------ */
/* Create VM                                                           */
/* ------------------------------------------------------------------ */
async function openCreate() {
  try {
    S.isos = (await api("GET", "/api/isos")).isos;
    S.networks = (await api("GET", "/api/networks")).networks;
  } catch (e) { toast(e.message, true); return; }
  const tpl = S.templates[0];
  const draft = { template: tpl.id, name: "", memory: tpl.memory, cpus: tpl.cpus, disk_gb: tpl.disk_gb, iso: "", network: tpl.network || "nat",
    color: S.colors[(S.state.vms || []).length % S.colors.length] };
  const render = () => {
    const t = S.templates.find((x) => x.id === draft.template);
    openOverlay('<form id="create-form"><div class="modal-head"><h2>Neue virtuelle Maschine</h2><p>Wähle eine Vorlage – die Werte lassen sich anpassen.</p></div>' +
      '<div class="modal-body"><div class="templates">' + S.templates.map((x) =>
        '<button type="button" data-tpl="' + x.id + '" class="' + (x.id === draft.template ? "active" : "") + '">' +
        osIcon({ ostype: x.ostype, type: x.type }) + esc(x.label) + "</button>").join("") + "</div>" +
      '<div class="row"><div class="field"><label>Name</label><input class="input" name="name" maxlength="64" value="' + esc(draft.name) + '" placeholder="z. B. Büro, Banking, Entwicklung" required></div>' +
      '<div class="field" style="flex:none"><label>Farbe</label><div class="colors" style="height:40px;align-items:center">' + S.colors.map((c) =>
        '<button type="button" data-color="' + c + '" class="' + (c === draft.color ? "active" : "") + '" style="background:' + c + '"></button>').join("") + "</div></div></div>" +
      '<div class="row"><div class="field"><label>Arbeitsspeicher (MB)</label><input class="input" name="memory" type="number" min="256" step="256" value="' + draft.memory + '"></div>' +
      '<div class="field"><label>CPUs</label><input class="input" name="cpus" type="number" min="1" max="64" value="' + draft.cpus + '"></div>' +
      '<div class="field"><label>Festplatte (GB)</label><input class="input" name="disk_gb" type="number" min="1" value="' + draft.disk_gb + '"></div></div>' +
      '<div class="row"><div class="field"><label>Installationsmedium</label><select class="input" name="iso"><option value="">– kein Medium –</option>' +
      S.isos.map((i) => '<option ' + (i.name === draft.iso ? "selected" : "") + ">" + esc(i.name) + "</option>").join("") + "</select>" +
      (S.isos.length ? "" : '<span class="hint">ISO-Abbilder unter „ISO-Abbilder“ hochladen.</span>') + "</div>" +
      '<div class="field"><label>Netzwerk</label><select class="input" name="network">' + S.networks.map((n) =>
        '<option value="' + esc(n.id) + '" ' + (n.id === draft.network ? "selected" : "") + ">" + esc(n.label) + "</option>").join("") + "</select></div></div>" +
      (t.tpm ? '<div class="notice">Diese Vorlage erhält UEFI, Secure Boot und ein virtuelles TPM 2.0.</div>' : "") +
      (t.legacy ? '<div class="notice warn">' + esc(t.label) + " bekommt passende Alt-Hardware (IDE, PCnet-Netzwerkkarte, AC’97-Ton, PS/2-Maus, VBoxVGA). " +
        "Da es keine Sicherheitsupdates mehr gibt, startet die VM ohne Netzwerk – bei Bedarf oben ändern, am besten auf ein internes Netz. " +
        "3D-Beschleunigung gibt es hier nicht; die aktuellen Gasterweiterungen unterstützen dieses System teils nicht mehr.</div>" :
       t.id === "windows7" ? '<div class="notice">Windows 7 erhält BIOS-Firmware, USB 2.0 und eine Intel-PRO/1000-Netzwerkkarte, für die Windows 7 eigene Treiber mitbringt. Für 3D und Aero nach der Installation die VirtualBox-Gasterweiterungen installieren.</div>' :
       t.accelerate3d ? '<div class="notice">3D-Beschleunigung ist aktiv. Sie wirkt, sobald im Gast die VirtualBox-Gasterweiterungen installiert sind.</div>' : "") +
      (t.type === "service" ? '<div class="notice">Service-VMs laufen ohne Anzeige im Hintergrund (z. B. als Netzwerk- oder Firewall-VM) und starten automatisch.</div>' : "") +
      '</div><div class="modal-foot"><button type="button" class="btn ghost" data-close>Abbrechen</button>' +
      '<button type="submit" class="btn primary" id="create-submit">' + icon("plus") + "Erstellen</button></div></form>", "modal");
    const form = $("#create-form");
    form.addEventListener("input", (ev) => { if (ev.target.name) draft[ev.target.name] = ev.target.value; });
    form.addEventListener("click", (ev) => {
      const tplBtn = ev.target.closest("[data-tpl]");
      const colBtn = ev.target.closest("[data-color]");
      if (tplBtn) {
        const nt = S.templates.find((x) => x.id === tplBtn.dataset.tpl);
        Object.assign(draft, { template: nt.id, memory: nt.memory, cpus: nt.cpus, disk_gb: nt.disk_gb, network: nt.network || "nat" });
        render();
      } else if (colBtn) {
        draft.color = colBtn.dataset.color;
        $$("[data-color]", form).forEach((b) => b.classList.toggle("active", b === colBtn));
      }
    });
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      $("#create-submit").disabled = true;
      try {
        const vm = await api("POST", "/api/vms", { ...draft, memory: +draft.memory, cpus: +draft.cpus, disk_gb: +draft.disk_gb });
        closeOverlay();
        toast("„" + vm.name + "“ wurde erstellt");
        refreshSoon();
      } catch (e) {
        toast(e.message, true);
        $("#create-submit").disabled = false;
      }
    });
    const nameInput = $("input[name=name]", form);
    nameInput.focus();
    nameInput.setSelectionRange(nameInput.value.length, nameInput.value.length);
  };
  render();
}

/* ------------------------------------------------------------------ */
/* VM drawer                                                           */
/* ------------------------------------------------------------------ */
async function openDrawer(uuid) {
  const vm = vmById(uuid);
  if (!vm) return;
  S.drawer = uuid;
  S.drawerTab = "general";
  S.draft = JSON.parse(JSON.stringify(vm));
  S.drawerState = vm.state;
  try {
    S.isos = (await api("GET", "/api/isos")).isos;
    S.networks = (await api("GET", "/api/networks")).networks;
  } catch (e) { /* ignore */ }
  renderDrawer();
}

function updateDrawer() {
  const vm = vmById(S.drawer);
  if (!vm) { closeOverlay(); return; }
  if (vm.state !== S.drawerState) {
    S.drawerState = vm.state;
    renderDrawer();
  }
}

const toggle = (name, checked, disabled) => '<label class="toggle"><input type="checkbox" data-field="' + name + '" ' +
  (checked ? "checked" : "") + " " + (disabled ? "disabled" : "") + '><span class="track"></span></label>';
const switchRow = (title, text, control) => '<div class="switch"><div class="text"><strong>' + title + "</strong><span>" + text + "</span></div>" + control + "</div>";

async function renderDrawer() {
  const vm = vmById(S.drawer);
  if (!vm) return;
  const d = S.draft;
  const off = vm.state === "stopped";
  const tab = S.drawerTab;
  let body = "";
  if (!off && ["general", "hardware", "policy"].includes(tab)) {
    body += '<div class="notice warn">Die VM ist ' + STATE_LABEL[vm.state].toLowerCase() + ". Hardware und Richtlinien können nur bei ausgeschalteter VM geändert werden.</div>";
  }
  if (tab === "general") {
    body += '<div class="field"><label>Name</label><input class="input" data-field="name" maxlength="64" value="' + esc(d.name) + '" ' + (off ? "" : "disabled") + "></div>" +
      '<div class="field"><label>Beschreibung</label><textarea class="input" data-field="description" ' + (off ? "" : "disabled") + ">" + esc(d.description) + "</textarea></div>" +
      '<div class="row"><div class="field"><label>Slot (Strg+Alt+Zahl)</label><select class="input" data-field="slot">' +
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => '<option value="' + n + '" ' + (n === +d.slot ? "selected" : "") + ">" + (n ? "Slot " + n : "Kein Slot") + "</option>").join("") +
      '</select></div><div class="field"><label>Typ</label><select class="input" data-field="type"><option value="user" ' + (d.type === "user" ? "selected" : "") +
      '>Arbeits-VM (mit Anzeige)</option><option value="service" ' + (d.type === "service" ? "selected" : "") + ">Service-VM (headless)</option></select></div></div>" +
      '<div class="field"><span class="label">Farbe (wird in der Statusleiste angezeigt)</span><div class="colors">' + S.colors.map((c) =>
        '<button type="button" data-color="' + c + '" class="' + (c === d.color ? "active" : "") + '" style="background:' + c + '"></button>').join("") + "</div></div>" +
      switchRow("Beim Systemstart starten", "Wird nach der Anmeldung automatisch gestartet.", toggle("start_on_boot", d.start_on_boot)) +
      switchRow("Im Dashboard ausblenden", "Nur sichtbar mit „Ausgeblendete zeigen“.", toggle("hidden", d.hidden));
  } else if (tab === "hardware") {
    body += '<div class="row"><div class="field"><label>Arbeitsspeicher (MB)</label><input class="input" type="number" min="256" step="256" data-field="memory" value="' + d.memory + '" ' + (off ? "" : "disabled") + "></div>" +
      '<div class="field"><label>CPUs</label><input class="input" type="number" min="1" max="64" data-field="cpus" value="' + d.cpus + '" ' + (off ? "" : "disabled") + "></div></div>" +
      '<div class="field"><label>Installationsmedium (CD/DVD)</label><select class="input" data-field="iso" ' + (d.policy.cd ? "" : "disabled") + '><option value="">– leer –</option>' +
      S.isos.map((i) => { const sel = d.iso && d.iso.endsWith("/" + i.name); return '<option value="' + esc(i.name) + '" ' + (sel ? "selected" : "") + ">" + esc(i.name) + "</option>"; }).join("") +
      '</select><span class="hint">Kann auch im laufenden Betrieb gewechselt werden.</span></div>' +
      '<dl class="kv" style="margin:18px 0"><dt>Festplatte</dt><dd>' + esc(vm.disk || "–") + "</dd><dt>Firmware</dt><dd>" + esc(vm.firmware.toUpperCase()) +
      "</dd><dt>Grafikspeicher</dt><dd>" + vm.vram + " MB</dd><dt>Gastsystem</dt><dd>" + esc(vm.ostype) + "</dd><dt>UUID</dt><dd>" + esc(vm.uuid) + "</dd></dl>" +
      switchRow("3D-Beschleunigung", "Hardwarebeschleunigte Grafik (Direct3D/OpenGL) – z. B. für Aero unter Windows 7. Benötigt die Gasterweiterungen im Gast.", toggle("accelerate3d", d.accelerate3d, !off || isLegacy(vm))) +
      switchRow("Wegwerf-Modus", "Alle Änderungen an der Festplatte werden beim Ausschalten verworfen (unveränderliches Abbild).", toggle("disposable", d.disposable, !off));
  } else if (tab === "policy") {
    body += '<div class="field"><label>Netzwerk</label><select class="input" data-field="network">' + S.networks.map((n) =>
      '<option value="' + esc(n.id) + '" ' + (n.id === d.network ? "selected" : "") + ">" + esc(n.label) + "</option>").join("") +
      '</select><span class="hint">NAT trennt jede VM von allen anderen. Interne Netze verbinden nur VMs untereinander (z. B. über eine Netzwerk-VM).</span></div>' +
      switchRow("USB-Durchreichung", "Erlaubt das Zuweisen von USB-Geräten an diese VM.", toggle("policy.usb", d.policy.usb, !off)) +
      switchRow("CD/DVD-Laufwerk", "Virtuelles optisches Laufwerk für ISO-Abbilder.", toggle("policy.cd", d.policy.cd, !off)) +
      switchRow("Audio", "Ton und Mikrofon für diese VM.", toggle("policy.audio", d.policy.audio, !off)) +
      '<div class="switch"><div class="text"><strong>Zwischenablage</strong><span>Austausch mit anderen VMs über die gemeinsame Zwischenablage.</span></div>' +
      '<div class="segmented">' + CLIPBOARD.map(([id, label]) => '<button type="button" data-clip="' + id + '" class="' + (d.policy.clipboard === id ? "active" : "") + '">' + label + "</button>").join("") + "</div></div>" +
      '<div class="notice" style="margin-top:16px">Drag &amp; Drop, gemeinsame Ordner und Fernzugriff (VRDE) sind in VMBox grundsätzlich deaktiviert.</div>';
  } else if (tab === "snapshots") {
    body += vm.disposable ? '<div class="notice warn">Wegwerf-VMs unterstützen keine Snapshots.</div>' :
      '<form id="snap-form" class="row" style="margin-bottom:12px"><input class="input" name="name" placeholder="Name des Snapshots" maxlength="64" required>' +
      '<button class="btn primary" style="flex:none">' + icon("camera") + "Snapshot erstellen</button></form>";
    body += '<div id="snap-list"><div class="empty">Lade …</div></div>';
  } else if (tab === "usb") {
    body += '<div id="drawer-usb"><div class="empty">Lade …</div></div>';
  }
  const tabs = [["general", "Allgemein"], ["hardware", "Hardware"], ["policy", "Netzwerk & Richtlinien"], ["snapshots", "Snapshots"], ["usb", "USB"]];
  const editable = ["general", "hardware", "policy"].includes(tab);
  openOverlay('<div class="drawer-head"><div class="os">' + osIcon(vm) + '</div><div class="grow"><h2>' + esc(vm.name) + '</h2><span class="status ' + vm.state + '">' +
    STATE_LABEL[vm.state] + '</span></div><button class="btn icon-only ghost" data-close>' + icon("x") + "</button></div>" +
    '<div class="tabs">' + tabs.map(([id, label]) => '<button data-tab="' + id + '" class="' + (id === tab ? "active" : "") + '">' + label + "</button>").join("") + "</div>" +
    '<div class="drawer-body">' + body + "</div>" +
    '<div class="drawer-foot"><button class="btn danger" id="vm-delete" ' + (off || vm.state === "saved" ? "" : "disabled") + ">" + icon("trash") + 'Löschen</button><span class="grow"></span>' +
    (editable ? '<button class="btn ghost" data-close>Abbrechen</button><button class="btn primary" id="vm-save">Speichern</button>' : '<button class="btn" data-close>Schließen</button>') + "</div>",
    "drawer");
  $(".drawer").style.setProperty("--vm-color", vm.color);
  if (tab === "snapshots") loadSnapshots(vm);
  if (tab === "usb") loadDrawerUSB(vm);
}

function setDraft(field, value) {
  const parts = field.split(".");
  let obj = S.draft;
  while (parts.length > 1) obj = obj[parts.shift()];
  obj[parts[0]] = value;
}

$("#overlay").addEventListener("input", (ev) => {
  const f = ev.target.dataset && ev.target.dataset.field;
  if (!f || !S.draft) return;
  setDraft(f, ev.target.type === "checkbox" ? ev.target.checked : ev.target.value);
});

$("#overlay").addEventListener("click", async (ev) => {
  if (!S.drawer) return;
  const vm = vmById(S.drawer);
  const tabBtn = ev.target.closest("[data-tab]");
  const colBtn = ev.target.closest(".drawer [data-color]");
  const clipBtn = ev.target.closest("[data-clip]");
  if (tabBtn) { S.drawerTab = tabBtn.dataset.tab; renderDrawer(); }
  else if (colBtn) { S.draft.color = colBtn.dataset.color; $$(".drawer [data-color]").forEach((b) => b.classList.toggle("active", b === colBtn)); }
  else if (clipBtn) { S.draft.policy.clipboard = clipBtn.dataset.clip; $$("[data-clip]").forEach((b) => b.classList.toggle("active", b === clipBtn)); }
  else if (ev.target.closest("#vm-save")) saveDrawer(vm);
  else if (ev.target.closest("#vm-delete")) {
    if (await confirmBox("„" + vm.name + "“ löschen?", "Die VM und alle ihre Festplatten und Snapshots werden unwiderruflich gelöscht.", "Endgültig löschen", true)) {
      run(api("DELETE", "/api/vms/" + vm.uuid), "VM gelöscht").catch(() => {});
    }
  }
  const snapBtn = ev.target.closest("[data-snap]");
  if (snapBtn) snapshotAction(vm, snapBtn.dataset.snap, snapBtn.dataset.id, snapBtn.dataset.name);
  const usbBtn = ev.target.closest("[data-usb]");
  if (usbBtn) usbAction(usbBtn.dataset.usb, usbBtn.dataset.id, vm.uuid).then(() => loadDrawerUSB(vm));
});

$("#overlay").addEventListener("submit", (ev) => {
  if (ev.target.id !== "snap-form") return;
  ev.preventDefault();
  const vm = vmById(S.drawer);
  const name = ev.target.name.value;
  run(api("POST", "/api/vms/" + vm.uuid + "/snapshots", { name }), "Snapshot erstellt").then(() => loadSnapshots(vm)).catch(() => {});
  ev.target.reset();
});

async function saveDrawer(vm) {
  const d = S.draft;
  const changes = {};
  for (const key of ["name", "description", "color", "start_on_boot", "hidden", "network", "disposable", "type", "accelerate3d"]) {
    if (d[key] !== vm[key]) changes[key] = d[key];
  }
  if (+d.slot !== vm.slot) changes.slot = +d.slot;
  if (+d.memory !== vm.memory) changes.memory = +d.memory;
  if (+d.cpus !== vm.cpus) changes.cpus = +d.cpus;
  if (JSON.stringify(d.policy) !== JSON.stringify(vm.policy)) changes.policy = d.policy;
  if ((d.iso || "") !== (vm.iso || "")) changes.iso = d.iso ? d.iso.split("/").pop() : "";
  if (!Object.keys(changes).length) { closeOverlay(); return; }
  try {
    await api("PATCH", "/api/vms/" + vm.uuid, changes);
    toast("Einstellungen gespeichert");
    closeOverlay();
    refreshSoon();
  } catch (e) { toast(e.message, true); }
}

async function loadSnapshots(vm) {
  let snaps = [];
  try { snaps = (await api("GET", "/api/vms/" + vm.uuid + "/snapshots")).snapshots; } catch (e) { toast(e.message, true); }
  const el = $("#snap-list");
  if (!el) return;
  const stopped = vm.state === "stopped" || vm.state === "saved";
  el.innerHTML = snaps.length ? snaps.map((s) => '<div class="snap ' + (s.current ? "current" : "") + '" style="padding-left:' + s.depth * 18 + 'px">' +
    '<span class="tree"></span><div class="main"><strong>' + esc(s.name) + "</strong><span>" + (s.current ? "Aktueller Stand · " : "") + esc(s.description || "") + "</span></div>" +
    '<button class="btn small" data-snap="restore" data-id="' + s.uuid + '" data-name="' + esc(s.name) + '" ' + (stopped ? "" : 'disabled title="Nur bei ausgeschalteter VM"') + ">" + icon("restore") + "Wiederherstellen</button>" +
    '<button class="btn small icon-only danger" title="Löschen" data-snap="delete" data-id="' + s.uuid + '" data-name="' + esc(s.name) + '">' + icon("trash") + "</button></div>").join("")
    : '<div class="empty">' + icon("camera") + "<div>Noch keine Snapshots vorhanden.</div></div>";
}

async function snapshotAction(vm, action, id, name) {
  if (action === "restore") {
    if (!(await confirmBox("Snapshot wiederherstellen?", "„" + vm.name + "“ wird auf den Stand „" + name + "“ zurückgesetzt. Nicht gesicherte Änderungen gehen verloren.", "Wiederherstellen"))) { renderDrawer(); return; }
    await run(api("POST", "/api/vms/" + vm.uuid + "/snapshots/" + id + "/restore"), "Snapshot wiederhergestellt").catch(() => {});
  } else {
    if (!(await confirmBox("Snapshot löschen?", "„" + name + "“ wird gelöscht. Der aktuelle Zustand der VM bleibt erhalten.", "Löschen", true))) { renderDrawer(); return; }
    await run(api("DELETE", "/api/vms/" + vm.uuid + "/snapshots/" + id), "Snapshot gelöscht").catch(() => {});
  }
  S.drawer = vm.uuid;
  S.draft = JSON.parse(JSON.stringify(vm));
  S.drawerTab = "snapshots";
  renderDrawer();
}

async function loadDrawerUSB(vm) {
  let devices = [];
  try { devices = (await api("GET", "/api/usb")).devices; } catch (e) { /* ignore */ }
  const el = $("#drawer-usb");
  if (!el) return;
  if (!vm.policy.usb) { el.innerHTML = '<div class="notice warn">USB ist für diese VM per Richtlinie deaktiviert.</div>'; return; }
  el.innerHTML = '<p style="color:var(--text-muted);font-size:13.5px;margin-top:0">„Immer zuweisen“ reicht das Gerät automatisch durch, sobald es eingesteckt wird und die VM läuft.</p>' +
    (devices.length ? devices.map((dev) => {
      const mine = dev.vm === vm.uuid;
      const sticky = dev.sticky_vms.includes(vm.uuid);
      return '<div class="list-item" style="padding:12px 0"><div class="lead">' + icon("usb") + '</div><div class="main"><strong>' + esc(dev.product || dev.id) +
        "</strong><span>" + esc(dev.manufacturer) + " · " + dev.id + (dev.vm && !mine ? " · bei " + esc((vmById(dev.vm) || {}).name || "anderer VM") : "") + '</span></div><div class="side">' +
        (mine ? '<button class="btn small" data-usb="release" data-id="' + dev.uuid + '">Trennen</button>'
          : '<button class="btn small primary" data-usb="assign" data-id="' + dev.uuid + '" ' + (vm.running ? "" : "disabled") + ">Zuweisen</button>") +
        '<button class="btn small ' + (sticky ? "primary" : "") + '" data-usb="' + (sticky ? "unstick" : "stick") + '" data-id="' + dev.uuid + '">' + icon("link") + (sticky ? "Immer" : "Immer zuweisen") + "</button></div></div>";
    }).join("") : '<div class="empty">' + icon("usb") + "<div>Keine USB-Geräte gefunden.</div></div>");
}

async function usbAction(action, devUuid, vmUuid, force) {
  try {
    if (action === "assign") await api("POST", "/api/usb/" + devUuid + "/assign", { vm: vmUuid, force: !!force });
    else if (action === "release") await api("POST", "/api/usb/" + devUuid + "/release", {});
    else await api("POST", "/api/usb/" + devUuid + "/sticky", { vm: vmUuid, sticky: action === "stick" });
    toast({ assign: "Gerät zugewiesen", release: "Gerät getrennt", stick: "Wird künftig automatisch zugewiesen", unstick: "Automatische Zuweisung entfernt" }[action]);
  } catch (e) {
    if (e.status === 403 && action === "assign" && /Eingabeger/.test(e.message) &&
        await confirmBox("Eingabegerät zuweisen?", "Tastaturen und Mäuse stehen danach VMBox nicht mehr zur Verfügung. Trotzdem zuweisen?", "Trotzdem zuweisen", true)) {
      return usbAction(action, devUuid, vmUuid, true);
    }
    toast(e.message, true);
  }
  refreshSoon();
}

/* ------------------------------------------------------------------ */
/* USB page                                                            */
/* ------------------------------------------------------------------ */
function renderUSB() {
  const vms = (S.state.vms || []).filter((v) => v.running && v.policy.usb && v.type === "user");
  let html = pageHead("USB-Geräte", "Weise Geräte gezielt einer VM zu. Nicht zugewiesene Geräte bleiben isoliert beim Host.");
  html += '<div class="glass list">' + (S.usb.length ? S.usb.map((dev) => {
    const owner = dev.vm && vmById(dev.vm);
    return '<div class="list-item"><div class="lead">' + icon("usb") + '</div><div class="main"><strong>' + esc(dev.product || "Unbekanntes Gerät") +
      "</strong><span>" + esc(dev.manufacturer || "–") + " · " + dev.id + (dev.hid ? " · Eingabegerät" : "") +
      (dev.sticky_vms.length ? " · immer für " + dev.sticky_vms.map((u) => esc((vmById(u) || {}).name || "?")).join(", ") : "") + '</span></div><div class="side">' +
      (owner ? '<span><span class="vm-dot" style="background:' + esc(owner.color) + '"></span>' + esc(owner.name) + "</span>" +
        '<button class="btn small" data-act="usb-release" data-id="' + dev.uuid + '">Trennen</button>'
        : '<select class="input" data-usb-assign="' + dev.uuid + '"><option value="">Einer VM zuweisen …</option>' +
          vms.map((v) => '<option value="' + v.uuid + '">' + esc(v.name) + "</option>").join("") + "</select>") + "</div></div>";
  }).join("") : '<div class="empty">' + icon("usb") + "<div>Keine USB-Geräte angeschlossen.</div></div>") + "</div>";
  return html;
}

$("#main").addEventListener("change", (ev) => {
  const sel = ev.target.closest("[data-usb-assign]");
  if (sel && sel.value) usbAction("assign", sel.dataset.usbAssign, sel.value).then(loadPage);
});

/* ------------------------------------------------------------------ */
/* Network page                                                        */
/* ------------------------------------------------------------------ */
function renderNetwork() {
  const vms = S.state.vms || [];
  let html = pageHead("Netzwerk", "Der Host selbst bietet keine Dienste an – eingehende Verbindungen werden blockiert.");
  html += '<div class="grid-2"><div class="glass panel"><h2>VM-Netzwerke</h2><p>Welche VM hängt an welchem Netz.</p>' + S.networks.map((n) => {
    const members = vms.filter((v) => v.network === n.id || (n.id === "nat" && !v.network));
    return '<div class="list-item" style="padding:10px 0"><div class="lead">' + icon("network") + '</div><div class="main"><strong>' + esc(n.label) + "</strong><span>" +
      (members.length ? members.map((v) => '<span class="vm-dot" style="background:' + esc(v.color) + '"></span>' + esc(v.name)).join(" &nbsp; ") : "nicht verwendet") + "</span></div></div>";
  }).join("") + "</div>";
  const wifi = S.wifi || { networks: [], devices: [] };
  html += '<div><div class="glass panel"><h2>Verbindungen des Hosts</h2><p>Wird von NAT- und Brücken-Netzen der VMs verwendet.</p>' +
    (wifi.devices.length ? wifi.devices.map((d) => '<div class="list-item" style="padding:10px 0"><div class="lead">' + icon(d.type === "wifi" ? "wifi" : "network") +
      '</div><div class="main"><strong>' + esc(d.connection || d.device) + "</strong><span>" + esc(d.device) + " · " + esc(d.state) + "</span></div></div>").join("")
      : '<div class="empty" style="padding:20px">' + esc(wifi.error || "Keine Netzwerkgeräte gefunden.") + "</div>") + "</div>" +
    '<div class="glass panel"><h2>WLAN</h2><p>Mit einem Funknetz verbinden.</p>' + (wifi.networks.length ? wifi.networks.map((n) =>
      '<div class="list-item" style="padding:10px 0"><div class="lead">' + icon("wifi") + '</div><div class="main"><strong>' + esc(n.ssid) + "</strong><span>" + n.signal + " % · " +
      esc(n.security || "offen") + '</span></div><div class="side">' + (n.connected ? '<span class="yes">Verbunden</span>'
        : '<button class="btn small" data-act="wifi" data-ssid="' + esc(n.ssid) + '" data-secure="' + (n.security ? 1 : 0) + '">Verbinden</button>') + "</div></div>").join("")
      : '<div class="empty" style="padding:20px">Keine Funknetze gefunden.</div>') + "</div></div></div>";
  return html;
}

/* ------------------------------------------------------------------ */
/* ISO page                                                            */
/* ------------------------------------------------------------------ */
function renderMedia() {
  const m = S.media;
  const inLibrary = (file) => S.isos.some((i) => i.name === file.split("/").pop().replace(/[^\w.+-]/g, "_"));
  const running = (dev, file) => m.jobs.some((j) => j.device === dev && j.state === "running" && j.name === file.split("/").pop().replace(/[^\w.+-]/g, "_"));
  let html = '<div class="section-title">Von USB-Stick importieren</div><div class="glass list" style="margin-bottom:22px">';
  if (!m.devices.length) {
    html += '<div class="empty" style="padding:28px">' + icon("usb") + "<div>" + esc(m.error || "Kein USB-Stick gefunden. Stick einstecken (exFAT, NTFS, FAT32, ext4 …) – er wird nur gelesen, nie verändert.") + "</div></div>";
  }
  m.devices.forEach((d) => {
    const isos = S.mediaIsos[d.device];
    html += '<div class="list-item"><div class="lead">' + icon("usb") + '</div><div class="main"><strong>' + esc(d.label) + "</strong><span>" +
      esc(d.fstype) + " · " + fmtBytes(d.size) + (d.model ? " · " + esc(d.model) : "") + '</span></div><div class="side">' +
      (isos ? '<button class="btn small" data-act="media-close" data-device="' + esc(d.device) + '">Ausblenden</button>'
        : '<button class="btn small primary" data-act="media-open" data-device="' + esc(d.device) + '">' + icon("disc") + "ISO-Dateien anzeigen</button>") +
      '<button class="btn small" data-act="media-eject" data-device="' + esc(d.device) + '">Auswerfen</button></div></div>';
    if (isos) {
      html += isos.length ? isos.map((f) => '<div class="list-item" style="padding-left:76px"><div class="main"><strong>' + esc(f.file.split("/").pop()) +
        "</strong><span>" + esc(f.file) + " · " + fmtBytes(f.size) + '</span></div><div class="side">' +
        (inLibrary(f.file) ? '<span class="yes">In der Bibliothek</span>' : running(d.device, f.file) ? '<span class="badge blue">Wird kopiert …</span>'
          : '<button class="btn small" data-act="media-import" data-device="' + esc(d.device) + '" data-file="' + esc(f.file) + '">' + icon("download") + "Importieren</button>") +
        "</div></div>").join("") : '<div class="empty" style="padding:20px">Keine .iso-Dateien auf diesem Stick gefunden.</div>';
    }
  });
  html += "</div>";
  const jobs = m.jobs.filter((j) => j.state !== "done" || Date.now() / 1000 - j.started < 600);
  if (jobs.length) {
    html += '<div class="glass list" style="margin-bottom:22px">' + jobs.map((j) => {
      const pct = j.size ? Math.floor((j.copied / j.size) * 100) : 0;
      return '<div class="list-item"><div class="lead">' + icon(j.state === "failed" ? "alert" : j.state === "done" ? "check" : "download") + '</div><div class="main"><strong>' +
        esc(j.name) + "</strong><span>" + (j.state === "running" ? pct + " % · " + fmtBytes(j.copied) + " von " + fmtBytes(j.size)
          : j.state === "done" ? "Importiert" : '<span class="no">' + esc(j.error) + "</span>") + "</span>" +
        (j.state === "running" ? '<div class="progress"><div style="width:' + pct + '%"></div></div>' : "") + "</div></div>";
    }).join("") + "</div>";
  }
  return html;
}

function renderISOs() {
  let html = pageHead("ISO-Abbilder", "Installationsmedien für deine virtuellen Maschinen.");
  html += renderMedia();
  html += '<div class="section-title">Bibliothek</div>';
  html += '<div class="dropzone" id="dropzone" data-act="upload">' + icon("upload") + "<div><strong>ISO-Datei hierher ziehen</strong> oder klicken, um eine Datei auszuwählen</div>" +
    '<div class="progress" id="upload-progress" hidden><div></div></div></div>';
  html += '<div class="glass list">' + (S.isos.length ? S.isos.map((i) => '<div class="list-item"><div class="lead">' + icon("disc") + '</div><div class="main"><strong>' +
    esc(i.name) + "</strong><span>" + fmtBytes(i.size) + '</span></div><div class="side"><button class="btn small icon-only danger" title="Löschen" data-act="iso-delete" data-name="' +
    esc(i.name) + '">' + icon("trash") + "</button></div></div>").join("") : '<div class="empty">' + icon("disc") + "<div>Noch keine Abbilder vorhanden.</div></div>") + "</div>";
  return html;
}

function uploadISO(file) {
  if (!/\.iso$/i.test(file.name)) { toast("Bitte eine .iso-Datei wählen", true); return; }
  const bar = $("#upload-progress");
  bar.hidden = false;
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/isos?name=" + encodeURIComponent(file.name));
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.setRequestHeader("X-VMBox-Upload", "1");
  xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) $("div", bar).style.width = (ev.loaded / ev.total) * 100 + "%"; };
  xhr.onload = () => {
    let msg = "";
    try { msg = JSON.parse(xhr.responseText).error; } catch (e) { /* ignore */ }
    if (xhr.status < 300) toast("„" + file.name + "“ hochgeladen"); else toast(msg || "Upload fehlgeschlagen", true);
    loadPage();
  };
  xhr.onerror = () => { toast("Upload fehlgeschlagen", true); loadPage(); };
  xhr.send(file);
}

document.addEventListener("dragover", (ev) => { const dz = ev.target.closest && ev.target.closest("#dropzone"); if (dz) { ev.preventDefault(); dz.classList.add("over"); } });
document.addEventListener("dragleave", (ev) => { const dz = ev.target.closest && ev.target.closest("#dropzone"); if (dz) dz.classList.remove("over"); });
document.addEventListener("drop", (ev) => {
  const dz = ev.target.closest && ev.target.closest("#dropzone");
  ev.preventDefault();
  if (dz && ev.dataTransfer.files.length) uploadISO(ev.dataTransfer.files[0]);
});

/* ------------------------------------------------------------------ */
/* System page                                                         */
/* ------------------------------------------------------------------ */
function renderSystem() {
  const h = S.host;
  let html = pageHead("System", "Informationen und Einstellungen des VMBox-Hosts.");
  if (!h) return html + '<div class="empty">Lade …</div>';
  const yn = (v, yes, no) => '<span class="' + (v ? "yes" : "no") + '">' + (v ? yes : no) + "</span>";
  html += '<div class="grid-2"><div class="glass panel"><h2>Host</h2><p>Der minimale Host-Rechner, auf dem VirtualBox läuft.</p><dl class="kv">' +
    "<dt>Rechnername</dt><dd>" + esc(h.hostname) + "</dd><dt>VMBox</dt><dd>" + esc(S.state.version) + (h.live ? " (Live)" : "") + "</dd>" +
    "<dt>VirtualBox</dt><dd>" + esc(h.virtualbox) + "</dd><dt>Kernel</dt><dd>" + esc(h.kernel) + "</dd>" +
    "<dt>Prozessor</dt><dd>" + esc(h.cpu) + " (" + h.cpus + " Threads)</dd>" +
    "<dt>Virtualisierung</dt><dd>" + yn(h.virtualization, "VT-x/AMD-V aktiv", "nicht verfügbar") + "</dd>" +
    "<dt>Arbeitsspeicher</dt><dd>" + fmtBytes(h.memory_free) + " frei von " + fmtBytes(h.memory_total) + "</dd>" +
    "<dt>VM-Speicher</dt><dd>" + fmtBytes(h.storage.free) + " frei von " + fmtBytes(h.storage.total) + "</dd>" +
    "<dt>Verschlüsselung</dt><dd>" + yn(h.encrypted, "LUKS2 aktiv", "aus") + "</dd>" +
    "<dt>TPM</dt><dd>" + yn(h.tpm, "TPM 2.0", "nicht gefunden") + "</dd>" +
    "<dt>Secure Boot</dt><dd>" + yn(h.secure_boot, "aktiv", "aus") + "</dd>" +
    "<dt>Laufzeit</dt><dd>" + fmtUptime(h.uptime) + "</dd></dl></div><div>";
  if (h.live) {
    html += '<div class="glass panel"><h2>Installation</h2><p>VMBox dauerhaft auf eine Festplatte installieren – mit verschlüsseltem VM-Speicher und optionaler TPM-Entsperrung.</p>' +
      '<button class="btn primary" data-act="install">' + icon("download") + "VMBox installieren</button></div>";
  }
  html += '<div class="glass panel"><h2>Passwort ändern</h2><p>Schützt Dashboard und Sperrbildschirm.</p><form id="pw-form">' +
    '<div class="field"><input class="input" type="password" name="old" placeholder="Bisheriges Passwort" required></div>' +
    '<div class="row"><div class="field"><input class="input" type="password" name="new" placeholder="Neues Passwort" required minlength="6"></div>' +
    '<div class="field"><input class="input" type="password" name="new2" placeholder="Wiederholen" required></div></div>' +
    '<button class="btn">' + icon("key") + "Passwort ändern</button></form></div>" +
    '<div class="glass panel"><h2>Energie</h2><p>Laufende VMs sollten vorher heruntergefahren werden.</p><div class="row" style="flex-wrap:wrap">' +
    '<button class="btn" data-act="host-reboot">' + icon("restore") + 'Neu starten</button><button class="btn danger" data-act="host-poweroff">' + icon("power") + "Ausschalten</button></div></div></div></div>";
  return html;
}

$("#main").addEventListener("submit", async (ev) => {
  if (ev.target.id !== "pw-form") return;
  ev.preventDefault();
  const f = ev.target;
  if (f.new.value !== f.new2.value) { toast("Die neuen Passwörter stimmen nicht überein", true); return; }
  try {
    await api("POST", "/api/password", { old: f.old.value, new: f.new.value });
    toast("Passwort geändert");
    f.reset();
  } catch (e) { toast(e.message, true); }
});

async function pageAction(act, el) {
  if (act === "usb-release") return usbAction("release", el.dataset.id).then(loadPage);
  if (act === "iso-delete") {
    if (await confirmBox("Abbild löschen?", "„" + el.dataset.name + "“ wird gelöscht.", "Löschen", true)) {
      run(api("DELETE", "/api/isos/" + encodeURIComponent(el.dataset.name)), "Abbild gelöscht").then(loadPage).catch(() => {});
    }
    return;
  }
  if (act === "media-open") {
    el.disabled = true;
    try {
      S.mediaIsos[el.dataset.device] = (await api("POST", "/api/media/isos", { device: el.dataset.device })).isos;
    } catch (e) { toast(e.message, true); }
    return renderPage(true);
  }
  if (act === "media-close") { delete S.mediaIsos[el.dataset.device]; return renderPage(true); }
  if (act === "media-import") {
    el.disabled = true;
    return run(api("POST", "/api/media/import", { device: el.dataset.device, file: el.dataset.file }), "Import gestartet").then(loadPage).catch(() => { el.disabled = false; });
  }
  if (act === "media-eject") {
    return run(api("POST", "/api/media/eject", { device: el.dataset.device }), "Der Stick kann jetzt entfernt werden").then(loadPage).catch(() => {});
  }
  if (act === "upload") {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".iso";
    input.onchange = () => { if (input.files.length) uploadISO(input.files[0]); };
    input.click();
    return;
  }
  if (act === "wifi") {
    let password = null;
    if (el.dataset.secure === "1") {
      const data = await prompt2("Mit „" + el.dataset.ssid + "“ verbinden", "Dieses Netz ist geschützt.", [{ name: "password", label: "WLAN-Passwort", type: "password" }], "Verbinden");
      if (!data) return;
      password = data.password;
    }
    return run(api("POST", "/api/wifi/connect", { ssid: el.dataset.ssid, password }), "Verbunden mit „" + el.dataset.ssid + "“").then(loadPage).catch(() => {});
  }
  if (act === "host-reboot" || act === "host-poweroff") {
    const running = (S.state.vms || []).filter((v) => v.running).length;
    const what = act === "host-reboot" ? "neu starten" : "ausschalten";
    if (await confirmBox("System " + what + "?", running ? running + " VM(s) laufen noch und werden hart beendet." : "Alle Dienste werden beendet.", act === "host-reboot" ? "Neu starten" : "Ausschalten", true)) {
      run(api("POST", "/api/host/" + act.slice(5))).catch(() => {});
    }
    return;
  }
  if (act === "install") {
    if (await confirmBox("VMBox installieren?", "Der Installationsassistent startet im Textmodus. Die Zielfestplatte wird dabei vollständig gelöscht.", "Installer starten")) {
      run(api("POST", "/api/host/install"), "Installer wird gestartet …").catch(() => {});
    }
  }
}

/* ------------------------------------------------------------------ */
document.addEventListener("contextmenu", (ev) => { if (!ev.target.closest("input, textarea")) ev.preventDefault(); });
poll();
