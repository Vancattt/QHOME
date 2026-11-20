"""QHome: no-code I/O, block scripts, multi-board links, MQTT and Home Assistant for the UNO Q.

On the UNO Q this file is started by Arduino App Lab. On a PC, `python python/main.py`
starts the same app with a simulated board (UI on http://localhost:7000).

Environment:
  QHOME_MODE   auto (default) | sim | pc   pc = no local board, this computer is only the hub
  QHOME_PORT   web port (7000)             QHOME_DATA  folder for config.json / state.json
  QHOME_LINK_PORT  TCP port the USB helper connects to (7001)
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import qh_hw  # noqa: E402
import qh_web  # noqa: E402
from qh_engine import Engine  # noqa: E402
from qh_mqtt import MqttHub  # noqa: E402
from qh_node import NodeAgent  # noqa: E402

WEB_PORT = int(os.environ.get("QHOME_PORT", "7000"))
DATA_DIR = os.environ.get("QHOME_DATA", os.path.join(ROOT, "data"))
MODE = os.environ.get("QHOME_MODE", "auto")

engine = Engine(DATA_DIR)
engine.log("QHome %s starting (data folder: %s)" % (__import__("qh_engine").VERSION, DATA_DIR))
app = engine  # replaced by a NodeAgent below when this board is a node

# Hardware and MQTT callbacks arrive on other threads; `app` only queues them.
local = qh_hw.create_local(lambda pin, val: app.hw_event(pin, val),
                           lambda ver: app.hw_boot(ver),
                           lambda lid, line: app.hw_link_rx(lid, line), engine.log, MODE)
cfg = engine.load_config(has_local=local is not None)
mqtt = MqttHub(engine.log, lambda t, p: app.mqtt_message(t, p), lambda: app.mqtt_connected())

role = cfg["device"].get("role", "master")
if role == "node" and local is None:
    engine.log("Node mode needs a board; starting as master instead", "warn")
    role = "master"
if role == "node":
    app = NodeAgent(engine, local, mqtt)
    if local.probe():
        engine.log("Microcontroller connected (firmware v%s)" % local.version)
    engine.log("Node mode: handing this board's pins to the master at %s"
               % (cfg["device"].get("master_host") or "(no address set)"))
else:
    engine.start(local, mqtt)

qh_web.start(app, WEB_PORT, os.path.join(ROOT, "assets"),
             {"role": role, "sim": bool(local is not None and local.simulated),
              "has_local": local is not None, "link_port": engine.ports.port})


def loop():
    app.step(0.1)
    if getattr(app, "want_restart", False):   # the role changed: start over in the new role
        time.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)


try:
    if local is None or local.simulated:
        while True:
            loop()
    else:
        from arduino.app_utils import App
        App.run(user_loop=loop)
except KeyboardInterrupt:
    pass
finally:
    try:
        topic = ("qlink/%s/status" if role == "node" else "%s/status") % engine.node()
        mqtt.shutdown(topic)
        time.sleep(0.3)
    except Exception:
        pass
