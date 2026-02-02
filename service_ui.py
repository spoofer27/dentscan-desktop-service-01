import json
import os
import subprocess
import sys
from urllib import request
from urllib.error import URLError

from PyQt5 import QtCore, QtGui, QtWidgets

from services import service_config

HOST = service_config.SERVICE_API_HOST
PORT = service_config.SERVICE_API_PORT
API_BASE = f"http://{HOST}:{PORT}"
API_HOST = HOST
API_PORT = PORT
API_SCRIPT = os.path.join(os.path.dirname(__file__), "service_api.py")


class WorkerSignals(QtCore.QObject):
    finished = QtCore.pyqtSignal(str, object, str)


class RequestWorker(QtCore.QRunnable):
    def __init__(self, method, path):
        super().__init__()
        self.method = method
        self.path = path
        self.signals = WorkerSignals()

    def run(self):
        payload = None
        error = None
        try:
            req = request.Request(API_BASE + self.path, method=self.method)
            with request.urlopen(req, timeout=3) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except URLError as err:
            error = str(err)
        except Exception as err:
            error = str(err)
        self.signals.finished.emit(self.path, payload, error)


class ServiceMonitorApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Service Monitor")
        self.setFixedSize(720, 480)

        self.api_process = None
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self.is_dark = True

        self._build_ui()
        self._apply_style()

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(2000)
        self.poll_timer.timeout.connect(self._poll_status)
        self.poll_timer.start()

    def _build_ui(self):
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._build_top_nav(root)
        self._build_body(root)

    def _build_body(self, layout):
        body = QtWidgets.QWidget()
        body.setObjectName("Body")
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setSpacing(0)
        self.body_stack = QtWidgets.QStackedWidget()
        self.body_stack.setObjectName("BodyStack")
        self._build_main_notebook()
        self.body_stack.addWidget(self.main_notebook)
        self.body_stack.setCurrentWidget(self.main_notebook)
        body_layout.addWidget(self.body_stack)
        layout.addWidget(body, 1)

    def _build_top_nav(self, layout):
        header = QtWidgets.QWidget()
        header.setObjectName("Header")
        header_layout = QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        nav_container = QtWidgets.QWidget()
        nav_container.setObjectName("NavBar")
        nav = QtWidgets.QHBoxLayout(nav_container)
        nav.setContentsMargins(12, 8, 12, 8)
        nav.setSpacing(0)

        self.theme_icon_light = self._icon_from_path(os.path.join("res", "icons", "light.svg"))
        self.theme_icon_dark = self._icon_from_path(os.path.join("res", "icons", "dark.svg"))

        self.title = QtWidgets.QLabel("Dentascan Service Monitor")
        self.title.setObjectName("Title")
        nav.addWidget(self.title)
        nav.addStretch(1)

        self.theme_btn = QtWidgets.QToolButton()
        self.theme_btn.setObjectName("NavButton")
        self.theme_btn.setToolTip("Toggle theme")
        self.theme_btn.setIcon(self.theme_icon_dark if self.is_dark else self.theme_icon_light)
        self.theme_btn.setIconSize(QtCore.QSize(24, 24))
        self.theme_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.theme_btn.clicked.connect(self._toggle_theme)
        nav.addWidget(self.theme_btn)

        header_layout.addWidget(nav_container)
        layout.addWidget(header)

    def _icon_from_path(self, icon_path):
        if not os.path.isabs(icon_path):
            icon_path = os.path.join(os.path.dirname(__file__), icon_path)
        if os.path.exists(icon_path):
            return QtGui.QIcon(icon_path)
        return QtGui.QIcon()

    def _build_main_notebook(self):
        self.main_notebook = QtWidgets.QTabWidget()
        self.main_notebook.setObjectName("MainNotebook")
        self.main_notebook.setDocumentMode(True)
        self.main_notebook.setTabPosition(QtWidgets.QTabWidget.North)
        self.main_notebook.setContentsMargins(5, 5, 5, 5)

        self.status_tab = QtWidgets.QWidget()
        self.status_tab.setContentsMargins(5, 5, 5, 5)
        self.conf_tab = QtWidgets.QWidget()
        self.conf_tab.setContentsMargins(5, 5, 5, 5)
        self._build_service_monitor(self.status_tab)
        self._build_configuration_tab(self.conf_tab)

        self.main_notebook.addTab(self.status_tab, "Status")
        self.main_notebook.addTab(self.conf_tab, "Configuration")

    def _build_configuration_tab(self, parent):
        container = QtWidgets.QVBoxLayout(parent)
        container.setContentsMargins(16, 16, 16, 16)
        container.setSpacing(12)

        title = QtWidgets.QLabel("Service Configuration")
        title.setObjectName("Title")
        container.addWidget(title)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.config_service_name = QtWidgets.QLineEdit(service_config.SERVICE_NAME)
        form.addRow("Service Name:", self.config_service_name)

        self.config_auto_start = QtWidgets.QCheckBox("Enable auto-start")
        self.config_auto_start.setChecked(service_config.SERVICE_AUTO_START)
        form.addRow("SERVICE_AUTO_START:", self.config_auto_start)

        self.config_api_host = QtWidgets.QLineEdit(API_HOST)
        form.addRow("API Host:", self.config_api_host)

        self.config_api_port = QtWidgets.QSpinBox()
        self.config_api_port.setRange(1, 65535)
        self.config_api_port.setValue(API_PORT)
        form.addRow("API Port:", self.config_api_port)

        container.addLayout(form)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self.config_save_btn = QtWidgets.QPushButton("Save")
        self.config_save_btn.clicked.connect(self._save_config)
        self.config_reset_btn = QtWidgets.QPushButton("Reset to Default")
        self.config_reset_btn.clicked.connect(self._reset_config)
        btn_row.addWidget(self.config_save_btn)
        btn_row.addWidget(self.config_reset_btn)
        container.addLayout(btn_row)
        container.addStretch(1)

    def _build_service_monitor(self, parent):
        container = QtWidgets.QVBoxLayout(parent)
        container.setContentsMargins(16, 16, 16, 16)
        container.setSpacing(12)

        title = QtWidgets.QLabel("Service Status")
        title.setObjectName("Title")
        container.addWidget(title)

        # self.api_base_label = QtWidgets.QLabel(f"API: {API_BASE}")
        # self.api_base_label.setObjectName("Subtle")
        # container.addWidget(self.api_base_label)

        self.service_name_label = QtWidgets.QLabel(f"Service Name: {service_config.SERVICE_NAME}")
        container.addWidget(self.service_name_label)

        status_grid = QtWidgets.QGridLayout()
        status_grid.setHorizontalSpacing(12)
        status_grid.setVerticalSpacing(8)

        self.service_state_label = QtWidgets.QLabel("Unknown")
        self.api_state_label = QtWidgets.QLabel("Disconnected")
        self.api_process_label = QtWidgets.QLabel("Not running")

        status_grid.addWidget(QtWidgets.QLabel("API Host:"), 0, 0)
        self.api_host_value_label = QtWidgets.QLabel(f"{API_HOST}")
        status_grid.addWidget(self.api_host_value_label, 0, 1)

        status_grid.addWidget(QtWidgets.QLabel("API Port:"), 1, 0)
        self.api_port_value_label = QtWidgets.QLabel(f"{API_PORT}")
        status_grid.addWidget(self.api_port_value_label, 1, 1)

        status_grid.addWidget(QtWidgets.QLabel("API Process:"), 2, 0)
        status_grid.addWidget(self.api_process_label, 2, 1)

        status_grid.addWidget(QtWidgets.QLabel("API Connected:"), 3, 0)
        status_grid.setRowMinimumHeight(3, 32)
        api_state_row = QtWidgets.QWidget()
        api_state_row.setMinimumHeight(32)
        api_state_layout = QtWidgets.QHBoxLayout(api_state_row)
        api_state_layout.setContentsMargins(0, 0, 0, 0)
        api_state_layout.setSpacing(10)
        api_state_layout.addWidget(self.api_state_label)

        self.api_connect_btn = QtWidgets.QPushButton("Connect")
        self.api_connect_btn.clicked.connect(lambda: self._post_action("/api/connect"))
        self.api_connect_btn.setMinimumHeight(16)
        self.api_connect_btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        self.api_disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.api_disconnect_btn.clicked.connect(lambda: self._post_action("/api/disconnect"))
        self.api_disconnect_btn.setMinimumHeight(16)
        self.api_disconnect_btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        api_state_layout.addWidget(self.api_connect_btn)
        api_state_layout.addWidget(self.api_disconnect_btn)
        api_state_layout.addStretch(1)
        status_grid.addWidget(api_state_row, 3, 1)

        status_grid.addWidget(QtWidgets.QLabel("Service:"), 4, 0)
        status_grid.setRowMinimumHeight(4, 32)
        service_state_row = QtWidgets.QWidget()
        service_state_row.setMinimumHeight(32)
        service_state_layout = QtWidgets.QHBoxLayout(service_state_row)
        service_state_layout.setContentsMargins(0, 0, 0, 0)
        service_state_layout.setSpacing(10)
        service_state_layout.addWidget(self.service_state_label)

        self.service_start_btn = QtWidgets.QPushButton("Start")
        self.service_start_btn.clicked.connect(lambda: self._post_action("/api/start"))
        self.service_start_btn.setMinimumHeight(16)
        self.service_start_btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)

        self.service_stop_btn = QtWidgets.QPushButton("Stop")
        self.service_stop_btn.clicked.connect(lambda: self._post_action("/api/stop"))
        self.service_stop_btn.setMinimumHeight(16)
        self.service_stop_btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)

        self.service_install_btn = QtWidgets.QPushButton("Install")
        self.service_install_btn.clicked.connect(lambda: self._post_action("/api/install"))
        self.service_install_btn.setMinimumHeight(16)
        self.service_install_btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)

        self.service_uninstall_btn = QtWidgets.QPushButton("Uninstall")
        self.service_uninstall_btn.clicked.connect(lambda: self._post_action("/api/uninstall"))
        self.service_uninstall_btn.setMinimumHeight(16)
        self.service_uninstall_btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)

        service_state_layout.addWidget(self.service_start_btn)
        service_state_layout.addWidget(self.service_stop_btn)
        service_state_layout.addWidget(self.service_install_btn)
        service_state_layout.addWidget(self.service_uninstall_btn)
        service_state_layout.addStretch(1)
        status_grid.addWidget(service_state_row, 4, 1)

        container.addLayout(status_grid)

        self.message_label = QtWidgets.QLabel("Waiting for status...")
        self.message_label.setObjectName("Subtle")
        container.addWidget(self.message_label)
        container.addStretch(1)

    def _action_btn(self, label, path):
        btn = QtWidgets.QPushButton(label)
        btn.clicked.connect(lambda: self._post_action(path))
        return btn

    def _toggle_theme(self):
        self.is_dark = not self.is_dark
        self._apply_style()

    def _poll_status(self):
        self._enqueue_request("GET", "/api/status")

    def _post_action(self, path):
        self._enqueue_request("POST", path)

    def _enqueue_request(self, method, path):
        worker = RequestWorker(method, path)
        worker.signals.finished.connect(self._handle_response)
        self.thread_pool.start(worker)

    def _update_api_base(self, host, port):
        global API_HOST, API_PORT, API_BASE
        API_HOST = host
        API_PORT = port
        API_BASE = f"http://{API_HOST}:{API_PORT}"
        if hasattr(self, "api_base_label"):
            self.api_base_label.setText(f"API: {API_BASE}")
        if hasattr(self, "api_host_value_label"):
            self.api_host_value_label.setText(str(API_HOST))
        if hasattr(self, "api_port_value_label"):
            self.api_port_value_label.setText(str(API_PORT))

    def _save_config(self):
        name = self.config_service_name.text().strip() or service_config.DEFAULT_SERVICE_NAME
        auto_start = self.config_auto_start.isChecked()
        host = self.config_api_host.text().strip() or service_config.DEFAULT_SERVICE_API_HOST
        port = int(self.config_api_port.value())

        config_path = os.path.join(os.path.dirname(__file__), "services", "service_config.py")
        with open(config_path, "w", encoding="utf-8") as fh:
            fh.write(
                "import os\n\n"
                f"DEFAULT_SERVICE_NAME = {service_config.DEFAULT_SERVICE_NAME!r}\n"
                f"DEFAULT_SERVICE_AUTO_START = {service_config.DEFAULT_SERVICE_AUTO_START!r}\n"
                f"DEFAULT_SERVICE_API_HOST = {service_config.DEFAULT_SERVICE_API_HOST!r}\n"
                f"DEFAULT_SERVICE_API_PORT = {service_config.DEFAULT_SERVICE_API_PORT!r}\n\n"
                f"SERVICE_NAME = {name!r}\n"
                f"SERVICE_AUTO_START = {auto_start!r}\n"
                f"SERVICE_API_HOST = {host!r}\n"
                f"SERVICE_API_PORT = {port!r}\n"
            )

        service_config.SERVICE_NAME = name
        service_config.SERVICE_AUTO_START = auto_start
        service_config.SERVICE_API_HOST = host
        service_config.SERVICE_API_PORT = port

        self.service_name_label.setText(f"Service Name: {name}")
        self._update_api_base(host, port)
        self.message_label.setText("Configuration saved")

    def _reset_config(self):
        self.config_service_name.setText(service_config.DEFAULT_SERVICE_NAME)
        self.config_auto_start.setChecked(service_config.DEFAULT_SERVICE_AUTO_START)
        self.config_api_host.setText(service_config.DEFAULT_SERVICE_API_HOST)
        self.config_api_port.setValue(service_config.DEFAULT_SERVICE_API_PORT)
        self._save_config()

    def _try_start_api(self):
        if self.api_process is not None and self.api_process.poll() is None:
            self.api_process_label.setText("Running")
            return

        if not os.path.exists(API_SCRIPT):
            self.message_label.setText("API script not found")
            self.api_process_label.setText("Missing")
            return

        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            self.api_process = subprocess.Popen(
                [sys.executable, API_SCRIPT],
                cwd=os.path.dirname(API_SCRIPT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self.message_label.setText("Starting local API...")
            self.api_process_label.setText("Starting")
        except Exception as err:
            self.message_label.setText(f"Failed to start API: {err}")
            self.api_process_label.setText("Failed")

    def _handle_response(self, path, payload, error):
        if error:
            self.api_state_label.setText("Disconnected")
            self.api_state_label.setStyleSheet("color: #ef4444;")
            self.service_state_label.setText("Unknown")
            self.service_state_label.setStyleSheet("color: #f59e0b;")
            self.message_label.setText(f"API error: {error}")
            self.message_label.setStyleSheet("color: #ef4444;")
            self.api_connect_btn.setEnabled(True)
            self.api_disconnect_btn.setEnabled(False)
            self.service_start_btn.setEnabled(True)
            self.service_stop_btn.setEnabled(False)
            if self.api_process is None:
                self.api_process_label.setText("Not running")
            if path == "/api/status":
                self._try_start_api()
            return

        if path == "/api/status":
            self.api_state_label.setText("Connected")
            self.api_state_label.setStyleSheet("color: #16a34a;")
            self.api_connect_btn.setEnabled(False)
            self.api_disconnect_btn.setEnabled(True)
            self.service_state_label.setText(payload.get("state", "Unknown"))
            state_text = self.service_state_label.text().strip()
            state_lower = state_text.lower()
            if state_text == "Unknown":
                self.service_state_label.setStyleSheet("color: #f59e0b;")
                self.service_start_btn.setEnabled(True)
                self.service_stop_btn.setEnabled(False)
            elif state_lower in {"stopped", "not running", "failed", "error", "inactive", "stopping"}:
                self.service_state_label.setStyleSheet("color: #ef4444;")
                self.service_start_btn.setEnabled(True)
                self.service_stop_btn.setEnabled(False)
            else:
                self.service_state_label.setStyleSheet("color: #16a34a;")
                self.service_start_btn.setEnabled(False)
                self.service_stop_btn.setEnabled(True)
            if payload.get("service"):
                self.service_name_label.setText(f"Service Name: {payload.get('service')}")
            if self.api_process is not None and self.api_process.poll() is None:
                self.api_process_label.setText("Running")
                self.api_process_label.setStyleSheet("color: #16a34a;")
            elif self.api_process is not None and self.api_process.poll() is not None:
                self.api_process_label.setText("Stopped")
                self.api_process_label.setStyleSheet("")
            else:
                self.api_process_label.setText("Running (external)")
                self.api_process_label.setStyleSheet("color: #16a34a;")
            if payload.get("ok"):
                self.message_label.setText("Status OK")
                self.message_label.setStyleSheet("color: #16a34a;")
            else:
                service_name = payload.get("service") or self.service_name_label.text()
                self.message_label.setText(
                    f"Error: {payload.get('error', 'Unknown')} (Service: {service_name})"
                )
                self.message_label.setStyleSheet("color: #ef4444;")
        else:
            if payload and payload.get("ok"):
                self.message_label.setText("Action OK")
                self.message_label.setStyleSheet("color: #16a34a;")
            else:
                out = None
                if payload:
                    out = payload.get("output") or payload.get("message")
                self.message_label.setText(f"Action failed: {out or 'Unknown'}")
                self.message_label.setStyleSheet("color: #ef4444;")

    def _apply_style(self):
        if hasattr(self, "theme_btn"):
            self.theme_btn.setIcon(self.theme_icon_dark if self.is_dark else self.theme_icon_light)

        if self.is_dark:
            # self.home_btn.setIcon(self._icon_from_path(os.path.join("res", "icons", "home-dark.svg")))  
            # self.settings_btn.setIcon(self._icon_from_path(os.path.join("res", "icons", "settings-dark.svg")))
            palette = {
                "window_bg": "#111827",
                "text": "#e2e8f0",
                "header_bg": "#0b1220",
                "body_bg": "#111827",
                "status_bg": "#0b1220",
                "status_border": "#1f2937",
                "nav_bg": "#1e293b",
                "button_bg": "#111827",
                "button_border": "#334155",
                "button_hover": "#1f2937",
                "tab_bar_bg": "#0b1220",
                "tab_bg": "#1f2937",
                "tab_selected": "#111827",
                "subtle": "#e2e8f0",
                "menu_bg": "#0f172a",
                "menu_border": "#334155",
                "menu_hover": "#1f2937",
            }
        else:
            # self.home_btn.setIcon(self._icon_from_path(os.path.join("res", "icons", "home.svg")))  
            # self.settings_btn.setIcon(self._icon_from_path(os.path.join("res", "icons", "settings.svg")))
            palette = {
                "window_bg": "#f5f7fb",
                "text": "#1f2937",
                "header_bg": "#e8f1ff",
                "body_bg": "#ffffff",
                "status_bg": "#f1f5f9",
                "status_border": "#cbd5e1",
                "nav_bg": "#e8f1ff",
                "button_bg": "#ffffff",
                "button_border": "#d5dbe7",
                "button_hover": "#eef2ff",
                "tab_bar_bg": "#e2e8f0",
                "tab_bg": "#e9edf7",
                "tab_selected": "#ffffff",
                "subtle": "#1f2937",
                "menu_bg": "#ffffff",
                "menu_border": "#d5dbe7",
                "menu_hover": "#eef2ff",
            }

        self.setStyleSheet(
            f"""
            QWidget {{
                font-family: "Segoe UI";
                font-size: 12pt;
                color: {palette['text']};
                background: transparent;
                border: none;
            }}
            QMainWindow {{
                background: {palette['window_bg']};
            }}
            QWidget#Header {{
                background: {palette['header_bg']};
                border-radius: 12px;
            }}
            QWidget#Body {{
                background: {palette['body_bg']};
            }}
            QWidget#StatusBar {{
                background: {palette['status_bg']};
                border-top: 1px solid {palette['status_border']};
            }}
            QWidget#NavBar {{
                background: {palette['nav_bg']};
            }}
            QTabWidget {{
                border: 0px;
                background: {palette['tab_bar_bg']};
            }}
            QToolButton, QPushButton {{
                font-size: 12pt;
                background: {palette['button_bg']};
                border: 1px solid {palette['button_border']};
                border-radius: 8px;
                padding: 3px 20px;
                min-height: 16px;
            }}
            QToolButton#NavButton {{
                background: transparent;
                border: none;
                padding: 4px;
            }}
            QToolButton#NavButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
            QToolButton#NavButton:hover {{
                background: {palette['button_hover']};
                border-radius: 8px;
            }}
            QToolButton:hover, QPushButton:hover {{
                border-color: {palette['button_border']};
                background: {palette['button_hover']};
            }}
            QTabWidget::pane {{
                border: 0px;
                border-radius: 12px;
                background: {palette['tab_selected']};
                top: 0px;
            }}
            QTabWidget::tab-bar {{
                left: 0px;
            }}
            QTabBar {{
                background: {palette['tab_bar_bg']};
                border: 0px;
                padding: 4px;
            }}
            QTabBar::base {{
                border: 0px;
                background: {palette['tab_bar_bg']};
            }}
            QTabBar::tab {{
                background: {palette['tab_bg']};
                border: 0px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-top: 0px;
                padding: 8px 16px;
                margin-right: 4px;
            }}
            QTabBar::tab:selected {{
                background: {palette['tab_selected']};
                margin-top: 0px;
            }}
            QLabel#Title {{
                font-size: 18pt;
                font-weight: 600;
            }}
            QLabel#Subtle {{
                color: {palette['subtle']};
            }}
            QLabel#StatusDot[status="green"] {{
                border-radius: 6px;
                background: #16a34a;
            }}
            QLabel#StatusDot[status="red"] {{
                border-radius: 6px;
                background: #ef4444;
            }}
            QMenu {{
                background: {palette['menu_bg']};
                border: 1px solid {palette['menu_border']};
                border-radius: 8px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 6px 16px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background: {palette['menu_hover']};
            }}
            """
        )


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = ServiceMonitorApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
