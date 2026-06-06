"""SpinnerWidget — 旋转加载指示器，用于表格中「生成中」状态"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QConicalGradient
from PySide6.QtWidgets import QWidget


class SpinnerWidget(QWidget):
    """旋转加载动画指示器

    以圆锥渐变绘制连续弧线，每 50ms 旋转 30°，平滑循环。
    """

    _ARC_SPAN = 120  # 弧线角度跨度
    _SPEED = 30      # 每 tick 旋转度数
    _INTERVAL = 50   # 毫秒

    def __init__(
        self,
        parent: QWidget | None = None,
        size: int = 22,
        color: str = "#EE0000",
        line_width: int = 3,
    ) -> None:
        super().__init__(parent)
        self._size = size
        self._color = QColor(color)
        self._line_width = line_width
        self._angle = 0

        self.setFixedSize(size + line_width * 2, size + line_width * 2)
        self.setStyleSheet("background: transparent;")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # 不自动启动：由外部显式调用 start()
        # （_StatusCell 会在 status==GENERATING 时调用 spinner.start()）

    # ------------------------------------------------------------------
    # 动画
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        self._angle = (self._angle + self._SPEED) % 360
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(self._INTERVAL)

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self._line_width
        r = (self._size) / 2.0
        cx = self.width() / 2.0
        cy = self.height() / 2.0

        # 底层：浅色圆环
        pen_bg = QPen(QColor(self._color.red(), self._color.green(),
                              self._color.blue(), 40), w)
        pen_bg.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_bg)
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # 上层：彩色渐变弧线
        pen_arc = QPen(self._color, w)
        pen_arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_arc)
        # QPainter.drawArc: startAngle 以 3 点钟方向为 0，逆时针；单位 1/16°
        start = (self._angle * 16)
        span = (self._ARC_SPAN * 16)
        painter.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), start, span)

        painter.end()
