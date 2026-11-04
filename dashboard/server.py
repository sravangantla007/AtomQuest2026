"""
===============================================================================
 NodeVAYU Web Bridge Server
 Serves the clean modern web dashboard on 0.0.0.0:3000 and bridges COM11 to HTTP.
 Compliant with NODEVAYU_UART_PROTOCOL_SPEC.md.
 Accessible on local Wi-Fi from mobile phones or any desktop browser.
===============================================================================
"""

import json
import time
import threading
import serial
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os

PORT = 3000
SERIAL_PORT = 'COM11'
SERIAL_BAUD = 115200

# Canonical telemetry state
latest_telemetry = {
    "type": "telemetry",
    "ts": int(time.time()),
    "uptime_ms": 0,
    "env": {
        "temp_c": 28.5,
        "rh_pct": 58.0,
        "heat_index_c": 30.2,
        "dew_point_c": 19.4
    },
    "radar": {
        "connected": True,
        "total_frames": 0,
        "targets": []
    },
    "fan": {
        "power": True,
        "level": 3,
        "counts": 330,
        "rpm": 1050,
        "mode": 1,
        "last_packet": "0xAF02E003",
        "last_crc": 14
    },
    "connected": False,
    "last_update": 0
}

# Calibrated fan presets (0 to 6)
LEVEL_MAP = {
    0: (0, 0, "OFF / Coasting"),
    1: (200, 650, "Level 1: Gentle Breeze"),
    2: (265, 900, "Level 2: Low-Medium"),
    3: (330, 1050, "Level 3: Medium"),
    4: (395, 1200, "Level 4: Medium-High"),
    5: (455, 1350, "Level 5: High"),
    6: (511, 1450, "Level 6: Full Throttle Turbo")
}

ser_lock = threading.Lock()
ser_handle = None


def compute_ir_packet(cmd: int, data_l: int, data_h_low: int = 0) -> tuple:
    """Computes ICD-compliant 32-bit packet word and 4-bit CRC."""
    addr = 0xAF
    crc = (addr ^ cmd ^ data_h_low ^ data_l) & 0x0F
    data_h = (crc << 4) | (data_h_low & 0x0F)
    pkt = (addr << 24) | (cmd << 16) | (data_h << 8) | (data_l & 0xFF)
    return f"0x{pkt:08X}", crc


def serial_worker():
    global latest_telemetry, ser_handle
    print(f"[SERIAL] Starting background UART bridge worker on {SERIAL_PORT} @ {SERIAL_BAUD}...")

    while True:
        try:
            with ser_lock:
                ser_handle = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1.0)
                ser_handle.reset_input_buffer()
            print(f"[SERIAL] Connected to {SERIAL_PORT} successfully!")
            latest_telemetry["connected"] = True

            while True:
                line = ser_handle.readline().decode('utf-8', errors='ignore').strip()
                if line and line.startswith('{') and line.endswith('}'):
                    try:
                        data = json.loads(line)
                        if data.get("type") == "telemetry":
                            latest_telemetry["connected"] = True
                            latest_telemetry["last_update"] = time.time()
                            
                            # Handle nested schema
                            if "env" in data:
                                latest_telemetry["env"].update(data["env"])
                            if "radar" in data:
                                latest_telemetry["radar"].update(data["radar"])
                            if "fan" in data:
                                latest_telemetry["fan"].update(data["fan"])
                                
                            # Handle flat fallback schema
                            if "temp" in data:
                                latest_telemetry["env"]["temp_c"] = data["temp"]
                            if "rh" in data:
                                latest_telemetry["env"]["rh_pct"] = data["rh"]
                            if "heat_index" in data:
                                latest_telemetry["env"]["heat_index_c"] = data["heat_index"]
                            if "targets" in data:
                                latest_telemetry["radar"]["targets"] = data["targets"]
                                
                    except Exception:
                        pass
                time.sleep(0.01)

        except Exception as e:
            latest_telemetry["connected"] = False
            time.sleep(2.0)


def send_pico_command(cmd_str: str) -> bool:
    global ser_handle
    with ser_lock:
        if ser_handle and ser_handle.is_open:
            try:
                ser_handle.write((cmd_str.strip() + "\n").encode('utf-8'))
                print(f"[SERIAL TX -> PICO] {cmd_str}")
                return True
            except Exception as e:
                print(f"[SERIAL ERR] Write failed: {e}")
                return False
    return False


