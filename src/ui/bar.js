/* VMBox top bar: running VMs, focus indicator, lock button and clock. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let last = "";

function post(path) {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: "{}" })
    .then(() => setTimeout(update, 150));
}

async function update() {
  let st;
  try { st = await fetch("/api/state", { credentials: "same-origin" }).then((r) => r.json()); } catch (e) { return; }
  const key = JSON.stringify([st.authenticated, st.focus, (st.vms || []).map((v) => [v.uuid, v.name, v.color, v.state, v.slot])]);
  if (key === last) return;
  last = key;
  if (!st.authenticated) {
    $("#vms").innerHTML = '<span class="locked">' + icon("lock") + (st.setup_required ? "Einrichtung" : "Gesperrt") + "</span>";
    $("#current").textContent = "";
    $("#lock").hidden = true;
    $("#home").classList.add("active");
    $("#indicator").style.background = "";
    return;
  }
  $("#lock").hidden = false;
  const vms = (st.vms || []).filter((v) => v.type === "user" && v.running);
  const focused = vms.find((v) => v.uuid === st.focus);
  $("#home").classList.toggle("active", !focused);
  $("#vms").innerHTML = vms.map((v) => '<button class="vm ' + v.state + (v === focused ? " active" : "") + '" data-uuid="' + v.uuid +
    '" style="--c:' + esc(v.color) + '" title="' + esc(v.name) + (v.slot ? " (Strg+Alt+" + v.slot + ")" : "") + '"><span class="dot"></span>' +
    esc(v.name) + (v.slot ? '<span class="slot">' + v.slot + "</span>" : "") + "</button>").join("");
  $("#current").textContent = focused ? focused.name : "Dashboard";
  $("#indicator").style.background = focused ? focused.color : "";
}

$("#vms").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-uuid]");
  if (btn) post("/api/vms/" + btn.dataset.uuid + "/switch");
});
$("#home").addEventListener("click", () => post("/api/switch/ui"));
$("#lock").addEventListener("click", () => post("/api/lock"));
$("#lock").innerHTML = icon("lock");

function clock() {
  $("#clock").textContent = new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}
document.addEventListener("contextmenu", (ev) => ev.preventDefault());
setInterval(clock, 1000);
setInterval(update, 1000);
clock();
update();
