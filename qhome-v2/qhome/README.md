# QHome 2

The ESPHome idea for the Arduino UNO Q, without code and without compiling.

Upload one general firmware once. After that everything happens in a web page: give every pin a job, snap Scratch-style blocks together to automate, and add more Arduino boards (other UNO Qs, UNO R4, even an UNO R3, Nano or Mega) whose pins then behave as if they were on the master. A saved change is live immediately; no sketch is rebuilt or re-uploaded.

| | ESPHome | QHome |
|---|---|---|
| You describe the device in | YAML | a web page (pins) and blocks (logic) |
| A change needs | compile + upload | Save & apply |
| Logic runs on | each ESP | the master UNO Q (Linux side) |
| More boards | one firmware each | one generic satellite firmware, pins appear on the master |

## How it fits together

| Part | Runs on | Job |
|---|---|---|
| `sketch/sketch.ino` | UNO Q microcontroller (STM32) | Generic pin firmware; relays UART and I2C links to other boards |
| `python/qh_engine.py` | UNO Q Linux side (or a PC) | Boards, devices, live state, MQTT and Home Assistant |
| `python/qh_script.py` | same | Runs the block scripts straight from the saved workspace |
| `python/qh_link.py`, `qh_hw.py` | same | Links to remote boards (USB, UART, I2C, Wi-Fi) and the QLink protocol |
| `python/qh_mqtt.py` | same | Built-in MQTT 3.1.1 broker and client, no dependencies |
| `python/qh_web.py` + `assets/` | same, port 7000 | Web UI and REST API |
| `satellite/qhome_satellite/` | UNO R3, Nano, Mega 2560, UNO R4 Minima / WiFi | Generic satellite firmware |
| `tools/qhome_usb_helper.py` | UNO Q Linux, outside App Lab | Lets the app reach boards plugged in over USB |

Python 3.9+, standard library only. The UI is plain JavaScript with Blockly and the Barlow font bundled in `assets/`, so the board needs no internet.

## Try it on a PC first (VS Code)

1. Open this folder in VS Code and install the recommended extensions when asked.
2. Press **F5** and pick **QHome: simulator (demo setup, 3 simulated boards)**. The browser opens on `http://localhost:7000`.

The simulator runs the real engine with a pretend UNO Q, a pretend UNO R3 "on USB" and a pretend Nano "on I2C". Analog inputs wander, and the dashboard has a "Hold to press" button for every simulated input. Without VS Code: `QHOME_MODE=sim python python/main.py`.

| VS Code piece | What it gives you |
|---|---|
| Run and Debug (F5) | Simulator with demo setup, simulator with an empty setup, PC-as-hub mode, tests; breakpoints work in all of them |
| Terminal > Run Task | Deploy to the UNO Q over SSH, restart the app, read its log, install the USB helper, compile and upload the satellite with arduino-cli |
| Setup file checks | `data/config.json` and any `*.qhome.json` get autocomplete and validation from `schema/qhome-config.schema.json` |
| Testing panel | The `unittest` suites in `tests/` |
| `.ino` IntelliSense | Works without the Arduino cores, through a small stand-in in `tests/host/mock` |

## Install on the UNO Q