def update_local_fan_state(cmd: str, val_str: str):
    """Updates internal fan state to provide immediate UI feedback even if offline."""
    fan = latest_telemetry["fan"]
    c = cmd.lower().strip()
    
    if c == "speed":
        try:
            counts = max(0, min(511, int(val_str)))
            fan["counts"] = counts
            fan["power"] = (counts > 0)
            fan["rpm"] = round((counts * 1450) / 511)
            # Map counts to approximate level
            if counts == 0:
                fan["level"] = 0
            elif counts < 235:
                fan["level"] = 1
            elif counts < 300:
                fan["level"] = 2
            elif counts < 365:
                fan["level"] = 3
            elif counts < 425:
                fan["level"] = 4
            elif counts < 485:
                fan["level"] = 5
            else:
                fan["level"] = 6
            pkt_hex, crc = compute_ir_packet(0x01, counts & 0xFF, (counts >> 8) & 0x01)
            fan["last_packet"] = pkt_hex
            fan["last_crc"] = crc
        except ValueError:
            pass

    elif c == "level":
        try:
            lvl = max(0, min(6, int(val_str)))
            fan["level"] = lvl
            counts, rpm, _ = LEVEL_MAP[lvl]
            fan["counts"] = counts
            fan["rpm"] = rpm
            fan["power"] = (lvl > 0)
            cmd_code = 0x00 if lvl == 0 else 0x02
            data_l = 0x00 if lvl == 0 else lvl
            pkt_hex, crc = compute_ir_packet(cmd_code, data_l, 0)
            fan["last_packet"] = pkt_hex
            fan["last_crc"] = crc
        except ValueError:
            pass

    elif c in ("on", "start"):
        fan["power"] = True
        if fan["level"] == 0:
            fan["level"] = 3
            fan["counts"] = 330
            fan["rpm"] = 1050
        pkt_hex, crc = compute_ir_packet(0x07, 0x00, 0)
        fan["last_packet"] = pkt_hex
        fan["last_crc"] = crc

    elif c in ("off", "stop"):
        fan["power"] = False
        fan["level"] = 0
        fan["counts"] = 0
        fan["rpm"] = 0
        pkt_hex, crc = compute_ir_packet(0x00, 0x00, 0)
        fan["last_packet"] = pkt_hex
        fan["last_crc"] = crc

    elif c in ("toggle", "p"):
        fan["power"] = not fan["power"]
        if fan["power"]:
            if fan["level"] == 0:
                fan["level"] = 3
                fan["counts"] = 330
                fan["rpm"] = 1050
        else:
            fan["level"] = 0
            fan["counts"] = 0
            fan["rpm"] = 0
        pkt_hex, crc = compute_ir_packet(0x03, 0x00, 0)
        fan["last_packet"] = pkt_hex
        fan["last_crc"] = crc

    elif c in ("+", "step_up"):
        new_lvl = min(6, fan["level"] + 1)
        update_local_fan_state("level", str(new_lvl))
        pkt_hex, crc = compute_ir_packet(0x05, 0x00, 0)
        fan["last_packet"] = pkt_hex
        fan["last_crc"] = crc

    elif c in ("-", "step_down"):
        new_lvl = max(1, fan["level"] - 1)
        update_local_fan_state("level", str(new_lvl))
        pkt_hex, crc = compute_ir_packet(0x06, 0x00, 0)
        fan["last_packet"] = pkt_hex
        fan["last_crc"] = crc

    elif c == "mode":
        try:
            m = max(0, min(5, int(val_str)))
            fan["mode"] = m
            pkt_hex, crc = compute_ir_packet(0x04, m, 0)
            fan["last_packet"] = pkt_hex
            fan["last_crc"] = crc
        except ValueError:
            pass


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        dash_dir = os.path.dirname(os.path.abspath(__file__))
        super().__init__(*args, directory=dash_dir, **kwargs)

    def do_GET(self):
        if self.path == '/api/telemetry':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            latest_telemetry["ts"] = int(time.time())
            self.wfile.write(json.dumps(latest_telemetry).encode('utf-8'))
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/command':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                cmd = str(data.get("cmd", "")).strip()
                val = str(data.get("val", "")).strip()

                pico_cmd = f"{cmd} {val}".strip()
                success = send_pico_command(pico_cmd)
                update_local_fan_state(cmd, val)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                resp = {
                    "status": "ok",
                    "sent": pico_cmd,
                    "serial_delivered": success,
                    "fan": latest_telemetry["fan"]
                }
                self.wfile.write(json.dumps(resp).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        if '/api/telemetry' not in args[0]:
            super().log_message(format, *args)


def run_server():
    t = threading.Thread(target=serial_worker, daemon=True)
    t.start()

    server = HTTPServer(('0.0.0.0', PORT), DashboardHandler)
    print("=" * 64)
    print("  NodeVAYU Web Bridge Server Active")
    print(f"  Access from Phone (Wi-Fi): http://10.30.36.5:{PORT}")
    print(f"  Access from Local PC   : http://localhost:{PORT}")
    print("=" * 64)
    server.serve_forever()


if __name__ == '__main__':
    run_server()
