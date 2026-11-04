"""
NodeVAYU Web Bridge Server
Serves the web dashboard on 0.0.0.0:3000 and bridges COM11 to the web UI.
Accessible on local Wi-Fi from mobile phones or any browser.
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

# Global shared state
latest_telemetry = {
    "type": "telemetry",
    "temp": 31.6,
    "rh": 59.9,
    "heat_index": 36.8,
    "targets": [],
    "fan": {
        "level": 3,
        "rpm": 1050,
        "counts": 330,
        "mode": 1,
        "last_pkt": "0xAF02E003"
    },
    "connected": False,
    "last_update": 0
}

ser_lock = threading.Lock()
ser_handle = None

def serial_worker():
    global latest_telemetry, ser_handle
    print(f"[SERIAL] Starting bridge worker on {SERIAL_PORT} @ {SERIAL_BAUD}...")

    while True:
        try:
            with ser_lock:
                ser_handle = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1.0)
                ser_handle.reset_input_buffer()
            print(f"[SERIAL] Connected to {SERIAL_PORT} successfully!")
            latest_telemetry["connected"] = True

            while True:
                line = ser_handle.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            data = json.loads(line)
                            if data.get("type") == "telemetry":
                                data["connected"] = True
                                data["last_update"] = time.time()
                                latest_telemetry.update(data)
                        except Exception:
                            pass
                time.sleep(0.01)

        except Exception as e:
            latest_telemetry["connected"] = False
            print(f"[SERIAL] Connection lost ({e}). Retrying in 2 seconds...")
            time.sleep(2.0)


def send_pico_command(cmd_str):
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


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Serve files from dashboard directory
        dash_dir = os.path.dirname(os.path.abspath(__file__))
        super().__init__(*args, directory=dash_dir, **kwargs)

    def do_GET(self):
        if self.path == '/api/telemetry':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(json.dumps(latest_telemetry).encode('utf-8'))
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/command':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                cmd = data.get("cmd", "")
                val = data.get("val", "")
                
                # Format text command for Pico
                pico_cmd = f"{cmd} {val}".strip()
                success = send_pico_command(pico_cmd)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok" if success else "error", "sent": pico_cmd}).encode('utf-8'))
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
        # Silence routine GET /api/telemetry poll spam from console
        if '/api/telemetry' not in args[0]:
            super().log_message(format, *args)


def run_server():
    # Start background serial worker
    t = threading.Thread(target=serial_worker, daemon=True)
    t.start()

    server = HTTPServer(('0.0.0.0', PORT), DashboardHandler)
    print(f"\n=======================================================")
    print(f"  NodeVAYU Local Dashboard Server Active")
    print(f"  Access from Phone (Wi-Fi): http://10.30.36.5:{PORT}")
    print(f"  Access from Local PC   : http://localhost:{PORT}")
    print(f"=======================================================\n")
    server.serve_forever()

if __name__ == '__main__':
    run_server()
