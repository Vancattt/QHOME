"""Compiles the real Arduino sketches against a PC stand-in for the Arduino core
(tests/host/mock) and talks to them: QLink over serial, I2C and Wi-Fi/MQTT, plus the whole
chain  block script -> engine -> USB helper -> serial -> satellite firmware.

Needs g++ (skipped without it). This checks the firmware's logic and protocol; it cannot
check electrical behaviour or the vendor cores, so still try a real board once.
"""
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from common import HERE, ROOT, Rig, demo_config

from qh_mqtt import MqttHub

MOCK = os.path.join(HERE, "host", "mock")
SAT = os.path.join(ROOT, "satellite", "qhome_satellite")
GXX = shutil.which("g++")
BUILD = tempfile.mkdtemp(prefix="qhome-fw-")


def build(name, flags=(), config=None, master=False):
    out = os.path.join(BUILD, name)
    if os.path.exists(out):
        return out
    d = os.path.join(BUILD, name + "-src")
    os.makedirs(d, exist_ok=True)
    src = os.path.join(ROOT, "sketch", "sketch.ino") if master else os.path.join(SAT, "qhome_satellite.ino")
    with open(src) as f, open(os.path.join(d, "sketch.cpp"), "w") as o:
        o.write("#include <Arduino.h>\n" + f.read())
    if not master:
        with open(os.path.join(SAT, "config.h")) as f:
            text = f.read()
        for key, val in (config or {}).items():
            lines = text.split("\n")
            text = "\n".join(("#define %s %s" % (key, val)) if ln.startswith("#define %s " % key) else ln for ln in lines)
        with open(os.path.join(d, "config.h"), "w") as o:
            o.write(text)
    inc = ["-I" + os.path.join(MOCK, "unoq")] if master else []
    cmd = [GXX, "-std=c++17", "-Wall", "-Werror", "-Wno-unused-function", *flags, *inc, "-I" + MOCK, "-I" + d,
           os.path.join(d, "sketch.cpp")]
    cmd += ["-fsyntax-only"] if master else [os.path.join(MOCK, "main.cpp"), "-o", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("compile failed:\n" + r.stderr[:3000])
    return out


class Board:
    """A compiled satellite running as a process; stdout = link, stderr = pin writes."""

    def __init__(self, exe, stdin=subprocess.PIPE, stdout=subprocess.PIPE):
        self.p = subprocess.Popen([exe], stdin=stdin, stdout=stdout, stderr=subprocess.PIPE)
        self.lines, self.writes = queue.Queue(), []
        if stdout == subprocess.PIPE:
            threading.Thread(target=self._read, args=(self.p.stdout, self.lines.put), daemon=True).start()
        threading.Thread(target=self._read, args=(self.p.stderr, self.writes.append), daemon=True).start()

    @staticmethod
    def _read(stream, sink):
        for raw in stream:
            sink(raw.decode("ascii", "replace").strip())

    def send(self, line):
        self.p.stdin.write((line + "\n").encode())
        self.p.stdin.flush()

    def expect(self, prefix, timeout=3.0):
        end = time.time() + timeout
        seen = []
        while time.time() < end:
            try:
                line = self.lines.get(timeout=0.05)
            except queue.Empty:
                continue
            seen.append(line)
            if line.startswith(prefix):
                return line
        raise AssertionError("expected a line starting with %r, got %r" % (prefix, seen))

    def wrote(self, text, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            if text in self.writes:
                return True
            time.sleep(0.02)
        return False

    def close(self):
        self.p.kill()
        self.p.wait()
        for stream in (self.p.stdin, self.p.stdout, self.p.stderr):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass


@unittest.skipUnless(GXX, "g++ not installed")
class Firmware(unittest.TestCase):
    def test_master_sketch_compiles(self):
        build("master", flags=["-DMOCK_UNOQ"], master=True)

    def test_uno_over_serial(self):
        b = Board(build("uno"))
        try:
            boot = b.expect("B ").split()[1]
            b.send("H")
            self.assertEqual(b.expect("h ").split(), ["h", "1", "uno", boot])
            b.send("P")
            self.assertEqual(b.expect("p ").split()[1], boot)
            b.send("C 4 2 1000 20")                 # input with pull-up
            self.assertEqual(b.expect("c 4"), "c 4 2")
            self.assertEqual(b.expect("E 4"), "E 4 1")
            b.send("~d 4 0")                        # contact closes
            self.assertEqual(b.expect("E 4"), "E 4 0")
            b.send("~a 14 512")
            b.send("C 14 6 50 0")                   # analog input, report every 50 ms
            self.assertEqual(b.expect("c 14"), "c 14 6")
            self.assertEqual(b.expect("E 14"), "E 14 512")
            b.send("C 3 5 0 0")                     # PWM: 12-bit in, 8-bit timer out
            self.assertEqual(b.expect("c 3"), "c 3 5")
            b.send("A 3 4095")
            self.assertTrue(b.wrote("AW 3 255"))
            b.send("C 2 8 100 0")                   # pulse counter on an interrupt pin
            self.assertEqual(b.expect("c 2"), "c 2 8")
            b.send("~i 2 5")
            self.assertEqual(b.expect("E 2 5"), "E 2 5")
            b.send("C 8 4 0 0")
            b.expect("c 8 4")
            b.send("D 8 1")
            self.assertTrue(b.wrote("DW 8 1"))
            for line, reply in (("C 7 7 0 0", "c 7 -2"),     # no DAC on an UNO R3
                                ("C 0 4 0 0", "c 0 -1"),     # D0 carries the serial link
                                ("C 4 5 0 0", "c 4 -2"),     # D4 has no PWM
                                ("C 5 8 0 0", "c 5 -2"),     # D5 has no interrupt
                                ("C 3 3 0 0", "c 3 1")):     # no pull-down on AVR: plain input
                b.send(line)
                self.assertEqual(b.expect(reply[:4]), reply)
            b.send("\x01\x02 zzz")                  # noise and an over-long line are ignored
            b.send("M " + "7" * 104)
            b.send("R")
            self.assertEqual(b.expect("r "), "r 1")
            b.send("D 8 1")                         # released pins no longer accept writes
            time.sleep(0.2)
            self.assertEqual(b.writes.count("DW 8 1"), 1)
        finally:
            b.close()

    def test_mega_pin_map(self):
        b = Board(build("mega", flags=["-D__AVR_ATmega2560__"]))
        try:
            b.send("H")
            self.assertEqual(b.expect("h ").split()[2], "mega")
            for line, reply in (("C 54 6 100 0", "c 54 6"), ("C 44 5 0 0", "c 44 5"), ("C 18 8 100 0", "c 18 8"),
                                ("C 53 4 0 0", "c 53 4"), ("C 69 6 100 0", "c 69 6"), ("C 70 4 0 0", "c 70 -1")):
                b.send(line)
                self.assertEqual(b.expect(reply.rsplit(" ", 1)[0]), reply)
        finally:
            b.close()

    def test_i2c_slave(self):
        b = Board(build("uno-i2c", flags=["-DMOCK_I2C"], config={"QL_LINK": "QL_I2C"}))
        try:
            b.expect("B ")
            b.send("H")
            self.assertEqual(b.expect("h ").split()[2], "uno")
            b.send("C 0 4 0 0")                     # D0/D1 are free on I2C ...
            self.assertEqual(b.expect("c 0"), "c 0 4")
            b.send("C 18 4 0 0")                    # ... SDA is not
            self.assertEqual(b.expect("c 18"), "c 18 -1")
            b.send("C 14 6 20 0")                   # a burst of events must survive the 32-byte polling
            for _ in range(20):
                b.expect("E 14 ")
        finally:
            b.close()

    def test_r4_wifi_over_mqtt(self):
        port = 18941
        got = queue.Queue()
        hub = MqttHub(lambda *a: None, lambda t, p: got.put((t, p)), lambda: None)
        hub.configure({"mode": "internal", "port": port}, "test", "test/status")
        hub.subscribe("qlink/kitchen/up")
        hub.subscribe("qlink/kitchen/status")

        def expect(topic, prefix, timeout=6):
            end = time.time() + timeout
            seen = []
            while time.time() < end:
                try:
                    t, p = got.get(timeout=0.05)
                except queue.Empty:
                    continue
                seen.append((t, p))
                if t == topic and p.startswith(prefix):
                    return p
            raise AssertionError("expected %s %r, got %r" % (topic, prefix, seen))
        b = Board(build("r4w", flags=["-DARDUINO_UNOR4_WIFI"], config={
            "QL_LINK": "QL_WIFI", "QL_MASTER_HOST": '"127.0.0.1"', "QL_MASTER_PORT": str(port), "QL_BOARD_ID": '"kitchen"'}))
        try:
            expect("qlink/kitchen/status", "online")
            boot = expect("qlink/kitchen/up", "B ").split()[1]
            hub.publish("qlink/kitchen/down", "H")
            self.assertEqual(expect("qlink/kitchen/up", "h ").split(), ["h", "1", "r4w", boot])
            hub.publish("qlink/kitchen/down", "C 14 7 0 0")      # the R4 has a DAC on A0
            self.assertEqual(expect("qlink/kitchen/up", "c 14"), "c 14 7")
            hub.publish("qlink/kitchen/down", "A 14 2048")
            self.assertTrue(b.wrote("AW 14 2048"))
            hub.publish("qlink/kitchen/down", "C 5 2 0 10")
            self.assertEqual(expect("qlink/kitchen/up", "E 5"), "E 5 1")
            b.close()                                            # power cut: the broker publishes the last will
            expect("qlink/kitchen/status", "offline")
        finally:
            b.close()

    def test_whole_chain_usb_helper_to_firmware(self):
        """Door button on the simulated UNO Q -> block script -> relay on a (compiled) UNO R3
        reached through the USB helper, exactly as on an UNO Q running App Lab."""
        port = 18951
        cfg = demo_config()
        cfg["boards"] = cfg["boards"][:2]                        # main + 'garage' (UNO on USB)
        cfg["components"] = [c for c in cfg["components"] if c.get("board") != "lab"]
        cfg["scripts"]["workspace"]["blocks"]["blocks"] = cfg["scripts"]["workspace"]["blocks"]["blocks"][:1]
        os.environ["QHOME_REAL_LINKS"] = "1"
        master_fd, slave_fd = os.openpty()
        board = Board(build("uno"), stdin=master_fd, stdout=master_fd)
        helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "qhome_usb_helper.py"), "--port", str(port),
                                   "--device", os.ttyname(slave_fd)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rig = Rig(cfg, link_port=port)
        try:
            self.assertTrue(rig.wait(lambda: rig.engine.online.get("garage"), 10), "satellite found through the helper")
            self.assertEqual(rig.engine.hws["garage"].remote_model, "uno")
            self.assertTrue(board.wrote("PM 7 1", 3), "relay pin configured as an output")
            self.assertTrue(rig.wait(lambda: rig.state("pir") is False), "PIR reading arrived from the firmware")
            os.write(slave_fd, b"~d 4 1\n")                      # motion on the satellite's D4
            self.assertTrue(rig.wait(lambda: rig.state("pir") is True))
            rig.wait(lambda: rig.state("button") is False)
            rig.engine.sim_input("main", 2, 0)                   # press the button on the master
            self.assertTrue(board.wrote("DW 7 1", 3), "the script switched the relay on the other board")
            board.close()                                        # unplug
            self.assertTrue(rig.wait(lambda: not rig.engine.online.get("garage"), 12), "unplugging is noticed")
        finally:
            os.environ.pop("QHOME_REAL_LINKS", None)
            helper.kill()
            helper.wait()
            board.close()
            rig.close()
            os.close(master_fd)
            os.close(slave_fd)


if __name__ == "__main__":
    unittest.main()
