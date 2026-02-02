import json
import os
import subprocess
import sys
from urllib import request
from urllib.error import URLError

from PyQt5 import QtCore, QtGui, QtWidgets

from services.service_config import SERVICE_NAME

HOST = os.environ.get("SERVICE_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("SERVICE_API_PORT", "8085"))
API_BASE = f"http://{HOST}:{PORT}"
API_SCRIPT = os.path.join(os.path.dirname(__file__), "service_api.py")


class WorkerSignals(QtCore.QObject):
    finished = QtCore.pyqtSignal(str, dict, str)


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
        # self._build_status_bar(root)

    def _build_body(self, layout):
        body = QtWidgets.QWidget()
        body.setObjectName("Body")
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setSpacing(0)

        self.body_stack = QtWidgets.QStackedWidget()
        self.body_stack.setObjectName("BodyStack")

        self._build_main_notebook()
        # self._build_settings_notebook()

        self.body_stack.addWidget(self.main_notebook)
        # self.body_stack.addWidget(self.settings_notebook)
        self.body_stack.setCurrentWidget(self.main_notebook)

        body_layout.addWidget(self.body_stack)
        layout.addWidget(body, 1)

    # def _build_status_bar(self, layout):
    #     status = QtWidgets.QWidget()
    #     status.setObjectName("StatusBar")
    #     status_layout = QtWidgets.QHBoxLayout(status)
    #     status_layout.setContentsMargins(12, 8, 12, 8)

    #     self.footer_status = QtWidgets.QLabel("Ready")
    #     self.footer_status.setObjectName("Subtle")
    #     status_layout.addWidget(self.footer_status)
    #     status_layout.addStretch(1)

    #     layout.addWidget(status)

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

        # icon_home = self._icon_from_path(os.path.join("res", "icons", "home.svg"))
        # icon_home_dark = self._icon_from_path(os.path.join("res", "icons", "home-dark.svg"))
        # icon_settings = self._icon_from_path(os.path.join("res", "icons", "settings.svg"))
        # icon_settings_dark = self._icon_from_path(os.path.join("res", "icons", "settings-dark.svg"))
        self.theme_icon_light = self._icon_from_path(os.path.join("res", "icons", "light.svg"))
        self.theme_icon_dark = self._icon_from_path(os.path.join("res", "icons", "dark.svg"))

        # self.home_btn = QtWidgets.QToolButton()
        # self.home_btn.setObjectName("NavButton")
        # self.home_btn.setToolTip("Dentascan Desktop Service Monitoring and Configuration")
        # self.home_btn.setIcon(icon_home_dark if self.is_dark else icon_home)
        # self.home_btn.setIconSize(QtCore.QSize(24, 24))
        # self.home_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        # self.home_btn.clicked.connect(self._show_home)

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

        # self.settings_btn = QtWidgets.QToolButton()
        # self.settings_btn.setObjectName("NavButton")
        # self.settings_btn.setToolTip("Settings")
        # self.settings_btn.setIcon(icon_settings_dark if self.is_dark else icon_settings)
        # self.settings_btn.setIconSize(QtCore.QSize(24, 24))
        # self.settings_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        # self.settings_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        # settings_menu = QtWidgets.QMenu(self)
        # services_menu = settings_menu.addMenu("Services")
        # settings_menu.addAction("Services", self._show_services)
        # self.settings_btn.setMenu(settings_menu)
        # nav.addWidget(self.settings_btn)

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
        # self.uploader_tab = QtWidgets.QWidget()

        self.main_notebook.addTab(self.status_tab, "Status")
        self.main_notebook.addTab(self.conf_tab, "Configuration")
        # self.main_notebook.addTab(self.uploader_tab, "Uploader")

    def _build_settings_notebook(self):
        self.settings_notebook = QtWidgets.QTabWidget()
        self.settings_notebook.setObjectName("SettingsNotebook")
        self.settings_notebook.setDocumentMode(True)
        self.settings_notebook.setTabPosition(QtWidgets.QTabWidget.North)

        self.settings_services_tab = QtWidgets.QWidget()
        self.settings_notebook.addTab(self.settings_services_tab, "Services")

        services_layout = QtWidgets.QVBoxLayout(self.settings_services_tab)
        services_layout.setContentsMargins(5, 5, 5, 5)
        services_layout.setSpacing(0)

        self.services_notebook = QtWidgets.QTabWidget()
        self.services_notebook.setObjectName("ServicesNotebook")
        self.services_notebook.setDocumentMode(True)
        self.services_notebook.setTabPosition(QtWidgets.QTabWidget.North)

        self.services_status_tab = QtWidgets.QWidget()
        self.services_config_tab = QtWidgets.QWidget()

        self.services_notebook.addTab(self.services_status_tab, "Status")
        self.services_notebook.addTab(self.services_config_tab, "Configuration")

        self._build_service_monitor(self.services_status_tab)

        services_layout.addWidget(self.services_notebook)

    def _build_service_monitor(self, parent):
        container = QtWidgets.QVBoxLayout(parent)
        container.setContentsMargins(16, 16, 16, 16)
        container.setSpacing(12)

        title = QtWidgets.QLabel("Service Monitor")
        title.setObjectName("Title")
        container.addWidget(title)

        sub = QtWidgets.QLabel(f"API: {API_BASE}")
        sub.setObjectName("Subtle")
        container.addWidget(sub)

        self.service_name_label = QtWidgets.QLabel(f"Configured Service: {SERVICE_NAME}")
        container.addWidget(self.service_name_label)

        status_grid = QtWidgets.QGridLayout()
        status_grid.setHorizontalSpacing(12)
        status_grid.setVerticalSpacing(8)

        self.service_state_label = QtWidgets.QLabel("Unknown")
        self.api_state_label = QtWidgets.QLabel("Disconnected")
        self.api_process_label = QtWidgets.QLabel("Not running")

        status_grid.addWidget(QtWidgets.QLabel("Service:"), 0, 0)
        status_grid.addWidget(self.service_state_label, 0, 1)

        status_grid.addWidget(QtWidgets.QLabel("UI Connected:"), 1, 0)
        status_grid.addWidget(self.api_state_label, 1, 1)

        self.api_dot = QtWidgets.QLabel()
        self.api_dot.setFixedSize(12, 12)
        self.api_dot.setObjectName("StatusDot")
        self._set_api_dot("red")
        status_grid.addWidget(self.api_dot, 1, 2)

        status_grid.addWidget(QtWidgets.QLabel("API Process:"), 2, 0)
        status_grid.addWidget(self.api_process_label, 2, 1)

        container.addLayout(status_grid)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self._action_btn("Start", "/api/start"))
        btn_row.addWidget(self._action_btn("Stop", "/api/stop"))
        btn_row.addWidget(self._action_btn("Restart", "/api/restart"))
        btn_row.addWidget(self._action_btn("Reconnect", "/api/reconnect"))
        btn_row.addStretch(1)
        container.addLayout(btn_row)

        self.message_label = QtWidgets.QLabel("Waiting for status...")
        self.message_label.setObjectName("Subtle")
        container.addWidget(self.message_label)
        container.addStretch(1)

    def _action_btn(self, label, path):
        btn = QtWidgets.QPushButton(label)
        btn.clicked.connect(lambda: self._post_action(path))
        return btn

    def _show_home(self):
        self.body_stack.setCurrentWidget(self.main_notebook)
        self.main_notebook.setCurrentWidget(self.status_tab)

    def _show_services(self):
        self.body_stack.setCurrentWidget(self.settings_notebook)
        self.settings_notebook.setCurrentWidget(self.settings_services_tab)

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

    def _set_api_dot(self, color):
        self.api_dot.setProperty("status", color)
        self.api_dot.style().unpolish(self.api_dot)
        self.api_dot.style().polish(self.api_dot)

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
            self.service_state_label.setText("Unknown")
            self._set_api_dot("red")
            self.message_label.setText(f"API error: {error}")
            if self.api_process is None:
                self.api_process_label.setText("Not running")
            if path == "/api/status":
                self._try_start_api()
            return

        if path == "/api/status":
            self.api_state_label.setText("Connected")
            self._set_api_dot("green")
            self.service_state_label.setText(payload.get("state", "Unknown"))
            if payload.get("service"):
                self.service_name_label.setText(f"Configured Service: {payload.get('service')}")
            if self.api_process is not None and self.api_process.poll() is None:
                self.api_process_label.setText("Running")
            elif self.api_process is not None and self.api_process.poll() is not None:
                self.api_process_label.setText("Stopped")
            else:
                self.api_process_label.setText("Running (external)")
            if payload.get("ok"):
                self.message_label.setText("Status OK")
            else:
                service_name = payload.get("service") or self.service_name_label.text()
                self.message_label.setText(
                    f"Error: {payload.get('error', 'Unknown')} (Service: {service_name})"
                )
        else:
            if payload and payload.get("ok"):
                self.message_label.setText("Action OK")
            else:
                out = None
                if payload:
                    out = payload.get("output") or payload.get("message")
                self.message_label.setText(f"Action failed: {out or 'Unknown'}")

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
                background: {palette['button_bg']};
                border: 1px solid {palette['button_border']};
                border-radius: 8px;
                padding: 6px 12px;
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
