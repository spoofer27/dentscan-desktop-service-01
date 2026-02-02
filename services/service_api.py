import json
import os
import subprocess
import sys
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from service_config import SERVICE_NAME
HOST = os.environ.get("SERVICE_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("SERVICE_API_PORT", "8085"))


def run_sc(args):
	result = subprocess.run(
		["sc"] + args,
		capture_output=True,
		text=True,
		timeout=10,
	)
	return result.returncode, result.stdout, result.stderr


def run_wrapper(args):
	wrapper_path = os.path.join(os.path.dirname(__file__), "Service_Wrapper.py")
	result = subprocess.run(
		[sys.executable, wrapper_path] + args,
		capture_output=True,
		text=True,
		timeout=30,
		cwd=os.path.dirname(wrapper_path),
	)
	return result.returncode, result.stdout, result.stderr


def is_admin():
	try:
		return ctypes.windll.shell32.IsUserAnAdmin() != 0
	except Exception:
		return False


def parse_state(sc_output):
	state = "UNKNOWN"
	for line in sc_output.splitlines():
		if "STATE" in line:
			parts = line.split(":", 1)
			if len(parts) == 2:
				tail = parts[1].strip()
				tokens = tail.split()
				if len(tokens) >= 2:
					state = tokens[1].upper()
				elif len(tokens) == 1:
					state = tokens[0].upper()
			break
	return state


def get_service_status():
	code, out, err = run_sc(["query", SERVICE_NAME])
	if code != 0:
		return {
			"ok": False,
			"service": SERVICE_NAME,
			"state": "UNKNOWN",
			"running": False,
			"error": (err or out).strip(),
		}

	state = parse_state(out)
	return {
		"ok": True,
		"service": SERVICE_NAME,
		"state": state,
		"running": state == "RUNNING",
	}


def write_json(handler, status_code, payload):
	data = json.dumps(payload).encode("utf-8")
	handler.send_response(status_code)
	handler.send_header("Content-Type", "application/json; charset=utf-8")
	handler.send_header("Content-Length", str(len(data)))
	handler.end_headers()
	handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
	def do_GET(self):
		parsed = urlparse(self.path)
		if parsed.path == "/api/status":
			status = get_service_status()
			status["apiConnected"] = True
			write_json(self, 200, status)
			return

		self.send_error(404, "Not Found")

	def do_POST(self):
		parsed = urlparse(self.path)
		if parsed.path == "/api/connect":
			write_json(self, 200, {"ok": True, "message": "Connected"})
			return

		if parsed.path == "/api/disconnect":
			write_json(self, 200, {"ok": True, "message": "Disconnected"})
			return
		if parsed.path == "/api/start":
			code, out, err = run_sc(["start", SERVICE_NAME])
			payload = {"ok": code == 0, "output": (out or err).strip()}
			write_json(self, 200 if code == 0 else 500, payload)
			return

		if parsed.path == "/api/stop":
			code, out, err = run_sc(["stop", SERVICE_NAME])
			payload = {"ok": code == 0, "output": (out or err).strip()}
			write_json(self, 200 if code == 0 else 500, payload)
			return

		if parsed.path == "/api/restart":
			stop_code, stop_out, stop_err = run_sc(["stop", SERVICE_NAME])
			start_code, start_out, start_err = run_sc(["start", SERVICE_NAME])
			ok = stop_code == 0 and start_code == 0
			payload = {
				"ok": ok,
				"stop": (stop_out or stop_err).strip(),
				"start": (start_out or start_err).strip(),
			}
			write_json(self, 200 if ok else 500, payload)
			return

		if parsed.path == "/api/reconnect":
			write_json(self, 200, {"ok": True, "message": "UI reconnected"})
			return

		if parsed.path == "/api/install":
			# code, out, err = run_wrapper(["install"])
			code, out, err =  run_sc(["install", SERVICE_NAME])
			payload = {"ok": code == 0, "output": (out or err).strip()}
			write_json(self, 200 if code == 0 else 500, payload)
			return

		if parsed.path == "/api/uninstall":
			print("Uninstall requested")
			if not is_admin():
				print("Uninstall failed: not admin")
				write_json(self, 403, {"ok": False, "output": "Administrator privileges required"})
				return

			# code, out, err = run_wrapper(["remove"])
			stop_code, stop_out, stop_err = run_sc(["stop", SERVICE_NAME])
			code, out, err = run_sc(["delete", SERVICE_NAME])

			payload = {
				"ok": code == 0,
				"stop": (stop_out or stop_err).strip(),
				"delete": (out or err).strip(),
			}
			write_json(self, 200 if code == 0 else 500, payload)
			return

		self.send_error(404, "Not Found")

	def log_message(self, format, *args):
		return


def main():
	server = ThreadingHTTPServer((HOST, PORT), Handler)
	print(f"Service API running on http://{HOST}:{PORT}")
	print(f"Service name: {SERVICE_NAME}")
	server.serve_forever()


if __name__ == "__main__":
	main()
