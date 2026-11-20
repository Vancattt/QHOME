"""Board models: which pins exist on each supported board and what each pin can do.

Pin ids are the Arduino pin numbers used in sketches (A0 on an UNO is 14, on a Mega 54).
String ids are on-board extras (LEDs, LED matrix).

Capabilities:
  din   digital input          dout  digital output       pwm  PWM output
  adc   analog input           dac   true analog output   cnt  pulse counter (interrupt)
  rgb   on-board RGB LED       matrix on-board LED matrix
Notes are short codes; the web UI translates them.
"""

PWM_MAX = 4095  # QLink and the master firmware always use 12-bit duty / DAC values


def _p(pid, label, caps, notes=()):
    return {"id": pid, "label": label, "caps": list(caps), "notes": list(notes)}


def _unoq():
    pins = []
    doc_pwm = (3, 5, 6, 9, 10, 11)          # the six "~" pins from the official pinout
    core_pwm = (2, 7, 8, 12, 13)            # also wired to timers in the Zephyr core
    notes = {0: ["uart_rx"], 1: ["uart_tx"], 3: ["no5v"], 4: ["can_tx"], 5: ["can_rx"],
             10: ["spi_ss"], 11: ["spi_mosi"], 12: ["spi_miso"], 13: ["spi_sck"]}
    for d in range(14):
        caps = ["din", "dout", "cnt"]
        n = list(notes.get(d, []))
        if d in doc_pwm:
            caps.append("pwm")
        elif d in core_pwm:
            caps.append("pwm")
            n.append("pwm_core")
        pins.append(_p(d, "D%d" % d, caps, n))
    for a in range(6):
        caps = ["adc", "din", "dout", "cnt"] + (["dac"] if a < 2 else [])
        n = ["no5v"] if a < 2 else ["ain3v3"]
        pins.append(_p(14 + a, "A%d" % a, caps, n))
    pins.append(_p(20, "SDA", ["din", "dout", "cnt", "pwm"], ["i2c_sda", "pwm_core"]))
    pins.append(_p(21, "SCL", ["din", "dout", "cnt", "pwm"], ["i2c_scl", "pwm_core"]))
    for pid, lab in ((22, "CIPO"), (23, "COPI"), (24, "SCK")):
        pins.append(_p(pid, lab, ["din", "dout", "cnt"], ["jspi"]))
    jmisc = ["PC6", "PD2", "PC7", "PE2", "PC8", "PE3", "PC9", "PE5", "PE4", "PE6", "PI4", "PE7",
             "PI6", "PE8", "PI7", "PF14", "PD9", "PF15", "PI5", "PA3", "PD8", "PA0", "PA8",
             "PA1", "PA10"]
    for i, port in enumerate(jmisc):
        pins.append(_p(25 + i, "D%d" % (25 + i), ["din", "dout"], ["jmisc", "port:" + port]))
    pins.append(_p("LED1", "LED1", ["rgb"], ["led_linux"]))
    pins.append(_p("LED2", "LED2", ["rgb"], ["led_linux", "led_status"]))
    pins.append(_p("LED3", "LED3", ["rgb"], ["led_mcu"]))
    pins.append(_p("LED4", "LED4", ["rgb"], ["led_mcu"]))
    pins.append(_p("MATRIX", "Matrix", ["matrix"], ["matrix_13x8"]))
    return {
        "label": "Arduino UNO Q", "adc_max": 16383, "vref": 3.3, "logic": 3.3,
        "pins": pins, "links": ["local", "wifi"], "serial_pins": [0, 1], "i2c_pins": [20, 21],
        "layout": [["digital", list(range(14)) + [20, 21]], ["analog", list(range(14, 20))],
                   ["onboard", ["LED1", "LED2", "LED3", "LED4", "MATRIX"]],
                   ["jspi", [22, 23, 24]], ["jmisc", list(range(25, 50))]],
    }


