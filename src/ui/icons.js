/* Minimal inline icon set (24x24, stroke based). */
"use strict";
const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  usb: '<path d="M12 2v14"/><path d="m9 5 3-3 3 3"/><path d="M12 16a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/><path d="M12 13 7 10V7"/><circle cx="7" cy="6" r="1.4"/><path d="m12 11 5-2.5V6"/><rect x="15.6" y="3.6" width="2.8" height="2.8"/>',
  network: '<rect x="9" y="2" width="6" height="6" rx="1.5"/><rect x="2" y="16" width="6" height="6" rx="1.5"/><rect x="16" y="16" width="6" height="6" rx="1.5"/><path d="M12 8v4M5 16v-2h14v2"/>',
  disc: '<circle cx="12" cy="12" r="9.5"/><circle cx="12" cy="12" r="2.5"/><path d="M12 5.5A6.5 6.5 0 0 0 5.5 12"/>',
  system: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  lock: '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
  play: '<path d="M7 4.8v14.4a1 1 0 0 0 1.5.86l11.6-7.2a1 1 0 0 0 0-1.72L8.5 3.94A1 1 0 0 0 7 4.8z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="4.5" width="4" height="15" rx="1.2" fill="currentColor" stroke="none"/><rect x="14" y="4.5" width="4" height="15" rx="1.2" fill="currentColor" stroke="none"/>',
  power: '<path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/>',
  monitor: '<rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8.5 21h7M12 17v4"/><path d="m10 9 3 1.5L10 12"/>',
  more: '<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  camera: '<path d="M4 7.5h3l1.8-2.5h6.4L17 7.5h3a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8.5a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.8"/>',
  restore: '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3 4v4.5h4.5"/><path d="M12 8v4.5l3 2"/>',
  windows: '<path d="M3 5.4 10.4 4.4v7H3zM12.2 4.1 21 3v8.4h-8.8zM3 12.9h7.4v7L3 18.9zM12.2 12.9H21V21l-8.8-1.2z" fill="currentColor" stroke="none"/>',
  linux: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7.5 9.5 3 2.5-3 2.5M12.5 15h4"/>',
  server: '<rect x="3" y="3.5" width="18" height="7" rx="2"/><rect x="3" y="13.5" width="18" height="7" rx="2"/><path d="M7 7h.01M7 17h.01"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  memory: '<rect x="2.5" y="7" width="19" height="10" rx="1.5"/><path d="M6 7v10M10 7v10M14 7v10M18 7v10M5 20v-3M19 20v-3"/>',
  storage: '<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.7 3.6 3 8 3s8-1.3 8-3v-13"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  shield: '<path d="M12 2.5 4 5.5v6c0 5 3.4 8.8 8 10 4.6-1.2 8-5 8-10v-6z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  wifi: '<path d="M2 8.8a15 15 0 0 1 20 0"/><path d="M5.2 12.4a10.5 10.5 0 0 1 13.6 0"/><path d="M8.6 15.9a5.7 5.7 0 0 1 6.8 0"/><circle cx="12" cy="19.4" r="1.2" fill="currentColor"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4.5M12 17.5h.01"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  key: '<circle cx="8" cy="15" r="4.5"/><path d="m11.2 11.8 9-9M17 6l3 3M14.5 8.5l2 2"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 18v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1"/>',
  link: '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3.2-3.2a4.5 4.5 0 0 0-6.4-6.4L12 5.6"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3.2 3.2a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2"/>',
  home: '<path d="M3.5 11 12 4l8.5 7"/><path d="M5.5 9.5V20h13V9.5"/>',
};

function icon(name, extraClass) {
  const body = ICONS[name] || "";
  return '<span class="icon ' + (extraClass || "") + '"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg></span>';
}

function osIcon(vm) {
  if (vm.type === "service") return icon("server");
  if ((vm.ostype || "").startsWith("Windows")) return icon("windows");
  return icon("linux");
}
