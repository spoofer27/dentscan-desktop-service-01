import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk
from urllib import request
from urllib.error import URLError


SERVICE_NAME = os.environ.get("SERVICE_NAME", "TestUploaderService")
HOST = os.environ.get("SERVICE_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("SERVICE_API_PORT", "8085"))
API_BASE = f"http://{HOST}:{PORT}"


class ServiceMonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Service Monitor")
        self.geometry("520x320")
        self.resizable(False, False)

        self.queue = queue.Queue()

        self._build_ui()
        self._schedule_poll()
        self._drain_queue()

    def _build_ui(self):
        container = ttk.Frame(self, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(container, text="Service Monitor", font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w")

        sub = ttk.Label(container, text=f"API: {API_BASE}")
        sub.pack(anchor="w", pady=(4, 12))

        status_frame = ttk.Frame(container)
        status_frame.pack(fill=tk.X)

        self.service_state_var = tk.StringVar(value="Unknown")
        self.api_state_var = tk.StringVar(value="Disconnected")

        ttk.Label(status_frame, text="Service:").grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.service_state_var, width=18).grid(row=0, column=1, sticky="w")

        ttk.Label(status_frame, text="UI Connected:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(status_frame, textvariable=self.api_state_var, width=18).grid(row=1, column=1, sticky="w", pady=(6, 0))

        btn_frame = ttk.Frame(container)
        btn_frame.pack(fill=tk.X, pady=16)

        ttk.Button(btn_frame, text="Start", command=lambda: self._post_action("/api/start")).grid(row=0, column=0, padx=4)
        ttk.Button(btn_frame, text="Stop", command=lambda: self._post_action("/api/stop")).grid(row=0, column=1, padx=4)
        ttk.Button(btn_frame, text="Restart", command=lambda: self._post_action("/api/restart")).grid(row=0, column=2, padx=4)
        ttk.Button(btn_frame, text="Reconnect", command=lambda: self._post_action("/api/reconnect")).grid(row=0, column=3, padx=4)

        self.message_var = tk.StringVar(value="Waiting for status...")
        ttk.Label(container, textvariable=self.message_var).pack(anchor="w", pady=(4, 0))

    def _schedule_poll(self):
        self.after(2000, self._poll_status)

    def _poll_status(self):
        self._enqueue_request("GET", "/api/status")
        self._schedule_poll()

    def _post_action(self, path):
        self._enqueue_request("POST", path)

    def _enqueue_request(self, method, path):
        thread = threading.Thread(target=self._request_worker, args=(method, path), daemon=True)
        thread.start()

    def _request_worker(self, method, path):
        try:
            req = request.Request(API_BASE + path, method=method)
            with request.urlopen(req, timeout=3) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            self.queue.put((path, payload, None))
        except URLError as err:
            self.queue.put((path, None, str(err)))
        except Exception as err:
            self.queue.put((path, None, str(err)))

    def _drain_queue(self):
        try:
            while True:
                path, payload, error = self.queue.get_nowait()
                if error:
                    self.api_state_var.set("Disconnected")
                    self.service_state_var.set("Unknown")
                    self.message_var.set(f"API error: {error}")
                    continue

                if path == "/api/status":
                    self.api_state_var.set("Connected")
                    self.service_state_var.set(payload.get("state", "Unknown"))
                    if payload.get("ok"):
                        self.message_var.set("Status OK")
                    else:
                        self.message_var.set(f"Error: {payload.get('error', 'Unknown')}")
                else:
                    if payload and payload.get("ok"):
                        self.message_var.set("Action OK")
                    else:
                        out = None
                        if payload:
                            out = payload.get("output") or payload.get("message")
                        self.message_var.set(f"Action failed: {out or 'Unknown'}")
        except queue.Empty:
            pass

        self.after(200, self._drain_queue)


def main():
    app = ServiceMonitorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
