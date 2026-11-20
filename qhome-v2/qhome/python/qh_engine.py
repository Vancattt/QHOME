"""QHome engine: boards, devices (components), live state, block scripts, MQTT / Home Assistant.

One thread owns all state. Everything from outside (web requests, board events, MQTT
messages, link traffic) arrives through `post()` and is handled in `step()`.
"""
import copy
import json
import os
import queue
import re
import threading
import time
from collections import deque

import qh_boards
import qh_font as font
import qh_link
import qh_script
from qh_hw import (M_ADC, M_CNT, M_DAC, M_DIN, M_DIN_PD, M_DIN_PU, M_DOUT, M_OFF, M_PWM,
                   RGB_BITS, QLinkHW)
from qh_mqtt import topic_match

VERSION = "2.0.0"
LINK_PORT = 7001

_DC_BIN = ["", "door", "window", "garage_door", "motion", "occupancy", "presence", "opening",
           "smoke", "moisture", "vibration", "power", "problem", "safety", "lock", "running"]
_DC_SENS = ["", "temperature", "humidity", "pressure", "voltage", "current", "power",
            "illuminance", "distance", "moisture", "battery", "volume_flow_rate", "weight"]
_RESTORE = ["off", "on", "restore"]


def _n(key, default, lo=None, hi=None, step=None, show=None):
    d = {"key": key, "type": "number", "default": default}
    for k, v in (("min", lo), ("max", hi), ("step", step), ("show", show)):
        if v is not None:
            d[k] = v
    return d


def _s(key, default, options, show=None):
    d = {"key": key, "type": "select", "default": default, "options": options}
    if show:
        d["show"] = show
    return d


# Component types. Labels live in the web UI's language packs (assets/i18n.js).
TYPES = {
    "binary_input": {"cap": "din", "tag": "DI", "kind": "in", "ha": "binary_sensor", "props": [
        _s("pull", "up", ["up", "down", "none"]),
        {"key": "invert", "type": "bool", "default": True},
        _n("debounce", 30, 0, 2000), _s("device_class", "", _DC_BIN)]},
    "analog_sensor": {"cap": "adc", "tag": "AI", "kind": "in", "ha": "sensor", "props": [
        _n("interval", 1000, 50, 600000),
        _s("calib", "voltage", ["voltage", "percent", "raw", "linear"]),
        _n("in_min", 0, step=0.001, show="calib=linear"),
        _n("in_max", 3.3, step=0.001, show="calib=linear"),
        _n("out_min", 0, step=0.001, show="calib=linear"),
        _n("out_max", 100, step=0.001, show="calib=linear"),
        {"key": "unit", "type": "text", "default": "V"},
        _n("decimals", 2, 0, 4), _n("avg", 4, 1, 50), _n("delta", 0, step=0.001),
        _s("device_class", "voltage", _DC_SENS)]},
    "pulse_counter": {"cap": "cnt", "tag": "CNT", "kind": "in", "ha": "sensor", "props": [
        _n("interval", 1000, 100, 600000), _s("mode", "rate", ["rate", "total"]),
        _n("multiplier", 1, step=0.000001),
        {"key": "unit", "type": "text", "default": "pulses/min"},
        _n("decimals", 1, 0, 4), _s("device_class", "", _DC_SENS)]},
    "switch_output": {"cap": "dout", "tag": "DO", "kind": "out", "ha": "switch", "props": [
        {"key": "invert", "type": "bool", "default": False},
        _s("restore", "off", _RESTORE), _s("ha_as", "switch", ["switch", "light"])]},
    "pwm_output": {"cap": "pwm", "tag": "PWM", "kind": "out", "ha": "light", "props": [
        {"key": "invert", "type": "bool", "default": False},
        _n("min_pct", 0, 0, 100), _n("max_pct", 100, 0, 100), _s("restore", "off", _RESTORE)]},
    "dac_output": {"cap": "dac", "tag": "AO", "kind": "out", "ha": "number", "props": [
        _s("restore", "off", ["off", "restore"])]},
    "rgb_led": {"cap": "rgb", "tag": "RGB", "kind": "out", "ha": "select", "props": [
        _s("restore", "off", ["off", "restore"])]},
    "led_matrix": {"cap": "matrix", "tag": "MX", "kind": "out", "ha": "text", "props": [
        _n("brightness", 4, 1, 7), _n("scroll_ms", 120, 40, 1000),
        {"key": "startup_text", "type": "text", "default": "QHOME"}]},
    "virtual_switch": {"cap": None, "tag": "VS", "kind": "out", "ha": "switch", "props": [
        _s("restore", "restore", _RESTORE)]},
}
BOOL_TYPES = ("binary_input", "switch_output", "virtual_switch")
OUTPUT_TYPES = ("switch_output", "virtual_switch", "pwm_output", "dac_output", "rgb_led", "led_matrix")
_TR_RGB = {"kapali": "off", "kirmizi": "red", "yesil": "green", "sari": "yellow", "mavi": "blue",
           "mor": "magenta", "camgobegi": "cyan", "beyaz": "white"}