1. Copy this folder to `~/ArduinoApps/qhome` on the board (VS Code task **UNO Q: deploy the app**, or App Lab's import, or `scp`).
2. In App Lab open the app, go to **Sketch Libraries** and add **Arduino_RouterBridge**.
3. Press **Run**. The first start compiles the sketch and uploads it to the microcontroller.
4. Open `http://<board-name>.local:7000`.

## Pins you can use

Every pin below can be configured from the **Boards & pins** tab. Free pins show coloured marks for what they can do; click a pin and pick its job.

| Board | Digital in/out | PWM | Analog in | Analog out | Pulse counter | Extras |
|---|---|---|---|---|---|---|
| UNO Q | D0-D13, A0-A5, SDA, SCL, JSPI (3), JMISC (25) | D3 D5 D6 D9 D10 D11, plus D2 D7 D8 D12 D13 SDA SCL (see note) | A0-A5, 14 bit | A0, A1 (DAC) | any header pin | LED1-LED4 (RGB), 13x8 LED matrix |
| UNO R3 | D2-D13, A0-A5 | D3 D5 D6 D9 D10 D11 | A0-A5, 10 bit | - | D2, D3 | |
| Nano | D2-D13, A0-A5 | same | A0-A7 | - | D2, D3 | |
| Mega 2560 | D2-D53, A0-A15 | D2-D13, D44-D46 | A0-A15 | - | D2 D3 D18-D21 | |
| UNO R4 Minima / WiFi | D0-D13, A0-A5 | D3 D5 D6 D9 D10 D11 | A0-A5, 14 bit | A0 (DAC) | D2, D3 | |

Notes on the UNO Q:

- The extra PWM pins, the three JSPI pins (22-24) and the JMISC pins (25-49) follow the pin table of the current Arduino Zephyr core. They depend on the core version installed on your board; the six `~` pins and D0-D21 are the documented, safe set. JMISC is the bottom connector and needs a breakout.
- LED1 and LED2 belong to the Linux side. LED2 normally shows system status.
- A0 and A1 never tolerate more than 3.3 V. All outputs are 3.3 V.
- A pin that carries a link (D0/D1 for UART, SDA/SCL for I2C) is hatched and cannot be given a job.

Not covered yet: servo and tone outputs, I2C/SPI sensor drivers, CAN.

## Connecting other boards

Add a board in **Boards & pins > Add board**. The dialog shows the exact `config.h` lines for the satellite and the wiring. Flash `satellite/qhome_satellite` once with the Arduino IDE (or the VS Code task).

| Link | Works with | Wiring | Good to know |
|---|---|---|---|
| USB cable | UNO R3, Nano, Mega, R4 | Board into the UNO Q through a USB-C hub | No level shifting, also powers the board. Inside App Lab the Python container cannot see USB devices, so run the USB helper once (below). On classic boards D0/D1 are taken by the USB link. |
| UART | all | Master D1 (TX) to board RX, master D0 (RX) to board TX, GND to GND | One board per master. A 5 V board needs a level shifter (or divider) on its TX line. The same link runs through RS-485 transceivers or HC-05 / HC-12 modules in transparent mode. |
| I2C | UNO R3, Nano, Mega, R4 | SDA, SCL, GND on the master's header or Qwiic; pull-ups to 3.3 V | Several boards per bus, each with its own address (8-119). The satellite keeps its 5 V pull-ups off; still use a level shifter with 5 V boards and keep cables short. |
| Wi-Fi (MQTT) | UNO R4 WiFi, other UNO Qs | none | Uses the master's built-in broker (or your own). Topics `qlink/<board>/down`, `up`, `status`. |

**USB helper.** On the UNO Q: `python3 ~/ArduinoApps/qhome/tools/qhome_usb_helper.py`. To keep it running after a reboot use the VS Code task **UNO Q: install the USB helper service** or follow the comments in `tools/qhome-usb-helper.service`. On a PC the helper is not needed.

**Another UNO Q as a node.** Install QHome on it too, open **Settings > Role of this board > Node**, and enter the master's address and a node name. On the master add an "Arduino UNO Q" board connected by Wi-Fi whose id is that node name. All of its pins, LEDs and its matrix then show up on the master.

**A PC as the hub.** `QHOME_MODE=pc python python/main.py` runs QHome without a local board. USB and Wi-Fi boards work; UART and I2C links need the UNO Q's microcontroller.

If a board drops off, its tiles grey out and scripts can react with "when board ... goes offline". When it returns the master sends its setup again.

## Blocks

The **Blocks** tab is a Scratch-style workspace (Blockly with the Scratch renderer).

| Category | Blocks |
|---|---|
| Events | when QHome starts; when a device turns on / off / changes; when a value goes above / below; every N seconds / minutes / hours; at HH:MM on chosen days; when a board goes online / offline; when an MQTT message arrives |
| Devices | turn on / off, toggle, set level, set RGB colour, show text on the matrix, clear the matrix |
| Sensing | is on, value of, board is online, current hour / minute / weekday, time is between |
| Control | wait, repeat, forever, if, if / else, wait until, repeat until, stop this script |
| Operators | arithmetic, compare, and / or / not, random, round, remainder, constrain, text, join |
| Variables | create, set, change; live values show on the dashboard |
| Network | publish to MQTT, the received MQTT message, write to the log |

Scratch rules apply: every "when" block is its own script, scripts run side by side, a script that is triggered again while running starts over, and a "forever" loop never blocks the others. Devices on any board can be mixed in one script, for example "when Door button (UNO Q) turns on, toggle Garage light (UNO R3)".

Scripts run on the master's Linux side. They stop while the master is off; satellites hold their last output state.

## MQTT and Home Assistant

The built-in broker listens on port 1883. In Home Assistant add the MQTT integration with the UNO Q's address; devices and one "online" sensor per board appear through auto-discovery. Topics: `<node>/<id>/state`, `<node>/<id>/set` (`ON`, `OFF`, `TOGGLE`, a number, a colour name or text), `<node>/<id>/level/set` for dimmers, availability on `<node>/status`.

Set a broker user name and password and a web password in **Settings**. The web page uses plain HTTP with basic auth, so keep it on your own network. To put it behind Nginx:

```nginx
server {
    listen 80;
    location / {
        proxy_pass http://127.0.0.1:7000;
        proxy_set_header Host $host;
    }
}
```

## Languages

English and Turkish, switched at the bottom left. All UI text lives in `assets/i18n.js`; copy the `en` block to add a language. Blockly's own blocks use its bundled language packs (`assets/vendor/blockly/msg`); add the matching file for a new language. Log lines and save errors from the server are English only.

## Tests

`python -m unittest discover -s tests -p "test_*.py"`

| Suite | Covers |
|---|---|
| `test_qhome.py` | Setup validation, version 1 migration, block scripts across three boards, QLink handshake and recovery, a UNO Q node over real MQTT sockets |
| `test_firmware.py` (needs g++) | Compiles the real satellite sketch against a PC stand-in for the Arduino core and drives it over serial, I2C and Wi-Fi/MQTT; runs the full chain block script > engine > USB helper > serial > firmware; syntax-checks the master sketch |
| `e2e_ui.py` (needs Playwright) | Builds scripts in the block editor in a real browser, saves through the page, checks the engine's behaviour |

The stand-in checks protocol and logic, not electrical behaviour or the vendor cores. The satellite was also compiled with avr-gcc against the official AVR core (UNO 8.1 kB flash / 0.7 kB RAM, Mega 11 kB / 1.8 kB). The UNO R4 paths and the master sketch have not been compiled against their real cores here; check them on the first build.

## QLink protocol

Text lines, newline terminated, the same on every link.

| Direction | Line | Meaning |
|---|---|---|
| to board | `H` | hello; reply `h <proto> <model> <boot id>` |
| to board | `P` | ping; reply `p <boot id>` |
| to board | `C <pin> <mode> <interval ms> <debounce ms>` | set a pin's job; reply `c <pin> <mode or negative error>` |
| to board | `D <pin> <0/1>` | digital write |
| to board | `A <pin> <0-4095>` | PWM or DAC write, always 12 bit |
| to board | `L <led> <bits>`, `M <104 digits>` | RGB LED, matrix frame (UNO Q nodes) |
| to board | `R` | release all pins; reply `r 1` |
| from board | `E <pin> <value>` | input changed or periodic reading |
| from board | `B <boot id>` | the board just started |

Modes: 0 off, 1 input, 2 input pull-up, 3 input pull-down, 4 output, 5 PWM, 6 analog in, 7 DAC, 8 pulse counter. A changed boot id tells the master the board restarted, and it sends the setup again.

## Not in this version

- Over-the-air flashing of satellite firmware from the master.
- eBPF packet handling from the project proposal.
- RS-485 multi-drop (several boards on one UART).
- Automatic conversion of version 1 IF/THEN automations; rebuild them as blocks. Version 1 devices are migrated.

## Licences of bundled parts

Blockly (Apache 2.0, `assets/vendor/blockly/LICENSE`), Barlow typeface (SIL OFL 1.1, `assets/fonts/LICENSE-Barlow-OFL.txt`).
