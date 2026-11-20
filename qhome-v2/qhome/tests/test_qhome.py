"""Engine, block scripts, validation and multi-board tests. Run:  python -m unittest discover tests"""
import time
import unittest

from common import Rig, clone, demo_config

import qh_boards
import qh_engine
import qh_hw
import qh_script
from qh_mqtt import MqttHub
from qh_node import NodeAgent

N = lambda v: {"shadow": {"type": "math_number", "fields": {"NUM": v}}}  # noqa: E731


def ws(*stacks):
    return {"blocks": {"languageVersion": 0, "blocks": list(stacks)}, "variables": [{"name": "n", "id": "vn"}]}


def chain(*blocks):
    for a, b in zip(blocks, blocks[1:]):
        a["next"] = {"block": b}
    return blocks[0]


class Validation(unittest.TestCase):
    def test_demo_is_valid(self):
        self.assertEqual(qh_engine.validate(demo_config()), [])

    def test_pin_used_twice(self):
        cfg = demo_config()
        cfg["components"].append({"id": "x", "type": "switch_output", "name": "X", "board": "main", "pin": 2, "props": {}})
        self.assertTrue(any("used by both" in e for e in qh_engine.validate(cfg)))

    def test_pin_capability(self):
        cfg = demo_config()
        cfg["components"].append({"id": "x", "type": "dac_output", "name": "X", "board": "garage", "pin": 5, "props": {}})
        self.assertTrue(any("cannot do that" in e for e in qh_engine.validate(cfg)))

    def test_link_pins_are_reserved(self):
        cfg = demo_config()   # 'lab' hangs on the master's SDA/SCL, 'garage' is a classic UNO on USB
        for board, pin in (("main", 20), ("garage", 0), ("lab", 18)):
            c = clone(cfg)
            c["components"].append({"id": "x", "type": "switch_output", "name": "X", "board": board, "pin": pin, "props": {}})
            self.assertTrue(any("board link" in e for e in qh_engine.validate(c)), (board, pin))

    def test_board_rules(self):
        cfg = demo_config()
        cfg["boards"].append({"id": "b2", "name": "b2", "model": "nano", "link": {"type": "i2c", "bus": "wire", "address": 16}})
        cfg["boards"].append({"id": "w", "name": "w", "model": "r4w", "link": {"type": "wifi"}})
        cfg["boards"].append({"id": "u", "name": "u", "model": "uno", "link": {"type": "wifi"}})
        errs = " ".join(qh_engine.validate(cfg))
        self.assertIn("already used on the same bus", errs)
        self.assertIn("need MQTT", errs)
        self.assertIn("cannot be connected by 'wifi'", errs)

    def test_script_references(self):
        cfg = demo_config(False)
        cfg["scripts"]["workspace"] = ws(chain({"type": "ev_start"}, {"type": "do_turn", "fields": {"ENT": "nope", "TO": "on"}}),
                                         chain({"type": "ev_start"}, {"type": "do_set", "fields": {"ENT": "relay"}, "inputs": {"VALUE": N(1)}}))
        errs = qh_engine.validate(cfg)
        self.assertEqual(len(errs), 2, errs)

    def test_migrate_v1(self):
        old = {"version": 1, "device": {"name": "x", "node": "qhome"}, "mqtt": {"mode": "off"},
               "components": [{"id": "r", "type": "switch_output", "name": "R", "pin": 7, "props": {}}],
               "automations": [{"id": "a"}]}
        notes = qh_engine.migrate(old)
        self.assertEqual(old["components"][0]["board"], "main")
        self.assertNotIn("automations", old)
        self.assertEqual(len(notes), 2)
        self.assertEqual(qh_engine.validate(old), [])

    def test_every_model_pin_table(self):
        for name, m in qh_boards.MODELS.items():
            ids = [p["id"] for p in m["pins"]]
            self.assertEqual(len(ids), len(set(ids)), name)
            laid = [p for _, pins in m["layout"] for p in pins]
            self.assertEqual(sorted(map(str, laid)), sorted(map(str, ids)), name)