MAIN_BOARD = {"id": "main", "name": "UNO Q", "model": "unoq", "link": {"type": "local"}}
DEFAULT_CONFIG = {
    "version": 2,
    "device": {"name": "QHome", "node": "qhome", "tz_offset": 3, "web_password": "",
               "role": "master", "master_host": "", "master_port": 1883,
               "master_user": "", "master_pass": ""},
    "mqtt": {"mode": "internal", "port": 1883, "username": "", "password": "", "host": "",
             "ext_port": 1883, "discovery": True, "prefix": "homeassistant"},
    "boards": [MAIN_BOARD],
    "components": [
        {"id": "status_led", "type": "rgb_led", "name": "Status LED", "board": "main",
         "pin": "LED3", "props": {}},
        {"id": "matrix", "type": "led_matrix", "name": "Matrix", "board": "main",
         "pin": "MATRIX", "props": {}},
    ],
    "scripts": {"workspace": {}},
}

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def props_of(comp):
    out = {p["key"]: p["default"] for p in TYPES[comp["type"]]["props"]}
    out.update(comp.get("props") or {})
    return out


def _num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def migrate(cfg):
    """Bring a version 1 config (single board, IF/THEN rules) up to version 2."""
    notes = []
    if "boards" not in cfg:
        cfg["boards"] = [copy.deepcopy(MAIN_BOARD)]
        for c in cfg.get("components", []):
            c.setdefault("board", "main")
        notes.append("Config upgraded to version 2 (components moved to board 'main').")
    if cfg.pop("automations", None):
        notes.append("Version 1 automations are not converted; rebuild them in the Blocks tab.")
    cfg["version"] = 2
    cfg.setdefault("scripts", {"workspace": {}})
    return notes


def validate(cfg):
    """Catch a broken config before it is saved. Returns a list of messages."""
    errs = []
    node = (cfg.get("device") or {}).get("node", "")
    if not re.match(r"^[a-z0-9_-]{1,32}$", node or ""):
        errs.append("Node name may only use lowercase letters, digits, - and _.")
    boards, i2c_seen, uarts, locals_ = {}, set(), 0, 0
    for b in cfg.get("boards", []):
        bid, m = b.get("id", ""), qh_boards.model(b.get("model"))
        name = b.get("name") or bid
        if not ID_RE.match(bid or ""):
            errs.append("Board id '%s' is not valid (start with a letter; a-z 0-9 _)." % bid)
        if bid in boards:
            errs.append("Board id '%s' is used twice." % bid)
        boards[bid] = b
        if m is None:
            errs.append("%s: unknown board model '%s'." % (name, b.get("model")))
            continue
        link = b.get("link") or {}
        lt = link.get("type")
        if lt not in m["links"]:
            errs.append("%s: a %s cannot be connected by '%s'." % (name, m["label"], lt))
        if lt == "local":
            locals_ += 1
        elif lt == "uart":
            uarts += 1
        elif lt == "i2c":
            addr = link.get("address")
            if not isinstance(addr, int) or not 8 <= addr <= 119:
                errs.append("%s: I2C address must be between 8 and 119." % name)
            key = (link.get("bus", "wire"), addr)
            if key in i2c_seen:
                errs.append("%s: that I2C address is already used on the same bus." % name)
            i2c_seen.add(key)
        elif lt == "wifi" and (cfg.get("mqtt") or {}).get("mode", "off") == "off":
            errs.append("%s: Wi-Fi boards need MQTT. Turn it on in Settings." % name)
    if locals_ > 1:
        errs.append("Only one board can be the built-in (local) one.")
    if uarts > 1:
        errs.append("Only one board can use the UART on D0/D1. Use I2C or USB for the others.")
    ids, used = set(), {}
    comps = {}
    for c in cfg.get("components", []):
        cid, typ = c.get("id", ""), c.get("type")
        name = c.get("name") or cid
        if typ not in TYPES:
            errs.append("Unknown device type: %s" % typ)
            continue
        if not ID_RE.match(cid or ""):
            errs.append("Device id '%s' is not valid (start with a letter; a-z 0-9 _)." % cid)
        if cid in ids:
            errs.append("Device id '%s' is used twice." % cid)
        ids.add(cid)
        comps[cid] = c
        cap = TYPES[typ]["cap"]
        if cap is None:
            continue
        b = boards.get(c.get("board"))
        m = qh_boards.model(b.get("model")) if b else None
        if m is None:
            errs.append("%s: its board '%s' does not exist." % (name, c.get("board")))
            continue
        pin = m["pin_by_id"].get(c.get("pin"))
        if pin is None:
            errs.append("%s: no pin selected." % name)
            continue
        if cap not in pin["caps"]:
            errs.append("%s: pin %s on %s cannot do that." % (name, pin["label"], b.get("name") or b["id"]))
        if pin["id"] in qh_boards.reserved_pins(b, cfg.get("boards", [])):
            errs.append("%s: pin %s is used by a board link and is not free." % (name, pin["label"]))
        key = (b["id"], pin["id"])
        if key in used:
            errs.append("Pin %s on %s is used by both %s and %s."
                        % (pin["label"], b.get("name") or b["id"], used[key], cid))
        used[key] = cid
    ws = (cfg.get("scripts") or {}).get("workspace") or {}
    errs += qh_script.validate(ws, comps, boards)
    return errs


