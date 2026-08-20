# -*- coding: utf-8 -*-
"""入口：启动桌宠悬浮窗 + 控制面板。"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from src import animgen, config  # noqa: E402
from src.overlay import PetOverlay  # noqa: E402
from src.panel import Panel  # noqa: E402


def main():
    config.ensure_dirs()
    cfg = config.load_config()

    # 首次运行：若无任何桌宠，生成内置示例桌宠
    if not animgen.list_pets():
        try:
            mf = animgen.generate_demo_pet()
            cfg["active_pet"] = mf["id"]
            config.save_config(cfg)
        except Exception as e:
            print("生成示例桌宠失败:", e)

    # 桌宠优先级：DeepSeek娘（内置精选，优先于旧的示例/默认形象）
    if not cfg.get("active_pet") or cfg.get("active_pet") in ("demo_pet", "蓝色大肥鱼"):
        for pid in ("deepseek_girl", "whale_girl"):
            if os.path.isdir(os.path.join(config.PET_ANIMS_DIR, pid)):
                cfg["active_pet"] = pid
                config.save_config(cfg)
                break

    app = QApplication(sys.argv)
    app.setApplicationName("大巴扎小帮手")
    app.setStyleSheet(WHALE_QSS)  # 鲸鱼娘海洋蓝主题
    # 保证中文渲染
    from PySide6.QtGui import QFont, QFontDatabase
    families = set(QFontDatabase.families())
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "Arial"):
        if family in families:
            app.setFont(QFont(family, 10))
            break

    overlay = PetOverlay(cfg)
    if cfg.get("active_pet"):
        overlay.set_pet(cfg["active_pet"])
    overlay.show_pet()

    panel = Panel(cfg, overlay)
    panel.hide()  # 控制面板默认隐藏：所有功能已浓缩进桌宠菜单（点击桌宠即可使用）
    sys.exit(app.exec())


# 鲸鱼娘主题（深海蓝渐变 + 圆角 + 清爽，DeepSeek 蓝白风格）
WHALE_QSS = """
QWidget { font-family: "Microsoft YaHei UI"; font-size: 10pt; color: #1e3a5f; }
QMainWindow, QDialog { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #eaf6ff, stop:1 #d8ecfb); }
QTabWidget::pane { border: 1px solid #a8d4f0; border-radius: 12px; background: #f7fcff; top: -1px; }
QTabBar::tab { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dff0fb, stop:1 #c8e4f7);
               border: 1px solid #a8d4f0; border-radius: 12px 12px 0 0;
               padding: 7px 18px; margin: 3px 3px 0 3px; color: #1e3a5f; }
QTabBar::tab:selected { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #38bdf8, stop:1 #0ea5e9);
                        color: white; font-weight: bold; }
QTabBar::tab:hover:!selected { background: #c2e2f7; }
QPushButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #42c3fb, stop:1 #0ea5e9);
              color: white; border: none; border-radius: 15px; padding: 8px 20px; font-weight: bold; }
QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #38bdf8, stop:1 #0284c7); }
QPushButton:pressed { background: #0284c7; }
QPushButton:disabled { background: #d8e2ea; color: #9db0c0; }
QPushButton#btn_start { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4ade80, stop:1 #22c55e); }
QPushButton#btn_start:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4ade80, stop:1 #16a34a); }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1.5px solid #a8d4f0;
    border-radius: 10px; padding: 5px 10px; selection-background-color: #7dd3fc; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #38bdf8; }
QComboBox::drop-down { border: none; width: 24px; }
QListWidget, QTableWidget, QTextEdit { background: white; border: 1.5px solid #a8d4f0; border-radius: 10px;
    selection-background-color: #bae6fd; selection-color: #0c4a6e; }
QListWidget::item:hover, QTableWidget::item:hover { background: #e8f6ff; }
QTableWidget { gridline-color: #dff1fc; }
QGroupBox { border: 1.5px solid #a8d4f0; border-radius: 12px; margin-top: 12px; padding-top: 10px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f2faff, stop:1 #e9f5fd); }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 8px; color: #0284c7; font-weight: bold; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1.5px solid #a8d4f0; background: white; }
QCheckBox::indicator:hover { border-color: #38bdf8; }
QCheckBox::indicator:checked { background: #38bdf8; border-color: #38bdf8; }
QMenu { background: #f7fcff; border: 1.5px solid #a8d4f0; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 8px 28px; border-radius: 8px; color: #1e3a5f; }
QMenu::item:selected { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4ecfa, stop:1 #bfe0f8); color: #0c4a6e; }
QMenu::item:disabled { color: #b0b8c4; }
QMenu::separator { height: 1px; background: #dff1fc; margin: 4px 10px; }
QHeaderView::section { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4ecfa, stop:1 #c2e2f7);
    border: none; padding: 7px 10px; color: #0c4a6e; font-weight: bold; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #a8d4f0; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #38bdf8; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QMessageBox { background: #f7fcff; }
QLabel { color: #1e3a5f; }
QLabel#lbl_summary { color: #0284c7; font-size: 11pt; font-weight: bold; }
QProgressBar { border: 1px solid #a8d4f0; border-radius: 8px; background: white; text-align: center; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38bdf8, stop:1 #0ea5e9); border-radius: 7px; }
QStatusBar { background: #d8ecfb; }
QToolTip { background: #f7fcff; color: #1e3a5f; border: 1px solid #a8d4f0; border-radius: 6px; padding: 4px; }
"""


if __name__ == "__main__":
    main()
