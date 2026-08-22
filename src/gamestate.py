# -*- coding: utf-8 -*-
"""游戏日志解析模块：直接从《大巴扎》的 Player.log 读取真实阵容（100% 准确）。

数据来源:
- C:\\Users\\<user>\\AppData\\LocalLow\\Tempo Storm\\The Bazaar\\Player.log
  - [BoardManager] Card Purchased: InstanceId: itm_xxx - TemplateId<uuid> - Target:PlayerSocket_N
  - [GameSimHandler] Cards Spawned: [itm_xxx [Player] [Hand/Stash] [Socket_N] [Size]
- BazaarHelper state_cache（补全日志轮转丢失的实例映射，若存在）
- 名称映射: BazaarHelper v2_cards_id_to_chinese.json / items_db.json / howbazaar items
"""
import json
import os
import re

from . import config

UUID2NAME = None  # {uuid: 中文名}

# 日志英雄名 -> 应用 key（含新英雄双龙 TheDragons -> dragons）
_HERO_NORM = {
    "mak": "mak", "vanessa": "vanessa", "dooley": "dooley", "pygmalien": "pygmalien",
    "stelle": "stelle", "jules": "jules", "karnok": "karnok",
    "thedragons": "dragons", "dragons": "dragons", "dragon": "dragons",
}


def _normalize_hero(hero: str) -> str:
    """规范化英雄名到应用 key；未知返回原值（调用方防御处理）。"""
    if not hero:
        return hero
    return _HERO_NORM.get(hero.lower(), hero.lower())


def _build_official_cn(out_path: str) -> dict:
    """从游戏文件构建官方 UUID->中文名 表（GameData TranslationKey + zh-CN 翻译）。"""
    import sqlite3
    gd = os.path.expandvars(r"%USERPROFILE%\AppData\LocalLow\Tempo Storm\The Bazaar\prod\cache\GameData.db")
    zh = os.path.expandvars(r"%USERPROFILE%\AppData\LocalLow\Tempo Storm\The Bazaar\prod\cache\translations\zh-CN.bytes")
    result = {"uuid2cn": {}, "uuid2en": {}}
    if not (os.path.exists(gd) and os.path.exists(zh)):
        return result
    try:
        zh_db = sqlite3.connect(zh)
        zh_map = dict(zh_db.execute("SELECT hash, text FROM translation").fetchall())
        zh_db.close()
        gd_db = sqlite3.connect(gd)
        rows = gd_db.execute("SELECT Id, Data FROM cards").fetchall()
        gd_db.close()
        for cid, data in rows:
            if not data:
                continue
            try:
                j = json.loads(data)
            except Exception:
                continue
            tkey = j.get("TranslationKey") or ""
            en = j.get("InternalName") or ""
            if tkey and tkey in zh_map:
                result["uuid2cn"][cid] = zh_map[tkey]
            if en:
                result["uuid2en"][cid] = en
        config.ensure_dirs()
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        config.invalidate(out_path)
    except Exception:
        pass
    return result


def _load_uuid_names() -> dict:
    global UUID2NAME
    if UUID2NAME is not None:
        return UUID2NAME
    out = {}
    # 1) 图鉴 curated 中文名（主）：items.json id -> nameCn
    try:
        for item in (config.load_json(config.ITEMS_PATH) or {}).values():
            iid = item.get("id")
            if iid:
                out[iid] = item.get("nameCn") or item.get("name") or ""
    except Exception:
        pass
    # 2) 官方名补全：仅补图鉴没有的物品；官方撞名（同一中文名对应多个物品）视为占位符，不用
    official_path = os.path.join(config.DATA_DIR, "official_cn.json")
    official = config.load_json(official_path)
    if not official:
        official = _build_official_cn(official_path)
    if official:
        uuid2cn = official.get("uuid2cn") or {}
        uuid2en = official.get("uuid2en") or {}
        from collections import Counter
        cn_count = Counter(uuid2cn.values())
        for u, c in uuid2cn.items():
            if u in out:
                continue  # 图鉴已有
            if cn_count.get(c, 0) <= 1:  # 唯一名才用中文
                out[u] = c
        for u, en in uuid2en.items():
            out.setdefault(u, en)  # 英文兜底（新物品至少可读）
    # 3) BazaarHelper 中文映射（仅填空缺）
    bh_dir = r"D:\BazaarHelper\resources"
    zh_path = os.path.join(bh_dir, "v2_cards_id_to_chinese.json")
    if os.path.exists(zh_path):
        try:
            for _u, _c in json.load(open(zh_path, encoding="utf-8-sig")).items():
                out.setdefault(_u, _c)
        except Exception:
            pass
    UUID2NAME = out
    return out


