import os
import sys
import servicemanager
import win32event
import win32service
import win32serviceutil
import threading
import time

from service_config import SERVICE_NAME
from folder_monitor import FolderMonitor

class MyService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "Dentascan Desktop Service 01"
    _svc_description_ = "Dentascan Desktop Service 01 for basic background processing tasks."
    _svc_type_ = win32service.SERVICE_AUTO_START

    # Use one of the following (optional):
    # _svc_account_ = "username"
    # _svc_account_ = "username@activedirectorydomain.com"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.stop_event = threading.Event()
        self.worker_thread = None
        try:
            os.chdir(os.path.dirname(__file__))
        except Exception:
            pass

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_event.set()
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ''))
        try:
            self.main()
        except Exception as exc:
            try:
                log_path = os.path.join(os.path.dirname(__file__), "service_boot.log")
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(f"Service error: {exc}\n")
            except Exception:
                pass
            servicemanager.LogErrorMsg(f"Service error: {exc}")
            raise

    def main(self):
        # Add your Python script code here
        print('Starting Service...')
        try:
            log_path = os.path.join(os.path.dirname(__file__), "service_boot.log")
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write("\n")
                log_file.write(f"Startup: {self._svc_name_}\n")
                log_file.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_file.write(f"PID: {os.getpid()}\n")
                log_file.write(f"Python: {sys.executable}\n")
                log_file.write(f"Working directory: {os.path.dirname(__file__)}\n")
        except Exception:
            pass

        try:
            FolderMonitor.from_config().ensure_today_folder()
            try:
                log_path = os.path.join(os.path.dirname(__file__), "service_boot.log")
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write("FolderMonitor started successfully\n")
            except Exception:
                pass
        except Exception as exc:
            try:
                log_path = os.path.join(os.path.dirname(__file__), "service_boot.log")
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(f"FolderMonitor failed to start: {exc}\n")
            except Exception:
                pass
        
        import CodeIWantToRun
        self.worker_thread = threading.Thread(
            target=CodeIWantToRun.main,
            args=(self.stop_event,),
            daemon=True,
        )
        self.worker_thread.start()

        # Wait until stop is requested
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)

        # Give the worker a moment to stop gracefully
        if self.worker_thread is not None:
            self.worker_thread.join(timeout=15)

if __name__ == '__main__':
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(MyService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(MyService)