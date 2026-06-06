"""应用入口 — 单实例启动"""

from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, qInstallMessageHandler, QtMsgType, QMessageLogContext
from PySide6.QtGui import QFont

from src.core.popup_suppressor import install_patch


# ------------------------------------------------------------------
# 自定义 Qt 消息处理器：静默所有 Qt 内部警告/错误弹窗
# ------------------------------------------------------------------
def _qt_message_handler(
    msg_type: QtMsgType,
    context: QMessageLogContext,
    msg: str,
) -> None:
    """吞掉所有 Qt 内部消息，不显示任何弹窗"""
    # 不做任何事，消息会被静默丢弃


def main() -> None:
    # 高 DPI 支持
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

    # ── 全局抑制 Qt 内部消息弹窗 ──
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