def default_log_path() -> str:
    return os.path.expandvars(r"%USERPROFILE%\AppData\LocalLow\Tempo Storm\The Bazaar\Player.log")


def parse_log(log_path: str = None) -> dict:
    """解析日志：事件跟踪 → 实时棋盘/背包状态（100%准确）。返回 {board, stash, board_items, all_items}。"""
    log_path = log_path or default_log_path()
    empty = {"board": {}, "stash": {}, "board_items": [], "all_items": [], "source": "game_log", "log_path": log_path}
    if not os.path.exists(log_path):
        return empty
    try:
        text = open(log_path, encoding="utf-8", errors="replace").read()
    except Exception:
        return empty

    names = _load_uuid_names()
    inst2uuid = {}
    for m in re.finditer(r"InstanceId: ([A-Za-z0-9_-]+)\s*-\s*TemplateId([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", text):
        inst2uuid[m.group(1)] = m.group(2)
    # uuid -> 英文 key（items.json 的 name 字段，避免中文撞名）
    uuid2en = {}
    for _item in (config.load_json(config.ITEMS_PATH) or {}).values():
        _iid = _item.get("id")
        if _iid:
            uuid2en.setdefault(_iid, _item.get("name") or "")
    sc_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "BazaarHelper", "state_cache.json")
    if os.path.exists(sc_path):
        try:
            sc = json.load(open(sc_path, encoding="utf-8")).get("inst_to_temp", {})
            for inst, tpl in sc.items():
                if inst.startswith("itm_") and tpl not in inst2uuid.values():
                    inst2uuid.setdefault(inst, tpl)
        except Exception:
            pass

    def resolve(inst):
        uuid = inst2uuid.get(inst, "")
        # 优先英文 key（唯一，可直接匹配攻略词表）；无则中文名
        name = uuid2en.get(uuid) or names.get(uuid) or None
        if not name:
            return None
        # 附魔前缀（来自 BPP 战斗快照；无附魔返回原名）
        try:
            from . import bpp
            return bpp.name_with_enchant(inst, name)
        except Exception:
            return name

    # 英雄识别：最后一次 "Changing EHero to X"
    lines = text.splitlines()
    hero = None
    for l in lines:
        m = re.search(r"Changing EHero to (\w+)", l)
        if m:
            hero = m.group(1).lower()
    # 规范化英雄名（日志原始值 -> 应用 key）
    if hero:
        hero = _normalize_hero(hero)

    # 事件按时间顺序播放：实例 -> (section, socket) 跟踪
    inst_pos = {}   # inst -> (section, socket)
    board, stash = {}, {}

    # 新一局起点：最后一次 run.started / NetMessageRunInitialized / Starting new run。
    # 该行之前的物品事件全部属于上一局，不应残留到当前识别结果。
    # （注意：Changing EHero 在选人界面会多次触发，不适合作为截断点）
    last_run_start = -1
    for idx, l in enumerate(lines):
        if ("run_lifecycle.run.started" in l or "Starting new run" in l
                or "NetMessageRunInitialized" in l):
            last_run_start = idx

    def apply_pos(inst, section, sock, swap=True):
        """把实例放到新位置（先移除旧位置）。swap=True 时处理拖拽交换：
        若目标格已有其他实例，被挤出的实例换到本实例的旧位置（The Bazaar 拖卡即交换）。"""
        old = inst_pos.get(inst)
        # 目标格当前占用者（若存在且不是自己）
        displaced = None
        for other, pos in inst_pos.items():
            if other != inst and pos == (section, sock):
                displaced = other
                break
        if old:
            old_section, old_sock = old
            (board if old_section == "Hand" else stash).pop(old_sock, None)
        if displaced is not None:
            if swap and old:
                # 交换：被挤出的实例放到本实例的旧位置
                inst_pos[displaced] = old
                nm_d = resolve(displaced)
                if nm_d:
                    (board if old[0] == "Hand" else stash)[old[1]] = nm_d
            else:
                # 非交换模式：目标格被占，仅移除占用者（避免双写）
                inst_pos.pop(displaced, None)
        inst_pos[inst] = (section, sock)
        nm = resolve(inst)
        if nm:
            (board if section == "Hand" else stash)[int(sock)] = nm

    # 只处理最后一次新局标记之后的日志（之前的物品事件属于上一局，全部丢弃）
    for l in lines[last_run_start + 1:]:
        # 新局开始（行内再次出现标记）：重置跟踪器（上一局的卡全部清掉）
        if "Starting new run" in l or "NetMessageRunInitialized" in l or "run_lifecycle.run.started" in l:
            inst_pos.clear()
            board.clear()
            stash.clear()
            continue
        # 购买：实例->UUID + 落位
        m = re.search(r"Card Purchased: InstanceId: ([A-Za-z0-9_-]+).*?TemplateId([0-9a-f-]{36}).*?Target:(PlayerSocket_(\d+)|PlayerStorageSocket_(\d+))", l)
        if m:
            inst, uuid, _t, sock_b, sock_s = m.groups()
            inst2uuid.setdefault(inst, uuid)
            sock = int(sock_b) if sock_b else int(sock_s)
            section = "Hand" if sock_b else "Stash"
            apply_pos(inst, section, sock)
            continue
        # 移动：moved card to: [inst [Player] [Hand/Stash] [Socket_N]
        m = re.search(r"moved card to: \[([A-Za-z0-9_-]+) \[Player\] \[(\w+)\] \[Socket_(\d+)\]", l)
        if m:
            inst, section, sock = m.groups()
            apply_pos(inst, section, int(sock))
            continue
        # 移动（简式）：moved card itm_xxx to Socket_N（玩家棋盘）
        m = re.search(r"moved card (itm_[A-Za-z0-9_-]+) to Socket_(\d+)", l)
        if m:
            inst, sock = m.groups()
            apply_pos(inst, "Hand", int(sock))
            continue
        # 移除/出售：删除实例
        m = re.search(r"(?:removed item|Sold Card) (itm_[A-Za-z0-9_-]+)", l)
        if m:
            inst = m.group(1)
            if inst in inst_pos:
                section, sock = inst_pos.pop(inst)
                (board if section == "Hand" else stash).pop(sock, None)
            continue
        # Transformed：物品转化（附魔/升级/变成其他物品）——只转移位置，不继承名字
        # （日志不记录新实例模板，继承旧名可能错误；新实例交由视觉/校准识别）
        m = re.search(r"Transformed: (itm_[A-Za-z0-9_-]+) into: (itm_[A-Za-z0-9_-]+)", l)
        if m:
            old, new = m.groups()
            if old in inst_pos:
                section, sock = inst_pos.pop(old)
                apply_pos(new, section, sock)
            continue
        # Cards Disposed（换天/战斗结束整批弃牌）：删除这些实例
        m = re.search(r"Cards Disposed: (.*)", l)
        if m:
            for inst in re.findall(r"itm_[A-Za-z0-9_-]+", m.group(1)):
                if inst in inst_pos:
                    section, sock = inst_pos.pop(inst)
                    (board if section == "Hand" else stash).pop(sock, None)
            continue
        # 快照：Cards Spawned（玩家 Hand/Stash）——只更新位置，绝不删除；快照是权威，不做交换
        if "Cards Spawned" in l:
            player_entries = list(re.finditer(r"\[(itm_[A-Za-z0-9_-]+)\s+\[Player\]\s+\[(\w+)\]\s+\[Socket_(\d+)\]", l))
            if player_entries:
                for m in player_entries:
                    inst, section, sock = m.groups()
                    apply_pos(inst, section, int(sock), swap=False)

    return {
        "board": board,
        "stash": stash,
        "board_items": [board[k] for k in sorted(board)],
        "all_items": sorted(set(board.values()) | set(stash.values())),
        "hero": hero,
        "source": "game_log",
        "log_path": log_path,
    }


def build_detected_items(state: dict) -> dict:
    """把日志阵容转成识别结果格式 {名称: {count, positions, source}}。"""
    items = {}
    for name in state.get("all_items", []):
        if not name or name == "?":
            continue
        items.setdefault(name, {"count": 0, "positions": [], "source": "game"})
        items[name]["count"] += 1
    return items


if __name__ == "__main__":
    st = parse_log()
    print("棋盘:", st["board"])
    print("背包:", st["stash"])
    print("棋盘物品:", st["board_items"])
