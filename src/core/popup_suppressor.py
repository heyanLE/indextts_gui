"""全局弹窗抑制器 — 加载期间静默所有 QMessageBox 弹窗

使用计数器而非布尔值，支持嵌套调用 suppress/restore 而不提前失效。
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Iterator

from PySide6.QtWidgets import QMessageBox

_suppress_count = 0
_lock = RLock()
_installed = False

# Store originals on the Qt class itself so reloading this module cannot capture
# an earlier wrapper and create recursive proxy calls.
if not hasattr(QMessageBox, "_indextts_original_warning"):
    QMessageBox._indextts_original_warning = QMessageBox.warning
    QMessageBox._indextts_original_critical = QMessageBox.critical
    QMessageBox._indextts_original_information = QMessageBox.information
    QMessageBox._indextts_original_question = QMessageBox.question

_original_warning = QMessageBox._indextts_original_warning
_original_critical = QMessageBox._indextts_original_critical
_original_information = QMessageBox._indextts_original_information
_original_question = QMessageBox._indextts_original_question


def suppress_popups() -> None:
    """全局抑制 QMessageBox 弹窗（可嵌套）"""
    global _suppress_count
    with _lock:
        _suppress_count += 1


def restore_popups() -> None:
    """恢复 QMessageBox 弹窗（与 suppress 配对调用）"""
    global _suppress_count
    with _lock:
        _suppress_count = max(0, _suppress_count - 1)


def is_suppressed() -> bool:
    """查询当前是否处于抑制状态"""
    with _lock:
        return _suppress_count > 0


@contextmanager
def popup_suppressed() -> Iterator[None]:
    """Exception-safe suppression scope for new call sites."""
    suppress_popups()
    try:
        yield
    finally:
        restore_popups()


# ------------------------------------------------------------------
# 代理函数（在 main.py 中完成 monkey-patch 后生效）
# ------------------------------------------------------------------
def _maybe_warning(parent, title, text, *args, **kwargs):
    if is_suppressed():
        return None
    return _original_warning(parent, title, text, *args, **kwargs)


def _maybe_critical(parent, title, text, *args, **kwargs):
    if is_suppressed():
        return None
    return _original_critical(parent, title, text, *args, **kwargs)


def _maybe_information(parent, title, text, *args, **kwargs):
    if is_suppressed():
        return None
    return _original_information(parent, title, text, *args, **kwargs)


def _maybe_question(parent, title, text, *args, **kwargs):
    if is_suppressed():
        return QMessageBox.StandardButton.No
    return _original_question(parent, title, text, *args, **kwargs)


def install_patch() -> None:
    """Monkey-patch QMessageBox 静态方法"""
    global _installed
    with _lock:
        if _installed:
            return
        QMessageBox.warning = staticmethod(_maybe_warning)
        QMessageBox.critical = staticmethod(_maybe_critical)
        QMessageBox.information = staticmethod(_maybe_information)
        QMessageBox.question = staticmethod(_maybe_question)
        _installed = True
