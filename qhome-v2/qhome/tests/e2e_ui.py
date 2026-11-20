"""End-to-end check through the real web UI (optional; needs Playwright and Chrome).

Starts nothing by itself: run QHome in simulation with the demo setup first, e.g.
    QHOME_MODE=sim QHOME_DATA=tests/fixtures/demo python python/main.py
then:  python tests/e2e_ui.py http://localhost:7000

It builds three scripts with the block editor's own API, saves them through the page,
presses a simulated button and checks that the engine did what the blocks say. The saved
workspace is written to tests/fixtures/demo_workspace.json for the offline tests.
"""
import asyncio
import json
import os
import sys
import urllib.request

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:7000"
HERE = os.path.dirname(os.path.abspath(__file__))
N = lambda v: {"shadow": {"type": "math_number", "fields": {"NUM": v}}}  # noqa: E731
T = lambda s: {"shadow": {"type": "text", "fields": {"TEXT": s}}}  # noqa: E731


def chain(*blocks):
    for a, b in zip(blocks, blocks[1:]):
        a["next"] = {"block": b}
    return blocks[0]


WORKSPACE = {"variables": [{"name": "count", "id": "v_count"}, {"name": "blink", "id": "v_blink"}],
             "blocks": {"languageVersion": 0, "blocks": [
    chain({"type": "ev_state", "x": 30, "y": 30, "fields": {"ENT": "button", "TO": "on"}},
          {"type": "do_toggle", "fields": {"ENT": "relay"}},
          {"type": "do_set", "fields": {"ENT": "fan"}, "inputs": {"VALUE": N(70)}},
          {"type": "do_show", "fields": {"ENT": "matrix"}, "inputs": {"TEXT": {"block": {
              "type": "text_join", "extraState": {"itemCount": 2}, "inputs": {
                  "ADD0": {"block": {"type": "text", "fields": {"TEXT": "T "}}},
                  "ADD1": {"block": {"type": "get_value", "fields": {"ENT": "temp"}}}}}}}},
          {"type": "math_change", "fields": {"VAR": {"id": "v_count"}}, "inputs": {"DELTA": N(1)}},
          {"type": "ctl_wait", "inputs": {"SECS": N(1)}},
          {"type": "do_rgb", "fields": {"ENT": "status_led", "COLOR": "green"}}),
    chain({"type": "ev_every", "x": 30, "y": 420, "fields": {"UNIT": "s"}, "inputs": {"N": N(1)}},
          {"type": "ctl_ifelse", "inputs": {
              "COND": {"block": {"type": "logic_compare", "fields": {"OP": "GT"}, "inputs": {
                  "A": {"block": {"type": "get_value", "fields": {"ENT": "ldr"}}}, "B": N(10)}}},
              "DO": {"block": {"type": "do_turn", "fields": {"ENT": "away", "TO": "on"}}},
              "ELSE": {"block": {"type": "do_turn", "fields": {"ENT": "away", "TO": "off"}}}}}),
    chain({"type": "ev_start", "x": 520, "y": 30},
          {"type": "ctl_repeat", "inputs": {"TIMES": N(3), "DO": {"block": chain(
              {"type": "math_change", "fields": {"VAR": {"id": "v_blink"}}, "inputs": {"DELTA": N(1)}},
              {"type": "ctl_wait", "inputs": {"SECS": N(0.2)}})}}}),
]}}


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


async def main():
    problems = []
    async with async_playwright() as p:
        exe = os.environ.get("CHROME", "/usr/bin/google-chrome")
        b = await p.chromium.launch(executable_path=exe if os.path.exists(exe) else None, args=["--no-sandbox"])
        pg = await (await b.new_context(viewport={"width": 1360, "height": 860})).new_page()
        pg.on("pageerror", lambda e: problems.append("page error: %s" % e))
        pg.on("console", lambda m: problems.append("console: " + m.text) if m.type == "error" else None)
        await pg.goto(BASE)
        await pg.wait_for_timeout(1200)
        await pg.locator("#nav button").nth(2).click()
        await pg.wait_for_timeout(1500)
        saved = await pg.evaluate("""ws => {
            const w = Blockly.getMainWorkspace();
            Blockly.serialization.workspaces.load(ws, w);     // fires change events -> draft updated
            return Blockly.serialization.workspaces.save(w);
        }""", WORKSPACE)
        await pg.wait_for_timeout(600)
        if len(sys.argv) > 2:
            await pg.screenshot(path=sys.argv[2])
        await pg.locator("#bSave").click()
        await pg.wait_for_timeout(800)
        err = await pg.locator("#errbox").inner_text() if await pg.locator("#errbox").is_visible() else ""
        if err:
            problems.append("save rejected: " + err)
        await b.close()
    with open(os.path.join(HERE, "fixtures", "demo_workspace.json"), "w") as f:
        json.dump(saved, f, indent=1)
    await asyncio.sleep(2.5)
    st = get("/api/state")
    if st["vars"].get("blink") != "3":
        problems.append("start script: blink should be 3, is %r" % st["vars"].get("blink"))
    if st["states"]["away"]["v"] is not True:
        problems.append("every-second script should have switched 'away' on")
    post("/api/sim", {"board": "main", "pin": 2, "value": 0})      # press (pull-up, inverted)
    await asyncio.sleep(0.5)
    st = get("/api/state")
    checks = [("relay", st["states"]["relay"]["v"] is True), ("fan", st["states"]["fan"]["v"] == {"on": True, "level": 70.0}),
              ("matrix", str(st["states"]["matrix"]["v"]).startswith("T ")), ("count", st["vars"].get("count") == "1"),
              ("led still off", st["states"]["status_led"]["v"] == "off")]
    post("/api/sim", {"board": "main", "pin": 2, "value": 1})
    await asyncio.sleep(1.3)
    st = get("/api/state")
    checks.append(("led green after wait", st["states"]["status_led"]["v"] == "green"))
    problems += ["after button press: %s is wrong" % name for name, ok in checks if not ok]
    print("\n".join(problems) if problems else "UI end-to-end: all checks passed")
    sys.exit(1 if problems else 0)

asyncio.run(main())
