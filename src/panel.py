# -*- coding: utf-8 -*-
"""主控制面板：监视 / 推荐 / 桌宠 / 数据 / 设置 五个页签。"""
import os
import re
import time

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, QTimer, Signal, Slot, Q_ARG, QPoint, QRect
from PySide6.QtGui import QFont, QIcon, QPixmap, QImage
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

from . import advisor, animgen, config, datahub, recognize
from .capture import ScreenCapture
from .overlay import PetOverlay

IMAGE_CACHE_DIR = os.path.join(config.DATA_DIR, "board_images")


class BoardRegionDialog(QDialog):
    """棋盘区域校准：框选你的棋盘+备战区（屏幕下方），用于视觉补充日志漏掉的事件卡。"""

    def __init__(self, frame, parent=None):
        super().__init__(parent)
        self.setWindowTitle("棋盘区域校准 — 拖动框选你的棋盘与备战区")
        self.resize(1000, 680)
        self.frame = frame  # BGR numpy
        fh, fw = frame.shape[:2]
        self.scale = min(1.0, 940.0 / fw, 560.0 / fh)
        self.disp_w, self.disp_h = int(fw * self.scale), int(fh * self.scale)
        self.region = None       # 原图坐标 [x0, y0, x1, y1]
        self._drag_start = None  # 显示坐标
        self._drag_cur = None

        lay = QVBoxLayout(self)
        self.lbl_img = QLabel()
        self.lbl_img.setFixedSize(self.disp_w, self.disp_h)
        self.lbl_img.setStyleSheet("border: 1px solid #666; background: #111;")
        self._update_image(None)
        lay.addWidget(self.lbl_img)
        self.lbl_hint = QLabel("提示：在图上按住左键拖动，框住你的棋盘和备战区（屏幕下方那块区域，别框对手的）。")
        lay.addWidget(self.lbl_hint)
        h = QHBoxLayout()
        b_done = QPushButton("保存区域")
        b_done.clicked.connect(self.accept)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        h.addStretch(1)
        h.addWidget(b_done)
        h.addWidget(b_cancel)
        lay.addLayout(h)

    def _update_image(self, cur):
        import cv2
        small = cv2.resize(self.frame, (self.disp_w, self.disp_h), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        if cur and self._drag_start:
            cv2.rectangle(rgb, self._drag_start, cur, (0, 255, 255), 2)
        img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.lbl_img.setPixmap(QPixmap.fromImage(img))

    def mousePressEvent(self, e):
        if not self.lbl_img.geometry().contains(e.position().toPoint()):
            return
        self._drag_start = (int(e.position().x()), int(e.position().y()))
        self._drag_cur = self._drag_start

    def mouseMoveEvent(self, e):
        if self._drag_start:
            self._drag_cur = (int(e.position().x()), int(e.position().y()))
            self._update_image(self._drag_cur)

    def mouseReleaseEvent(self, e):
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = (int(e.position().x()), int(e.position().y()))
        self._drag_start = None
        self._drag_cur = None
        if abs(x1 - x0) < 20 or abs(y1 - y0) < 20:
            self.lbl_hint.setText("框选太小了，请重新拖动。")
            return
        ox0, oy0 = int(min(x0, x1) / self.scale), int(min(y0, y1) / self.scale)
        ox1, oy1 = int(max(x0, x1) / self.scale), int(max(y0, y1) / self.scale)
        self.region = [ox0, oy0, ox1, oy1]
        self._update_image(None)
        fh, fw = self.frame.shape[:2]
        self.lbl_hint.setText(f"已框选 (x {ox0}–{ox1}, y {oy0}–{oy1})（原图 {fw}x{fh}）。可重新拖动调整，点“保存区域”。")


class CalibrationDialog(QDialog):
    """卡牌校准：显示当前屏幕，用户点击每张卡牌，程序自动匹配卡名。"""

    def __init__(self, frame, parent=None):
        super().__init__(parent)
        self.setWindowTitle("卡牌校准 — 依次点击你棋盘上的每张卡牌")
        self.resize(1000, 640)
        self.frame = frame  # BGR numpy
        fh, fw = frame.shape[:2]
        self.scale = min(1.0, 940.0 / fw, 520.0 / fh)
        self.disp_w, self.disp_h = int(fw * self.scale), int(fh * self.scale)

        lay = QVBoxLayout(self)
        self.lbl_img = QLabel()
        self.lbl_img.setFixedSize(self.disp_w, self.disp_h)
        self.lbl_img.setStyleSheet("border: 1px solid #666; background: #111;")
        self._update_image()
        lay.addWidget(self.lbl_img)
        self.lbl_hint = QLabel("提示：点击画面中一张卡牌的中心 → 自动识别卡名。识别错可点“撤销”。完成后点“完成校准”。")
        lay.addWidget(self.lbl_hint)
        self.list = QListWidget()
        self.list.setMaximumHeight(150)
        lay.addWidget(self.list)
        h = QHBoxLayout()
        b_undo = QPushButton("撤销上一张")
        b_undo.clicked.connect(self._undo)
        b_done = QPushButton("完成校准")
        b_done.clicked.connect(self.accept)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        h.addWidget(b_undo)
        h.addStretch(1)
        h.addWidget(b_done)
        h.addWidget(b_cancel)
        lay.addLayout(h)
        self.cards = []  # [{x,y,w,h,name,conf}]

    def _update_image(self):
        import cv2
        small = cv2.resize(self.frame, (self.disp_w, self.disp_h), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.lbl_img.setPixmap(QPixmap.fromImage(img))

    def mousePressEvent(self, e):
        if not self.lbl_img.geometry().contains(e.position().toPoint()):
            return
        # 映射到原图坐标
        px = int(e.position().x() / self.scale)
        py = int(e.position().y() / self.scale)
        fh, fw = self.frame.shape[:2]
        # 以点击为中心取一块卡牌区域
        ch, cw = int(fh * 0.22), int(fw * 0.11)
        x0 = max(0, px - cw // 2)
        y0 = max(0, py - ch // 2)
        x1 = min(fw, x0 + cw)
        y1 = min(fh, y0 + ch)
        card = self.frame[y0:y1, x0:x1]
        hit = recognize.match_card_icon(card)
        if hit:
            name, conf, src = hit
        else:
            name, conf, src = "（未识别）", 0.0, "?"
        cn = datahub.item_cn(name) if name else ""
        self.cards.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "name": name, "conf": conf})
        label = f"#{len(self.cards)}  {cn}（{name}） 置信度 {conf}" if cn != name else f"#{len(self.cards)}  {name} 置信度 {conf}"
        self.list.addItem(label)
        self.lbl_hint.setText(f"已点 {len(self.cards)} 张。继续点击下一张…（错点可撤销）")

    def _undo(self):
        if self.cards:
            self.cards.pop()
            self.list.takeItem(self.list.count() - 1)
            self.lbl_hint.setText(f"已撤销，当前 {len(self.cards)} 张。")


class AnalyzeWorker(QObject):
    """后台分析 worker：解析日志 + 分析流派 + 下载布局图（不阻塞 GUI）。"""
    finished = Signal(dict, dict)     # (rec, build)
    image_ready = Signal(dict)        # {ok, path, link, slug}
    error = Signal(str)

    def __init__(self, build, hero, detected_items):
        super().__init__()
        self.build = build
        self.hero = hero
        self.detected = detected_items

    @Slot()
    def run(self):
        try:
            from . import gamestate
            gs = gamestate.parse_log()
            sockets, stash_sockets = gs.get("board"), gs.get("stash")
            rec = advisor.analyze_build(self.build, self.detected, self.hero,
                                        sockets=sockets, stash_sockets=stash_sockets)
            self.finished.emit(rec, self.build)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def load_image(self):
        """后台加载布局图（网络请求可能耗时）。"""
        try:
            slug = self.build.get("slug")
            ok, path, link = False, "", self.build.get("link") or ""
            if slug:
                os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
                out = os.path.join(IMAGE_CACHE_DIR, f"{slug}.png")
                if os.path.exists(out) and os.path.getsize(out) >= 1000:
                    ok, path = True, out
                else:
                    try:
                        ok = datahub.fetch_build_image(slug, out)
                        if ok:
                            path = out
                    except Exception:
                        ok = False
            self.image_ready.emit({"ok": ok, "path": path, "link": link, "slug": slug})
        except Exception as e:
            self.image_ready.emit({"ok": False, "path": "", "link": self.build.get("link") or "",
                                   "slug": self.build.get("slug"), "error": str(e)})


class MonitorWorker(QObject):
    """监视线程：截屏 -> 识别 -> 推荐。"""
    detected = Signal(dict)
    recommendation = Signal(dict)
    status = Signal(str)
    frame_saved = Signal(str)
    hero_changed = Signal(str)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._running = False
        self._cap = None
        self._hero_handled = None

    def run(self):
        self._running = True
        self._cap = ScreenCapture()
        try:
            self._loop()
        finally:
            self._cap.close()

    def stop(self):
        self._running = False

    def _loop(self):
        last_state = None          # 上次建议的 (build, missing) 状态
        last_advice_time = 0.0     # 上次说话时间
        while self._running:
            t0 = time.time()
            try:
                region = self.cfg.get("capture_region")
                frame = self._cap.grab(region, self.cfg.get("monitor_index", 0))
            except Exception as e:
                self.status.emit(f"截屏失败: {e}")
                self._sleep(2.0)
                continue
            try:
                items = self._detect_on_frame(frame)
            except Exception as e:
                items = {}
                self.status.emit(f"识别失败: {e}")
            # 英雄自动识别：若日志检测到新英雄，自动更新并下载该英雄攻略；同时获取棋盘格位用于摆放建议
            gs = {}
            try:
                from . import gamestate
                gs = gamestate.parse_log()
                hero = gs.get("hero")
                if hero and hero != self._hero_handled:
                    self._hero_handled = hero
                    self.cfg["hero"] = hero
                    config.save_config(self.cfg)
                    self.hero_changed.emit(hero)
                    # 后台抓取该英雄攻略（避免阻塞监视线程/崩溃）
                    if hero in datahub.HEROES and not datahub.get_builds(hero):
                        def _fetch(hero_=hero):
                            try:
                                datahub.fetch_builds(hero_, refresh=True)
                                self.status.emit(f"已自动更新 {hero_} 的攻略数据")
                            except Exception as e:
                                self.status.emit(f"攻略更新失败: {e}")
                        import threading
                        threading.Thread(target=_fetch, daemon=True).start()
            except Exception:
                pass
            self.detected.emit(items)
            try:
                rec = self._compute_rec(items, gs, self.cfg.get("hero", "mak"))
            except Exception as e:
                rec = {"summary": f"推荐失败: {e}", "teach": [], "tips": []}
            self.recommendation.emit(rec)

            # 自动建议：① 推荐状态变化时立即提醒 ② 状态没变但核心件仍缺时定期提醒
            if self.cfg.get("auto_advice", True):
                now = time.time()
                best = rec.get("best")
                state = None
                if best and best.get("missing"):
                    state = (best["build"].get("slug"), tuple(best["core_missing"] or best["missing"][:1]))
                changed = (state != last_state) and state is not None
                nudge = (state is not None and now - last_advice_time >= self.cfg.get("auto_advice_cooldown", 90))
                if changed or nudge:
                    last_state = state
                    last_advice_time = now
                    self._advice_requested.emit(rec["summary"])
            dt = time.time() - t0
            self.status.emit(f"最近一次识别: {len(items)} 件物品, 耗时 {dt:.2f}s")
            self._sleep(max(0.5, self.cfg.get("monitor_interval", 3.0) - dt))

    def _compute_rec(self, items, gs, hero):
        """计算推荐：若用户锁定了流派，则始终以该流派为目标分析；否则自动推荐。"""
        locked = self.cfg.get("locked_build")
        locked_hero = self.cfg.get("locked_hero")
        # 锁定有效：存在锁定、英雄一致、当前英雄攻略可找到该流派
        if locked and locked_hero and locked_hero.lower() == (hero or "").lower():
            builds = datahub.get_builds(hero) or []
            slug = locked.get("slug")
            match = next((b for b in builds if b.get("slug") == slug), None)
            if match is not None:
                return advisor.analyze_build(match, items, hero,
                                             sockets=gs.get("board") if gs else None,
                                             stash_sockets=gs.get("stash") if gs else None)
        return advisor.recommend(items, hero,
                                 sockets=gs.get("board") if gs else None,
                                 stash_sockets=gs.get("stash") if gs else None)

    def _detect_on_frame(self, frame):
        """识别帧内物品：① 游戏日志（100%准确阵容）② 卡牌校准位置 ③ 视觉识别补充。"""
        from . import gamestate
        items = {}
        # 1) 游戏日志（权威）
        try:
            gs = gamestate.parse_log()
            items.update(gamestate.build_detected_items(gs))
        except Exception:
            pass
        # 2) 卡牌校准位置
        calib = self.cfg.get("board_calibration")
        if calib:
            region = self.cfg.get("capture_region")
            ox = region[0] if region else 0
            oy = region[1] if region else 0
            fh, fw = frame.shape[:2]
            for c in calib:
                x0 = int(c["x"] - ox)
                y0 = int(c["y"] - oy)
                x1 = x0 + int(c["w"])
                y1 = y0 + int(c["h"])
                if x0 < 0 or y0 < 0 or x1 > fw or y1 > fh or x1 - x0 < 20 or y1 - y0 < 20:
                    continue
                card = frame[y0:y1, x0:x1]
                hit = recognize.match_card_icon(card)
                if hit:
                    name, conf, src = hit
                    entry = items.setdefault(name, {"count": 0, "positions": [], "source": src})
                    entry["count"] += 1
                    entry["positions"].append((x0 + (x1 - x0) / 2.0, y0 + (y1 - y0) / 2.0, conf))
        # 3) 玩家棋盘区域视觉补充：日志识别不到的事件/技能获得的卡（只扫玩家棋盘区，不碰对手/商店）
        #    仅当棋盘区域已校准且日志已识别到物品时启用，避免把商店/对手的卡误加进来。
        if items and self.cfg.get("board_region"):
            try:
                vis = recognize.detect_player_board(frame, self.cfg)
                for k, v in vis.items():
                    if k not in items:
                        items[k] = v
            except Exception:
                pass
        # 4) 视觉识别补充（仅当日志无数据时；否则视觉会扫到对手/商店的卡造成干扰）
        if not items:
            try:
                vis = recognize.detect_items(frame, self.cfg)
                for k, v in vis.items():
                    items.setdefault(k, v)
            except Exception:
                pass
        return items

    _advice_requested = Signal(str)  # 需要连接后才可 emit（见 __init__ 说明）

    def _sleep(self, s):
        import time
        end = time.time() + s
        while self._running and time.time() < end:
            time.sleep(0.1)


class Panel(QWidget):
    partner_result_ready = Signal(str)
    partner_hot_ready = Signal(str)

    def __init__(self, cfg: dict, overlay: PetOverlay):
        super().__init__()
        self.cfg = cfg
        self.overlay = overlay
        self.worker = None
        self.thread = None
        self.last_frame = None
        self.last_detected = {}

        self.setWindowTitle("🐳 大巴扎小帮手 v0.1")
        self.resize(880, 660)
        self.setMinimumSize(700, 480)   # 允许用户拖动改变大小

        # 顶部装饰横幅（深蓝渐变 + 鲸鱼图标 + 标题）
        header = QWidget(self)
        header.setFixedHeight(64)
        header.setStyleSheet(
            "QWidget { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 #0ea5e9, stop:0.5 #38bdf8, stop:1 #7dd3fc); border-radius: 14px; }")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 6, 16, 6)
        whale_icon = QLabel(header)
        try:
            from PySide6.QtGui import QPixmap
            deco = os.path.join(config.ROOT, "assets", "ui", "deco_whale.png")
            if os.path.exists(deco):
                pm = QPixmap(deco).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                whale_icon.setPixmap(pm)
        except Exception:
            whale_icon.setText("🐳")
        hl.addWidget(whale_icon)
        title_box = QVBoxLayout()
        lbl_title = QLabel("大巴扎小帮手")
        lbl_title.setStyleSheet("color: white; font-size: 17pt; font-weight: bold; border: none; background: transparent;")
        lbl_sub = QLabel("屏幕识别 · 流派推荐 · 摆放指导 · DeepSeek娘桌宠")
        lbl_sub.setStyleSheet("color: rgba(255,255,255,220); font-size: 9pt; border: none; background: transparent;")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        hl.addLayout(title_box)
        hl.addStretch(1)
        bubbles = QLabel(header)
        try:
            deco2 = os.path.join(config.ROOT, "assets", "ui", "deco_bubbles.png")
            if os.path.exists(deco2):
                pm2 = QPixmap(deco2).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                bubbles.setPixmap(pm2)
        except Exception:
            pass
        hl.addWidget(bubbles)

        # 内容区放入滚动区域：窗口较小时可滚动查看，不裁剪
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        tabs = QTabWidget(content)
        self._build_monitor_tab(tabs)
        self._build_recommend_tab(tabs)
        self._build_partner_tab(tabs)
        self._build_pet_tab(tabs)
        self._build_data_tab(tabs)
        self._build_settings_tab(tabs)
        cl = QVBoxLayout(content)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(8)
        cl.addWidget(tabs)
        self._scroll.setWidget(content)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.addWidget(header)
        lay.addWidget(self._scroll)

        # 载入桌宠列表
        self.refresh_pet_list()
        # 桌宠 <-> 面板 双向联动
        overlay.panel = self
        overlay.monitorToggleRequested.connect(self.toggle_monitor)
        overlay.panelRequested.connect(self._show_panel)
        overlay.monitoring = self.worker is not None
        # 若配置了活跃桌宠则加载
        if self.cfg.get("active_pet"):
            self.overlay.set_pet(self.cfg["active_pet"])
        # 恢复锁定状态显示（重启后仍锁定）
        if self.cfg.get("locked_build") and hasattr(self, "btn_unlock"):
            self.btn_unlock.setVisible(True)

    def _show_panel(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _use_pet(self, pid):
        """从桌宠菜单选择形象。"""
        self.cfg["active_pet"] = pid
        config.save_config(self.cfg)
        self.overlay.set_pet(pid)
        self.overlay.show_pet()
        self.refresh_pet_list()

    # ================================================================ 监视页

    def _build_monitor_tab(self, tabs):
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("监视控制")
        f = QFormLayout(g)
        self.btn_start = QPushButton("开始监视")
        self.btn_start.clicked.connect(self.toggle_monitor)
        f.addRow(self.btn_start)
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(1.0, 60.0)
        self.spin_interval.setSingleStep(0.5)
        self.spin_interval.setValue(self.cfg.get("monitor_interval", 3.0))
        f.addRow("识别间隔(秒)", self.spin_interval)
        self.cmb_monitor = QComboBox()
        try:
            for m in ScreenCapture.list_monitors():
                self.cmb_monitor.addItem(f"显示器{m['index']} {m['width']}x{m['height']}", m["index"])
        except Exception:
            self.cmb_monitor.addItem("显示器0", 0)
        f.addRow("捕获屏幕", self.cmb_monitor)
        self.btn_region = QPushButton("全屏捕获")
        self.btn_region.clicked.connect(self._cycle_region)
        f.addRow("捕获区域", self.btn_region)
        self.lbl_status = QLabel("未开始")
        f.addRow("状态", self.lbl_status)
        v.addWidget(g)

        h = QHBoxLayout()
        self.btn_save_frame = QPushButton("保存当前截图")
        self.btn_save_frame.clicked.connect(self.save_frame)
        self.btn_diagnose = QPushButton("导出识别诊断")
        self.btn_diagnose.clicked.connect(self.export_diagnose)
        self.btn_calib = QPushButton("卡牌校准（推荐先做）")
        self.btn_calib.clicked.connect(self.start_calibration)
        self.btn_board_region = QPushButton("框选棋盘区域（补全识别）")
        self.btn_board_region.clicked.connect(self.start_board_region)
        h.addWidget(self.btn_save_frame)
        h.addWidget(self.btn_diagnose)
        h.addWidget(self.btn_calib)
        h.addWidget(self.btn_board_region)
        h.addStretch(1)
        v.addLayout(h)

        v.addWidget(QLabel("识别到的物品（双击条目可让桌宠指出该物品位置）："))
        self.list_items = QListWidget()
        self.list_items.itemDoubleClicked.connect(self._on_item_dblclick)
        v.addWidget(self.list_items)
        tabs.addTab(w, "监视")

    def _cycle_region(self):
        """捕获区域循环：全屏 -> 备战区(棋盘+背包) -> 下半屏(商店)。"""
        region = self.cfg.get("capture_region")
        try:
            m = ScreenCapture.list_monitors()[self.cfg.get("monitor_index", 0)]
        except Exception:
            m = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        if region is None:
            # -> 备战区：上半屏（棋盘 2x5 + 右侧背包）
            h = int(m["height"] * 0.62)
            self.cfg["capture_region"] = [m["left"], m["top"], m["width"], h]
            self.btn_region.setText(f"备战区(棋盘+背包) {m['width']}x{h}")
        elif region[3] > m["height"] * 0.6:
            # 已是备战区 -> 下半屏（商店区域）
            h = int(m["height"] * 0.45)
            self.cfg["capture_region"] = [m["left"], m["top"] + m["height"] - h, m["width"], h]
            self.btn_region.setText(f"下半屏(商店) {m['width']}x{h}")
        else:
            # -> 全屏
            self.cfg["capture_region"] = None
            self.btn_region.setText("全屏捕获")
        config.save_config(self.cfg)

    def _on_item_dblclick(self, item):
        name = item.data(Qt.UserRole) or item.text().split("  ")[0]
        info = self.last_detected.get(name)
        if info and info.get("positions") and self.overlay.isVisible():
            cx, cy, _ = info["positions"][0]
            # 屏幕坐标换算：需要知道捕获区域的偏移
            region = self.cfg.get("capture_region")
            sx = cx + (region[0] if region else 0)
            sy = cy + (region[1] if region else 0)
            QMetaObject.invokeMethod(self.overlay, "point_to", Qt.QueuedConnection,
                                     Q_ARG(int, int(sx)), Q_ARG(int, int(sy)), Q_ARG(str, f"这是 {datahub.item_cn(name)}"))

    # ================================================================ 推荐页

    def _build_recommend_tab(self, tabs):
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("流派选择")
        f = QFormLayout(g)
        self.cmb_hero = QComboBox()
        for h in datahub.HEROES:
            self.cmb_hero.addItem(datahub.HERO_CN.get(h, h), h)
        idx = datahub.HEROES.index(self.cfg.get("hero", "mak")) if self.cfg.get("hero") in datahub.HEROES else 0
        self.cmb_hero.setCurrentIndex(idx)
        self.cmb_hero.currentIndexChanged.connect(self._on_hero_changed)
        f.addRow("当前英雄", self.cmb_hero)

        # 流派搜索：按物品名/标题搜索
        self.edt_search = QLineEdit()
        self.edt_search.setPlaceholderText("输入物品名（如：悠悠球 / yoyo）或流派关键词搜索…")
        self.edt_search.textChanged.connect(self._on_search_changed)
        f.addRow("流派搜索", self.edt_search)

        h_btn = QHBoxLayout()
        self.btn_analyze = QPushButton("立即分析（推荐流派）")
        self.btn_analyze.clicked.connect(lambda: self._refresh_recommendation(force=True))
        self.btn_analyze_sel = QPushButton("分析选中的流派")
        self.btn_analyze_sel.clicked.connect(self._analyze_selected_build)
        self.btn_analyze_sel.setEnabled(False)
        self.btn_unlock = QPushButton("解除锁定（恢复自动推荐）")
        self.btn_unlock.clicked.connect(self.unlock_build)
        self.btn_unlock.setVisible(False)
        h_btn.addWidget(self.btn_analyze)
        h_btn.addWidget(self.btn_analyze_sel)
        h_btn.addWidget(self.btn_unlock)
        f.addRow(h_btn)
        v.addWidget(g)

        # 流派列表（可搜索过滤；单击选中，双击直接分析）
        self.lst_builds = QListWidget()
        self.lst_builds.setWordWrap(True)
        self.lst_builds.itemSelectionChanged.connect(self._on_list_build_selected)
        self.lst_builds.itemDoubleClicked.connect(lambda _it: self._analyze_selected_build())
        self.lst_builds.setMaximumHeight(160)
        v.addWidget(QLabel("流派列表（双击直接分析；单击选中后点下方按钮；顶部搜索可过滤）："))
        v.addWidget(self.lst_builds)

        self.lbl_summary = QLabel("尚未分析")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet("font-weight: bold;")
        v.addWidget(self.lbl_summary)

        self.tbl_builds = QTableWidget(0, 5)
        self.tbl_builds.setHorizontalHeaderLabels(["覆盖", "流派", "组件", "缺失", "备注"])
        self.tbl_builds.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_builds.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_builds.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_builds.itemSelectionChanged.connect(self._on_build_selected)
        self.tbl_builds.setMaximumHeight(150)
        v.addWidget(self.tbl_builds)

        h = QHBoxLayout()
        h.addWidget(QLabel("教学思路："))
        h.addStretch(1)
        self.btn_teach = QPushButton("桌宠讲解")
        self.btn_teach.clicked.connect(self._overlay_teach)
        h.addWidget(self.btn_teach)
        self.btn_swap = QPushButton("查看替换建议")
        self.btn_swap.clicked.connect(self._show_swaps)
        h.addWidget(self.btn_swap)
        v.addLayout(h)

        self.txt_teach = QTextEdit()
        self.txt_teach.setReadOnly(True)
        self.txt_teach.setMaximumHeight(150)
        v.addWidget(self.txt_teach)

        self.lbl_board = QLabel("（点选流派后可加载该流派作者的获胜截图）")
        self.lbl_board.setAlignment(Qt.AlignCenter)
        self.lbl_board.setMinimumHeight(180)
        self.lbl_board.setStyleSheet("border: 1px dashed #aaa; color: #888;")
        v.addWidget(self.lbl_board)

        self._last_rec = None
        self._all_builds_cache = {}   # hero -> [build, ...]
        self._analyze_thread = None
        self._analyze_worker = None
        self._image_thread = None
        self._image_worker = None
        tabs.addTab(w, "推荐")
        self._refresh_build_list()

    def _refresh_build_list(self, filter_text: str = ""):
        """刷新左侧流派列表（按搜索词过滤）。"""
        hero = self.cfg.get("hero", "mak")
        builds = self._all_builds_cache.get(hero)
        if builds is None:
            builds = datahub.get_builds(hero) or []
            self._all_builds_cache[hero] = builds
        self.lst_builds.clear()
        kw = (filter_text or "").strip().lower()
        # 搜索词规范化（去空格/连字符，便于 yoyo<->yo-yo 匹配）
        kw_norm = re.sub(r"[\s\-_]", "", kw)
        items_db = datahub.get_items()
        count = 0
        for b in builds:
            title = b.get("title") or ""
            bitems = b.get("items") or []
            if kw:
                hit = kw in title.lower()
                if not hit:
                    for it in bitems:
                        it_db = items_db.get(it.lower()) or items_db.get(datahub.normalize_name(it))
                        cn = (it_db or {}).get("nameCn") or ""
                        it_norm = re.sub(r"[\s\-_]", "", it.lower())
                        cn_norm = re.sub(r"[\s\-_]", "", cn.lower())
                        if kw_norm and (kw_norm in it_norm or (cn_norm and kw_norm in cn_norm)):
                            hit = True
                            break
                if not hit and kw in ((b.get("type") or "") + " " + " ".join(b.get("types") or [])).lower():
                    hit = True
                if not hit:
                    continue
            items_cn = "、".join(datahub.item_cn(i) for i in bitems[:8])
            label = f"{title}\n    [{items_cn}]" if items_cn else title
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, b)
            self.lst_builds.addItem(item)
            count += 1
        if count == 0:
            it = QListWidgetItem("（没有匹配的流派）")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.lst_builds.addItem(it)

    def _on_search_changed(self, text):
        self._refresh_build_list(text)

    def _on_list_build_selected(self):
        items = self.lst_builds.selectedItems()
        if items:
            self.btn_analyze_sel.setEnabled(True)

    def _analyze_build_direct(self, build, hero=None):
        """直接分析指定流派（供桌宠菜单"流派搜索"调用）。

        分析在后台线程执行（日志解析 + 流派分析 + 布局图下载都不阻塞 GUI），
        界面立即响应，完成后自动更新。"""
        hero = hero or self.cfg.get("hero", "mak")
        # 锁定该流派：后续监视循环始终以它为目标（除非解除）
        self.cfg["locked_build"] = build
        self.cfg["locked_hero"] = hero
        config.save_config(self.cfg)
        if hasattr(self, "btn_unlock"):
            self.btn_unlock.setVisible(True)
        # 界面即时反馈（不阻塞）
        self.lbl_summary.setText(f"正在分析「{build.get('title', '')[:40]}」…")
        self.txt_teach.setText("加载中…")
        self.lbl_board.setText("正在获取流派信息与布局图…")
        self.lbl_board.setStyleSheet("border: 1px dashed #aaa; color: #888;")

        # 清理旧的 worker
        self._cleanup_analyze_worker()

        # 后台分析
        self._analyze_thread = QThread(self)
        self._analyze_worker = AnalyzeWorker(build, hero, dict(self.last_detected))
        self._analyze_worker.moveToThread(self._analyze_thread)
        self._analyze_thread.started.connect(self._analyze_worker.run)
        self._analyze_worker.finished.connect(self._on_analyze_done)
        self._analyze_worker.image_ready.connect(self._on_analyze_image)
        self._analyze_worker.error.connect(self._on_analyze_error)
        self._analyze_worker.finished.connect(self._analyze_thread.quit)
        self._analyze_worker.error.connect(self._analyze_thread.quit)
        self._analyze_thread.finished.connect(self._cleanup_analyze_worker)
        self._analyze_thread.start()

        # 布局图下载同样在后台线程（与分析并行）
        self._image_thread = QThread(self)
        self._image_worker = AnalyzeWorker(build, hero, {})
        self._image_worker.moveToThread(self._image_thread)
        self._image_thread.started.connect(self._image_worker.load_image)
        self._image_worker.image_ready.connect(self._on_analyze_image)
        self._image_thread.finished.connect(self._cleanup_image_worker)
        self._image_thread.start()

        self.show()
        self.raise_()

    def _cleanup_analyze_worker(self):
        for attr in ("_analyze_thread", "_analyze_worker"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.deleteLater()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _cleanup_image_worker(self):
        for attr in ("_image_thread", "_image_worker"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.deleteLater()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _on_analyze_done(self, rec, build):
        """后台分析完成：更新界面。"""
        self._last_rec = rec
        try:
            self.overlay.update_sticky(rec)
        except Exception:
            pass
        if hasattr(self, "lbl_summary"):
            self.lbl_summary.setText(rec.get("summary", ""))
            self.txt_teach.setText("\n".join(rec.get("teach", [])))
            self._fill_build_table(rec)
        title = (build.get("title") or "")[:60]
        QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                 Q_ARG(str, f"已锁定流派「{title}」，接下来按它给你建议～"), Q_ARG(int, 6000))

    def _on_analyze_image(self, info):
        """后台布局图加载完成。"""
        if not hasattr(self, "lbl_board"):
            return
        ok = info.get("ok")
        if ok and info.get("path") and os.path.exists(info["path"]):
            pix = QPixmap(info["path"])
            if not pix.isNull():
                self.lbl_board.setPixmap(pix.scaled(self.lbl_board.width(), self.lbl_board.height(),
                                                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.lbl_board.setStyleSheet("")
                return
        if info.get("slug") is None:
            return  # 无 slug，保持"加载中"文字
        link = info.get("link") or ""
        self.lbl_board.setText(f"无法获取参考图，可在浏览器打开: {link}" if link else "该流派没有布局图。")

    def _on_analyze_error(self, msg):
        if hasattr(self, "lbl_summary"):
            self.lbl_summary.setText(f"分析失败：{msg}")

    def unlock_build(self):
        """解除流派锁定，恢复自动推荐。"""
        self.cfg["locked_build"] = None
        self.cfg["locked_hero"] = None
        config.save_config(self.cfg)
        if hasattr(self, "btn_unlock"):
            self.btn_unlock.setVisible(False)
        self._refresh_recommendation(force=True)
        if hasattr(self, "lbl_summary"):
            self.lbl_summary.setText(self._last_rec.get("summary", "") if self._last_rec else "尚未分析")

    def _analyze_selected_build(self):
        """分析用户自选的流派。"""
        sel = self.lst_builds.selectedItems()
        if not sel:
            return
        build = sel[0].data(Qt.UserRole)
        if not build:
            return
        self._analyze_build_direct(build, self.cfg.get("hero", "mak"))

    def _fill_build_table(self, rec):
        self.tbl_builds.setRowCount(0)
        for s in rec.get("builds", []):
            r = self.tbl_builds.rowCount()
            self.tbl_builds.insertRow(r)
            b = s["build"]
            self.tbl_builds.setItem(r, 0, QTableWidgetItem(f"{s['coverage']*100:.0f}%"))
            self.tbl_builds.setItem(r, 1, QTableWidgetItem(b.get("title", "")))
            have = s.get("have_cn") or s["have"]
            have_disp = "、".join(("✨ " + h if "·" in h else h) for h in have)
            miss = (s.get("missing_cn") or s["missing"])[:4]
            miss_disp = "、".join(("✨ " + m if "·" in m else m) for m in miss)
            self.tbl_builds.setItem(r, 2, QTableWidgetItem(have_disp))
            self.tbl_builds.setItem(r, 3, QTableWidgetItem(miss_disp))
            self.tbl_builds.setItem(r, 4, QTableWidgetItem(s.get("note", "")))

    def _show_build_image(self, build):
        self.lbl_board.setText("加载布局参考图中…")
        self.lbl_board.setStyleSheet("border: 1px dashed #aaa; color: #888;")
        slug = build.get("slug")
        if not slug:
            self.lbl_board.setText("该流派没有布局图。")
            return
        os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
        out = os.path.join(IMAGE_CACHE_DIR, f"{slug}.png")
        ok = False
        if not os.path.exists(out) or os.path.getsize(out) < 1000:
            ok = datahub.fetch_build_image(slug, out)
        else:
            ok = True
        if ok and os.path.exists(out):
            pix = QPixmap(out)
            if not pix.isNull():
                self.lbl_board.setPixmap(pix.scaled(self.lbl_board.width(), self.lbl_board.height(),
                                                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.lbl_board.setStyleSheet("")
            else:
                self.lbl_board.setText(f"参考图加载失败: {build.get('link','')}")
        else:
            self.lbl_board.setText(f"无法获取参考图，可在浏览器打开: {build.get('link','')}")

    def _on_hero_changed(self, idx):
        hero = self.cmb_hero.itemData(idx)
        self.cfg["hero"] = hero
        config.save_config(self.cfg)
        self._refresh_build_list(self.edt_search.text())
        self._refresh_recommendation(force=True)

    def _compute_rec(self, items, gs, hero):
        """计算推荐：若用户锁定了流派，则始终以该流派为目标分析；否则自动推荐。"""
        locked = self.cfg.get("locked_build")
        locked_hero = self.cfg.get("locked_hero")
        if locked and locked_hero and locked_hero.lower() == (hero or "").lower():
            builds = datahub.get_builds(hero) or []
            slug = locked.get("slug")
            match = next((b for b in builds if b.get("slug") == slug), None)
            if match is not None:
                return advisor.analyze_build(match, items, hero,
                                             sockets=gs.get("board") if gs else None,
                                             stash_sockets=gs.get("stash") if gs else None)
        return advisor.recommend(items, hero,
                                 sockets=gs.get("board") if gs else None,
                                 stash_sockets=gs.get("stash") if gs else None)

    def _refresh_recommendation(self, force=False):
        hero = self.cfg.get("hero", "mak")
        builds = datahub.get_builds(hero)
        if not builds:
            self.lbl_summary.setText("该英雄暂无缓存攻略，请到“数据”页更新。")
            return
        try:
            from . import gamestate
            gs = gamestate.parse_log()
            sockets, stash_sockets = gs.get("board"), gs.get("stash")
        except Exception:
            gs, sockets, stash_sockets = {}, None, None
        # 尊重锁定：若锁定了流派，则用锁定流派分析（与监视线程同一逻辑）
        rec = self._compute_rec(self.last_detected, gs, hero)
        self._last_rec = rec
        try:
            self.overlay.update_sticky(rec)
        except Exception:
            pass
        self.lbl_summary.setText(rec.get("summary", ""))
        self.txt_teach.setText("\n".join(rec.get("teach", [])))
        self._fill_build_table(rec)
        # 推荐结果顶部流派默认选中（便于查看布局图）
        if self.tbl_builds.rowCount() > 0:
            self.tbl_builds.selectRow(0)
        self._refresh_build_list(self.edt_search.text() if hasattr(self, "edt_search") else "")

    def _on_build_selected(self):
        row = self.tbl_builds.currentRow()
        if row < 0 or not self._last_rec:
            return
        builds = self._last_rec.get("builds", [])
        if row >= len(builds):
            return
        b = builds[row]["build"]
        self.lbl_board.setText("加载布局参考图中…")
        self.lbl_board.setStyleSheet("border: 1px dashed #aaa; color: #888;")
        slug = b.get("slug")
        if not slug:
            self.lbl_board.setText("该流派没有布局图。")
            return
        # 已在缓存则直接显示，否则后台下载（不阻塞 GUI）
        out = os.path.join(IMAGE_CACHE_DIR, f"{slug}.png")
        if os.path.exists(out) and os.path.getsize(out) >= 1000:
            pix = QPixmap(out)
            if not pix.isNull():
                self.lbl_board.setPixmap(pix.scaled(self.lbl_board.width(), self.lbl_board.height(),
                                                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.lbl_board.setStyleSheet("")
                return
        self._cleanup_image_worker()
        self._image_thread = QThread(self)
        self._image_worker = AnalyzeWorker(b, self.cfg.get("hero", "mak"), {})
        self._image_worker.moveToThread(self._image_thread)
        self._image_thread.started.connect(self._image_worker.load_image)
        self._image_worker.image_ready.connect(self._on_analyze_image)
        self._image_thread.finished.connect(self._cleanup_image_worker)
        self._image_thread.start()

    def _show_swaps(self):
        if not self._last_rec:
            return
        swaps = self._last_rec.get("swaps", [])
        if not swaps:
            QMessageBox.information(self, "替换建议", "当前没有建议替换的物品。")
            return
        lines = "\n".join(f"· {datahub.item_cn(s['item'])}（{s['item']}）：{s['reason']}" for s in swaps[:10])
        QMessageBox.information(self, "替换建议", lines)

    def _show_swaps_msg(self):
        pass

    def _overlay_teach(self):
        if not self._last_rec:
            return
        steps = self._last_rec.get("teach", [])
        if steps:
            text = "\n".join(steps)
            QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                     Q_ARG(str, text[:160]), Q_ARG(int, 9000))

    # ================================================================ 物品搭配分析页

    def _build_partner_tab(self, tabs):
        w = QWidget()
        v = QVBoxLayout(w)

        g = QGroupBox("🔍 物品搭配分析（巴扎丘Bot 天梯统计）")
        f = QFormLayout(g)
        # 输入物品名（支持中文/英文）
        self.edt_partner = QLineEdit()
        self.edt_partner.setPlaceholderText("输入物品名，如：齿轮 / Cog / 悠悠球…")
        self.edt_partner.returnPressed.connect(self._run_partner)
        f.addRow("物品", self.edt_partner)
        # 热门物品快捷标签
        self.lbl_partner_hot = QLabel("热门：")
        f.addRow(self.lbl_partner_hot)
        h_btn = QHBoxLayout()
        self.btn_partner = QPushButton("分析搭配")
        self.btn_partner.clicked.connect(self._run_partner)
        self.btn_partner.setMinimumHeight(30)
        h_btn.addWidget(self.btn_partner)
        h_btn.addStretch(1)
        f.addRow(h_btn)
        v.addWidget(g)

        # 结果区
        self.txt_partner = QTextEdit()
        self.txt_partner.setReadOnly(True)
        self.txt_partner.setMinimumHeight(320)
        v.addWidget(self.txt_partner)
        v.addStretch(1)
        tabs.addTab(w, "搭配分析")
        # 信号连接（后台线程 -> GUI 线程）
        self.partner_result_ready.connect(self._show_partner_result)
        self.partner_hot_ready.connect(self._set_partner_hot)
        # 加载热门物品建议
        self._load_partner_hot()

    def _load_partner_hot(self):
        """从 qiubot suggestions 拉取热门物品，作为快捷标签。"""
        try:
            import threading
            def work():
                try:
                    data = datahub._qiubot_http_get(f"{datahub.QIUBOT_BASE}/api/suggestions")
                    cards = [c["name"] for c in (data.get("cards") or [])][:8]
                    self.partner_hot_ready.emit("、".join(cards))
                except Exception:
                    pass
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def _set_partner_hot(self, text):
        try:
            self.lbl_partner_hot.setText("热门：" + text)
        except Exception:
            pass

    def _run_partner(self):
        """执行物品搭配分析（后台线程，不阻塞界面）。"""
        card = self.edt_partner.text().strip()
        if not card:
            self.txt_partner.setText("请输入物品名。")
            return
        self.txt_partner.setText("分析中…")
        hero = self.cfg.get("hero", "mak")

        import threading
        def work():
            try:
                # 中文名 -> 英文名（qiubot 支持中文名，直接用）
                data = datahub.qiubot_partner(card)
                self.partner_result_ready.emit(self._partner_result_text(data, hero))
            except Exception as e:
                self.partner_result_ready.emit(f"分析失败：{e}")

        threading.Thread(target=work, daemon=True).start()

    def _show_partner_result(self, text):
        self.txt_partner.setText(text)

    def _partner_result_text(self, data: dict, hero: str) -> str:
        """把 partner API 结果格式化为中文可读文本。"""
        import json
        if not isinstance(data, dict):
            return "无数据。"
        if data.get("_error"):
            return data["_error"]
        if data.get("not_found"):
            return f"未找到物品「{data.get('card_name')}」的天梯数据，试试英文名（如 Cog / Yoyo）。"
        card = data.get("card_name") or ""
        total = data.get("target_total") or 0
        lines = [f"📊 「{card}」 搭配分析（天梯 {total} 局）", ""]

        by_appear = data.get("by_appear") or []
        if by_appear:
            lines.append("🤝 最常一同使用的卡（按出现率）：")
            for i, it in enumerate(by_appear[:6]):
                zh = it.get("name") or it.get("name_en") or ""
                en = it.get("name_en") or ""
                rate = (it.get("rate") or 0) * 100
                appear = (it.get("appear_rate") or 0) * 100
                lines.append(f"  {i+1}. {zh}（{en}） 同用率 {appear:.1f}% · 10胜率 {rate:.1f}%")
            lines.append("")

        by_win = data.get("by_winrate") or []
        if by_win:
            lines.append("🏆 一同使用 10 连胜概率最高的卡：")
            for i, it in enumerate(by_win[:6]):
                zh = it.get("name") or it.get("name_en") or ""
                en = it.get("name_en") or ""
                rate = (it.get("rate") or 0) * 100
                ten = it.get("ten_win") or 0
                ttl = it.get("total") or 0
                lines.append(f"  {i+1}. {zh}（{en}） 10胜率 {rate:.1f}%（{ten}/{ttl}）")
        if not lines[1:]:
            lines.append("（该物品暂无搭配数据）")
        return "\n".join(lines)

    # ================================================================ 桌宠页

    def _build_pet_tab(self, tabs):
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("桌宠形象")
        f = QFormLayout(g)
        self.lst_pets = QListWidget()
        self.lst_pets.setIconSize(QPixmap(48, 48).size())
        f.addRow(self.lst_pets)
        h = QHBoxLayout()
        self.btn_upload = QPushButton("上传新形象…")
        self.btn_upload.clicked.connect(self.upload_pet)
        self.btn_use = QPushButton("使用选中形象")
        self.btn_use.clicked.connect(self.use_selected_pet)
        self.btn_delete = QPushButton("删除选中")
        self.btn_delete.clicked.connect(self.delete_selected_pet)
        h.addWidget(self.btn_upload)
        h.addWidget(self.btn_use)
        h.addWidget(self.btn_delete)
        f.addRow(h)
        v.addWidget(g)

        g2 = QGroupBox("显示模式")
        f2 = QFormLayout(g2)
        h_mode = QHBoxLayout()
        self.radio_icon = QCheckBox("静态图标（显示原图，无动画）")
        self.radio_anim = QCheckBox("动画桌宠（自动生成动画）")
        self.radio_icon.setChecked(self.cfg.get("pet_mode", "icon") != "animated")
        self.radio_anim.setChecked(self.cfg.get("pet_mode", "icon") == "animated")
        self.radio_icon.toggled.connect(self._on_mode_toggled)
        h_mode.addWidget(self.radio_icon)
        h_mode.addWidget(self.radio_anim)
        f2.addRow(h_mode)
        self.spin_icon_size = QSpinBox()
        self.spin_icon_size.setRange(48, 320)
        self.spin_icon_size.setValue(int(self.cfg.get("pet_icon_size", 160)))
        self.spin_icon_size.valueChanged.connect(self._on_icon_size_changed)
        f2.addRow("图标大小(像素)", self.spin_icon_size)
        v.addWidget(g2)

        g3 = QGroupBox("动画演示（仅动画模式可用）")
        f3 = QFormLayout(g3)
        h2 = QHBoxLayout()
        self.anim_btns = []
        for name, label in [("startup", "启动"), ("advice", "给出建议"), ("teach", "教学指点"), ("idle", "待机")]:
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, n=name: self._play_pet_anim(n))
            h2.addWidget(b)
            self.anim_btns.append(b)
        f3.addRow(h2)
        self.edt_say = QLineEdit()
        self.edt_say.setPlaceholderText("输入想让桌宠说的话…")
        btn_say = QPushButton("说")
        btn_say.clicked.connect(self._pet_say)
        row = QHBoxLayout()
        row.addWidget(self.edt_say)
        row.addWidget(btn_say)
        f3.addRow(row)
        h3 = QHBoxLayout()
        self.chk_show = QCheckBox("显示桌宠")
        self.chk_show.setChecked(True)
        self.chk_show.toggled.connect(self._toggle_pet_visible)
        self.chk_click = QCheckBox("鼠标穿透")
        self.chk_click.setChecked(self.cfg.get("pet_click_through", True))
        self.chk_click.toggled.connect(self._toggle_click_through)
        h3.addWidget(self.chk_show)
        h3.addWidget(self.chk_click)
        h3.addStretch(1)
        f3.addRow(h3)
        v.addWidget(g3)
        v.addStretch(1)
        tabs.addTab(w, "桌宠")
        self._refresh_anim_btns()

    def _on_mode_toggled(self, checked):
        if self.radio_icon.isChecked():
            mode = "icon"
        else:
            mode = "animated"
        self.cfg["pet_mode"] = mode
        config.save_config(self.cfg)
        self.overlay.set_mode(mode)
        self._refresh_anim_btns()

    def _on_icon_size_changed(self, value):
        self.cfg["pet_icon_size"] = value
        config.save_config(self.cfg)
        QMetaObject.invokeMethod(self.overlay, "set_icon_size", Qt.QueuedConnection, Q_ARG(int, value))

    def _refresh_anim_btns(self):
        enabled = self.cfg.get("pet_mode", "icon") == "animated"
        for b in self.anim_btns:
            b.setEnabled(enabled)

    def refresh_pet_list(self):
        self.lst_pets.clear()
        for pet in animgen.list_pets():
            icon_path = os.path.join(config.PET_ICONS_DIR, f"{pet['id']}.png")
            icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
            it = QListWidgetItem(icon, pet["manifest"].get("name", pet["id"]))
            it.setData(Qt.UserRole, pet["id"])
            self.lst_pets.addItem(it)
            if pet["id"] == self.cfg.get("active_pet"):
                self.lst_pets.setCurrentItem(it)

    def upload_pet(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择桌宠形象图", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif)")
        if not path:
            return
        try:
            import shutil
            config.ensure_dirs()
            pid = os.path.splitext(os.path.basename(path))[0]
            dst = os.path.join(config.PET_INPUT_DIR, os.path.basename(path))
            shutil.copy(path, dst)
            mf = animgen.generate_pet(dst, pet_id=pid, name=pid)
            self.cfg["active_pet"] = pid
            config.save_config(self.cfg)
            self.refresh_pet_list()
            self.overlay.set_pet(pid)
            self.overlay.show_pet()
            QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                     Q_ARG(str, "新形象制作完成！动画已自动生成～"), Q_ARG(int, 4000))
        except Exception as e:
            QMessageBox.critical(self, "上传失败", str(e))

    def use_selected_pet(self):
        it = self.lst_pets.currentItem()
        if not it:
            return
        pid = it.data(Qt.UserRole)
        self.cfg["active_pet"] = pid
        config.save_config(self.cfg)
        self.overlay.set_pet(pid)
        self.overlay.show_pet()

    def delete_selected_pet(self):
        it = self.lst_pets.currentItem()
        if not it:
            return
        pid = it.data(Qt.UserRole)
        if QMessageBox.question(self, "删除", f"确定删除桌宠「{pid}」？") != QMessageBox.Yes:
            return
        import shutil
        shutil.rmtree(os.path.join(config.PET_ANIMS_DIR, pid), ignore_errors=True)
        ic = os.path.join(config.PET_ICONS_DIR, f"{pid}.png")
        if os.path.exists(ic):
            os.remove(ic)
        if self.cfg.get("active_pet") == pid:
            self.cfg["active_pet"] = None
            config.save_config(self.cfg)
        self.refresh_pet_list()

    def _play_pet_anim(self, name):
        self.overlay.show_pet()
        QMetaObject.invokeMethod(self.overlay, "play", Qt.QueuedConnection, Q_ARG(str, name))

    def _pet_say(self):
        text = self.edt_say.text().strip()
        if text:
            QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                     Q_ARG(str, text), Q_ARG(int, 6000))

    def _toggle_pet_visible(self, checked):
        if checked:
            self.overlay.show_pet()
        else:
            self.overlay.hide_pet()

    def _toggle_click_through(self, checked):
        self.overlay.setAttribute(Qt.WA_TransparentForMouseEvents, checked)
        self.cfg["pet_click_through"] = checked
        config.save_config(self.cfg)

    # ================================================================ 数据页

    def _build_data_tab(self, tabs):
        w = QWidget()
        v = QVBoxLayout(w)
        self.lbl_data = QLabel()
        self.lbl_data.setWordWrap(True)
        v.addWidget(self.lbl_data)
        h = QHBoxLayout()
        self.btn_upd_items = QPushButton("更新物品图鉴")
        self.btn_upd_items.clicked.connect(lambda: self._update_data("items"))
        self.btn_upd_builds = QPushButton("更新流派攻略")
        self.btn_upd_builds.clicked.connect(lambda: self._update_data("builds"))
        self.btn_upd_qiubot = QPushButton("更新天梯组合（巴扎丘Bot）")
        self.btn_upd_qiubot.clicked.connect(lambda: self._update_data("qiubot"))
        h.addWidget(self.btn_upd_items)
        h.addWidget(self.btn_upd_builds)
        h.addWidget(self.btn_upd_qiubot)
        v.addLayout(h)
        self.progress = QProgressBar()
        self.progress.hide()
        v.addWidget(self.progress)

        links = QLabel(
            '<a href="https://bazaar-builds.net/category/builds/mak-builds/">攻略站 bazaar-builds.net</a>　'
            '<a href="https://mobalytics.gg/the-bazaar/builds">图鉴站 mobalytics.gg</a>　'
            '<a href="https://howbazaar.gg/items">物品数据 howbazaar.gg</a>　'
            '<a href="https://bazaarqiubot.com">天梯统计 巴扎丘Bot</a>')
        links.setOpenExternalLinks(True)
        v.addWidget(links)
        v.addStretch(1)
        tabs.addTab(w, "数据")
        self._update_data_labels()

    def _update_data_labels(self):
        items = config.load_json(config.ITEMS_PATH)
        builds = config.load_json(config.BUILDS_PATH)
        heroes = config.load_json(config.HEROES_PATH)
        qiubot = config.load_json(datahub.QIUBOT_CACHE_PATH)
        hero = self.cfg.get("hero", "mak")
        n_builds = len(builds.get(hero, [])) if isinstance(builds, dict) else 0
        n_qiubot = len(qiubot) if isinstance(qiubot, dict) else 0
        self.lbl_data.setText(
            f"物品图鉴: {len(items or {})} 件\n"
            f"英雄分类: {len(heroes or {})} 个\n"
            f"当前英雄({hero})攻略: {n_builds} 套\n"
            f"天梯组合(巴扎丘Bot): {n_qiubot} 个英雄\n\n"
            "数据缓存于 data/ 目录。首次运行建议点击更新。")

    def _update_data(self, kind):
        """更新数据（后台执行，不阻塞界面）。kind: items / builds / qiubot。"""
        self.progress.setRange(0, 0)
        self.progress.show()
        self.btn_upd_items.setEnabled(False)
        self.btn_upd_builds.setEnabled(False)
        if hasattr(self, "btn_upd_qiubot"):
            self.btn_upd_qiubot.setEnabled(False)

        def work():
            try:
                if kind == "items":
                    n = len(datahub.fetch_items(refresh=True))
                elif kind == "qiubot":
                    # 刷新当前英雄 + 双龙的天梯组合
                    hero = self.cfg.get("hero", "mak")
                    n1 = len(datahub.qiubot_builds(hero, refresh=True))
                    n2 = len(datahub.qiubot_builds("dragons", refresh=True))
                    n = n1 + n2
                else:
                    n = len(datahub.fetch_builds(self.cfg.get("hero", "mak"), refresh=True))
                return n, None
            except Exception as e:
                return 0, str(e)

        def done(n, err):
            self.progress.hide()
            self.btn_upd_items.setEnabled(True)
            self.btn_upd_builds.setEnabled(True)
            if hasattr(self, "btn_upd_qiubot"):
                self.btn_upd_qiubot.setEnabled(True)
            self._update_data_labels()
            self._refresh_recommendation(force=True)
            if err:
                QMessageBox.critical(self, "更新失败", err)
            else:
                QMessageBox.information(self, "完成", f"更新完成：{n} 条。")

        # 后台线程执行（避免网络请求卡住界面）
        import threading
        th = threading.Thread(target=lambda: QMetaObject.invokeMethod(
            self, "_on_data_done", Qt.QueuedConnection,
            Q_ARG(int, 0), Q_ARG(str, "")), daemon=True)
        # 用简单闭包传递结果
        result = {}

        def runner():
            n, err = work()
            result["n"], result["err"] = n, err
            QMetaObject.invokeMethod(self, "_on_data_done", Qt.QueuedConnection)

        self._data_result = result
        self._data_callback = done
        th = threading.Thread(target=runner, daemon=True)
        th.start()

    def _on_data_done(self):
        """后台数据更新完成回调（GUI 线程）。"""
        result = getattr(self, "_data_result", None) or {}
        cb = getattr(self, "_data_callback", None)
        if cb:
            cb(result.get("n", 0), result.get("err"))

    # ================================================================ 设置页

    def _build_settings_tab(self, tabs):
        w = QWidget()
        v = QVBoxLayout(w)
        f = QFormLayout()
        self.chk_auto = QCheckBox()
        self.chk_auto.setChecked(self.cfg.get("auto_advice", True))
        f.addRow("自动给出建议", self.chk_auto)
        self.chk_sticky = QCheckBox()
        self.chk_sticky.setChecked(self.cfg.get("sticky_enabled", True))
        f.addRow("建议便利贴（悬浮常驻）", self.chk_sticky)
        self.spin_cd = QDoubleSpinBox()
        self.spin_cd.setRange(5, 600)
        self.spin_cd.setValue(self.cfg.get("auto_advice_cooldown", 60))
        f.addRow("建议冷却(秒)", self.spin_cd)
        self.spin_mincov = QDoubleSpinBox()
        self.spin_mincov.setRange(0, 1)
        self.spin_mincov.setSingleStep(0.05)
        self.spin_mincov.setValue(self.cfg.get("min_coverage_trigger", 0.4))
        f.addRow("触发建议的最低覆盖率", self.spin_mincov)
        self.chk_ocr = QCheckBox()
        self.chk_ocr.setChecked(self.cfg.get("ocr_enabled", True))
        f.addRow("OCR 识别", self.chk_ocr)
        self.chk_tmpl = QCheckBox()
        self.chk_tmpl.setChecked(self.cfg.get("template_enabled", True))
        f.addRow("模板匹配", self.chk_tmpl)
        v.addLayout(f)
        btn_save = QPushButton("保存设置")
        btn_save.clicked.connect(self._save_settings)
        v.addWidget(btn_save)
        v.addStretch(1)
        tabs.addTab(w, "设置")

    def _save_settings(self):
        self.cfg["auto_advice"] = self.chk_auto.isChecked()
        self.cfg["sticky_enabled"] = self.chk_sticky.isChecked()
        self.cfg["auto_advice_cooldown"] = self.spin_cd.value()
        self.cfg["min_coverage_trigger"] = self.spin_mincov.value()
        self.cfg["ocr_enabled"] = self.chk_ocr.isChecked()
        self.cfg["template_enabled"] = self.chk_tmpl.isChecked()
        self.cfg["monitor_interval"] = self.spin_interval.value()
        config.save_config(self.cfg)
        if self.chk_sticky.isChecked():
            try:
                self.overlay.update_sticky(self._last_rec)
            except Exception:
                pass
        else:
            try:
                self.overlay.sticky.hide()
            except Exception:
                pass
        QMessageBox.information(self, "已保存", "设置已保存。")

    # ================================================================ 监视线程

    def toggle_monitor(self):
        if self.worker is not None:
            self.worker.stop()
            self.thread.quit()
            self.btn_start.setText("开始监视")
            self.lbl_status.setText("停止中…")
            self.overlay.monitoring = False
            if self.thread.isRunning():
                # 等线程真正结束（首次 OCR 初始化可能耗时数秒）后再清理引用
                self.thread.finished.connect(self._monitor_thread_finished)
            else:
                self._monitor_thread_finished()
            return
        self.cfg["monitor_interval"] = self.spin_interval.value()
        self.cfg["monitor_index"] = self.cmb_monitor.currentData()
        config.save_config(self.cfg)
        self.thread = QThread(self)
        self.worker = MonitorWorker(self.cfg)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        # 自定义信号：自动建议
        adv_signal = self.worker._advice_requested
        adv_signal.connect(self._on_auto_advice)
        self.worker.detected.connect(self._on_detected)
        self.worker.recommendation.connect(self._on_rec)
        self.worker.status.connect(self.lbl_status.setText)
        self.worker.hero_changed.connect(self._on_hero_changed)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()
        self.btn_start.setText("停止监视")
        self.overlay.monitoring = True

    def _on_hero_changed(self, hero):
        """英雄自动识别：更新界面并提示。"""
        idx = datahub.HEROES.index(hero) if hero in datahub.HEROES else 0
        self.cmb_hero.setCurrentIndex(idx)
        self.lbl_status.setText(f"已自动识别英雄：{datahub.HERO_CN.get(hero, hero)}")
        self._refresh_recommendation(force=True)
        QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                 Q_ARG(str, f"已识别到你在玩 {datahub.HERO_CN.get(hero, hero)}，按这个英雄给你建议～"), Q_ARG(int, 5000))

    def _monitor_thread_finished(self):
        self.worker = None
        self.thread = None
        if self.lbl_status.text().startswith("停止中"):
            self.lbl_status.setText("已停止")

    def _on_detected(self, items: dict):
        self.last_detected = items
        self.overlay.last_detected = items
        self.list_items.clear()
        for name, info in sorted(items.items(), key=lambda kv: -kv[1]["count"]):
            pos = f" @{info['positions'][0][0]:.0f},{info['positions'][0][1]:.0f}" if info.get("positions") else ""
            src_label = {"game": "游戏数据", "cardname": "卡面", "orb": "特征", "template": "图标", "ocr": "文字"}.get(
                info.get("source", ""), info.get("source", ""))
            cn = datahub.item_cn(name)
            label = f"{cn}（{name}）  x{info['count']}  [{src_label}]{pos}" if cn != name else f"{name}  x{info['count']}  [{src_label}]{pos}"
            it = QListWidgetItem(label)
            # 附魔物品：✨ + 金色文字高亮（名字含"·"即带附魔前缀，如 致命·Cog）
            if "·" in name:
                label = "✨ " + label
                it = QListWidgetItem(label)
                from PySide6.QtGui import QColor, QBrush
                it.setForeground(QBrush(QColor(176, 120, 20)))  # 附魔金
                it.setData(Qt.UserRole, name)
            else:
                it.setData(Qt.UserRole, name)  # 存英文名用于指点定位
            self.list_items.addItem(it)

    def _on_rec(self, rec: dict):
        self._last_rec = rec
        if self.isVisible():
            self.lbl_summary.setText(rec.get("summary", ""))
            self.txt_teach.setText("\n".join(rec.get("teach", [])))
        # 同步到建议便利贴
        try:
            self.overlay.update_sticky(rec)
        except Exception:
            pass

    def _on_auto_advice(self, text: str):
        if not self.overlay.isVisible():
            self.overlay.show()
        QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                 Q_ARG(str, text[:180]), Q_ARG(int, 8000))

    def start_calibration(self):
        """卡牌校准：显示当前屏幕，用户点击每张卡，保存位置与识别结果。"""
        from .capture import ScreenCapture
        # 校准基于全屏坐标（region=None），确保记录绝对位置
        cap = ScreenCapture()
        try:
            frame = cap.grab(None, self.cfg.get("monitor_index", 0))
        finally:
            cap.close()
        dlg = CalibrationDialog(frame, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.cards:
            self.cfg["board_calibration"] = dlg.cards
            config.save_config(self.cfg)
            # 保存校准裁剪（用于识别不准时的调优）
            try:
                import datetime
                import cv2
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                ddir = os.path.join(config.ROOT, "diagnose", "calibration")
                os.makedirs(ddir, exist_ok=True)
                for i, c in enumerate(dlg.cards):
                    card = frame[c["y"]:c["y"] + c["h"], c["x"]:c["x"] + c["w"]]
                    cv2.imencode(".png", card)[1].tofile(os.path.join(ddir, f"{ts}_card{i}_{c['name'] or 'x'}.png"))
            except Exception:
                pass
            names = [c["name"] for c in dlg.cards if c.get("name") and c.get("name") != "（未识别）"]
            cn_names = "、".join(datahub.item_cn(n) for n in names[:8])
            self.lbl_status.setText(f"校准完成：{len(dlg.cards)} 张卡，识别到：{cn_names}")
            QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                     Q_ARG(str, f"校准完成！识别到 {len(names)} 张卡：{cn_names}"), Q_ARG(int, 8000))
            self._refresh_recommendation(force=True)
        elif dlg.cards:
            QMessageBox.information(self, "校准", "未完成校准，已取消。")

    def start_board_region(self):
        """棋盘区域校准：框选玩家棋盘+备战区，用于视觉补充日志漏掉的事件卡。"""
        from .capture import ScreenCapture
        cap = ScreenCapture()
        try:
            frame = cap.grab(None, self.cfg.get("monitor_index", 0))
        finally:
            cap.close()
        dlg = BoardRegionDialog(frame, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.region:
            x0, y0, x1, y1 = dlg.region
            self.cfg["board_region"] = [x0, y0, x1 - x0, y1 - y0]
            config.save_config(self.cfg)
            self.lbl_status.setText(f"棋盘区域已设置: ({x0},{y0}) {x1-x0}x{y1-y0}")
            QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                     Q_ARG(str, "棋盘区域已保存，之后会用它补全日志漏掉的事件卡～"), Q_ARG(int, 5000))
            self._refresh_recommendation(force=True)
        else:
            QMessageBox.information(self, "校准", "未保存棋盘区域。")

    def clear_calibration(self):
        self.cfg.pop("board_calibration", None)
        config.save_config(self.cfg)
        self.lbl_status.setText("已清除卡牌校准")
        QMetaObject.invokeMethod(self.overlay, "say", Qt.QueuedConnection,
                                 Q_ARG(str, "已清除卡牌校准，改用自动检测"), Q_ARG(int, 4000))

    def save_frame(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存截图", "screenshot.png", "PNG (*.png)")
        if not path:
            return
        from .capture import ScreenCapture
        cap = ScreenCapture()
        try:
            frame = cap.grab(self.cfg.get("capture_region"), self.cfg.get("monitor_index", 0))
            import cv2
            cv2.imwrite(path, frame)
            QMessageBox.information(self, "已保存", f"截图已保存: {path}")
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))
        finally:
            cap.close()

    def export_diagnose(self):
        """导出当前屏幕的 OCR/卡图识别诊断数据（JSON + 标注图），便于调优。"""
        import datetime
        import json
        import cv2
        import numpy as np
        from .capture import ScreenCapture
        diag_dir = os.path.join(config.ROOT, "diagnose")
        os.makedirs(diag_dir, exist_ok=True)
        cap = ScreenCapture()
        try:
            frame = cap.grab(self.cfg.get("capture_region"), self.cfg.get("monitor_index", 0))
            lines = recognize.ocr_frame(frame)
            ocr_items = recognize.match_items_from_ocr(lines, datahub.get_items())
            rects = recognize.yolo_detect_cards(frame) or recognize.detect_cards(frame)
            tmpl_items = recognize.match_cards(frame, rects)
            items = dict(ocr_items)
            items.update(tmpl_items)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            # 每张卡的裁剪 + 卡面 OCR 文本（用于诊断）
            card_crops = []
            for i, (x, y, w, h) in enumerate(rects):
                crop = frame[y:y + h, x:x + w]
                cpath = os.path.join(diag_dir, f"diagnose_{ts}_card{i}.png")
                cv2.imencode(".png", crop)[1].tofile(cpath)
                clines = recognize.ocr_frame(crop)
                card_crops.append({
                    "crop": os.path.basename(cpath),
                    "ocr": [{"text": t, "score": round(float(s), 3)} for t, s, _b in clines],
                })
            payload = {
                "time": ts,
                "region": self.cfg.get("capture_region"),
                "monitor": self.cfg.get("monitor_index", 0),
                "frame": [frame.shape[1], frame.shape[0]],
                "ocr_lines": [{"text": t, "score": round(float(s), 3), "box": b} for t, s, b in lines],
                "cards_detected": [{"x": r[0], "y": r[1], "w": r[2], "h": r[3]} for r in rects],
                "card_ocr": card_crops,
                "template_matches": {k: {"count": v["count"], "positions": v["positions"]} for k, v in tmpl_items.items()},
                "detected": {k: {"count": v["count"], "positions": v["positions"], "source": v.get("source")} for k, v in items.items()},
            }
            jpath = os.path.join(diag_dir, f"diagnose_{ts}.json")
            with open(jpath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            # 标注图：绿框=检测到的卡牌，黄字=识别结果；红框=OCR 文本
            anno = frame.copy()
            for t, s, box in lines:
                pts = np.array(box, dtype=np.int32)
                cv2.polylines(anno, [pts], True, (0, 0, 255), 1)
            anno = recognize.debug_card_view(anno, rects, tmpl_items)
            ppath = os.path.join(diag_dir, f"diagnose_{ts}.png")
            cv2.imencode(".png", anno)[1].tofile(ppath)  # 兼容中文路径
            # 原始帧
            rpath = os.path.join(diag_dir, f"diagnose_{ts}_raw.png")
            cv2.imencode(".png", frame)[1].tofile(rpath)
            QMessageBox.information(
                self, "诊断导出",
                f"已导出：\n{diag_dir}\\diagnose_{ts}.json / .png / _raw.png\n"
                f"（另存了每张检测到的卡牌裁剪 diagnose_{ts}_card*.png）\n\n"
                f"OCR 文本 {len(lines)} 条 | 检测到卡牌 {len(rects)} 张 | 识别出 {len(items)} 件物品\n"
                f"把整个 diagnose 文件夹发给我即可调优。")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
        finally:
            cap.close()

    def quit_app(self):
        """完全退出：停止监视线程 + 关闭面板（由桌宠"退出"调用）。"""
        try:
            if self.worker is not None:
                self.worker.stop()
                self.thread.quit()
                self.thread.wait(5000)  # 缩短等待，避免长时间卡住
                self.worker = None
                self.thread = None
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.stop()
            self.thread.quit()
            # 等待线程真正结束，避免析构时线程仍在运行
            self.thread.wait(5000)
            self.worker = None
            self.thread = None
        # 清理后台分析/图片线程
        for th in (getattr(self, "_analyze_thread", None), getattr(self, "_image_thread", None)):
            if th is not None:
                try:
                    th.quit()
                    th.wait(3000)
                except Exception:
                    pass
        self.cfg["monitor_interval"] = self.spin_interval.value()
        config.save_config(self.cfg)
        super().closeEvent(event)
