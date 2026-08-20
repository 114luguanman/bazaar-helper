# -*- coding: utf-8 -*-
"""桌宠悬浮窗：透明、置顶、可穿透点击的桌宠窗口。

- 播放 animgen 生成的 GIF 动画（QMovie）
- 支持“说话”气泡（含小尾巴）
- 支持移动到指定屏幕坐标并播放“教学指点”动画
- 默认鼠标穿透；可切换为可交互（拖拽/右键菜单）
"""
import json
import os

from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Q_ARG, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QVBoxLayout, QWidget

from . import config, datahub

PET_SIZE = 256
BUBBLE_W = 260
BUBBLE_H = 96

ONE_SHOT = ("startup", "teach")


class FramePlayer(QObject):
    """PNG 帧序列播放器（透明保真，替代 QMovie/GIF）。"""

    finished = Signal()

    def __init__(self, label: QLabel, parent=None):
        super().__init__(parent)
        self._label = label
        self._frames = []
        self._idx = 0
        self._loop = True
        self._running = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next)

    def load(self, frame_files, frame_ms, target_size: int = 0):
        self.stop()
        self._frames = []
        for f in frame_files:
            if os.path.exists(f):
                pm = QPixmap(f)
                if not pm.isNull():
                    if target_size and (pm.width() != target_size or pm.height() != target_size):
                        pm = pm.scaled(target_size, target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self._frames.append(pm)
        self._frame_ms = max(16, frame_ms)
        if self._frames:
            self._label.setPixmap(self._frames[0])

    @property
    def has_frames(self):
        return bool(self._frames)

    def play(self, loop=True):
        if not self._frames:
            return
        self._loop = loop
        self._idx = 0
        self._label.setPixmap(self._frames[0])
        self._timer.start(self._frame_ms)
        self._running = True

    def stop(self):
        self._timer.stop()
        self._running = False

    def _next(self):
        self._idx += 1
        if self._idx >= len(self._frames):
            if self._loop:
                self._idx = 0
            else:
                self._idx = len(self._frames) - 1
                self.stop()
                self.finished.emit()
                return
        self._label.setPixmap(self._frames[self._idx])


class BubbleWidget(QWidget):
    """圆角气泡 + 指向下方的小尾巴，自动换行、随文本自动增高。"""

    BUBBLE_W = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_text(self, text: str):
        self._text = text
        f = QFont()
        f.setPointSize(10 if len(text) <= 60 else 9)
        fm = QFontMetrics(f)
        rect = fm.boundingRect(QRect(14, 8, self.BUBBLE_W - 28, 1000),
                               Qt.TextWordWrap, self._text)
        h = max(64, min(220, rect.height() + 22))  # 上限 220，防止超屏
        self.setFixedSize(self.BUBBLE_W, h + 14)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRect(0, 0, self.width(), self.height() - 14)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        tail = QPainterPath()
        tail.moveTo(self.width() // 2 - 10, self.height() - 14)
        tail.lineTo(self.width() // 2, self.height() - 2)
        tail.lineTo(self.width() // 2 + 10, self.height() - 14)
        tail.closeSubpath()
        path = path.united(tail)
        # 深海蓝渐变气泡（与 DeepSeek 品牌蓝呼应）
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(214, 238, 255, 252))
        grad.setColorAt(1.0, QColor(232, 247, 255, 252))
        p.fillPath(path, grad)
        p.setPen(QPen(QColor(56, 132, 205, 230), 2))
        p.drawPath(path)
        # 顶部小装饰线（鲸鱼蓝）
        p.setPen(QPen(QColor(56, 132, 205, 120), 3))
        p.drawLine(18, 6, self.width() - 18, 6)
        p.setPen(QColor(15, 45, 85, 255))
        f = QFont()
        f.setPointSize(10 if len(self._text) <= 60 else 9)
        p.setFont(f)
        p.drawText(QRect(16, 10, self.width() - 32, self.height() - 34),
                   Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, self._text)


class AdviceSticky(QWidget):
    """建议便利贴：独立悬浮窗口，长驻显示当前推荐。

    - 半透明浅蓝圆角卡片，可拖动、可调大小
    - 点击“×”收起；右下角拖动缩放；右键菜单
    - 显示：当前流派 + 缺失组件（含商店/技能建议）
    """

    BTN_W = 22   # 关闭按钮边长

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag = None
        self._press_pos = None
        self._text = ""
        self._resize_edge = None  # 正在拖拽的缩放边: 'se' | 'e' | 's' | None
        self.setMouseTracking(True)
        self._w = int(cfg.get("sticky_w", 340))
        self._h = int(cfg.get("sticky_h", 210))
        self.resize(self._w, self._h)
        pos = cfg.get("sticky_pos")
        if pos:
            self.move(pos[0], pos[1])
        else:
            # 默认贴屏幕右上角
            from PySide6.QtGui import QGuiApplication
            scr = QGuiApplication.primaryScreen()
            geo = scr.availableGeometry() if scr else QRect(0, 0, 1920, 1080)
            self.move(geo.right() - self._w - 30, geo.top() + 30)

    # ---------------- 内容 ----------------

    def set_advice(self, rec: dict):
        """设置建议内容（recommend 返回的 dict）。"""
        lines = []
        best = rec.get("best") if rec else None
        if best:
            b = best["build"]
            title = b.get("title") or ""
            lines.append(f"📌 {title}")
            have_brief = best.get("have_brief") or best.get("have_cn") or []
            if have_brief:
                shown = [("✨ " + hb if "·" in hb else hb) for hb in have_brief[:4]]
                lines.append(f"✅ 已有：{'、'.join(shown)}（{best['coverage']*100:.0f}%）")
            miss_brief = best.get("missing_brief") or best.get("missing_cn") or best.get("missing") or []
            if best.get("core_missing_cn"):
                core = best.get("missing_brief") or []
                # 核心件优先显示（带效果）
                core_brief = [cb for cb in miss_brief if cb.split("（")[0] in best.get("core_missing_cn", [])]
                shown = [("✨ " + cb if "·" in cb else cb) for cb in (core_brief or miss_brief)[:3]]
                lines.append("🎯 优先补：" + "、".join(shown))
            elif miss_brief:
                shown = [("✨ " + cb if "·" in cb else cb) for cb in miss_brief[:4]]
                lines.append("🔧 还需补：" + "、".join(shown))
            else:
                lines.append("🎉 组件已齐！")
        else:
            lines.append(rec.get("summary") or "暂无建议")
        swaps = rec.get("swaps") if rec else []
        if swaps:
            lines.append("🗑 可换：" + "、".join(datahub.item_cn(s["item"]) for s in swaps[:3]))
        # 摆放建议（tips 里含"摆放："前缀的条目）
        tips = rec.get("tips") if rec else []
        p_tips = [t for t in tips if t.startswith("摆放：")][:3]
        if p_tips:
            lines.append("📐 " + "；".join(t.replace("摆放：", "") for t in p_tips))
        text = "\n".join(lines)
        if text != self._text:
            self._text = text
            self.update()
        self.show()
        self.raise_()

    # ---------------- 绘制 ----------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 外阴影（柔和深蓝）
        sh = QRectF(3, 4, w - 6, h - 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 60, 110, 40))
        p.drawRoundedRect(sh, 14, 14)
        # 半透明浅蓝卡片（渐变）
        rect = QRectF(1, 1, w - 2, h - 2)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(222, 240, 255, 240))
        grad.setColorAt(1.0, QColor(205, 230, 252, 240))
        p.setPen(QPen(QColor(70, 150, 220, 220), 1.5))
        p.setBrush(grad)
        p.drawRoundedRect(rect, 14, 14)
        # 标题栏：深蓝渐变条
        head = QRectF(2, 2, w - 4, 28)
        hgrad = QLinearGradient(0, 0, w, 0)
        hgrad.setColorAt(0.0, QColor(14, 165, 233, 235))
        hgrad.setColorAt(1.0, QColor(56, 189, 248, 235))
        p.setPen(Qt.NoPen)
        p.setBrush(hgrad)
        p.drawRoundedRect(head, 12, 12)
        # 标题文字
        p.setPen(QColor(255, 255, 255, 255))
        f = QFont()
        f.setBold(True)
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(QRectF(12, 4, w - 90, 22), Qt.AlignLeft | Qt.AlignVCenter, "🐳 建议便利贴")
        # 关闭按钮（圆形底 + ×，与点击区域一致）
        bx = w - 26
        by = 6
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QRectF(bx, by, self.BTN_W, self.BTN_W))
        p.setPen(QColor(255, 255, 255, 255))
        f2 = QFont()
        f2.setPointSize(10)
        f2.setBold(True)
        p.setFont(f2)
        p.drawText(QRectF(bx, by, self.BTN_W, self.BTN_W), Qt.AlignCenter, "×")
        # 正文（按行渲染，按前缀着色）
        f3 = QFont()
        f3.setPointSize(9)
        fm = QFontMetrics(f3)
        lines = self._text.split("\n")
        y = 38
        wrap_w = w - 24
        line_h = 17
        for ln in lines:
            if not ln.strip():
                y += 4
                continue
            # 类型着色
            if ln.startswith("📌"):
                color = QColor(12, 74, 110, 255)
                fb = QFont(); fb.setBold(True); fb.setPointSize(10)
                p.setFont(fb)
                fm2 = QFontMetrics(fb)
                line_h = 20
            else:
                color = {
                    "✅": QColor(22, 130, 90, 255),
                    "🎯": QColor(200, 90, 30, 255),
                    "🔧": QColor(200, 90, 30, 255),
                    "📐": QColor(30, 90, 190, 255),
                    "🗑": QColor(160, 60, 60, 255),
                    "🎉": QColor(22, 130, 90, 255),
                }.get(ln[:1], QColor(40, 60, 90, 255))
                p.setFont(f3)
                fm2 = fm
                line_h = 17
            p.setPen(color)
            # 按字符宽度换行（适配 emoji）
            wrapped = []
            cur = ""
            for ch in ln:
                if cur and fm2.horizontalAdvance(cur + ch) > wrap_w:
                    wrapped.append(cur)
                    cur = ch
                else:
                    cur += ch
            wrapped.append(cur)
            for wl in wrapped:
                if y > h - 12:
                    break
                p.drawText(QRectF(12, y, wrap_w, line_h + 4), Qt.AlignLeft | Qt.AlignTop, wl)
                y += line_h
            if y > h - 12:
                break
        # 右下角缩放把手
        p.setPen(QPen(QColor(70, 150, 220, 220), 2))
        p.drawLine(w - 16, h - 6, w - 6, h - 16)
        p.drawLine(w - 12, h - 6, w - 6, h - 12)

    # ---------------- 交互 ----------------

    def _close_rect(self):
        return QRect(self.width() - 30, 4, 28, 26)

    def _handle_rect(self):
        return QRect(self.width() - 22, self.height() - 22, 20, 20)

    def _edge_at(self, pos):
        """返回鼠标所在缩放边：右下角/右边缘/下边缘/None。"""
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        if x >= w - 22 and y >= h - 22:
            return "se"
        if x >= w - 8:
            return "e"
        if y >= h - 8:
            return "s"
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._close_rect().contains(e.position().toPoint()):
                self.hide()
                return
            edge = self._edge_at(e.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._drag = e.globalPosition().toPoint()
                return
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = e.globalPosition().toPoint()
        elif e.button() == Qt.RightButton:
            self._show_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            gp = e.globalPosition().toPoint()
            if self._resize_edge:
                if self._resize_edge == "se":
                    nw = max(220, gp.x() - self.x())
                    nh = max(120, gp.y() - self.y())
                elif self._resize_edge == "e":
                    nw = max(220, gp.x() - self.x())
                    nh = self.height()
                else:  # 's'
                    nw = self.width()
                    nh = max(120, gp.y() - self.y())
                self.resize(nw, nh)
                self._w, self._h = nw, nh
                self.cfg["sticky_w"], self.cfg["sticky_h"] = nw, nh
            else:
                self.move(gp - self._drag)
        else:
            pos = e.position().toPoint()
            if self._edge_at(pos):
                self.setCursor(Qt.SizeFDiagCursor if self._edge_at(pos) == "se"
                               else Qt.SizeHorCursor if self._edge_at(pos) == "e"
                               else Qt.SizeVerCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag is not None:
            moved = (e.globalPosition().toPoint() - self._press_pos).manhattanLength() if self._press_pos else 0
            self._drag = None
            self._press_pos = None
            self._resize_edge = None
            try:
                self.cfg["sticky_pos"] = [self.x(), self.y()]
                config.save_config(self.cfg)
            except Exception:
                pass
            if moved < 6 and not self.isVisible():
                self.show()

    def _show_menu(self, pos):
        menu = QMenu(self)
        a = menu.addAction("重新显示")
        a.triggered.connect(self.show)
        a = menu.addAction("默认大小")
        a.triggered.connect(self._reset_size)
        menu.addSeparator()
        a = menu.addAction("隐藏")
        a.triggered.connect(self.hide)
        menu.exec(pos)

    def _reset_size(self):
        self.resize(340, 210)
        self._w, self._h = 340, 210
        self.cfg["sticky_w"], self.cfg["sticky_h"] = 340, 210
        config.save_config(self.cfg)

    def closeEvent(self, event):
        try:
            self.cfg["sticky_pos"] = [self.x(), self.y()]
            config.save_config(self.cfg)
        except Exception:
            pass
        super().closeEvent(event)


class BuildPickSticky(QWidget):
    """流派选择便利贴：搜索后弹出的候选流派卡片（最多 3 个）。

    每个候选显示：流派标题 + 覆盖率 + 命中物品。点击某张卡片即选择该流派。
    选择后通过 build_picked 信号回调（由 PetOverlay 连接后执行分析）。
    """

    build_picked = Signal(object)
    dismissed = Signal()

    CARD_H = 92        # 每个候选卡片高度
    HEADER_H = 30

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._items = []       # [(build, title, cov, have_cn, missing_cn)]
        self._hover = -1
        self.setMouseTracking(True)
        self.setFixedSize(420, 30 + 3 * 92 + 8)
        self.hide()

    def set_results(self, keyword: str, results: list, hero_cn: str = ""):
        """设置搜索结果（最多取前 3 个）并显示。"""
        self._items = []
        for r in results[:3]:
            b = r["build"]
            have = "、".join(r.get("have_cn") or []) or "（无）"
            miss = "、".join(r.get("missing_cn") or []) or "（无）"
            self._items.append({
                "build": b,
                "title": b.get("title") or "",
                "cov": r.get("coverage", 0.0),
                "have": have,
                "miss": miss,
                "hits": "、".join(r.get("item_hits") or []),
            })
        self._keyword = keyword
        self._hero_cn = hero_cn
        self._hover = -1
        h = self.HEADER_H + max(1, len(self._items)) * self.CARD_H + 10
        self.setFixedSize(420, h)
        self.update()
        self.show()
        self.raise_()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 外阴影
        sh = QRectF(3, 4, w - 6, h - 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 60, 110, 40))
        p.drawRoundedRect(sh, 14, 14)
        # 主卡片渐变
        rect = QRectF(1, 1, w - 2, h - 2)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(224, 242, 255, 246))
        grad.setColorAt(1.0, QColor(206, 231, 253, 246))
        p.setPen(QPen(QColor(70, 150, 220, 220), 1.5))
        p.setBrush(grad)
        p.drawRoundedRect(rect, 14, 14)
        # 标题栏
        head = QRectF(2, 2, w - 4, self.HEADER_H - 2)
        hgrad = QLinearGradient(0, 0, w, 0)
        hgrad.setColorAt(0.0, QColor(14, 165, 233, 235))
        hgrad.setColorAt(1.0, QColor(56, 189, 248, 235))
        p.setPen(Qt.NoPen)
        p.setBrush(hgrad)
        p.drawRoundedRect(head, 12, 12)
        p.setPen(QColor(255, 255, 255, 255))
        f = QFont(); f.setBold(True); f.setPointSize(9)
        p.setFont(f)
        title = f"🔍 搜索「{self._keyword}」— 请选择流派" if hasattr(self, "_keyword") else "选择流派"
        p.drawText(QRectF(12, 4, w - 90, self.HEADER_H - 8), Qt.AlignLeft | Qt.AlignVCenter, title)
        # 关闭按钮
        bx, by = w - 26, 6
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QRectF(bx, by, 22, 22))
        p.setPen(QColor(255, 255, 255, 255))
        f2 = QFont(); f2.setPointSize(10); f2.setBold(True)
        p.setFont(f2)
        p.drawText(QRectF(bx, by, 22, 22), Qt.AlignCenter, "×")
        # 候选卡片
        y0 = self.HEADER_H + 4
        for idx, it in enumerate(self._items):
            cy = y0 + idx * self.CARD_H
            card = QRectF(8, cy + 4, w - 16, self.CARD_H - 8)
            if idx == self._hover:
                p.setBrush(QColor(14, 165, 233, 60))
                p.setPen(QPen(QColor(14, 165, 233, 200), 2))
            else:
                p.setBrush(QColor(255, 255, 255, 200))
                p.setPen(QPen(QColor(120, 180, 230, 180), 1.2))
            p.drawRoundedRect(card, 10, 10)
            tx = 16
            ty = card.y() + 8
            # 排名徽标
            badge = QRectF(tx, ty, 20, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(56, 189, 248, 220))
            p.drawEllipse(badge)
            p.setPen(QColor(255, 255, 255, 255))
            f3 = QFont(); f3.setBold(True); f3.setPointSize(9)
            p.setFont(f3)
            p.drawText(badge, Qt.AlignCenter, str(idx + 1))
            # 标题 + 覆盖率
            p.setPen(QColor(12, 74, 110, 255))
            f4 = QFont(); f4.setBold(True); f4.setPointSize(9)
            p.setFont(f4)
            p.drawText(QRectF(tx + 28, ty - 2, w - 150, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       it["title"][:40])
            cov = it["cov"]
            cov_color = QColor(22, 130, 90, 255) if cov >= 0.5 else QColor(200, 120, 30, 255)
            p.setPen(cov_color)
            p.drawText(QRectF(w - 120, ty - 2, 108, 20), Qt.AlignRight | Qt.AlignVCenter,
                       f"覆盖 {cov*100:.0f}%")
            # 命中 + 组件行
            p.setPen(QColor(40, 70, 110, 255))
            f5 = QFont(); f5.setPointSize(8)
            p.setFont(f5)
            p.drawText(QRectF(tx + 28, ty + 20, w - 40, 18), Qt.AlignLeft | Qt.AlignTop,
                       ("命中：" + it["hits"]) if it["hits"] else "·")
            p.setPen(QColor(90, 120, 150, 255))
            miss_short = it["miss"][:44] + ("…" if len(it["miss"]) > 44 else "")
            p.drawText(QRectF(tx + 28, ty + 40, w - 40, 18), Qt.AlignLeft | Qt.AlignTop,
                       f"缺：{miss_short}" if it["miss"] != "（无）" else "组件已齐")
        # 底部提示
        p.setPen(QColor(90, 120, 150, 220))
        f6 = QFont(); f6.setPointSize(8)
        p.setFont(f6)
        p.drawText(QRectF(12, h - 22, w - 24, 18), Qt.AlignCenter,
                   "点击卡片选择流派，桌宠将以此流派为目标提供建议（× 关闭）")

    # ---------------- 交互 ----------------

    def _close_rect(self):
        return QRect(self.width() - 30, 4, 28, 26)

    def _card_index_at(self, pos):
        y0 = self.HEADER_H + 4
        for idx in range(len(self._items)):
            cy = y0 + idx * self.CARD_H
            if QRectF(8, cy + 4, self.width() - 16, self.CARD_H - 8).contains(QPointF(pos.x(), pos.y())):
                return idx
        return -1

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._close_rect().contains(e.position().toPoint()):
                self.hide()
                self.dismissed.emit()
                return
            idx = self._card_index_at(e.position().toPoint())
            if idx >= 0 and idx < len(self._items):
                build = self._items[idx]["build"]
                self.hide()
                self.build_picked.emit(build)

    def mouseMoveEvent(self, e):
        idx = self._card_index_at(e.position().toPoint())
        if idx != self._hover:
            self._hover = idx
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()


class PetOverlay(QWidget):
    """桌宠主窗口。"""

    monitorToggleRequested = Signal()
    panelRequested = Signal()

    def __init__(self, cfg: dict):
        super().__init__(None)
        self.cfg = cfg
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, cfg.get("pet_click_through", True))
        self.setMinimumSize(PET_SIZE, PET_SIZE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.bubble = BubbleWidget(self)
        self.bubble.hide()
        layout.addWidget(self.bubble, alignment=Qt.AlignHCenter)
        self.pet_label = QLabel(self)
        self.pet_label.setFixedSize(PET_SIZE, PET_SIZE)
        self.pet_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.pet_label, alignment=Qt.AlignHCenter)

        self.movie = None
        self.manifest = None
        self.anim_state = "idle"
        self._after_one_shot = "idle"
        self._bubble_timer = None
        self._drag = None
        self.player = FramePlayer(self.pet_label, self)
        self.player.finished.connect(self._on_one_shot_done)
        self.monitoring = False          # 监视状态（由面板更新，用于菜单文案）
        self.last_detected = {}          # 最近识别结果（由面板更新，用于菜单展示）
        self.panel = None                # 控制面板引用（由面板注入）

        pos = cfg.get("pet_pos")
        if pos:
            self.move(pos[0], pos[1])

        # 建议便利贴（独立悬浮窗）
        self.sticky = AdviceSticky(cfg)
        self.sticky.hide()
        # 流派选择便利贴（搜索后弹出）
        self.pick_sticky = BuildPickSticky()
        self.pick_sticky.build_picked.connect(self._on_build_picked)
        self.pick_sticky.dismissed.connect(lambda: None)

    # ---------------- 宠物加载与显示 ----------------

    @Slot(str)
    def set_pet(self, pet_id: str):
        d = os.path.join(config.PET_ANIMS_DIR, pet_id)
        mf_path = os.path.join(d, "manifest.json")
        if not os.path.exists(mf_path):
            return
        with open(mf_path, "r", encoding="utf-8") as f:
            self.manifest = json.load(f)
        if self.cfg.get("pet_mode", "icon") == "icon":
            self._show_icon(pet_id)
        else:
            self.play("idle")

    def _show_icon(self, pet_id: str):
        """静态图标模式：显示原图（带圆角、可调大小），无动画。"""
        self.player.stop()
        source = (self.manifest or {}).get("source") or f"{pet_id}.png"
        candidates = [
            os.path.join(config.PET_INPUT_DIR, source),
            os.path.join(config.PET_INPUT_DIR, f"{pet_id}.png"),
            os.path.join(config.PET_INPUT_DIR, f"{pet_id}.jpg"),
        ]
        path = next((p for p in candidates if os.path.exists(p)), None)
        if not path:
            return
        pm = QPixmap(path)
        if pm.isNull():
            return
        size = int(self.cfg.get("pet_icon_size", 160))
        pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        radius = max(6, int(size * float(self.cfg.get("pet_icon_radius_ratio", 0.15))))
        pm = self._rounded_pixmap(pm, radius)
        self.pet_label.setPixmap(pm)
        self.anim_state = "icon"

    @staticmethod
    def _rounded_pixmap(pm: QPixmap, radius: int) -> QPixmap:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPainterPath
        out = QPixmap(pm.size())
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, pm.width(), pm.height()), radius, radius)
        p.setClipPath(path)
        p.drawPixmap(0, 0, pm)
        p.end()
        return out

    @Slot(int)
    def set_icon_size(self, size: int):
        """调整静态图标大小并立即生效。"""
        size = max(48, min(320, int(size)))
        self.cfg["pet_icon_size"] = size
        config.save_config(self.cfg)
        if self.manifest and self.cfg.get("pet_mode", "icon") == "icon":
            self._show_icon(self.manifest["id"])

    @Slot()
    def set_mode(self, mode: str):
        """切换显示模式: icon | animated。"""
        self.cfg["pet_mode"] = mode
        config.save_config(self.cfg)
        if self.manifest:
            if mode == "icon":
                self._show_icon(self.manifest["id"])
            else:
                self.play("idle")

    @Slot(str)
    def play(self, name: str):
        if self.cfg.get("pet_mode", "icon") == "icon":
            return  # 图标模式：无动画
        if not self.manifest:
            return
        anim = self.manifest.get("anims", {}).get(name)
        if not anim:
            return
        base = os.path.join(config.PET_ANIMS_DIR, self.manifest["id"])
        frames = [os.path.join(base, fn) for fn in anim.get("frames", [])]
        if not frames:
            gif = os.path.join(base, anim.get("file", ""))
            if os.path.exists(gif):
                self._play_legacy_gif(gif, name)
            return
        self.anim_state = name
        self.player.load(frames, anim.get("frame_ms", 83), target_size=PET_SIZE)
        self.player.play(loop=(name == "idle"))

    def _play_legacy_gif(self, gif_path, name):
        """兼容旧版 GIF 动画（无 PNG 帧时）。"""
        from PySide6.QtGui import QMovie
        if self.movie:
            self.movie.stop()
            self.movie.deleteLater()
        self.movie = QMovie(gif_path)
        self.movie.setCacheMode(QMovie.CacheMode.CacheAll)
        self.movie.frameChanged.connect(lambda _f: self.pet_label.setPixmap(self.movie.currentPixmap()))
        self.anim_state = name
        if name in ONE_SHOT:
            self.movie.finished.connect(self._on_one_shot_done)
        self.movie.start()

    def _on_one_shot_done(self):
        self.play(self._after_one_shot)

    # ---------------- 说话与指点 ----------------

    @Slot(str)
    @Slot(str, int)
    def say(self, text: str, duration_ms: int = 6000):
        self.bubble.set_text(text)
        self.bubble.show()
        self.adjustSize()  # 气泡增高时窗口自适应
        if self._bubble_timer:
            self._bubble_timer.stop()
            self._bubble_timer.deleteLater()
        from PySide6.QtCore import QTimer
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self.bubble.hide)
        self._bubble_timer.start(max(3000, duration_ms))
        if self.cfg.get("pet_mode", "icon") != "icon":
            self.play("advice")

    @Slot(int, int, str)
    def point_to(self, screen_x: int, screen_y: int, text: str = ""):
        """把桌宠移动到目标点附近并播放教学动画。"""
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
        # 目标点左侧放置
        x = max(geo.left() + 8, screen_x - PET_SIZE - 20)
        y = max(geo.top() + 8, min(screen_y - PET_SIZE // 2, geo.bottom() - self.height() - 8))
        self.move(x, y)
        self._after_one_shot = "idle"
        if self.cfg.get("pet_mode", "icon") != "icon":
            self.play("teach")
        if text:
            self.say(text, 8000)

    # ---------------- 显示控制 ----------------

    @Slot()
    def show_pet(self):
        self.show()
        if self.cfg.get("pet_mode", "icon") != "icon":
            self.play("startup")
        self.raise_()

    @Slot()
    def hide_pet(self):
        self.hide()

    @Slot()
    def toggle_interactive(self):
        cur = self.testAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not cur)
        self.cfg["pet_click_through"] = not cur
        config.save_config(self.cfg)

    # ---------------- 建议便利贴 ----------------

    @Slot(dict)
    def update_sticky(self, rec: dict):
        """更新建议便利贴内容。若功能关闭或数据为空则隐藏。"""
        if not self.cfg.get("sticky_enabled", True):
            self.sticky.hide()
            return
        if not rec:
            self.sticky.hide()
            return
        self.sticky.set_advice(rec)

    @Slot()
    def toggle_sticky(self):
        self.cfg["sticky_enabled"] = not self.cfg.get("sticky_enabled", True)
        config.save_config(self.cfg)
        if self.cfg["sticky_enabled"]:
            p = getattr(self, "panel", None)
            rec = getattr(p, "_last_rec", None) if p else None
            if rec:
                self.update_sticky(rec)
            else:
                self.sticky.set_advice({"summary": "开始监视后这里会显示建议"})
        else:
            self.sticky.hide()

    def closeEvent(self, event):
        try:
            self.cfg["pet_pos"] = [self.x(), self.y()]
            config.save_config(self.cfg)
        except Exception:
            pass
        super().closeEvent(event)

    # ---------------- 点击菜单与拖拽 ----------------

    def mousePressEvent(self, e):
        if self.testAttribute(Qt.WA_TransparentForMouseEvents):
            return
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = e.globalPosition().toPoint()
        elif e.button() == Qt.RightButton:
            self._show_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag is not None:
            moved = (e.globalPosition().toPoint() - self._press_pos).manhattanLength()
            self._drag = None
            self._press_pos = None
            if moved < 6:  # 原地点击 -> 弹出菜单
                self._show_menu(e.globalPosition().toPoint())
            else:
                try:
                    self.cfg["pet_pos"] = [self.x(), self.y()]
                    config.save_config(self.cfg)
                except Exception:
                    pass

    def _show_menu(self, pos):
        menu = self._build_menu()
        menu.exec(pos)

    def _build_menu(self):
        """构建完整功能菜单（浓缩控制面板全部功能）。"""
        from . import animgen, datahub

        menu = QMenu(self)

        # ---------------- 监视 ----------------
        m_mon = menu.addMenu("监视")
        m_mon.addAction("停止监视" if self.monitoring else "开始监视").triggered.connect(self.monitorToggleRequested.emit)
        m_mon.addAction("打开控制面板").triggered.connect(self.panelRequested.emit)
        m_mon.addSeparator()
        m_mon.addAction("识别间隔：当前 %.1f 秒" % self.cfg.get("monitor_interval", 3.0)).triggered.connect(self._menu_set_interval)
        m_mon.addSeparator()
        m_det = m_mon.addMenu("识别结果（%d 件）" % len(self.last_detected))
        if not self.last_detected:
            it = m_det.addAction("（无，请先开始监视）")
            it.setEnabled(False)
        else:
            for name, info in sorted(self.last_detected.items(), key=lambda kv: -kv[1]["count"])[:15]:
                cn = datahub.item_cn(name)
                label = f"{cn}（{name}） x{info['count']}" if cn != name else f"{name} x{info['count']}"
                if "·" in name:
                    label = "✨ " + label  # 附魔物品
                m_det.addAction(label).triggered.connect(lambda _=False, n=name: self._point_item(n))
        m_mon.addSeparator()
        m_mon.addAction("卡牌校准（点击棋盘上的牌）").triggered.connect(
            lambda: self._panel_call("start_calibration"))
        m_mon.addAction("清除卡牌校准").triggered.connect(
            lambda: self._panel_call("clear_calibration"))
        m_mon.addSeparator()
        m_mon.addAction("导出识别诊断").triggered.connect(self._menu_diagnose)
        m_mon.addAction("保存当前截图").triggered.connect(self._menu_save_frame)

        # ---------------- 推荐 ----------------
        m_rec = menu.addMenu("推荐")
        hero = self.cfg.get("hero", "mak")
        m_hero = m_rec.addMenu("当前英雄：" + datahub.HERO_CN.get(hero, hero))
        for h in datahub.HEROES:
            a = m_hero.addAction(datahub.HERO_CN.get(h, h))
            a.setCheckable(True)
            a.setChecked(h == hero)
            a.triggered.connect(lambda _=False, hh=h: self._menu_set_hero(hh))
        m_rec.addAction("立即分析").triggered.connect(self._menu_analyze)
        m_rec.addAction("流派搜索…").triggered.connect(self._menu_search_builds)
        m_rec.addSeparator()
        # 当前锁定状态
        locked = self.cfg.get("locked_build")
        if locked:
            it = m_rec.addAction("📌 已锁定流派：" + (locked.get("title") or "")[:40])
            it.setEnabled(False)
            a = m_rec.addAction("解除锁定（恢复自动推荐）")
            a.triggered.connect(self._menu_unlock_build)
            m_rec.addSeparator()
        rec = None
        if self.panel is not None and getattr(self.panel, "_last_rec", None):
            rec = self.panel._last_rec
        if rec and rec.get("best"):
            best = rec["best"]
            it = m_rec.addAction("建议：" + (best["build"].get("title") or ""))
            it.setEnabled(False)
            miss = best.get("missing_cn") or best.get("missing") or []
            it = m_rec.addAction("还缺：" + "、".join(miss[:5]) if miss else "组件已齐")
            it.setEnabled(False)
        m_rec.addAction("查看完整建议").triggered.connect(self._menu_show_advice)
        m_rec.addAction("桌宠讲解").triggered.connect(self._menu_teach)
        m_rec.addAction("替换建议").triggered.connect(self._menu_swaps)
        m_rec.addSeparator()
        a = m_rec.addAction("建议便利贴：显示" if self.cfg.get("sticky_enabled", True) else "建议便利贴：隐藏")
        a.triggered.connect(self.toggle_sticky)

        # ---------------- 桌宠 ----------------
        m_pet = menu.addMenu("桌宠")
        a = m_pet.addAction("静态图标（原图圆角）")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("pet_mode", "icon") == "icon")
        a.triggered.connect(lambda ch: self._menu_set_mode("icon" if ch else "animated"))
        a = m_pet.addAction("动画桌宠")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("pet_mode", "icon") == "animated")
        a.triggered.connect(lambda ch: self._menu_set_mode("animated" if ch else "icon"))
        m_pet.addSeparator()
        m_size = m_pet.addMenu("图标大小：%dpx" % int(self.cfg.get("pet_icon_size", 160)))
        for sz in (96, 128, 160, 192, 224, 256, 320):
            a = m_size.addAction(f"{sz} px")
            a.setCheckable(True)
            a.setChecked(int(self.cfg.get("pet_icon_size", 160)) == sz)
            a.triggered.connect(lambda _=False, s=sz: self.set_icon_size(s))
        m_pet.addSeparator()
        m_pet.addAction("上传新形象…").triggered.connect(self._menu_upload)
        m_choose = m_pet.addMenu("选择形象")
        pets = animgen.list_pets()
        if not pets:
            it = m_choose.addAction("（无形象）")
            it.setEnabled(False)
        for p in pets:
            a = m_choose.addAction(p["manifest"].get("name", p["id"]))
            a.setCheckable(True)
            a.setChecked(self.cfg.get("active_pet") == p["id"])
            a.triggered.connect(lambda _=False, pid=p["id"]: self._menu_use_pet(pid))
        m_anim = m_pet.addMenu("测试动画")
        for name, label in [("startup", "启动"), ("advice", "建议"), ("teach", "教学"), ("idle", "待机")]:
            m_anim.addAction(label).triggered.connect(lambda _=False, n=name: self._menu_test_anim(n))
        m_pet.addSeparator()
        m_pet.addAction("说一句话…").triggered.connect(self._menu_say)

        # ---------------- 数据 ----------------
        m_data = menu.addMenu("数据更新")
        m_data.addAction("更新物品图鉴").triggered.connect(lambda: self._panel_call("_update_data", "items"))
        m_data.addAction("更新流派攻略").triggered.connect(lambda: self._panel_call("_update_data", "builds"))

        # ---------------- 设置 ----------------
        m_set = menu.addMenu("设置")
        a = m_set.addAction("自动建议")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("auto_advice", True))
        a.triggered.connect(self._menu_auto_advice)
        a = m_set.addAction("鼠标穿透（不挡游戏操作）")
        a.setCheckable(True)
        a.setChecked(self.testAttribute(Qt.WA_TransparentForMouseEvents))
        a.triggered.connect(self.toggle_interactive)
        m_set.addAction("建议触发阈值：%.0f%%" % (self.cfg.get("min_coverage_trigger", 0.4) * 100)).triggered.connect(self._menu_mincov)

        # ---------------- 支持作者 ----------------
        menu.addSeparator()
        menu.addAction("💖 支持作者（B站充电）").triggered.connect(self._menu_support_author)

        menu.addSeparator()
        menu.addAction("隐藏桌宠").triggered.connect(self.hide_pet)
        menu.addAction("退出").triggered.connect(self.close)
        return menu

    # ---------------- 菜单动作实现 ----------------

    def _panel_call(self, method, *args):
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, method):
            getattr(p, method)(*args)

    def _menu_set_interval(self):
        from PySide6.QtWidgets import QInputDialog
        cur = float(self.cfg.get("monitor_interval", 3.0))
        v, ok = QInputDialog.getDouble(self, "识别间隔", "间隔（秒）：", cur, 1.0, 60.0, 1, 0.5)
        if ok:
            self.cfg["monitor_interval"] = v
            config.save_config(self.cfg)

    def _menu_mincov(self):
        from PySide6.QtWidgets import QInputDialog
        cur = float(self.cfg.get("min_coverage_trigger", 0.4))
        v, ok = QInputDialog.getDouble(self, "建议触发阈值", "覆盖率（0-1）：", cur, 0.0, 1.0, 2, 0.05)
        if ok:
            self.cfg["min_coverage_trigger"] = v
            config.save_config(self.cfg)

    def _menu_support_author(self):
        """支持作者：跳转到作者B站主页（可在主页点击“充电”支持作者）。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        url = self.cfg.get("support_bilibili_url") or "https://space.bilibili.com/383865189"
        self.say("感谢支持！正在打开B站主页，可在页面点击“充电”支持作者～", 6000)
        QDesktopServices.openUrl(QUrl(url))

    def _menu_auto_advice(self, checked):
        self.cfg["auto_advice"] = bool(checked)
        config.save_config(self.cfg)

    def _menu_set_mode(self, mode):
        self.cfg["pet_mode"] = mode
        config.save_config(self.cfg)
        self.set_mode(mode)

    def _menu_set_hero(self, hero):
        self.cfg["hero"] = hero
        config.save_config(self.cfg)
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "_refresh_recommendation"):
            p._refresh_recommendation(force=True)

    def _menu_analyze(self):
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "_refresh_recommendation"):
            p._refresh_recommendation(force=True)
        else:
            self.say("请先打开控制面板更新数据", 4000)

    def _menu_unlock_build(self):
        """解除流派锁定，恢复自动推荐。"""
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "unlock_build"):
            p.unlock_build()
        else:
            self.cfg["locked_build"] = None
            self.cfg["locked_hero"] = None
            config.save_config(self.cfg)
        self.say("已解除流派锁定，恢复自动推荐～", 4000)

    def _menu_search_builds(self):
        """流派搜索：输入关键词 -> 弹出前3个覆盖率高的候选（便利贴）-> 选择后分析。"""
        from PySide6.QtWidgets import QInputDialog
        from . import advisor, gamestate

        hero = self.cfg.get("hero", "mak")
        text, ok = QInputDialog.getText(self, "流派搜索",
                                        f"输入物品名或流派关键词（{datahub.HERO_CN.get(hero, hero)}）：")
        if not ok or not text.strip():
            return
        # 基于当前识别阵容计算覆盖率，取覆盖率高的前 3 个候选
        gs = gamestate.parse_log()
        detected = gamestate.build_detected_items(gs)
        results = advisor.search_builds(text.strip(), hero, detected_items=detected)
        if not results:
            self.say(f"没有找到包含「{text.strip()}」的流派", 4000)
            return
        self._pending_search_hero = hero
        # 定位到桌宠附近显示
        geo = self.frameGeometry()
        self.pick_sticky.move(geo.left(), geo.top() - self.pick_sticky.height() - 10)
        self.pick_sticky.set_results(text.strip(), results,
                                     hero_cn=datahub.HERO_CN.get(hero, hero))

    def _on_build_picked(self, build):
        """玩家在流派选择便利贴中选定流派：锁定该流派为目标并提供建议。"""
        hero = getattr(self, "_pending_search_hero", None) or self.cfg.get("hero", "mak")
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "_analyze_build_direct"):
            # 面板方法会设置锁定、更新便利贴与界面
            p._analyze_build_direct(build, hero)
        else:
            from . import advisor, gamestate
            gs = gamestate.parse_log()
            rec = advisor.analyze_build(build, self.last_detected, hero,
                                        sockets=gs.get("board"), stash_sockets=gs.get("stash"))
            self.cfg["locked_build"] = build
            self.cfg["locked_hero"] = hero
            config.save_config(self.cfg)
            try:
                self.update_sticky(rec)
            except Exception:
                pass
        title = (build.get("title") or "")[:60]
        self.say(f"已锁定流派「{title}」，接下来始终按它给你建议～", 6000)

    def _menu_show_advice(self):
        from PySide6.QtWidgets import QMessageBox
        p = getattr(self, "panel", None)
        rec = getattr(p, "_last_rec", None) if p else None
        if not rec:
            self.say("还没有建议，先开始监视吧", 4000)
            return
        lines = [rec.get("summary", "")]
        lines += ["· " + t for t in rec.get("teach", [])]
        swaps = rec.get("swaps", [])
        if swaps:
            lines.append("替换建议：" + "、".join(datahub.item_cn(s["item"]) for s in swaps[:5]))
        QMessageBox.information(self, "当前建议", "\n".join(lines))

    def _menu_teach(self):
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "_overlay_teach"):
            p._overlay_teach()
        else:
            self.say("先开始监视并等待分析", 4000)

    def _menu_swaps(self):
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "_show_swaps"):
            p._show_swaps()
        else:
            self.say("还没有替换建议", 4000)

    def _menu_diagnose(self):
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "export_diagnose"):
            p.export_diagnose()
        else:
            self.say("需要控制面板", 3000)

    def _menu_save_frame(self):
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "save_frame"):
            p.save_frame()

    def _menu_upload(self):
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "upload_pet"):
            p.upload_pet()

    def _menu_use_pet(self, pid):
        p = getattr(self, "panel", None)
        if p is not None and hasattr(p, "_use_pet"):
            p._use_pet(pid)
        else:
            self.cfg["active_pet"] = pid
            config.save_config(self.cfg)
            self.set_pet(pid)
            self.show_pet()

    def _menu_test_anim(self, name):
        if self.cfg.get("pet_mode", "icon") != "animated":
            self.say("请先在桌宠菜单切换到“动画桌宠”模式", 3000)
            return
        self.play(name)

    def _menu_say(self):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "桌宠说话", "说点什么：")
        if ok and text.strip():
            self.say(text.strip(), 6000)

    def _point_item(self, name):
        from . import datahub
        info = self.last_detected.get(name)
        if not (info and info.get("positions") and self.isVisible()):
            return
        cx, cy, _ = info["positions"][0]
        region = self.cfg.get("capture_region")
        sx = cx + (region[0] if region else 0)
        sy = cy + (region[1] if region else 0)
        QMetaObject.invokeMethod(self, "point_to", Qt.QueuedConnection,
                                 Q_ARG(int, int(sx)), Q_ARG(int, int(sy)), Q_ARG(str, f"这是 {datahub.item_cn(name)}"))
