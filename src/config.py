# -*- coding: utf-8 -*-
"""路径与用户配置管理。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(ROOT, "data")
TEMPLATES_DIR = os.path.join(ROOT, "templates", "items")
PET_INPUT_DIR = os.path.join(ROOT, "assets", "pet", "input")
PET_ANIMS_DIR = os.path.join(ROOT, "assets", "pet", "anims")
PET_ICONS_DIR = os.path.join(ROOT, "assets", "pet", "icons")
TEST_IMAGES_DIR = os.path.join(ROOT, "research", "test_images")

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
ITEMS_PATH = os.path.join(DATA_DIR, "items.json")
BUILDS_PATH = os.path.join(DATA_DIR, "builds.json")
HEROES_PATH = os.path.join(DATA_DIR, "heroes.json")
TAGS_CACHE_PATH = os.path.join(DATA_DIR, "tags_cache.json")
CODEX_CACHE_PATH = os.path.join(DATA_DIR, "codex_cache.json")

DEFAULT_CONFIG = {
    "hero": "mak",                 # 当前游玩英雄 (mak/vanessa/dooley/pygmalien/stelle/jules/karnok)
    "monitor_interval": 3.0,       # 监视间隔(秒)
    "capture_region": None,        # None=全屏; 否则 [left, top, width, height]
    "monitor_index": 0,            # 主显示器下标
    "active_pet": None,            # 当前桌宠 id
    "auto_advice": True,           # 自动弹建议
    "auto_advice_cooldown": 60.0,  # 同一条建议的最小间隔(秒)
    "min_coverage_trigger": 0.4,   # 覆盖率超过该值才弹建议
    "sticky_enabled": True,        # 建议便利贴（悬浮常驻）
    "sticky_w": 340,               # 便利贴宽度
    "sticky_h": 210,               # 便利贴高度
    "locked_build": None,          # 用户自选锁定的流派（build dict）；None=自动推荐
    "locked_hero": None,           # 锁定流派所属英雄
    "ocr_enabled": True,
    "template_enabled": True,
    "pet_mode": "icon",            # 桌宠显示模式: icon=静态图标 | animated=动画桌宠
    "pet_icon_size": 160,          # 静态图标边长(像素)
    "pet_icon_radius_ratio": 0.15, # 圆角半径占边长的比例
    "pet_click_through": False,    # 默认可点击（点击桌宠弹菜单）；游戏中可切换为穿透
    "support_bilibili_url": "https://space.bilibili.com/383865189",  # 作者B站主页（充电支持）
    "bpp_db_path": "",           # BazaarPlusPlus 数据库路径（留空自动探测）
}

_cache = {}


def ensure_dirs():
    for d in (DATA_DIR, TEMPLATES_DIR, PET_INPUT_DIR, PET_ANIMS_DIR, PET_ICONS_DIR, TEST_IMAGES_DIR):
        os.makedirs(d, exist_ok=True)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict):
    ensure_dirs()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_json(path: str, default=None):
    if path in _cache:
        return _cache[path]
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache[path] = data
        return data
    except Exception:
        return default


def invalidate(path: str):
    _cache.pop(path, None)


def resource_path(rel: str) -> str:
    """打包场景下兼容资源路径。"""
    return os.path.join(ROOT, rel)


if __name__ == "__main__":
    ensure_dirs()
    cfg = load_config()
    print("config:", cfg)
    print("ROOT:", ROOT)