def _classic(label, ndig, nana, pwm, ints, i2c, adc_max=1023, vref=5.0, ain_only=(), dac=(),
             links=("usb", "uart", "i2c"), usb_shares_uart=True, led=13):
    pins = []
    for d in range(ndig):
        caps = ["din", "dout"]
        n = []
        if d in pwm:
            caps.append("pwm")
        if d in ints:
            caps.append("cnt")
        if d in (0, 1):
            n.append("uart_rx" if d == 0 else "uart_tx")
        if d == led:
            n.append("led_builtin")
        if d in i2c:
            n.append("i2c_sda" if d == i2c[0] else "i2c_scl")
        pins.append(_p(d, "D%d" % d, caps, n))
    for a in range(nana):
        pid = ndig + a
        if a in ain_only:
            caps, n = ["adc"], ["ain_only"]
        else:
            caps, n = ["adc", "din", "dout"], []
        if a in dac:
            caps.append("dac")
        if pid in i2c:
            n.append("i2c_sda" if pid == i2c[0] else "i2c_scl")
        pins.append(_p(pid, "A%d" % a, caps, n))
    return {
        "label": label, "adc_max": adc_max, "vref": vref, "logic": 5.0, "pins": pins,
        "links": list(links), "serial_pins": [0, 1], "i2c_pins": list(i2c),
        "usb_shares_uart": usb_shares_uart,
        "layout": [["digital", list(range(ndig))], ["analog", list(range(ndig, ndig + nana))]],
    }


_AVR_PWM = (3, 5, 6, 9, 10, 11)
MODELS = {
    "unoq": _unoq(),
    "uno": _classic("Arduino UNO R3", 14, 6, _AVR_PWM, (2, 3), (18, 19)),
    "nano": _classic("Arduino Nano", 14, 8, _AVR_PWM, (2, 3), (18, 19), ain_only=(6, 7)),
    "mega": _classic("Arduino Mega 2560", 54, 16, tuple(range(2, 14)) + (44, 45, 46),
                     (2, 3, 18, 19, 20, 21), (20, 21)),
    "r4m": _classic("Arduino UNO R4 Minima", 14, 6, _AVR_PWM, (2, 3), (18, 19), adc_max=16383,
                    dac=(0,), usb_shares_uart=False),
    "r4w": _classic("Arduino UNO R4 WiFi", 14, 6, _AVR_PWM, (2, 3), (18, 19), adc_max=16383,
                    dac=(0,), usb_shares_uart=False, links=("usb", "uart", "i2c", "wifi")),
}
for _m in MODELS.values():
    _m["pin_by_id"] = {p["id"]: p for p in _m["pins"]}

LINK_TYPES = ("local", "usb", "uart", "i2c", "wifi")


def model(name):
    return MODELS.get(name)


def reserved_pins(board, boards):
    """Pins a board cannot hand out because its link (or a link it hosts) uses them.

    Returns {pin_id: reason_code}.
    """
    m = MODELS.get(board.get("model"))
    if not m:
        return {}
    out = {}
    lt = (board.get("link") or {}).get("type")
    if lt == "uart" or (lt == "usb" and m.get("usb_shares_uart")):
        for p in m["serial_pins"]:
            out[p] = "link_serial"
    if lt == "i2c":
        for p in m["i2c_pins"]:
            out[p] = "link_i2c"
    if lt == "local":  # the master also hosts the wired links of the other boards
        for other in boards:
            ol = other.get("link") or {}
            if ol.get("type") == "uart":
                for p in m["serial_pins"]:
                    out[p] = "host_uart"
            if ol.get("type") == "i2c" and ol.get("bus", "wire") == "wire":
                for p in m["i2c_pins"]:
                    out[p] = "host_i2c"
    return out


def public_models():
    """Model table for the web UI (without the lookup helper dicts)."""
    return {k: {kk: vv for kk, vv in v.items() if kk != "pin_by_id"} for k, v in MODELS.items()}
