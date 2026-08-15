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
