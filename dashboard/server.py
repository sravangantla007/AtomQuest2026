"""
===============================================================================
 NodeVAYU Web Bridge Server
 Serves the clean modern web dashboard on 0.0.0.0:3000.
 Compliant with NODEVAYU_UART_PROTOCOL_SPEC.md.
 
 Serial Policy:
  - ZERO automatic port polling or grabbing.
  - The serial port (COM11) is ONLY opened when explicitly requested by the user
    via the 'Connect Serial' button in the dashboard UI.
  - Can be disconnected at any time to immediately release COM11 for Thonny.
===============================================================================
"""

import json
import time
import threading
import serial
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os

PORT = 3000
DEFAULT_SERIAL_PORT = 'COM11'
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
        "connected": False,
        "total_frames": 0,
        "targets": []
    },
    "fan": {
        "power": False,
        "level": 0,
        "counts": 0,
        "rpm": 0,
        "mode": 1,
        "last_packet": "0xAF00F000",
        "last_crc": 15
    },
    "serial_status": {
        "connected": False,
        "port": DEFAULT_SERIAL_PORT,
        "baud": SERIAL_BAUD
    },
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
ser_thread = None
ser_should_run = False


def compute_ir_packet(cmd: int, data_l: int, data_h_low: int = 0) -> tuple:
    """Computes ICD-compliant 32-bit packet word and 4-bit CRC."""
    addr = 0xAF
    crc = (addr ^ cmd ^ data_h_low ^ data_l) & 0x0F
    data_h = (crc << 4) | (data_h_low & 0x0F)
    pkt = (addr << 24) | (cmd << 16) | (data_h << 8) | (data_l & 0xFF)
    return f"0x{pkt:08X}", crc


# =============================================================================
# EXPLICIT SERIAL CONNECTION MANAGEMENT (NO AUTO-GRAB)
# =============================================================================
def serial_reader_loop():
    global latest_telemetry, ser_handle, ser_should_run
    print(f"[SERIAL] Reader thread started for {DEFAULT_SERIAL_PORT}.")

    while ser_should_run:
        try:
            line = ""
            with ser_lock:
                if ser_handle and ser_handle.is_open:
                    if ser_handle.in_waiting:
                        line = ser_handle.readline().decode('utf-8', errors='ignore').strip()
            
            if line and line.startswith('{') and line.endswith('}'):
                try:
                    data = json.loads(line)
                    if data.get("type") == "telemetry":
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
                    
            time.sleep(0.02)
        except Exception as e:
            print(f"[SERIAL] Read error ({e}). Closing port.")
            break

    # Clean teardown
    disconnect_serial()


def connect_serial(port_name: str = DEFAULT_SERIAL_PORT) -> tuple:
    """Explicitly connects to the serial port. Returns (success: bool, message: str)."""
    global ser_handle, ser_thread, ser_should_run, latest_telemetry

    with ser_lock:
        if ser_handle and ser_handle.is_open:
            return True, f"Already connected to {ser_handle.port}"

        try:
            ser_handle = serial.Serial(port_name, SERIAL_BAUD, timeout=1.0)
            ser_handle.reset_input_buffer()
            ser_should_run = True
            latest_telemetry["serial_status"]["connected"] = True
            latest_telemetry["serial_status"]["port"] = port_name
            print(f"[SERIAL] Successfully opened {port_name} @ {SERIAL_BAUD} upon user request.")

            ser_thread = threading.Thread(target=serial_reader_loop, daemon=True)
            ser_thread.start()
            return True, f"Connected to {port_name}"
        except Exception as e:
            latest_telemetry["serial_status"]["connected"] = False
            err_msg = f"Failed to open {port_name}: {e}"
            print(f"[SERIAL] {err_msg}")
            return False, err_msg


def disconnect_serial() -> tuple:
    """Explicitly disconnects and releases the serial port. Returns (success: bool, message: str)."""
    global ser_handle, ser_should_run, latest_telemetry

    ser_should_run = False
    with ser_lock:
        if ser_handle:
            try:
                ser_handle.close()
            except Exception:
                pass
            ser_handle = None
            
        latest_telemetry["serial_status"]["connected"] = False
        print("[SERIAL] Disconnected. Port released for Thonny / external tools.")
        return True, "Serial port disconnected and released."


def send_pico_command(cmd_str: str) -> tuple:
    """Sends command line if serial is connected."""
    global ser_handle
    with ser_lock:
        if ser_handle and ser_handle.is_open:
            try:
                ser_handle.write((cmd_str.strip() + "\n").encode('utf-8'))
                print(f"[SERIAL TX -> PICO] {cmd_str}")
                return True, "Delivered to Pico"
            except Exception as e:
                return False, f"Write error: {e}"
    return False, "Serial port not connected"


def update_local_fan_state(cmd: str, val_str: str):
    """Updates internal fan state to provide immediate UI feedback."""
    fan = latest_telemetry["fan"]
    c = cmd.lower().strip()
    
    if c == "speed":
        try:
            counts = max(0, min(511, int(val_str)))
            fan["counts"] = counts
            fan["power"] = (counts > 0)
            fan["rpm"] = round((counts * 1450) / 511)
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


# =============================================================================
# HTTP REQUEST HANDLER
# =============================================================================
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

        elif self.path == '/api/serial/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(latest_telemetry["serial_status"]).encode('utf-8'))

        else:
            super().do_GET()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else "{}"

        # 1. Connect Serial Endpoint
        if self.path == '/api/serial/connect':
            try:
                data = json.loads(body) if body else {}
                target_port = data.get("port", DEFAULT_SERIAL_PORT)
            except Exception:
                target_port = DEFAULT_SERIAL_PORT

            ok, msg = connect_serial(target_port)
            self.send_response(200 if ok else 409)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "connected" if ok else "error",
                "message": msg,
                "serial_status": latest_telemetry["serial_status"]
            }).encode('utf-8'))

        # 2. Disconnect Serial Endpoint
        elif self.path == '/api/serial/disconnect':
            ok, msg = disconnect_serial()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "disconnected",
                "message": msg,
                "serial_status": latest_telemetry["serial_status"]
            }).encode('utf-8'))

        # 3. Fan Command Endpoint
        elif self.path == '/api/command':
            try:
                data = json.loads(body)
                cmd = str(data.get("cmd", "")).strip()
                val = str(data.get("val", "")).strip()

                pico_cmd = f"{cmd} {val}".strip()
                success, note = send_pico_command(pico_cmd)
                update_local_fan_state(cmd, val)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                resp = {
                    "status": "ok",
                    "sent": pico_cmd,
                    "serial_delivered": success,
                    "serial_note": note,
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
    server = HTTPServer(('0.0.0.0', PORT), DashboardHandler)
    print("=" * 68)
    print("  NodeVAYU Dashboard Web Server Active")
    print(f"  Access from Phone (Wi-Fi): http://10.30.36.5:{PORT}")
    print(f"  Access from Local PC   : http://localhost:{PORT}")
    print("  Serial Policy: IDLE / DISCONNECTED (COM11 is 100% free for Thonny)")
    print("  Connect serial only when explicitly requested via the UI.")
    print("=" * 68)
    server.serve_forever()


if __name__ == '__main__':
    run_server()
