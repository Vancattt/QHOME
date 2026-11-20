"""Runs the Scratch-style block programs.

The web editor saves a Blockly workspace (JSON). This module reads that JSON directly:
every block that starts with "when ..." (a hat block) becomes a script, and each running
script is a Python generator that the engine advances a little on every tick. Nothing is
compiled and nothing is uploaded to a board; a saved change is live immediately.

Scratch rules apply: a script that is triggered again while it is still running starts
over, loops give other scripts a turn on every pass, and "wait" never blocks anything else.
"""
import random
import re
import time

HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
STEP_BUDGET = 3000       # blocks one script may run before it must give others a turn
MAX_THREADS = 200

# which component types each entity dropdown accepts (the editor uses the same table)
BOOLISH = ("binary_input", "switch_output", "virtual_switch", "pwm_output", "rgb_led")
NUMERIC = ("analog_sensor", "pulse_counter", "pwm_output", "dac_output")
SWITCHABLE = ("switch_output", "virtual_switch", "pwm_output", "rgb_led")
ENTITY_FIELDS = {
    "ev_state": BOOLISH, "ev_above": NUMERIC, "do_turn": SWITCHABLE, "do_toggle": SWITCHABLE,
    "do_set": ("pwm_output", "dac_output"), "do_rgb": ("rgb_led",), "do_show": ("led_matrix",),
    "do_clear": ("led_matrix",), "get_on": BOOLISH, "get_value": NUMERIC,
}
BOARD_BLOCKS = ("ev_board", "get_board")
HATS = ("ev_start", "ev_state", "ev_above", "ev_every", "ev_time", "ev_mqtt", "ev_board")
STATEMENTS = ("do_turn", "do_toggle", "do_set", "do_rgb", "do_show", "do_clear", "do_publish",
              "do_log", "ctl_wait", "ctl_repeat", "ctl_forever", "ctl_if", "ctl_ifelse",
              "ctl_wait_until", "ctl_repeat_until", "ctl_stop", "variables_set", "math_change")
VALUES = ("get_on", "get_value", "get_board", "get_time", "get_between", "get_mqtt",
          "math_number", "math_arithmetic", "math_round", "math_modulo", "math_constrain",
          "math_random_int", "logic_compare", "logic_operation", "logic_negate",
          "logic_boolean", "text", "text_join", "variables_get")
KNOWN = set(HATS) | set(STATEMENTS) | set(VALUES)
DAYSETS = {"all": range(7), "weekdays": range(5), "weekend": (5, 6), "mon": (0,), "tue": (1,),
           "wed": (2,), "thu": (3,), "fri": (4,), "sat": (5,), "sun": (6,)}


class StopScript(Exception):
    pass


def _child(block, name):
    slot = (block.get("inputs") or {}).get(name) or {}
    return slot.get("block") or slot.get("shadow")


def _field(block, name, default=None):
    return (block.get("fields") or {}).get(name, default)


def _walk(block):
    """Yield a block and everything attached to it."""
    while block:
        yield block
        for slot in (block.get("inputs") or {}).values():
            for key in ("block", "shadow"):
                if slot.get(key) and not (key == "shadow" and slot.get("block")):
                    yield from _walk(slot[key])
        block = (block.get("next") or {}).get("block")


def top_blocks(workspace):
    return ((workspace or {}).get("blocks") or {}).get("blocks") or []


def hats(workspace):
    return [b for b in top_blocks(workspace) if b.get("type") in HATS and b.get("enabled", True)]


def validate(workspace, comps, boards):
    """Return a list of problems that would stop the scripts from running correctly."""
    errs = []
    for n, hat in enumerate(hats(workspace), 1):
        where = "Script %d" % n
        for b in _walk(hat):
            typ = b.get("type")
            if typ not in KNOWN:
                errs.append("%s uses a block this version does not know: %s" % (where, typ))
                continue
            if typ in ENTITY_FIELDS:
                cid = _field(b, "ENT", "")
                c = comps.get(cid)
                if c is None:
                    errs.append("%s: pick a device in every block (\"%s\" does not exist)."
                                % (where, cid or "none"))
                elif c["type"] not in ENTITY_FIELDS[typ]:
                    errs.append("%s: \"%s\" cannot be used in that block." % (where, cid))
            if typ in BOARD_BLOCKS and _field(b, "BOARD", "") not in boards:
                errs.append("%s: pick a board that exists." % where)
            if typ == "ev_time" and not HHMM_RE.match(str(_field(b, "AT", ""))):
                errs.append("%s: write the time as HH:MM, for example 07:30." % where)
            if typ == "get_between" and not (HHMM_RE.match(str(_field(b, "FROM", "")))
                                             and HHMM_RE.match(str(_field(b, "TO", "")))):
                errs.append("%s: write both times as HH:MM." % where)
    return errs