class Engine:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.cfg_path = os.path.join(data_dir, "config.json")
        self.state_path = os.path.join(data_dir, "state.json")
        self.q = queue.Queue()
        self.lock = threading.RLock()
        self.logs = deque(maxlen=400)
        self.log_seq = 0
        self.rev = 0
        self.state, self.stamp, self.aux, self.mx = {}, {}, {}, {}
        self.hws, self.transports, self.applied, self.online = {}, {}, {}, {}
        self.lid_board = {}
        self.disc_topics = set()
        self.local = None
        self.mqtt = None
        self.ports = qh_link.PortHub(self.log)
        self.scripts = qh_script.Runtime(self)
        self.started = time.time()
        self._last_probe = 0
        self._state_dirty = 0
        self._scripts_started = False
        self._seen = set()
        self.want_restart = False
        self.cfg = None
        self.saved_state = self._load_json(self.state_path, {})

    # ------------------------------------------------------------ helpers
    def log(self, text, level="info"):
        with self.lock:
            self.log_seq += 1
            self.logs.append({"n": self.log_seq, "t": time.time(), "lvl": level, "msg": text})
        print("[%s] %s" % (level, text), flush=True)

    @staticmethod
    def _load_json(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return copy.deepcopy(default)

    def load_config(self, has_local=True):
        default = copy.deepcopy(DEFAULT_CONFIG)
        if not has_local:
            default["boards"], default["components"] = [], []
        cfg = self._load_json(self.cfg_path, default)
        for note in migrate(cfg):
            self.log(note, "warn")
        for k, v in default.items():
            if k not in cfg:
                cfg[k] = copy.deepcopy(v)
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    cfg[k].setdefault(kk, vv)
        self.cfg = cfg
        return cfg

    def _atomic_write(self, path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def comps(self):
        return {c["id"]: c for c in self.cfg["components"]}

    def boards(self):
        return {b["id"]: b for b in self.cfg["boards"]}

    def model_of(self, c):
        b = self.boards().get(c.get("board"))
        return qh_boards.model(b.get("model")) if b else None

    def node(self):
        return self.cfg["device"]["node"]

    def local_time(self):
        return time.gmtime(time.time() + _num(self.cfg["device"].get("tz_offset"), 3) * 3600)

    def board_online(self, bid):
        return bool(self.online.get(bid))

    # ------------------------------------------------------------ thread-safe inputs
    def post(self, item):
        self.q.put(item)

    def hw_event(self, pin, val):
        self.q.put(("hw", self._local_board_id(), pin, val))

    def hw_boot(self, ver):
        self.q.put(("boot", "main_local", ver))

    def hw_link_rx(self, lid, line):
        self.q.put(("mcu_link", lid, line))

    def mqtt_message(self, topic, payload):
        self.q.put(("mqtt", topic, payload))

    def mqtt_connected(self):
        self.q.put(("mqtt_up",))

    def submit_command(self, cid, action, value=None):
        self.q.put(("cmd", cid, action, value))

    def submit_config(self, cfg):
        migrate(cfg)
        errs = validate(cfg)
        if errs:
            return errs
        self._atomic_write(self.cfg_path, cfg)
        self.q.put(("config", cfg))
        return []

    def sim_input(self, bid, pin, value):
        hw = self.hws.get(bid)
        tr = self.transports.get(bid)
        sim = hw if getattr(hw, "simulated", False) else getattr(tr, "sim", None)
        return bool(sim and sim.sim_input(pin, value))

    # ------------------------------------------------------------ start-up
    def start(self, local, mqtt):
        self.local, self.mqtt = local, mqtt
        self.ports.listen(int(os.environ.get("QHOME_LINK_PORT", LINK_PORT)))
        self._setup_mqtt()
        self._build_boards()
        self._init_states()
        self.scripts.load(self.cfg["scripts"].get("workspace"))
        if local is not None:
            if local.probe():
                self.log("Microcontroller connected (firmware v%s)" % local.version)
                self._board_up("main_local")
            else:
                self.log("The microcontroller does not answer yet. Is the sketch uploaded? Retrying.", "warn")

    def _local_board_id(self):
        return next((b["id"] for b in self.cfg["boards"]
                     if (b.get("link") or {}).get("type") == "local"), None)

    def _build_boards(self):
        for tr in self.transports.values():
            tr.stop()
        self.hws, self.transports, self.applied, self.lid_board = {}, {}, {}, {}
        lid = 0
        for b in self.cfg["boards"]:
            bid = b["id"]
            self.applied[bid] = {}
            if (b.get("link") or {}).get("type") == "local":
                if self.local is not None:
                    self.hws[bid] = self.local
                    self.online[bid] = bool(self.local.connected)
                else:
                    self.online[bid] = False
                continue
            hw = QLinkHW(bid, lambda pin, val, bid=bid: self.post(("hw", bid, pin, val)),
                         lambda boot, bid=bid: self.post(("boot", bid, boot)), self.log)
            tr = qh_link.create(self, b, hw, lid)
            if isinstance(tr, qh_link.McuTransport):
                self.lid_board[lid] = bid
                lid += 1
            self.hws[bid], self.transports[bid] = hw, tr
            self.online[bid] = False
            tr.start()
        for bid in list(self.online):
            if bid not in self.boards():
                del self.online[bid]

    def _init_states(self):
        for c in self.cfg["components"]:
            cid, typ, p = c["id"], c["type"], props_of(c)
            if cid in self.state:
                continue
            saved = self.saved_state.get(cid)
            restore = p.get("restore", "off")
            if typ in ("switch_output", "virtual_switch"):
                v = bool(saved) if restore == "restore" and saved is not None else restore == "on"
            elif typ == "pwm_output":
                v = {"on": False, "level": 100}
                if restore == "restore" and isinstance(saved, dict):
                    v = {"on": bool(saved.get("on")), "level": _num(saved.get("level"), 100)}
                elif restore == "on":
                    v = {"on": True, "level": 100}
            elif typ == "dac_output":
                v = _num(saved, 0) if restore == "restore" and saved is not None else 0.0
            elif typ == "rgb_led":
                saved = _TR_RGB.get(saved, saved)
                v = saved if restore == "restore" and saved in RGB_BITS else "off"
            elif typ == "led_matrix":
                v = ""
                self.mx[cid] = {"cols": [], "offset": 0, "scroll": False, "next": 0, "text": None,
                                "template": p.get("startup_text", ""), "icon": None, "last": None,
                                "rerender": 0, "startup": True}
            else:
                v = None
            self.state[cid] = v
            self.stamp[cid] = time.time()

    # ------------------------------------------------------------ applying to hardware
    def _desired_pins(self, bid):
        out = {}
        for c in self.cfg["components"]:
            typ, pin, p = c["type"], c.get("pin"), props_of(c)
            if c.get("board") != bid or not isinstance(pin, int):
                continue
            if typ == "binary_input":
                mode = {"up": M_DIN_PU, "down": M_DIN_PD}.get(p["pull"], M_DIN)
                out[pin] = (mode, 1000, int(_num(p["debounce"], 30)))
            elif typ == "analog_sensor":
                out[pin] = (M_ADC, int(_num(p["interval"], 1000)), 0)
            elif typ == "pulse_counter":
                out[pin] = (M_CNT, int(_num(p["interval"], 1000)), 0)
            elif typ == "switch_output":
                out[pin] = (M_DOUT, 1000, 0)
            elif typ == "pwm_output":
                out[pin] = (M_PWM, 1000, 0)
            elif typ == "dac_output":
                out[pin] = (M_DAC, 1000, 0)
        return out

    def apply_hw(self, bid, force=False):
        hw = self.hws.get(bid)
        if hw is None or not hw.connected:
            return
        applied = self.applied.setdefault(bid, {})
        if force:
            applied.clear()
            if bid not in self._seen:      # first contact: the board's pin state is unknown
                self._seen.add(bid)
                hw.reset()
        desired = self._desired_pins(bid)
        for pin in list(applied):
            if pin not in desired:
                hw.configure(pin, M_OFF, 1000, 0)
                del applied[pin]
        for pin, spec in desired.items():
            if applied.get(pin) != spec:
                r = hw.configure(pin, *spec)
                if r is None or (isinstance(r, int) and r < 0):
                    self.log("Could not set up pin %s on %s (reply %s)" % (pin, bid, r), "err")
                else:
                    applied[pin] = spec
        mine = [c for c in self.cfg["components"] if c.get("board") == bid]
        for c in mine:
            if c["type"] in OUTPUT_TYPES:
                self._write_output(c)
        m = qh_boards.model(self.boards()[bid].get("model")) or {}
        for led in (3, 4):   # the MCU's own LEDs go dark when nothing is assigned to them
            if "LED%d" % led in m.get("pin_by_id", {}) and not any(c.get("pin") == "LED%d" % led for c in mine):
                hw.rgb(led, 0)
        for c in mine:
            if c["type"] == "led_matrix":
                self.mx[c["id"]]["last"] = None
                self._matrix_refresh(c, force=True)

    def _write_output(self, c):
        hw = self.hws.get(c.get("board"))
        if hw is None or not hw.connected:
            return
        typ, pin, p, v = c["type"], c.get("pin"), props_of(c), self.state.get(c["id"])
        if typ == "switch_output":
            hw.write_digital(pin, bool(v) != bool(p["invert"]))
        elif typ == "pwm_output":
            lo, hi = _num(p["min_pct"]), _num(p["max_pct"], 100)
            pct = lo + (hi - lo) * _num(v.get("level"), 100) / 100.0 if v and v.get("on") else 0.0
            v12 = int(round(max(0.0, min(100.0, pct)) / 100.0 * qh_boards.PWM_MAX))
            hw.write_analog(pin, qh_boards.PWM_MAX - v12 if p["invert"] else v12)
        elif typ == "dac_output":
            vref = (self.model_of(c) or {}).get("vref", 3.3)
            hw.write_analog(pin, int(round(max(0.0, min(vref, _num(v))) / vref * qh_boards.PWM_MAX)))
        elif typ == "rgb_led":
            hw.rgb(int(str(pin)[3:]), RGB_BITS.get(v, 0))

    # ------------------------------------------------------------ state
    def state_of(self, cid):
        return self.state.get(cid)

    def value_of(self, cid):
        v = self.state.get(cid)
        c = self.comps().get(cid)
        if c is None:
            return None
        if c["type"] == "pwm_output":
            return _num(v.get("level")) if v and v.get("on") else 0.0
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        return v

    def fmt_value(self, cid):
        c = self.comps().get(cid)
        v = self.state.get(cid)
        if c is None or v is None:
            return "--"
        typ = c["type"]
        if typ in BOOL_TYPES:
            return "ON" if v else "OFF"
        if typ == "pwm_output":
            return "%d" % round(self.value_of(cid))
        if typ in ("analog_sensor", "pulse_counter"):
            return ("%." + str(int(_num(props_of(c).get("decimals"), 2))) + "f") % v
        if typ == "dac_output":
            return "%.2f" % v
        return str(v)

    def render_template(self, text):
        return re.sub(r"\{([a-z][a-z0-9_]*)\}", lambda m: self.fmt_value(m.group(1)), text or "")

    def set_state(self, cid, value, force=False):
        old = self.state.get(cid)
        if old == value and not force:
            return
        with self.lock:
            self.state[cid] = value
            self.stamp[cid] = time.time()
            self.rev += 1
        c = self.comps().get(cid)
        if c is None:
            return
        if c["type"] in OUTPUT_TYPES:
            self._state_dirty = time.time()
        self._mqtt_state(c)
        if old != value:
            self.scripts.on_state(cid, old, value)

    # ------------------------------------------------------------ events from boards
    def _handle_hw(self, bid, pin, raw):
        c = next((c for c in self.cfg["components"]
                  if c.get("board") == bid and c.get("pin") == pin), None)
        if c is None:
            return
        cid, typ, p = c["id"], c["type"], props_of(c)
        if typ == "binary_input":
            self.set_state(cid, (raw == 1) != bool(p["invert"]))
        elif typ == "analog_sensor":
            m = self.model_of(c) or {}
            full, vref = float(m.get("adc_max", 1023)), m.get("vref", 5.0)
            a = self.aux.setdefault(cid, {"buf": deque(maxlen=50)})
            a["buf"].append(raw)
            samples = list(a["buf"])[-max(1, int(_num(p["avg"], 1))):]
            r_avg = sum(samples) / len(samples)
            v_avg = r_avg / full * vref
            cal = p["calib"]
            if cal == "raw":
                val = r_avg
            elif cal == "percent":
                val = r_avg / full * 100.0
            elif cal == "linear":
                i0, i1 = _num(p["in_min"]), _num(p["in_max"], vref)
                o0, o1 = _num(p["out_min"]), _num(p["out_max"], 100)
                val = o0 + (v_avg - i0) * (o1 - o0) / ((i1 - i0) or 1e-9)
            else:
                val = v_avg
            val = round(val, int(_num(p["decimals"], 2)))
            old, delta = self.state.get(cid), _num(p["delta"])
            if old is not None and delta > 0 and abs(val - old) < delta:
                return
            self.set_state(cid, val)
        elif typ == "pulse_counter":
            a = self.aux.setdefault(cid, {"last": None, "t": None, "base": 0, "prev_raw": 0,
                                          "offset": _num(self.saved_state.get(cid), 0)})
            now, mult, d = time.time(), _num(p["multiplier"], 1), int(_num(p["decimals"], 1))
            if p["mode"] == "total":
                if raw < a["prev_raw"]:      # the board restarted and counts from zero again
                    a["base"] += a["prev_raw"]
                a["prev_raw"] = raw
                self.set_state(cid, round(a["offset"] + (a["base"] + raw) * mult, d))
            else:
                if a["last"] is not None and now > a["t"]:
                    diff = raw - a["last"] if raw >= a["last"] else raw
                    self.set_state(cid, round(diff / (now - a["t"]) * 60.0 * mult, d))
                a["last"], a["t"] = raw, now

    # ------------------------------------------------------------ commands
    def command(self, cid, action, value=None):
        """Change an output. Called by scripts, the web UI and MQTT (engine thread only)."""
        c = self.comps().get(cid)
        if c is None:
            self.log("Command for unknown device '%s'" % cid, "warn")
            return
        typ, cur = c["type"], self.state.get(cid)
        action = (action or "").lower()
        if typ in ("switch_output", "virtual_switch"):
            new = {"on": True, "off": False, "toggle": not cur}.get(action, cur)
            if action == "set":
                new = str(value).upper() in ("1", "1.0", "ON", "TRUE")
            self.set_state(cid, bool(new))
        elif typ == "pwm_output":
            v = dict(cur or {"on": False, "level": 100})
            if action == "on":
                v["on"] = True
            elif action == "off":
                v["on"] = False
            elif action == "toggle":
                v["on"] = not v["on"]
            elif action == "set":
                lvl = max(0.0, min(100.0, _num(value)))
                v["on"] = lvl > 0
                if lvl > 0:
                    v["level"] = lvl
            if v["on"] and _num(v.get("level")) <= 0:
                v["level"] = 100
            self.set_state(cid, v)
        elif typ == "dac_output":
            vref = (self.model_of(c) or {}).get("vref", 3.3)
            if action == "off":
                self.set_state(cid, 0.0)
            elif action == "set":
                self.set_state(cid, round(max(0.0, min(vref, _num(value))), 3))
        elif typ == "rgb_led":
            last = self.aux.get(cid, {}).get("last_color", "white")
            if action == "off":
                new = "off"
            elif action == "on":
                new = last
            elif action == "toggle":
                new = "off" if cur != "off" else last
            else:
                value = str(value).lower()
                new = value if value in RGB_BITS else cur
            if new != "off":
                self.aux.setdefault(cid, {})["last_color"] = new
            self.set_state(cid, new)
        elif typ == "led_matrix":
            if action in ("off", "clear"):
                self.matrix_show(c, "clear")
            elif action == "icon":
                self.matrix_show(c, "icon", pixels=value)
            else:
                self.matrix_show(c, "text", text=str(value if value is not None else ""))
            return
        else:
            self.log("'%s' is an input and cannot be switched" % cid, "warn")
            return
        self._write_output(c)

    def publish(self, topic, payload, retain=False):
        if self.mqtt and topic:
            self.mqtt.publish(topic, payload, retain)

    # ------------------------------------------------------------ LED matrix
    def matrix_show(self, c, mode, text="", pixels=None):
        mx = self.mx[c["id"]]
        mx["startup"] = False
        if mode == "clear":
            mx.update(template="", icon=None, cols=[], scroll=False, text=None)
            self.set_state(c["id"], "")
        elif mode == "icon":
            px = re.sub(r"[^0-7]", "", str(pixels or ""))[:104].ljust(104, "0")
            mx.update(template="", icon=px, cols=[], scroll=False, text=None)
            self.set_state(c["id"], "[icon]")
        else:
            mx.update(template=text, icon=None, text=None)
        self._matrix_refresh(c, force=True)

    def _matrix_refresh(self, c, force=False):
        mx, p, now = self.mx.get(c["id"]), props_of(c), time.time()
        hw = self.hws.get(c.get("board"))
        if mx is None:
            return
        level = int(_num(p["brightness"], 4))
        if mx["icon"]:
            frame = "".join("0" if ch == "0" else str(max(1, int(ch) * level // 7)) for ch in mx["icon"])
        elif mx["template"]:
            if force or now >= mx["rerender"]:
                mx["rerender"] = now + 1.0
                txt = self.render_template(mx["template"])
                if txt != mx["text"]:
                    mx["text"] = txt
                    mx["cols"] = font.text_columns(txt)
                    mx["scroll"] = len(mx["cols"]) > font.W
                    mx["offset"] = -font.W if mx["scroll"] else 0
                    self.set_state(c["id"], txt)
            if mx["scroll"]:
                if now >= mx["next"]:
                    mx["next"] = now + _num(p["scroll_ms"], 120) / 1000.0
                    mx["offset"] += 1
                    if mx["offset"] > len(mx["cols"]):
                        mx["offset"] = -font.W
                frame = font.render(mx["cols"], mx["offset"], level)
            else:
                frame = font.centered(mx["cols"], level)
        else:
            frame = font.blank()
        if frame != mx["last"] and hw is not None and hw.connected:
            mx["last"] = frame
            hw.matrix(frame)
            with self.lock:
                self.rev += 1

    # ------------------------------------------------------------ MQTT / Home Assistant
    def _avail_topic(self):
        return "%s/status" % self.node()

    def _setup_mqtt(self):
        if not self.mqtt:
            return
        self.mqtt.configure(self.cfg["mqtt"], self.node(), self._avail_topic())
        self.mqtt.subscribe("%s/+/set" % self.node())
        self.mqtt.subscribe("%s/+/level/set" % self.node())
        self.mqtt.subscribe("%s/status" % self.cfg["mqtt"].get("prefix", "homeassistant"))

    def _mqtt_payload(self, c):
        typ, v = c["type"], self.state.get(c["id"])
        if v is None:
            return None
        if typ in BOOL_TYPES:
            return "ON" if v else "OFF"
        if typ == "pwm_output":
            return "ON" if v.get("on") else "OFF"
        return self.fmt_value(c["id"]) if typ != "led_matrix" else str(v)

    def _mqtt_state(self, c):
        if not self.mqtt or self.mqtt.mode == "off":
            return
        pl = self._mqtt_payload(c)
        if pl is None:
            return
        base = "%s/%s" % (self.node(), c["id"])
        self.mqtt.publish(base + "/state", pl, retain=True)
        if c["type"] == "pwm_output":
            self.mqtt.publish(base + "/level", "%d" % round(_num(self.state[c["id"]].get("level"))), retain=True)

    def _mqtt_board(self, bid):
        if self.mqtt and self.mqtt.mode != "off":
            self.mqtt.publish("%s/board/%s/state" % (self.node(), bid),
                              "ON" if self.online.get(bid) else "OFF", retain=True)

    def _discovery(self):
        m = self.cfg["mqtt"]
        if not self.mqtt or self.mqtt.mode == "off":
            return
        node, prefix = self.node(), m.get("prefix", "homeassistant")
        avail = self._avail_topic()
        self.mqtt.publish(avail, "online", retain=True)
        new_topics = set()
        if m.get("discovery", True):
            dev = {"identifiers": [node], "name": self.cfg["device"]["name"], "manufacturer": "Arduino",
                   "model": "UNO Q", "sw_version": "QHome " + VERSION}
            for c in self.cfg["components"]:
                typ, p, cid = c["type"], props_of(c), c["id"]
                base = "%s/%s" % (node, cid)
                comp = TYPES[typ]["ha"]
                d = {"name": c.get("name") or cid, "unique_id": "%s_%s" % (node, cid),
                     "availability_topic": avail, "device": dev, "state_topic": base + "/state"}
                if typ == "binary_input":
                    d.update(payload_on="ON", payload_off="OFF")
                    if p.get("device_class"):
                        d["device_class"] = p["device_class"]
                elif typ in ("switch_output", "virtual_switch"):
                    comp = p.get("ha_as", "switch") if typ == "switch_output" else "switch"
                    d.update(command_topic=base + "/set", payload_on="ON", payload_off="OFF")
                elif typ == "pwm_output":
                    d.update(command_topic=base + "/set", payload_on="ON", payload_off="OFF",
                             brightness_state_topic=base + "/level",
                             brightness_command_topic=base + "/level/set", brightness_scale=100)
                elif typ == "dac_output":
                    d.update(command_topic=base + "/set", min=0, step=0.01, mode="slider",
                             max=(self.model_of(c) or {}).get("vref", 3.3), unit_of_measurement="V")
                elif typ in ("analog_sensor", "pulse_counter"):
                    if p.get("unit"):
                        d["unit_of_measurement"] = p["unit"]
                    if p.get("device_class"):
                        d["device_class"] = p["device_class"]
                    total = typ == "pulse_counter" and p.get("mode") == "total"
                    d["state_class"] = "total_increasing" if total else "measurement"
                    d["suggested_display_precision"] = int(_num(p.get("decimals"), 2))
                elif typ == "rgb_led":
                    d.update(command_topic=base + "/set", options=list(RGB_BITS))
                elif typ == "led_matrix":
                    d.update(command_topic=base + "/set", max=100, mode="text")
                topic = "%s/%s/%s/%s/config" % (prefix, comp, node, cid)
                new_topics.add(topic)
                self.mqtt.publish(topic, json.dumps(d, ensure_ascii=False), retain=True)
            for b in self.cfg["boards"]:
                topic = "%s/binary_sensor/%s/board_%s/config" % (prefix, node, b["id"])
                new_topics.add(topic)
                self.mqtt.publish(topic, json.dumps({
                    "name": "%s online" % (b.get("name") or b["id"]), "device": dev,
                    "unique_id": "%s_board_%s" % (node, b["id"]), "device_class": "connectivity",
                    "entity_category": "diagnostic", "availability_topic": avail,
                    "state_topic": "%s/board/%s/state" % (node, b["id"])}), retain=True)
        for old in self.disc_topics - new_topics:
            self.mqtt.publish(old, "", retain=True)   # removes deleted devices from Home Assistant
        self.disc_topics = new_topics
        for c in self.cfg["components"]:
            self._mqtt_state(c)
        for b in self.cfg["boards"]:
            self._mqtt_board(b["id"])

    def _handle_mqtt(self, topic, payload):
        if topic.startswith("qlink/"):
            parts = topic.split("/")
            tr = self.transports.get(parts[1]) if len(parts) == 3 else None
            if isinstance(tr, qh_link.MqttTransport):
                tr.on_mqtt(topic, payload)
            return
        node = self.node()
        prefix = self.cfg["mqtt"].get("prefix", "homeassistant")
        if topic == "%s/status" % prefix:
            if payload.strip() == "online":
                self.log("Home Assistant restarted; sending discovery again")
                self._discovery()
            return
        parts = topic.split("/")
        if len(parts) >= 3 and parts[0] == node and parts[-1] == "set":
            cid = parts[1]
            c = self.comps().get(cid)
            if c:
                pl = payload.strip()
                if len(parts) == 4 and parts[2] == "level":
                    self.command(cid, "set", pl)
                elif c["type"] in ("switch_output", "virtual_switch", "pwm_output"):
                    self.command(cid, {"ON": "on", "OFF": "off", "TOGGLE": "toggle"}.get(pl.upper(), "set"), pl)
                elif c["type"] == "led_matrix":
                    self.command(cid, "text", pl)
                else:
                    self.command(cid, "set", pl)
        self.scripts.on_mqtt(topic, payload.strip(), topic_match)

    # ------------------------------------------------------------ main loop
    def step(self, budget=0.1):
        end = time.time() + budget
        while True:
            try:
                item = self.q.get(timeout=min(max(0.0, end - time.time()), 0.02))
            except queue.Empty:
                item = None
            if item:
                try:
                    self._dispatch(item)
                except Exception as e:
                    self.log("Problem while handling '%s': %r" % (item[0], e), "err")
            try:
                self.scripts.tick()
            except Exception as e:
                self.log("Script scheduler problem: %r" % e, "err")
            if time.time() >= end:
                break
        try:
            self._periodic()
        except Exception as e:
            self.log("Housekeeping problem: %r" % e, "err")

    def _board_up(self, bid):
        if bid == "main_local":
            bid = self._local_board_id()
            if bid is None:
                return
        was = self.online.get(bid)
        self.online[bid] = True
        self.apply_hw(bid, force=True)
        with self.lock:
            self.rev += 1
        if not was:
            self._mqtt_board(bid)
            self.scripts.on_board(bid, True)
        if self.hws.get(bid) is self.local:
            for tr in self.transports.values():      # wired links live on the microcontroller
                if isinstance(tr, qh_link.McuTransport):
                    tr.reopen()

    def _board_down(self, bid):
        if self.online.get(bid):
            self.online[bid] = False
            self.applied.get(bid, {}).clear()
            with self.lock:
                self.rev += 1
            self.log("Board '%s' is offline" % bid, "warn")
            self._mqtt_board(bid)
            self.scripts.on_board(bid, False)

    def _dispatch(self, item):
        kind = item[0]
        if kind == "hw":
            self._handle_hw(item[1], item[2], item[3])
        elif kind == "cmd":
            self.command(item[1], item[2], item[3])
        elif kind == "mqtt":
            self._handle_mqtt(item[1], item[2])
        elif kind == "mqtt_up":
            self._discovery()
            for t in self.scripts.topics:
                self.mqtt.subscribe(t)
        elif kind == "link":
            hw = self.hws.get(item[1])
            if isinstance(hw, QLinkHW):
                hw.feed(item[2])
        elif kind == "link_state":
            hw = self.hws.get(item[1])
            if isinstance(hw, QLinkHW):
                hw.set_link(item[2])
        elif kind == "mcu_link":
            bid = self.lid_board.get(item[1])
            hw = self.hws.get(bid)
            if isinstance(hw, QLinkHW):
                hw.feed(item[2])
        elif kind == "boot":
            bid, boot = item[1], item[2]
            if boot is None:
                self._board_down(bid)
            else:
                hw = self.hws.get(bid)
                if bid == "main_local":
                    self.log("The microcontroller restarted (v%s); sending its setup again" % boot, "warn")
                elif not self.online.get(bid):
                    self.log("Board '%s' is online (%s)" % (bid, getattr(hw, "remote_model", "")))
                    want = self.boards().get(bid, {}).get("model")
                    got = getattr(hw, "remote_model", None)
                    if got and want and got != want:
                        self.log("Board '%s' says it is a '%s' but is set up as '%s'. Check the "
                                 "board model." % (bid, got, want), "warn")
                self._board_up(bid)
        elif kind == "config":
            self._apply_config(item[1])

    def _apply_config(self, cfg):
        old = self.cfg
        old_mqtt = json.dumps(old.get("mqtt"), sort_keys=True) + self.node()
        old_boards = json.dumps(old.get("boards"), sort_keys=True)
        old_types = {c["id"]: (c["type"], c.get("board")) for c in old["components"]}
        old_ws = json.dumps((old.get("scripts") or {}).get("workspace"), sort_keys=True)
        self.cfg = cfg
        comps = self.comps()
        for cid in list(self.state):
            if cid not in comps or (comps[cid]["type"], comps[cid].get("board")) != old_types.get(cid):
                for d in (self.state, self.aux, self.mx, self.stamp):
                    d.pop(cid, None)
        for cid, mx in self.mx.items():
            if mx.get("startup") and cid in comps:
                mx.update(template=props_of(comps[cid]).get("startup_text", ""), text=None)
        if json.dumps(cfg.get("mqtt"), sort_keys=True) + self.node() != old_mqtt:
            self._setup_mqtt()
        if json.dumps(cfg.get("boards"), sort_keys=True) != old_boards:
            self._build_boards()
        self._init_states()
        for bid in self.boards():
            self.apply_hw(bid)
        new_ws = (cfg.get("scripts") or {}).get("workspace")
        if json.dumps(new_ws, sort_keys=True) != old_ws:
            self.scripts.load(new_ws)
            self.scripts.start_all()
        for t in self.scripts.topics:
            if self.mqtt:
                self.mqtt.subscribe(t)
        self._discovery()
        with self.lock:
            self.rev += 1
        self.log("Setup applied: %d boards, %d devices, %d scripts"
                 % (len(cfg["boards"]), len(cfg["components"]), len(self.scripts.hats)))

    def _periodic(self):
        now = time.time()
        local = self.local
        if local is not None:
            local.poll(now)
            if not local.connected:
                self._board_down(self._local_board_id())
                if now - self._last_probe > 5:
                    self._last_probe = now
                    if local.probe():
                        self.log("Microcontroller connected again")
                        self._board_up("main_local")
        for bid, tr in self.transports.items():
            tr.poll(now)
            self.hws[bid].poll(now)
        if not self._scripts_started and now - self.started > 2:
            self._scripts_started = True
            self.scripts.start_all()
        for c in self.cfg["components"]:
            if c["type"] == "led_matrix":
                self._matrix_refresh(c)
        if self._state_dirty and now - self._state_dirty > 2:
            self._state_dirty = 0
            snap = {}
            for c in self.cfg["components"]:
                if c["type"] in OUTPUT_TYPES or (c["type"] == "pulse_counter"
                                                 and props_of(c).get("mode") == "total"):
                    snap[c["id"]] = self.state.get(c["id"])
            try:
                self._atomic_write(self.state_path, snap)
            except OSError as e:
                self.log("Could not save output states: %s" % e, "err")

    # ------------------------------------------------------------ for the web UI
    def snapshot(self, since_log=0):
        with self.lock:
            st = {c["id"]: {"v": self.state.get(c["id"]), "txt": self.fmt_value(c["id"]),
                            "t": self.stamp.get(c["id"])} for c in self.cfg["components"]}
            boards = {}
            for b in self.cfg["boards"]:
                bid, hw, tr = b["id"], self.hws.get(b["id"]), self.transports.get(b["id"])
                local = (b.get("link") or {}).get("type") == "local"
                boards[bid] = {
                    "online": bool(self.online.get(bid)),
                    "text": ("simulated" if getattr(hw, "simulated", False) else
                             "built-in" if hw is not None else "not available on this computer")
                    if local else (tr.text if tr else ""),
                    "model": getattr(hw, "remote_model", None), "fw": getattr(hw, "version", None)}
            return {
                "rev": self.rev, "states": st, "boards": boards,
                "logs": [x for x in self.logs if x["n"] > since_log],
                "mqtt": self.mqtt.status() if self.mqtt else {"mode": "off", "ok": False, "text": "-"},
                "matrix": {cid: mx["last"] or font.blank() for cid, mx in self.mx.items()},
                "vars": self.scripts.variables(), "running": self.scripts.running(),
                "uptime": int(time.time() - self.started),
            }
