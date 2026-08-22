"""应用入口与诊断日志配置。"""

from __future__ import annotations

import sys
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import qInstallMessageHandler, QtMsgType, QMessageLogContext
from PySide6.QtGui import QFont

from src.core.popup_suppressor import install_patch


# ------------------------------------------------------------------
# Windows 系统代理 → 环境变量
# ------------------------------------------------------------------
def _apply_windows_system_proxy() -> None:
    """读取 Windows IE 系统代理设置并写入 HTTP_PROXY / HTTPS_PROXY 环境变量。

    httpx 的 trust_env=True (默认) 会从这些环境变量读取代理配置，
    从而让全部 HTTP 请求自动走系统代理。
    """
    if sys.platform != "win32":
        return

    try:
        import winreg
    except ImportError:
        return

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
    except OSError:
        return

    try:
        proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
    except OSError:
        winreg.CloseKey(key)
        return

    if not proxy_enable:
        winreg.CloseKey(key)
        return

    try:
        proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        winreg.CloseKey(key)
        return

    # ProxyServer 格式示例:
    #   127.0.0.1:7890                       → 统一代理
    #   http=127.0.0.1:7890;https=127.0.0.1:7890  → 分协议代理
    http_proxy: str | None = None
    https_proxy: str | None = None

    if "=" in proxy_server:
        # 分协议格式: key=value;key=value
        for part in proxy_server.split(";"):
            part = part.strip()
            if "=" in part:
                proto, addr = part.split("=", 1)
                proto = proto.strip().lower()
                addr = addr.strip()
                if proto in ("http", "https", "socks"):
                    # httpx 要求带 http:// 前缀
                    url = f"http://{addr}" if not addr.startswith("http") else addr
                    if proto == "http":
                        http_proxy = url
                    elif proto == "https":
                        https_proxy = url
    else:
        http_proxy = f"http://{proxy_server}"
        https_proxy = http_proxy

    if http_proxy and not os.environ.get("HTTP_PROXY"):
        os.environ["HTTP_PROXY"] = http_proxy
    if https_proxy and not os.environ.get("HTTPS_PROXY"):
        os.environ["HTTPS_PROXY"] = https_proxy

    # ProxyOverride → NO_PROXY (不为空时才设置)
    try:
        proxy_override, _ = winreg.QueryValueEx(key, "ProxyOverride")
        if proxy_override and not os.environ.get("NO_PROXY"):
            # Windows 的通配符 <local> 和分号分隔转为逗号分隔
            override = proxy_override.replace(";", ",").replace("<local>", "localhost,127.0.0.1")
            os.environ["NO_PROXY"] = override
    except OSError:
        pass

    winreg.CloseKey(key)


# ------------------------------------------------------------------
# 自定义 Qt 消息处理器：转入 Python 日志，避免掩盖线程/生命周期错误
# ------------------------------------------------------------------
_qt_logger = logging.getLogger("qt")


def _configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        log_dir = Path.home() / ".indextts-gui2" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / "app.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )
    except OSError:
        # A read-only home directory must not prevent the GUI from starting.
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=handlers,
    )


def _qt_message_handler(
    msg_type: QtMsgType,
    context: QMessageLogContext,
    msg: str,
) -> None:
    """Forward Qt diagnostics to logging without showing UI popups."""
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    location = ""
    if context.file:
        location = f" ({context.file}:{context.line})"
    _qt_logger.log(levels.get(msg_type, logging.WARNING), "%s%s", msg, location)


def main() -> None:
    _configure_logging()

    # 高 DPI 支持
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

    # ── 读取 Windows 系统代理，设置环境变量供 httpx 使用 ──
    _apply_windows_system_proxy()

    # ── Qt 内部诊断统一写入日志 ──
    qInstallMessageHandler(_qt_message_handler)

    # ── Monkey-patch QMessageBox 以支持运行时抑制 ──
    install_patch()

    app = QApplication(sys.argv)
    app.setApplicationName("IndexTTS-GUI2")
    app.setOrganizationName("IndexTTS-GUI2")

    # 全局字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    # 启动主窗口
    from src.app import MainWindow
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