def num(x, default=0.0):
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def text(x):
    if isinstance(x, bool):
        return "on" if x else "off"
    if isinstance(x, float):
        return "%d" % x if x == int(x) and abs(x) < 1e15 else ("%.3f" % x).rstrip("0").rstrip(".")
    return "" if x is None else str(x)


def truthy(x):
    if isinstance(x, dict):
        return bool(x.get("on"))
    if isinstance(x, str):
        return x not in ("", "off", "0", "false")
    return bool(x)


class _Thread:
    def __init__(self, hat, gen, ctx):
        self.hat, self.gen, self.ctx = hat, gen, ctx
        self.wake = 0.0


class Runtime:
    def __init__(self, engine):
        self.e = engine
        self.hats = []
        self.vars = {}        # variable id -> value
        self.var_names = {}   # variable id -> name
        self.threads = []
        self.rt = {}          # hat id -> trigger memory (armed flags, next due time)
        self.topics = []
        self._minute = None
        self._budget = 0

    # ------------------------------------------------------------ loading
    def load(self, workspace, keep_vars=True):
        old = self.vars if keep_vars else {}
        self.hats = hats(workspace)
        self.var_names = {v["id"]: v.get("name", "") for v in (workspace or {}).get("variables", [])}
        self.vars = {vid: old.get(vid, 0.0) for vid in self.var_names}
        self.threads = []
        self.rt = {}
        self.topics = [_field(h, "TOPIC", "") for h in self.hats if h["type"] == "ev_mqtt"]
        self.topics = [t for t in self.topics if t]

    def start_all(self):
        for h in self.hats:
            if h["type"] == "ev_start":
                self._launch(h)

    def variables(self):
        return {self.var_names[k]: text(v) for k, v in self.vars.items()}

    # ------------------------------------------------------------ triggers
    def _launch(self, hat, ctx=None):
        body = (hat.get("next") or {}).get("block")
        self.threads = [t for t in self.threads if t.hat is not hat]   # restart, like Scratch
        if body is None:
            return
        if len(self.threads) >= MAX_THREADS:
            self.e.log("Too many scripts are running at once; one start was skipped", "warn")
            return
        ctx = dict(ctx or {})
        self.threads.append(_Thread(hat, self._run_stack(body, ctx), ctx))

    def on_state(self, cid, old, new):
        for h in self.hats:
            if _field(h, "ENT") != cid:
                continue
            if h["type"] == "ev_state":
                want = _field(h, "TO", "on")
                if want == "change":
                    if old is not None:
                        self._launch(h)
                elif old is None:
                    continue                      # first reading after start-up is not an edge
                elif want == "on" and truthy(new) and not truthy(old):
                    self._launch(h)
                elif want == "off" and not truthy(new) and truthy(old):
                    self._launch(h)
            elif h["type"] == "ev_above":
                v = self.e.value_of(cid)
                if not isinstance(v, (int, float)):
                    continue
                try:
                    lim = num(self._eval(_child(h, "VALUE"), {}))
                except Exception:
                    continue
                cond = v > lim if _field(h, "OP", "above") == "above" else v < lim
                mem = self.rt.setdefault(h.get("id"), {})
                if cond and mem.get("armed", False):
                    self._launch(h)
                mem["armed"] = not cond

    def on_mqtt(self, topic, payload, match):
        for h in self.hats:
            if h["type"] == "ev_mqtt" and _field(h, "TOPIC") and match(_field(h, "TOPIC"), topic):
                self._launch(h, {"mqtt": payload, "topic": topic})

    def on_board(self, board_id, online):
        for h in self.hats:
            if h["type"] == "ev_board" and _field(h, "BOARD") == board_id \
                    and (_field(h, "TO", "online") == "online") == online:
                self._launch(h)

    def _timers(self, now):
        lt = self.e.local_time()
        minute = (lt.tm_yday, lt.tm_hour, lt.tm_min)
        new_minute = minute != self._minute
        self._minute = minute
        for h in self.hats:
            if h["type"] == "ev_every":
                mem = self.rt.setdefault(h.get("id"), {})
                if now >= mem.get("next", 0):
                    try:
                        n = num(self._eval(_child(h, "N"), {}), 1)
                    except Exception:
                        n = 1
                    period = max(0.1, n * {"s": 1, "m": 60, "h": 3600}.get(_field(h, "UNIT", "s"), 1))
                    if "next" in mem:
                        self._launch(h)
                    mem["next"] = now + period
            elif h["type"] == "ev_time" and new_minute:
                if _norm_hhmm(_field(h, "AT", "")) == "%02d:%02d" % (lt.tm_hour, lt.tm_min) \
                        and lt.tm_wday in DAYSETS.get(_field(h, "DAYS", "all"), range(7)):
                    self._launch(h)

    # ------------------------------------------------------------ scheduler
    def tick(self, now=None):
        now = now or time.time()
        self._timers(now)
        for _ in range(4):                 # let freshly triggered scripts start in the same tick
            ran = False
            for th in list(self.threads):
                if th.wake > now or th not in self.threads:
                    continue
                ran = True
                self._budget = STEP_BUDGET
                try:
                    delay = next(th.gen)
                    th.wake = now + max(0.0, delay) if delay else now + 0.01
                except (StopIteration, StopScript):
                    self._drop(th)
                except Exception as e:
                    self._drop(th)
                    self.e.log("A script stopped with an error: %s" % e, "err")
            if not ran:
                break

    def _drop(self, th):
        if th in self.threads:
            self.threads.remove(th)

    def running(self):
        return len(self.threads)

    # ------------------------------------------------------------ statements
    def _run_stack(self, block, ctx):
        while block:
            self._budget -= 1
            if self._budget <= 0:
                yield 0
            yield from self._exec(block, ctx)
            block = (block.get("next") or {}).get("block")

    def _body(self, block, name, ctx):
        inner = _child(block, name)
        if inner:
            yield from self._run_stack(inner, ctx)

    def _exec(self, b, ctx):
        t, e = b.get("type"), self.e
        if b.get("enabled", True) is False:
            return
        if t == "do_turn":
            e.command(_field(b, "ENT"), _field(b, "TO", "on"))
        elif t == "do_toggle":
            e.command(_field(b, "ENT"), "toggle")
        elif t == "do_set":
            e.command(_field(b, "ENT"), "set", num(self._eval(_child(b, "VALUE"), ctx)))
        elif t == "do_rgb":
            e.command(_field(b, "ENT"), "set", _field(b, "COLOR", "off"))
        elif t == "do_show":
            e.command(_field(b, "ENT"), "text", text(self._eval(_child(b, "TEXT"), ctx)))
        elif t == "do_clear":
            e.command(_field(b, "ENT"), "clear")
        elif t == "do_publish":
            e.publish(text(self._eval(_child(b, "TOPIC"), ctx)),
                      text(self._eval(_child(b, "PAYLOAD"), ctx)))
        elif t == "do_log":
            e.log("Script: " + text(self._eval(_child(b, "TEXT"), ctx)))
        elif t == "variables_set":
            self.vars[self._var(b)] = self._eval(_child(b, "VALUE"), ctx)
        elif t == "math_change":
            vid = self._var(b)
            self.vars[vid] = num(self.vars.get(vid)) + num(self._eval(_child(b, "DELTA"), ctx))
        elif t == "ctl_wait":
            end = time.time() + max(0.0, num(self._eval(_child(b, "SECS"), ctx)))
            while True:
                left = end - time.time()
                if left <= 0:
                    break
                yield left
        elif t == "ctl_repeat":
            for _ in range(int(max(0, min(1e7, num(self._eval(_child(b, "TIMES"), ctx)))))):
                yield from self._body(b, "DO", ctx)
                yield 0
        elif t == "ctl_forever":
            while True:
                yield from self._body(b, "DO", ctx)
                yield 0
        elif t == "ctl_if":
            if truthy(self._eval(_child(b, "COND"), ctx)):
                yield from self._body(b, "DO", ctx)
        elif t == "ctl_ifelse":
            branch = "DO" if truthy(self._eval(_child(b, "COND"), ctx)) else "ELSE"
            yield from self._body(b, branch, ctx)
        elif t == "ctl_wait_until":
            while not truthy(self._eval(_child(b, "COND"), ctx)):
                yield 0.05
        elif t == "ctl_repeat_until":
            while not truthy(self._eval(_child(b, "COND"), ctx)):
                yield from self._body(b, "DO", ctx)
                yield 0
        elif t == "ctl_stop":
            raise StopScript()
        else:
            raise ValueError("unknown block %s" % t)

    def _var(self, b):
        v = _field(b, "VAR") or {}
        return v.get("id") if isinstance(v, dict) else v

    # ------------------------------------------------------------ values
    def _eval(self, b, ctx):
        if b is None:
            return 0.0
        t, e = b.get("type"), self.e
        ev = lambda name: self._eval(_child(b, name), ctx)  # noqa: E731
        if t == "math_number":
            return num(_field(b, "NUM", 0))
        if t == "text":
            return str(_field(b, "TEXT", ""))
        if t == "logic_boolean":
            return _field(b, "BOOL") == "TRUE"
        if t == "variables_get":
            return self.vars.get(self._var(b), 0.0)
        if t == "get_on":
            return truthy(e.state_of(_field(b, "ENT")))
        if t == "get_value":
            return num(e.value_of(_field(b, "ENT")))
        if t == "get_board":
            return e.board_online(_field(b, "BOARD"))
        if t == "get_mqtt":
            return ctx.get("mqtt", "")
        if t == "get_time":
            lt = e.local_time()
            what = _field(b, "WHAT", "hour")
            return float({"hour": lt.tm_hour, "minute": lt.tm_min, "second": lt.tm_sec,
                          "weekday": lt.tm_wday + 1, "uptime": time.time() - e.started}.get(what, 0))
        if t == "get_between":
            lt = e.local_time()
            now = lt.tm_hour * 60 + lt.tm_min
            a, c = _minutes(_field(b, "FROM", "00:00")), _minutes(_field(b, "TO", "23:59"))
            return a <= now <= c if a <= c else (now >= a or now <= c)
        if t == "math_arithmetic":
            a, c, op = num(ev("A")), num(ev("B")), _field(b, "OP", "ADD")
            if op == "ADD":
                return a + c
            if op == "MINUS":
                return a - c
            if op == "MULTIPLY":
                return a * c
            if op == "DIVIDE":
                return a / c if c else 0.0
            if op == "POWER":
                try:
                    return float(a ** c)
                except (OverflowError, ZeroDivisionError, TypeError):
                    return 0.0
        if t == "math_round":
            v, op = num(ev("NUM")), _field(b, "OP", "ROUND")
            import math
            return float(math.ceil(v) if op == "ROUNDUP" else math.floor(v) if op == "ROUNDDOWN"
                         else math.floor(v + 0.5))
        if t == "math_modulo":
            d = num(ev("DIVISOR"))
            return num(ev("DIVIDEND")) % d if d else 0.0
        if t == "math_constrain":
            return max(num(ev("LOW")), min(num(ev("HIGH")), num(ev("VALUE"))))
        if t == "math_random_int":
            a, c = int(num(ev("FROM"))), int(num(ev("TO")))
            return float(random.randint(min(a, c), max(a, c)))
        if t == "logic_compare":
            a, c, op = ev("A"), ev("B"), _field(b, "OP", "EQ")
            if not (isinstance(a, str) and isinstance(c, str)):
                try:
                    a, c = float(a), float(c)
                except (TypeError, ValueError):
                    a, c = text(a), text(c)
            return {"EQ": a == c, "NEQ": a != c, "LT": a < c, "LTE": a <= c,
                    "GT": a > c, "GTE": a >= c}.get(op, False)
        if t == "logic_operation":
            if _field(b, "OP", "AND") == "AND":
                return truthy(ev("A")) and truthy(ev("B"))
            return truthy(ev("A")) or truthy(ev("B"))
        if t == "logic_negate":
            return not truthy(ev("BOOL"))
        if t == "text_join":
            n = int((b.get("extraState") or {}).get("itemCount", 2))
            return "".join(text(ev("ADD%d" % i)) for i in range(n) if _child(b, "ADD%d" % i))
        raise ValueError("unknown block %s" % t)


def _norm_hhmm(s):
    m = HHMM_RE.match(str(s or ""))
    if not m:
        return ""
    h, mm = str(s).split(":")
    return "%02d:%s" % (int(h), mm)


def _minutes(s):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return 0
