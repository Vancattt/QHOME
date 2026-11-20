// QHome web UI. No build step: plain ES2020, served as-is by python/qh_web.py.
"use strict";

// ------------------------------------------------------------------ helpers
const $ = sel => document.querySelector(sel);
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) {
    if (c == null || c === false) continue;
    e.append(c.nodeType ? c : document.createTextNode(c));
  }
  return e;
}
const clone = o => JSON.parse(JSON.stringify(o));
async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  return r.json();
}
function toast(text) {
  const e = $("#toast");
  e.textContent = text;
  e.classList.add("show");
  clearTimeout(e._t);
  e._t = setTimeout(() => e.classList.remove("show"), 2400);
}
const RGB_CSS = { off: "#2B3331", red: "#F04438", green: "#22C55E", yellow: "#FACC15", blue: "#3B82F6", magenta: "#D946EF", cyan: "#22D3EE", white: "#FFFFFF" };
const ID_PREFIX = { binary_input: "input", analog_sensor: "sensor", pulse_counter: "counter", switch_output: "relay", pwm_output: "dimmer",
                    dac_output: "analog_out", rgb_led: "led", led_matrix: "matrix", virtual_switch: "flag" };
const ID_RE = /^[a-z][a-z0-9_]{0,31}$/;

// ------------------------------------------------------------------ state
let META = null, CFG = null, DRAFT = null, ST = null;
let VIEW = "dash", SELB = null, SEL = null, LOGSEQ = 0, LOGS = [], BLOCKS_READY = false, PORTS = [];
const HIST = {};
const dirty = () => JSON.stringify(CFG) !== JSON.stringify(DRAFT);
const isNode = () => META.role === "node";
const comps = () => DRAFT.components;
const compById = id => comps().find(c => c.id === id);
const boardById = (id, cfg = DRAFT) => cfg.boards.find(b => b.id === id);
const modelOf = b => (b ? META.models[b.model] : null);
const pinOf = (b, pid) => (modelOf(b) ? modelOf(b).pins.find(p => p.id === pid) : null);
const compAt = (bid, pid) => comps().find(c => c.board === bid && c.pin === pid);
const stOf = id => (ST && ST.states[id]) || {};
const typeLabel = type => t("type." + type);
function propsOf(c) {
  const o = {};
  for (const p of META.types[c.type].props) o[p.key] = p.default;
  return Object.assign(o, c.props || {});
}
function reservedPins(b) {
  const m = modelOf(b), out = {}, lt = (b.link || {}).type;
  if (!m) return out;
  if (lt === "uart" || (lt === "usb" && m.usb_shares_uart)) m.serial_pins.forEach(p => (out[p] = "link_serial"));
  if (lt === "i2c") m.i2c_pins.forEach(p => (out[p] = "link_i2c"));
  if (lt === "local") for (const o of DRAFT.boards) {
    const ol = o.link || {};
    if (ol.type === "uart") m.serial_pins.forEach(p => (out[p] = "host_uart"));
    if (ol.type === "i2c" && (ol.bus || "wire") === "wire") m.i2c_pins.forEach(p => (out[p] = "host_i2c"));
  }
  return out;
}
function changed() { updateTop(); }
function updateTop() {
  const d = dirty();
  $("#bSave").disabled = !d;
  $("#bUndo").hidden = !d;
  $("#dirtyTxt").textContent = d ? t("unsaved") : "";
}

// ------------------------------------------------------------------ navigation
function views() {
  return isNode() ? [["dash", "nav.dash"], ["settings", "nav.settings"], ["log", "nav.log"]]
    : [["dash", "nav.dash"], ["boards", "nav.boards"], ["blocks", "nav.blocks"], ["settings", "nav.settings"], ["log", "nav.log"]];
}
function renderNav() {
  const n = $("#nav");
  n.replaceChildren(...views().map(([k, label]) =>
    h("button", { "aria-current": VIEW === k ? "page" : null, onclick: () => go(k) }, t(label))));
}
function go(view) { VIEW = view; renderNav(); render(); }
function render() {
  const main = $("#main"), bv = $("#blocksView");
  const blocks = VIEW === "blocks";
  main.hidden = blocks;
  bv.hidden = !blocks;
  if (blocks) showBlocks();
  else {
    main.replaceChildren();
    ({ dash: isNode() ? viewNode : viewDash, boards: viewBoards, settings: viewSettings, log: viewLog })[VIEW](main);
  }
  updateTop();
}

