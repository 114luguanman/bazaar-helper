# -*- coding: utf-8 -*-
"""BazaarPlusPlus (BPP) 数据桥接：读取 BPP 的 SQLite 数据库，获取物品附魔信息。

BPP（BepInEx 插件）在每场战斗时把双方完整手牌（含附魔 enchant 字段）写入
bazaarplusplus.db 的 battle_snapshots 表。本模块构建 实例ID -> 附魔 索引，
与游戏日志的实例 ID 关联，实现附魔识别。

路径探测顺序：BPP V5 -> V4 -> 用户配置。
"""
import json
import os
import sqlite3

from . import config

# 附魔英文 -> 中文
ENCHANT_CN = {
    "Obsidian": "黑曜石", "Fiery": "灼热", "Restorative": "复苏", "Icy": "寒冰",
    "Shielded": "护盾", "Turbo": "涡轮", "Radiant": "光辉", "Mossy": "苔藓",
    "Shiny": "闪亮", "Deadly": "致命", "Toxic": "剧毒", "Heavy": "沉重",
}

_STATE = {"index": None, "db_path": None, "mtime": 0}


def find_db() -> str:
    """定位 bazaarplusplus.db。返回路径或 ''。"""
    try:
        cfg_path = config.load_config().get("bpp_db_path") or ""
        if cfg_path and os.path.exists(cfg_path):
            return cfg_path
    except Exception:
        pass
    for ver in ("BazaarPlusPlusV5", "BazaarPlusPlusV4"):
        p = os.path.join(r"N:\SteamLibrary\steamapps\common\The Bazaar", ver, "bazaarplusplus.db")
        if os.path.exists(p):
            return p
    # 通过 BepInEx 配置目录推断
    for ver in ("BazaarPlusPlusV5", "BazaarPlusPlusV4"):
        p = os.path.join(os.path.expandvars(r"%USERPROFILE%"), "AppData", "LocalLow",
                         "Tempo Storm", "The Bazaar", ver, "bazaarplusplus.db")
        if os.path.exists(p):
            return p
    return ""


def _load_index():
    """构建 实例ID -> {name, enchant, tier} 索引（每个实例取最新快照）。"""
    db_path = find_db()
    if not db_path:
        _STATE["index"] = {}
        return _STATE["index"]
    try:
        mtime = os.path.getmtime(db_path)
        if _STATE["index"] is not None and _STATE["db_path"] == db_path and mtime == _STATE["mtime"]:
            return _STATE["index"]
    except Exception:
        pass

    index = {}
    try:
        conn = sqlite3.connect(db_path, timeout=3)
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute("""
            SELECT b.recorded_at_utc, s.player_hand_json
            FROM battle_snapshots s JOIN battles b ON b.battle_id = s.battle_id
            ORDER BY b.recorded_at_utc DESC
        """).fetchall()
        conn.close()
        for _ts, ph in rows:
            if not ph:
                continue
            try:
                data = json.loads(ph)
            except Exception:
                continue
            for it in data.get("items") or []:
                iid = it.get("instance_id")
                if iid and iid not in index:
                    index[iid] = {
                        "name": it.get("name") or "",
                        "enchant": (it.get("enchant") or "").strip(),
                        "tier": it.get("tier") or "",
                    }
    except Exception:
        index = {}
    _STATE["index"] = index
    _STATE["db_path"] = db_path
    try:
        _STATE["mtime"] = os.path.getmtime(db_path)
    except Exception:
        _STATE["mtime"] = 0
    return index


def enchant_of(instance_id: str) -> str:
    """按实例 ID 查询附魔（英文）。无附魔/未知返回 ''。"""
    if not instance_id:
        return ""
    index = _load_index()
    entry = index.get(instance_id)
    return (entry or {}).get("enchant") or ""


def enchant_cn(instance_id: str) -> str:
    """附魔中文名（如 灼热）。无附魔返回 ''。"""
    en = enchant_of(instance_id)
    return ENCHANT_CN.get(en, en) if en else ""


def name_with_enchant(instance_id: str, name: str) -> str:
    """物品名带附魔前缀：如「灼热·Monitor Lizard」。无附魔返回原名。"""
    cn = enchant_cn(instance_id)
    if cn:
        return f"{cn}·{name}"
    return name


if __name__ == "__main__":
    print("BPP DB:", find_db())
    idx = _load_index()
    print("索引实例数:", len(idx))
    en = sum(1 for v in idx.values() if v["enchant"])
    print("附魔实例数:", en)
    for k, v in list(idx.items())[:5]:
        print(f"  {k}: {v}")