class Scripts(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(demo_config())

    def tearDown(self):
        self.rig.close()

    def test_demo_scripts_across_boards(self):
        r = self.rig
        self.assertTrue(r.wait(lambda: all(r.engine.online.get(b) for b in ("main", "garage", "lab"))))
        self.assertTrue(r.wait(lambda: r.engine.scripts.variables().get("blink") == "3"), "start script")
        self.assertTrue(r.wait(lambda: r.state("away") is True), "every-second script")
        self.assertTrue(r.wait(lambda: r.state("button") is False))
        r.engine.sim_input("main", 2, 0)                       # press the door button
        self.assertTrue(r.wait(lambda: r.state("relay") is True), "relay on the USB board")
        garage = r.engine.transports["garage"].sim
        self.assertEqual(garage.out.get(7), 1, "the write reached the remote board")
        self.assertEqual(r.state("fan"), {"on": True, "level": 70.0})
        self.assertEqual(r.local.out.get(9), round(0.7 * 4095))
        self.assertTrue(str(r.state("matrix")).startswith("T "))
        self.assertEqual(r.state("status_led"), "off")
        self.assertTrue(r.wait(lambda: r.state("status_led") == "green", 3), "runs on after the wait")
        self.assertEqual(r.local.leds.get(3), qh_hw.RGB_BITS["green"])

    def test_retrigger_restarts_script(self):
        r = self.rig
        r.wait(lambda: r.state("button") is False)
        for _ in range(2):
            r.engine.sim_input("main", 2, 0)
            time.sleep(0.3)
            r.engine.sim_input("main", 2, 1)
            time.sleep(0.3)
        self.assertEqual(r.engine.scripts.variables().get("count"), "2")
        self.assertEqual(r.state("relay"), False, "toggled twice")

    def test_remote_adc_scaling(self):
        r = self.rig                                           # the Nano has a 10-bit ADC
        self.assertTrue(r.wait(lambda: r.state("ldr") is not None))
        self.assertTrue(0 <= r.state("ldr") <= 100)

    def test_threshold_edge_and_runaway_loop(self):
        r = self.rig
        cfg = demo_config(False)
        cfg["scripts"]["workspace"] = ws(
            chain({"type": "ev_above", "id": "h1", "fields": {"ENT": "temp", "OP": "above"}, "inputs": {"VALUE": N(-1)}},
                  {"type": "math_change", "fields": {"VAR": {"id": "vn"}}, "inputs": {"DELTA": N(1)}}),
            chain({"type": "ev_start", "id": "h2"},
                  {"type": "ctl_forever", "inputs": {"DO": {"block": {"type": "do_toggle", "fields": {"ENT": "away"}}}}}))
        self.assertEqual(r.engine.submit_config(cfg), [])
        time.sleep(1.5)                                        # temp is always above -1: armed only once
        self.assertEqual(r.engine.scripts.variables().get("n"), "0", "no edge, no trigger")
        self.assertEqual(r.engine.scripts.running(), 1, "the forever loop keeps running without blocking")
        self.assertTrue(r.wait(lambda: r.state("temp") is not None))

    def test_board_offline_event(self):
        r = self.rig
        cfg = demo_config(False)
        cfg["scripts"]["workspace"] = ws(chain({"type": "ev_board", "fields": {"BOARD": "garage", "TO": "offline"}},
                                               {"type": "do_turn", "fields": {"ENT": "away", "TO": "on"}}))
        self.assertEqual(r.engine.submit_config(cfg), [])
        self.assertTrue(r.wait(lambda: r.engine.online.get("garage")))
        r.engine.post(("link_state", "garage", False))
        self.assertTrue(r.wait(lambda: r.state("away") is True))
        self.assertFalse(r.engine.online["garage"])


class QLink(unittest.TestCase):
    def test_round_trip(self):
        events, boots, to_dev, to_hw = [], [], [], []
        sim = qh_hw.SimHW(lambda pin, val: dev.event(pin, val), adc_max=1023)
        dev = qh_hw.QLinkDevice(sim, "uno", to_hw.append)
        hw = qh_hw.QLinkHW("b", lambda p, v: events.append((p, v)), boots.append, lambda *a: None)
        hw.send = to_dev.append
        hw.set_link(True)

        def pump():
            while to_dev or to_hw:
                while to_dev:
                    dev.feed(to_dev.pop(0))
                while to_hw:
                    hw.feed(to_hw.pop(0))
        hw.poll(time.time())
        pump()
        self.assertTrue(hw.connected)
        self.assertEqual((hw.remote_model, boots), ("uno", [dev.boot_id]))
        hw.configure(4, qh_hw.M_DIN_PU, 1000, 30)
        hw.write_digital(7, 1)
        hw.write_analog(9, 2048)
        pump()
        self.assertEqual(events, [(4, 1)])
        self.assertEqual((sim.out[7], sim.out[9]), (1, 2048))
        dev.rebooted()                                         # board restarts: master handshakes again
        pump()
        self.assertFalse(hw.connected)
        hw.poll(time.time() + 2)
        pump()
        self.assertTrue(hw.connected)
        self.assertEqual(len(boots), 2)
        hw.feed("garbage \x00 line")                           # noise is ignored
        hw.feed("E x y")


class WifiNode(unittest.TestCase):
    """Two QHome instances: a master with the built-in broker and a UNO Q in node mode."""

    def test_node_hands_pins_to_master(self):
        port = 18931
        cfg = demo_config(False)
        cfg["mqtt"].update(mode="internal", port=port)
        cfg["boards"] = [cfg["boards"][0], {"id": "attic", "name": "Attic Q", "model": "unoq", "link": {"type": "wifi"}}]
        cfg["components"] = [
            {"id": "win", "type": "binary_input", "name": "Window", "board": "attic", "pin": 3, "props": {}},
            {"id": "lamp", "type": "switch_output", "name": "Lamp", "board": "attic", "pin": 8, "props": {}},
            {"id": "led", "type": "rgb_led", "name": "LED", "board": "attic", "pin": "LED4", "props": {}},
            {"id": "mx", "type": "led_matrix", "name": "MX", "board": "attic", "pin": "MATRIX", "props": {"startup_text": "HI"}}]
        import os
        os.environ["QHOME_REAL_LINKS"] = "1"
        holder = {}
        mq = MqttHub(lambda *a: None, lambda t, p: holder["rig"].engine.mqtt_message(t, p),
                     lambda: holder["rig"].engine.mqtt_connected())
        rig = holder["rig"] = Rig(cfg, mqtt=mq)
        # the node: its own store, simulated microcontroller, MQTT client to the master
        import tempfile
        store = qh_engine.Engine(tempfile.mkdtemp(prefix="qhome-node-"))
        store.load_config(True)
        store.cfg["device"].update(role="node", node="attic", master_host="127.0.0.1", master_port=port)
        nh = {}
        node_hw = qh_hw.SimHW(lambda pin, val: nh["a"].hw_event(pin, val))
        node_mq = MqttHub(lambda *a: None, lambda t, p: nh["a"].mqtt_message(t, p), lambda: nh["a"].mqtt_connected())
        agent = nh["a"] = NodeAgent(store, node_hw, node_mq)
        import threading
        run = [True]
        threading.Thread(target=lambda: [agent.step(0.02) for _ in iter(lambda: run[0], False)], daemon=True).start()
        try:
            self.assertTrue(rig.wait(lambda: rig.engine.online.get("attic"), 8), "node comes online over MQTT")
            self.assertTrue(rig.wait(lambda: node_hw.modes.get(3) == qh_hw.M_DIN_PU), "pins configured on the node")
            self.assertTrue(rig.wait(lambda: rig.state("win") is False))
            node_hw.sim_input(3, 0)
            self.assertTrue(rig.wait(lambda: rig.state("win") is True), "input event travels to the master")
            rig.engine.submit_command("lamp", "on")
            rig.engine.submit_command("led", "set", "blue")
            self.assertTrue(rig.wait(lambda: node_hw.out.get(8) == 1 and node_hw.leds.get(4) == 4))
            self.assertTrue(rig.wait(lambda: "7" in node_hw.matrix_frame or "4" in node_hw.matrix_frame), "matrix frame")
            run[0] = False                                     # the node stops answering ...
            time.sleep(0.1)
            node_mq.shutdown("qlink/attic/status")             # ... and its status turns to "offline"
            self.assertTrue(rig.wait(lambda: not rig.engine.online.get("attic"), 5), "master notices")
        finally:
            run[0] = False
            os.environ.pop("QHOME_REAL_LINKS", None)
            rig.close()


if __name__ == "__main__":
    unittest.main()