// ------------------------------------------------------------------ dashboard
function command(id, action, value) { api("/api/cmd", { id, action, value }); }
function sparkline(id) {
  const a = HIST[id] || [];
  if (a.length < 2) return h("div", { class: "small muted" }, t("dash.waiting"));
  const mn = Math.min(...a), mx = Math.max(...a), r = (mx - mn) || 1;
  const pts = a.map((v, i) => (i / (a.length - 1) * 100).toFixed(1) + "," + (28 - (v - mn) / r * 26 + 1).toFixed(1)).join(" ");
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 100 30");
  s.setAttribute("preserveAspectRatio", "none");
  s.classList.add("spark");
  s.innerHTML = `<polyline points="${pts}" fill="none" stroke="#B0650A" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
  return s;
}
function matrixView(frame, cls) {
  const g = h("div", { class: cls || "mxview" });
  for (let i = 0; i < 104; i++) {
    const b = +((frame || "")[i] || 0);
    g.append(h("i", { style: b ? `background:rgba(125,211,252,${0.3 + b * 0.1})` : null }));
  }
  return g;
}
function toggle(on, onclick) { return h("button", { class: "sw", role: "switch", "aria-checked": on ? "true" : "false", onclick }); }
function tile(c) {
  const s = stOf(c.id), v = s.v, ty = META.types[c.type], p = propsOf(c);
  const b = boardById(c.board, CFG), pin = pinOf(b, c.pin);
  const off = ty.cap && !(ST && ST.boards[c.board] && ST.boards[c.board].online);
  const el = h("div", { class: "tile" + (off ? " offline" : ""), "data-id": c.id },
    h("div", { class: "tt" }, h("span", { class: "tag f-" + (ty.cap || "virt") }, ty.tag), h("span", {}, c.name),
      h("span", { class: "where" }, pin ? pin.label : "")));
  if (c.type === "binary_input") {
    el.append(h("div", { class: "line" }, h("span", { class: "lamp" + (v ? " on" : "") }), h("span", { class: "big" }, v == null ? "--" : (v ? t("on") : t("off")))));
    if (ST && ST.boards[c.board] && /simulated/.test(ST.boards[c.board].text)) {
      const press = val => api("/api/sim", { board: c.board, pin: c.pin, value: val });
      // released on the document: the tile itself is redrawn while the button is held
      const down = () => { press(p.invert ? 0 : 1); document.addEventListener("pointerup", () => press(p.invert ? 1 : 0), { once: true }); };
      el.append(h("button", { class: "btn sm", onpointerdown: down }, t("dash.hold")));
    }
  } else if (c.type === "switch_output" || c.type === "virtual_switch") {
    el.append(h("div", { class: "line" }, toggle(v, () => command(c.id, "toggle")), h("span", { class: "big" }, v ? t("on") : t("off"))));
  } else if (c.type === "pwm_output") {
    const on = v && v.on, lv = v ? Math.round(v.level) : 0;
    el.append(h("div", { class: "line" }, toggle(on, () => command(c.id, "toggle")), h("span", { class: "big" }, on ? lv + "%" : t("off"))),
      h("input", { type: "range", min: 0, max: 100, value: lv, "aria-label": c.name, onchange: e => command(c.id, "set", +e.target.value) }));
  } else if (c.type === "dac_output") {
    const vref = (modelOf(b) || {}).vref || 3.3;
    el.append(h("div", { class: "big" }, (v || 0).toFixed(2), h("span", { class: "unit" }, " V")),
      h("input", { type: "range", min: 0, max: vref, step: 0.01, value: v || 0, "aria-label": c.name, onchange: e => command(c.id, "set", +e.target.value) }));
  } else if (c.type === "analog_sensor" || c.type === "pulse_counter") {
    el.append(h("div", { class: "big" }, s.txt || "--", h("span", { class: "unit" }, " " + (p.unit || ""))), sparkline(c.id));
  } else if (c.type === "rgb_led") {
    el.append(h("div", { class: "swatches" }, META.colors.map(k => h("button", { class: "swatch", title: t("color." + k), "aria-label": t("color." + k),
      "aria-pressed": v === k ? "true" : "false", style: "background:" + RGB_CSS[k], onclick: () => command(c.id, "set", k) }))));
  } else if (c.type === "led_matrix") {
    const inp = h("input", { type: "text", placeholder: t("dash.text.ph"), "aria-label": t("dash.text.ph") });
    el.append(matrixView(ST && ST.matrix[c.id]),
      h("div", { class: "inline" }, inp, h("button", { class: "btn sm", onclick: () => command(c.id, "text", inp.value) }, t("dash.show")),
        h("button", { class: "btn sm", onclick: () => command(c.id, "clear") }, t("dash.clear"))));
  }
  return el;
}
function viewDash(m) {
  if (!CFG.components.length) {
    m.append(h("div", { class: "panel empty" }, h("p", {}, t("dash.empty")), h("button", { class: "btn primary", onclick: () => go("boards") }, t("dash.empty.btn"))));
    return;
  }
  if (dirty()) m.append(h("div", { class: "note", style: "margin:0 0 16px" }, t("dash.unsaved")));
  const groups = CFG.boards.map(b => [b, CFG.components.filter(c => c.board === b.id && META.types[c.type].cap)]);
  for (const [b, list] of groups) {
    if (!list.length) continue;
    const on = ST && ST.boards[b.id] && ST.boards[b.id].online;
    m.append(h("div", { class: "group-title" }, h("span", { class: "dot " + (on ? "ok" : "err") }), h("h3", {}, b.name || b.id),
      h("span", { class: "small muted" }, modelOf(b) ? modelOf(b).label : "")), h("div", { class: "tiles" }, list.map(tile)));
  }
  const virt = CFG.components.filter(c => !META.types[c.type].cap);
  if (virt.length) m.append(h("div", { class: "group-title" }, h("h3", {}, t("dash.virtual"))), h("div", { class: "tiles" }, virt.map(tile)));
  const vars = ST ? Object.entries(ST.vars || {}) : [];
  if (vars.length) m.append(h("div", { class: "group-title" }, h("h3", {}, t("dash.vars"))),
    h("div", { class: "vars", id: "varbox" }, vars.map(([k, v]) => h("span", { class: "var" }, k + " = " + v))));
}
function viewNode(m) {
  const n = (ST && ST.node) || {};
  m.append(h("div", { class: "panel", style: "max-width:640px" }, h("h2", {}, t("node.title")),
    h("p", { style: "margin:10px 0 14px" }, t("node.body", CFG.device.master_host || "?", CFG.device.node)),
    h("div", { class: "stat" }, h("span", { class: "dot " + (n.online ? "ok" : "warn") }), n.online ? t("node.connected") : t("node.waiting")),
    h("div", { class: "stat", style: "margin-top:6px" }, h("span", { class: "dot " + (n.mcu ? "ok" : "err") }), t("node.mcu"))));
}

// ------------------------------------------------------------------ boards & pins
function newId(prefix, taken) { let n = 1; while (taken(prefix + n)) n++; return prefix + n; }
function addComp(type, bid, pid) {
  const id = newId(ID_PREFIX[type] || "dev", x => !!compById(x));
  const c = { id, type, name: typeLabel(type) + " " + id.replace(/^\D+/, ""), board: bid, pin: pid, props: {} };
  const m = modelOf(boardById(bid));
  if (type === "analog_sensor" && m && m.vref !== 3.3) c.props.in_max = m.vref;
  comps().push(c);
  SEL = { comp: id };
  changed(); render();
}
function scriptsText() { return JSON.stringify((DRAFT.scripts || {}).workspace || {}); }
function removeComp(id) {
  if (scriptsText().includes('"ENT":"' + id + '"') && !confirm(t("pin.inuse", id))) return;
  DRAFT.components = comps().filter(c => c.id !== id);
  SEL = null;
  changed(); render();
}
function renameComp(c, nv) {
  const old = c.id;
  c.id = nv;
  DRAFT.scripts.workspace = JSON.parse(scriptsText().split('"ENT":"' + old + '"').join('"ENT":"' + nv + '"'));
  SEL = { comp: nv };
}
function pinCell(b, pid, reserved) {
  const p = pinOf(b, pid);
  if (!p) return null;
  const c = compAt(b.id, pid), res = reserved[pid];
  const sel = SEL && ((SEL.comp && c && SEL.comp === c.id) || (SEL.pin === pid && SEL.board === b.id));
  const sock = h("span", { class: "sock" + (c ? " f-" + META.types[c.type].cap : "") }, c ? META.types[c.type].tag : "");
  let val = "";
  if (c && c.type === "led_matrix") sock.replaceChildren(matrixView(ST && ST.matrix[c.id], "mxmini"));
  else if (c && c.type === "rgb_led") sock.style.background = RGB_CSS[stOf(c.id).v] || "";
  else if (c) val = stOf(c.id).txt || "";
  return h("button", {
    class: "pin" + (c ? " used" : "") + (res ? " reserved" : ""), "aria-pressed": sel ? "true" : "false", "data-pin": String(pid),
    title: p.label + (res ? " · " + t("res." + res) : c ? " · " + c.name : " · " + p.caps.map(k => t("cap." + k)).join(", ")),
    onclick: () => { SEL = c ? { comp: c.id } : { board: b.id, pin: pid }; render(); },
  }, h("span", { class: "pl" }, p.label), sock,
    c || res ? null : h("span", { class: "caps" }, p.caps.map(k => h("i", { class: "f-" + k }))),
    h("span", { class: "nm" }, c ? c.name : res ? t("res." + res) : ""), c ? h("span", { class: "val" }, val) : null);
}
function linkSummary(b) {
  const l = b.link || {};
  if (l.type === "i2c") return t("link.i2c") + " 0x" + (l.address || 16).toString(16).toUpperCase() + " · " + t("link.bus." + (l.bus || "wire"));
  if (l.type === "uart") return t("link.uart") + " · " + (l.baud || 115200);
  if (l.type === "usb") return t("link.usb") + (l.port && l.port !== "auto" ? " · " + l.port : "");
  return t("link." + l.type);
}
function viewBoards(m) {
  if (!boardById(SELB)) SELB = DRAFT.boards.length ? DRAFT.boards[0].id : null;
  const tabs = h("div", { class: "tabs", role: "tablist" }, DRAFT.boards.map(b => {
    const on = ST && ST.boards[b.id] && ST.boards[b.id].online;
    return h("button", { role: "tab", "aria-selected": b.id === SELB ? "true" : "false", onclick: () => { SELB = b.id; SEL = null; render(); } },
      h("span", { class: "dot " + (on ? "ok" : "err") }), b.name || b.id);
  }), h("button", { role: "tab", "aria-selected": "false", onclick: () => boardDialog(null) }, "+ " + t("boards.add")));
  m.append(tabs);
  const b = boardById(SELB);
  if (!b) {
    m.append(h("div", { class: "panel empty" }, h("p", {}, t("boards.none")), h("button", { class: "btn primary", onclick: () => boardDialog(null) }, t("boards.add"))));
    return;
  }
  const model = modelOf(b), st = (ST && ST.boards[b.id]) || {}, reserved = reservedPins(b);
  const left = h("div", {});
  left.append(h("div", { class: "boardmeta" }, h("h2", {}, b.name || b.id), h("span", { class: "chip" }, model.label),
    h("span", { class: "chip" }, linkSummary(b)),
    h("span", { class: "chip", id: "bstat" }, h("span", { class: "dot " + (st.online ? "ok" : "err") }), st.online ? t("online") : (st.text || t("offline"))),
    h("span", { class: "grow" }), h("button", { class: "btn sm", onclick: () => boardDialog(b) }, t("boards.edit")),
    h("button", { class: "btn sm danger", onclick: () => removeBoard(b) }, t("boards.remove"))));
  if (st.model && st.model !== b.model && META.models[st.model])
    left.append(h("div", { class: "note" }, t("boards.mismatch", META.models[st.model].label)));
  const caps = [...new Set(model.pins.flatMap(p => p.caps))];
  left.append(h("div", { class: "legend" }, h("span", {}, t("boards.legend")), caps.map(k => h("span", {}, h("span", { class: "tag f-" + k, style: "min-width:14px;height:8px;padding:0" }), t("cap." + k)))));
  const pcb = h("div", { class: "pcb" });
  for (const [group, pins] of model.layout) {
    const strip = h("div", { class: "strip" }, pins.map(pid => pinCell(b, pid, reserved)));
    if (group === "jmisc") pcb.append(h("details", { class: "adv", open: pins.some(pid => compAt(b.id, pid)) || null }, h("summary", {}, t("group.jmisc")), strip));
    else pcb.append(h("div", { class: "strip-title" }, t("group." + group)), strip);
  }
  left.append(pcb);
  const virt = comps().filter(c => !META.types[c.type].cap);
  left.append(h("div", { class: "virtrow" }, h("span", { class: "small muted" }, t("boards.virtual")),
    virt.map(c => h("button", { class: "vchip", "aria-pressed": SEL && SEL.comp === c.id ? "true" : "false", onclick: () => { SEL = { comp: c.id }; render(); } },
      h("span", { class: "tag" }, META.types[c.type].tag), c.name)),
    h("button", { class: "btn sm ghost", onclick: () => addComp("virtual_switch", null, null) }, "+ " + t("boards.virtual.add"))));
  m.append(h("div", { class: "boardgrid" }, left, h("div", { class: "panel inspector" }, inspector(b, reserved))));
}
function pinNotes(b, p) {
  const model = modelOf(b);
  const items = p.notes.map(n => (n.startsWith("port:") ? t("note.port", n.slice(5)) : t("note." + n)));
  items.push(t(model.logic === 5 ? "note.logic5" : "note.logic33"));
  return h("ul", { class: "pinnotes" }, items.map(x => h("li", {}, x)));
}
function field(def, obj, type, rerender) {
  const v = obj[def.key] ?? def.default;
  const label = tOr("prop." + def.key + "." + type, t("prop." + def.key));
  if (def.type === "bool")
    return h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!v, onchange: e => { obj[def.key] = e.target.checked; changed(); } }), label);
  let inp;
  if (def.type === "select") {
    inp = h("select", { onchange: e => { obj[def.key] = e.target.value; changed(); if (rerender) render(); } },
      def.options.map(o => h("option", { value: o, selected: o === v ? true : null }, tOr("opt." + def.key + "." + o, o || t("none")))));
  } else {
    inp = h("input", { type: def.type === "number" ? "number" : "text", value: v, min: def.min, max: def.max, step: def.step || "any",
      oninput: e => { obj[def.key] = def.type === "number" ? (e.target.value === "" ? def.default : +e.target.value) : e.target.value; changed(); } });
  }
  return h("div", {}, h("label", {}, label), inp);
}
function inspector(b, reserved) {
  const c = SEL && SEL.comp ? compById(SEL.comp) : null;
  if (!c) {
    const p = SEL && SEL.board === b.id ? pinOf(b, SEL.pin) : null;
    if (!p) return [h("h3", {}, t("nav.boards")), h("p", { class: "muted" }, t("pin.select")), h("div", { class: "note info" }, t("pin.tip"))];
    if (reserved[p.id]) return [h("h3", {}, p.label), h("p", { class: "muted" }, t("pin.reserved", p.label))];
    const types = Object.entries(META.types).filter(([, ty]) => ty.cap && p.caps.includes(ty.cap));
    return [h("h3", {}, t("pin.free", p.label)), h("p", { class: "muted small" }, t("pin.free.hint")),
      h("div", { class: "choices" }, types.map(([k, ty]) => h("button", { class: "choice", onclick: () => addComp(k, b.id, p.id) },
        h("span", { class: "tag f-" + ty.cap }, ty.tag), h("b", {}, typeLabel(k)), h("span", { class: "d" }, t("type." + k + ".d"))))),
      pinNotes(b, p)];
  }
  const ty = META.types[c.type], cb = boardById(c.board);
  c.props = c.props || {};
  const f = h("div", { class: "form" });
  f.append(h("h3", {}, h("span", { class: "tag f-" + (ty.cap || "virt") }, ty.tag), " ", typeLabel(c.type)));
  f.append(h("label", {}, t("pin.name")), h("input", { type: "text", value: c.name, oninput: e => { c.name = e.target.value; changed(); } }));
  f.append(h("label", {}, t("pin.id")), h("input", { type: "text", value: c.id, spellcheck: "false", onchange: e => {
    const nv = e.target.value.trim();
    if (!ID_RE.test(nv)) { toast(t("pin.id.bad")); e.target.value = c.id; return; }
    if (compById(nv) && nv !== c.id) { toast(t("pin.id.used")); e.target.value = c.id; return; }
    renameComp(c, nv); changed(); render();
  } }));
  if (ty.cap && cb) {
    const res = reservedPins(cb);
    const opts = modelOf(cb).pins.filter(p => p.caps.includes(ty.cap) && !res[p.id]).map(p => {
      const u = compAt(cb.id, p.id), taken = u && u.id !== c.id;
      return h("option", { value: JSON.stringify(p.id), selected: p.id === c.pin ? true : null, disabled: taken ? true : null }, p.label + (taken ? " (" + u.name + ")" : ""));
    });
    f.append(h("label", {}, t("pin.pin")), h("select", { onchange: e => { c.pin = JSON.parse(e.target.value); changed(); render(); } }, opts));
  }
  const pr = propsOf(c);
  for (const def of ty.props) {
    if (def.show) { const [k, want] = def.show.split("="); if (String(pr[k]) !== want) continue; }
    f.append(field(def, c.props, c.type, ty.props.some(x => x.show && x.show.startsWith(def.key + "="))));
  }
  const p = cb ? pinOf(cb, c.pin) : null;
  if (p) f.append(pinNotes(cb, p));
  f.append(h("div", { style: "margin-top:16px;display:flex;gap:8px" }, h("button", { class: "btn danger", onclick: () => removeComp(c.id) }, t("remove")),
    h("button", { class: "btn", onclick: () => { SEL = null; render(); } }, t("close"))));
  return [f];
}
function removeBoard(b) {
  const n = comps().filter(c => c.board === b.id).length;
  if (!confirm(t("boards.remove.confirm", b.name || b.id, n))) return;
  DRAFT.components = comps().filter(c => c.board !== b.id);
  DRAFT.boards = DRAFT.boards.filter(x => x.id !== b.id);
  SELB = null; SEL = null;
  changed(); render();
}
function masterHost() {   // what another board should dial; "localhost" would point at itself
  return /^(localhost|127\.|\[?::1)/.test(location.hostname) ? "<IP of this master>" : location.hostname;
}
function firmwareHelp(b) {
  const l = b.link || {}, box = h("div", {});
  if (l.type === "local") return box;
  box.append(h("h3", { style: "margin-top:18px" }, t("fw.title")));
  if (META.sim) box.append(h("div", { class: "note info" }, t("wire.sim")));
  if (b.model === "unoq") {
    box.append(h("p", { class: "small" }, t("fw.node")), h("pre", { class: "code" }, t("fw.node.vals", b.id || "?", masterHost(), DRAFT.mqtt.port || 1883)));
  } else {
    const lines = { usb: ["#define QL_LINK  QL_USB", "#define QL_BAUD  " + (l.baud || 115200)],
      uart: ["#define QL_LINK  QL_UART", "#define QL_BAUD  " + (l.baud || 115200)],
      i2c: ["#define QL_LINK  QL_I2C", "#define QL_I2C_ADDRESS  0x" + (l.address || 16).toString(16).toUpperCase()],
      wifi: ["#define QL_LINK  QL_WIFI", '#define QL_BOARD_ID     "' + (b.id || "?") + '"', '#define QL_MASTER_HOST  "' + masterHost() + '"',
        "#define QL_MASTER_PORT  " + (DRAFT.mqtt.mode === "external" ? DRAFT.mqtt.ext_port : DRAFT.mqtt.port || 1883), '#define QL_WIFI_SSID    "..."', '#define QL_WIFI_PASS    "..."'] }[l.type] || [];
    box.append(h("p", { class: "small" }, t("fw.sat")), h("pre", { class: "code" }, lines.join("\n")));
  }
  box.append(h("p", { class: "small muted", style: "margin-top:8px" }, t("wire." + l.type)));
  return box;
}
function boardDialog(existing) {
  const isNew = !existing;
  const hasLocal = DRAFT.boards.some(x => (x.link || {}).type === "local" && x !== existing);
  const b = existing ? clone(existing) : { id: "", name: "", model: "uno", link: { type: "usb", port: "auto" } };
  let idTouched = !isNew;
  const modal = $("#modal");
  const closeDlg = () => { modal.hidden = true; modal.replaceChildren(); };
  function linkTypes() { return modelOf(b).links.filter(k => k !== "local" || (!hasLocal && META.has_local)); }
  function setLink(type) {
    const used = DRAFT.boards.filter(x => x !== existing && (x.link || {}).type === "i2c").map(x => x.link.address);
    let addr = 16; while (used.includes(addr)) addr++;
    b.link = { local: { type: "local" }, usb: { type: "usb", port: "auto" }, uart: { type: "uart", baud: 115200 },
      i2c: { type: "i2c", bus: "wire", address: addr }, wifi: { type: "wifi" } }[type];
  }
  function draw() {
    if (!linkTypes().includes(b.link.type)) setLink(linkTypes()[0]);
    const l = b.link, f = h("div", { class: "form" });
    f.append(h("div", { class: "row" },
      h("div", {}, h("label", {}, t("board.name")), h("input", { type: "text", value: b.name, id: "bdName", oninput: e => {
        b.name = e.target.value;
        if (!idTouched) { b.id = e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^[^a-z]+/, "").slice(0, 24); $("#bdId").value = b.id; }
      } })),
      h("div", {}, h("label", {}, t("board.id")), h("input", { type: "text", value: b.id, id: "bdId", disabled: isNew ? null : true, spellcheck: "false",
        oninput: e => { idTouched = true; b.id = e.target.value.trim(); } }))));
    f.append(h("label", {}, t("board.model")), h("select", { disabled: isNew ? null : true, onchange: e => { b.model = e.target.value; draw(); } },
      Object.entries(META.models).map(([k, mm]) => h("option", { value: k, selected: k === b.model ? true : null }, mm.label))));
    f.append(h("label", {}, t("board.link")), h("div", { class: "seg" }, linkTypes().map(k =>
      h("button", { "aria-pressed": l.type === k ? "true" : "false", onclick: () => { setLink(k); draw(); } }, t("link." + k)))));
    if (l.type === "usb") {
      const names = ["auto", ...new Set([...PORTS.map(p => p.name), l.port].filter(x => x && x !== "auto"))];
      f.append(h("label", {}, t("link.port")), h("select", { onchange: e => { l.port = e.target.value; } },
        names.map(n => h("option", { value: n, selected: n === (l.port || "auto") ? true : null }, n === "auto" ? t("link.port.auto") : n))));
    } else if (l.type === "uart") {
      f.append(h("label", {}, t("link.baud")), h("select", { onchange: e => { l.baud = +e.target.value; draw(); } },
        [9600, 19200, 38400, 57600, 115200].map(n => h("option", { value: n, selected: n === l.baud ? true : null }, String(n)))));
    } else if (l.type === "i2c") {
      f.append(h("div", { class: "row" },
        h("div", {}, h("label", {}, t("link.bus")), h("select", { onchange: e => { l.bus = e.target.value; } },
          ["wire", "qwiic"].map(k => h("option", { value: k, selected: k === l.bus ? true : null }, t("link.bus." + k))))),
        h("div", {}, h("label", {}, t("link.addr")), h("input", { type: "number", min: 8, max: 119, value: l.address, onchange: e => { l.address = +e.target.value; draw(); } }))));
    }
    f.append(firmwareHelp(b));
    const ok = () => {
      b.name = b.name.trim() || b.id;
      if (!ID_RE.test(b.id)) { toast(t("pin.id.bad")); return; }
      if (isNew && boardById(b.id)) { toast(t("pin.id.used")); return; }
      if (isNew) DRAFT.boards.push(b); else Object.assign(existing, b);
      const res = reservedPins(b);               // a link may claim pins that already had a job
      DRAFT.components = comps().filter(c => !(c.board === b.id && res[c.pin]));
      SELB = b.id; SEL = null;
      closeDlg(); changed(); render();
    };
    modal.replaceChildren(h("div", { class: "dialog", role: "dialog", "aria-modal": "true" }, h("h2", {}, t(isNew ? "boards.add" : "boards.edit")), f,
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: closeDlg }, t("cancel")), h("button", { class: "btn primary", onclick: ok }, t(isNew ? "add" : "done")))));
  }
  modal.hidden = false;
  draw();
  api("/api/ports").then(p => { PORTS = p || []; if (!modal.hidden && b.link.type === "usb") draw(); }).catch(() => {});
  setTimeout(() => { const n = $("#bdName"); if (n) n.focus(); }, 0);
}

// ------------------------------------------------------------------ blocks
async function showBlocks() {
  if (!BLOCKS_READY) {
    BLOCKS_READY = true;
    await QBlocks.init($("#blocklyDiv"), { comps: () => DRAFT.components, boards: () => DRAFT.boards, kinds: META.entity_fields }, META.colors,
      ws => { DRAFT.scripts = DRAFT.scripts || {}; DRAFT.scripts.workspace = ws; changed(); });
  }
  QBlocks.resize();
  QBlocks.load((DRAFT.scripts || {}).workspace);   // also refreshes device names inside blocks
  blocksFoot();
}
function blocksFoot() {
  const vars = ST ? Object.entries(ST.vars || {}) : [];
  $("#blocksFoot").replaceChildren(h("span", {}, t("blocks.hint")), h("span", { class: "grow" }),
    h("span", { class: "vars" }, vars.map(([k, v]) => h("span", { class: "var" }, k + " = " + v))),
    h("span", { class: "chip" }, h("span", { class: "dot " + (ST && ST.running ? "ok" : "") }), t("blocks.running", ST ? ST.running : 0)));
}

// ------------------------------------------------------------------ settings
function viewSettings(m) {
  const d = DRAFT.device, q = DRAFT.mqtt;
  const inp = (o, k, label, type) => h("div", {}, h("label", {}, label), h("input", { type: type || "text", value: o[k] ?? "", autocomplete: "off",
    oninput: e => { o[k] = type === "number" ? +e.target.value : e.target.value; changed(); } }));
  const seg = (o, k, opts) => h("div", { class: "seg" }, opts.map(([v, label]) =>
    h("button", { "aria-pressed": (o[k] || opts[0][0]) === v ? "true" : "false", onclick: () => { o[k] = v; changed(); render(); } }, label)));
  const g = h("div", { class: "cols" });
  m.append(g);
  g.append(h("div", { class: "panel form" }, h("h3", {}, t("set.device")), inp(d, "name", t("set.name")), inp(d, "node", t("set.node")),
    inp(d, "tz_offset", t("set.tz"), "number"), inp(d, "web_password", t("set.pw"), "password"),
    h("p", { class: "small muted", style: "margin-top:10px" }, t("set.url", location.origin))));
  if (META.has_local) {
    const role = h("div", { class: "panel form" }, h("h3", {}, t("set.role")), seg(d, "role", [["master", t("role.master")], ["node", t("role.node")]]),
      h("p", { class: "small muted", style: "margin-top:8px" }, t("role." + (d.role || "master") + ".d")));
    if (d.role === "node") role.append(inp(d, "master_host", t("set.master_host")), inp(d, "master_port", t("set.master_port"), "number"),
      h("div", { class: "row" }, inp(d, "master_user", t("set.master_user")), inp(d, "master_pass", t("set.master_pass"), "password")));
    g.append(role);
  }
  if (!isNode()) {
    const mq = h("div", { class: "panel form" }, h("h3", {}, t("set.mqtt")),
      seg(q, "mode", [["internal", t("mqtt.internal")], ["external", t("mqtt.external")], ["off", t("mqtt.off")]]));
    if (q.mode === "internal") {
      mq.append(inp(q, "port", t("mqtt.port"), "number"), h("div", { class: "row" }, inp(q, "username", t("mqtt.user")), inp(q, "password", t("mqtt.pass"), "password")),
        h("div", { class: "note info" }, t("mqtt.ha", location.hostname, q.port || 1883)));
      if (!q.username) mq.append(h("div", { class: "note" }, t("mqtt.open")));
    } else if (q.mode === "external") {
      mq.append(h("div", { class: "row" }, inp(q, "host", t("mqtt.host")), inp(q, "ext_port", t("mqtt.port"), "number")),
        h("div", { class: "row" }, inp(q, "username", t("mqtt.user")), inp(q, "password", t("mqtt.pass"), "password")));
    }
    if (q.mode !== "off") mq.append(h("label", { class: "check" }, h("input", { type: "checkbox", checked: q.discovery !== false, onchange: e => { q.discovery = e.target.checked; changed(); } }), t("mqtt.discovery")),
      inp(q, "prefix", t("mqtt.prefix")), h("p", { class: "small muted", style: "margin-top:10px" }, t("mqtt.topics", d.node)));
    g.append(mq);
    const usb = h("div", { class: "panel" }, h("h3", {}, t("set.usb")), h("p", { class: "small muted" }, t("set.usb.help", META.link_port || "-")), h("div", { id: "portlist" }));
    g.append(usb);
    api("/api/ports").then(p => {
      PORTS = p || [];
      const box = $("#portlist");
      if (box) box.replaceChildren(PORTS.length ? h("ul", { class: "pinnotes" }, PORTS.map(x => h("li", {}, x.name + (x.busy ? " (" + t("set.busy") + ")" : "")))) : h("p", { class: "small muted" }, t("set.usb.none")));
    }).catch(() => {});
  }
  const file = h("input", { type: "file", accept: ".json", hidden: true, onchange: async e => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      if (!j.components || !j.device) throw new Error("shape");
      j.boards = j.boards || [{ id: "main", name: "UNO Q", model: "unoq", link: { type: "local" } }];
      j.components.forEach(c => { if (!c.board && META.types[c.type] && META.types[c.type].cap) c.board = "main"; });
      j.scripts = j.scripts || { workspace: {} };
      delete j.automations;
      DRAFT = j; SEL = null; changed(); toast(t("set.imported")); render();
    } catch (err) { toast(t("set.import.bad")); }
  } });
  g.append(h("div", { class: "panel" }, h("h3", {}, t("set.backup")), h("div", { style: "display:flex;gap:8px;flex-wrap:wrap" },
    h("a", { class: "btn", href: "/api/export" }, t("set.export")), h("button", { class: "btn", onclick: () => file.click() }, t("set.import")), file)));
}

// ------------------------------------------------------------------ log
function logLine(l) { return h("div", { class: l.lvl }, h("span", { class: "muted" }, new Date(l.t * 1000).toLocaleTimeString()), h("span", {}, l.msg)); }
function viewLog(m) {
  m.append(h("div", { class: "panel log", id: "logbox" }, LOGS.length ? LOGS.slice().reverse().map(logLine) : h("p", { class: "muted" }, t("log.empty"))));
}

// ------------------------------------------------------------------ live updates
function applyLive(newLogs) {
  const chips = $("#chips");
  const list = isNode() ? [] : CFG.boards.map(b => {
    const s = ST.boards[b.id] || {};
    return h("span", { class: "chip", title: s.text || "" }, h("span", { class: "dot " + (s.online ? "ok" : "err") }), b.name || b.id);
  });
  if (META.sim) list.push(h("span", { class: "chip", style: "border-color:var(--adc);color:var(--adc)" }, t("sim.badge")));
  chips.replaceChildren(...list);
  const mq = $("#statMqtt");
  mq.querySelector(".dot").className = "dot " + (ST.mqtt.ok ? "ok" : ST.mqtt.mode === "off" ? "" : "warn");
  mq.querySelector(".lbl").textContent = "MQTT: " + ST.mqtt.text;
  const typing = document.activeElement && document.activeElement.matches("input,select,textarea");
  if (VIEW === "dash" && !typing) render();
  else if (VIEW === "boards") {
    document.querySelectorAll(".pin.used").forEach(el => {
      const raw = el.dataset.pin, c = compAt(SELB, isNaN(+raw) ? raw : +raw);
      if (!c) return;
      const v = el.querySelector(".val"), sock = el.querySelector(".sock");
      if (c.type === "led_matrix") sock.replaceChildren(matrixView(ST.matrix[c.id], "mxmini"));
      else if (c.type === "rgb_led") sock.style.background = RGB_CSS[stOf(c.id).v] || "";
      else if (v) v.textContent = stOf(c.id).txt || "";
    });
    const bs = $("#bstat"), s = ST.boards[SELB];
    if (bs && s) bs.replaceChildren(h("span", { class: "dot " + (s.online ? "ok" : "err") }), s.online ? t("online") : (s.text || t("offline")));
  } else if (VIEW === "blocks") blocksFoot();
  else if (VIEW === "log" && newLogs) render();
}
let lastBoards = "";
async function poll() {
  try {
    const s = await api("/api/state?log=" + LOGSEQ);
    for (const l of s.logs) { LOGS.push(l); LOGSEQ = Math.max(LOGSEQ, l.n); }
    if (LOGS.length > 400) LOGS = LOGS.slice(-400);
    for (const [id, v] of Object.entries(s.states)) {
      if (typeof v.v !== "number") continue;
      const a = (HIST[id] = HIST[id] || []);
      if (a._t !== v.t) { a.push(v.v); a._t = v.t; if (a.length > 60) a.shift(); }
    }
    const boardsNow = JSON.stringify(s.boards) + JSON.stringify(s.mqtt) + JSON.stringify(s.vars) + s.running + JSON.stringify(s.node || 0);
    const fresh = !ST || ST.rev !== s.rev || boardsNow !== lastBoards;
    lastBoards = boardsNow;
    ST = s;
    if (fresh || s.logs.length) applyLive(s.logs.length);
  } catch (e) {
    $("#statMqtt .dot").className = "dot err";
    $("#statMqtt .lbl").textContent = t("server.down");
  }
  setTimeout(poll, 800);
}

// ------------------------------------------------------------------ boot
function showErrors(list) {
  const eb = $("#errbox");
  eb.replaceChildren(h("b", {}, t("save.failed")), h("ul", {}, list.map(x => h("li", {}, x))));
  eb.hidden = false;
}
async function save() {
  const r = await api("/api/config", DRAFT);
  if (!r.ok) { showErrors(r.errors || ["?"]); return; }
  CFG = clone(DRAFT);
  $("#errbox").hidden = true;
  toast(t("saved"));
  if (r.restart) { toast(t("restart.note")); setTimeout(() => location.reload(), 5000); }
  render();
}
(async () => {
  document.documentElement.lang = LANG;
  $("#lang").replaceChildren(...Object.entries(LANG_NAMES).map(([k, label]) =>
    h("button", { "aria-pressed": k === LANG ? "true" : "false", onclick: () => { try { localStorage.setItem("qhome.lang", k); } catch (e) { /* ignore */ } location.reload(); } }, label)));
  $("#bSave").textContent = t("save");
  $("#bUndo").textContent = t("discard");
  $("#bSave").onclick = save;
  $("#bUndo").onclick = () => { DRAFT = clone(CFG); SEL = null; $("#errbox").hidden = true; render(); };
  META = await api("/api/meta");
  CFG = await api("/api/config");
  CFG.scripts = CFG.scripts || { workspace: {} };
  DRAFT = clone(CFG);
  $("#ver").textContent = "QHome " + META.version;
  window.addEventListener("beforeunload", e => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("resize", () => { if (VIEW === "blocks") QBlocks.resize(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#modal").hidden) { $("#modal").hidden = true; $("#modal").replaceChildren(); } });
  renderNav();
  render();
  poll();
})();
