# -*- coding: utf-8 -*-
"""推荐引擎：根据已识别物品 + 攻略库，给出换卡/摆放/教学建议。"""
import os
import re
from collections import Counter

from . import config, datahub

# 物品效果缓存（英文key -> 中文效果简介，含CD），由 tooltips.py 构建
_EFFECTS_CACHE = None


def _effects():
    global _EFFECTS_CACHE
    if _EFFECTS_CACHE is None:
        _EFFECTS_CACHE = config.load_json(os.path.join(config.DATA_DIR, "item_effects.json")) or {}
    return _EFFECTS_CACHE


def _strip_enchant(name: str) -> str:
    """剥离附魔前缀（「致命·Cog」->「Cog」），无前缀返回原名。"""
    if "·" in name:
        _, _, rest = name.partition("·")
        if rest.strip():
            return rest.strip()
    return name


def item_brief(item_en: str) -> str:
    """物品效果简介（中文，含 CD）。无缓存返回空。"""
    eff = _effects()
    base = _strip_enchant(item_en)
    return eff.get(base.lower()) or eff.get(datahub.normalize_name(base)) or ""


def cn_with_brief(item_en: str) -> str:
    """物品中文名 + 效果简介。如「工装短裤（CD 8秒 · 治疗20…）」。

    附魔物品显示为「附魔·中文名」，如「致命·多尔王（CD 24秒…）」。
    """
    cn = datahub.item_cn(item_en)
    brief = item_brief(item_en)
    if brief:
        return f"{cn}（{brief}）"
    return cn

TYPE_CN = {
    "Aquatic Build": "水系", "Burn Build": "灼烧流", "Burst Build": "爆发流",
    "Crit Build": "暴击流", "Freeze Build": "冰冻流", "Poison Build": "中毒流",
    "Control Build": "控制流", "Heal Build": "治疗流", "Shield Build": "护盾流",
    "Ammo Build": "弹药流", "Weapon Build": "武器流", "Combo Build": "连击流",
    "Dragon Build": "龙系", "Core Build": "核心流", "Economy Build": "经济流",
    "Scale Build": "成长流", "Potion Build": "药水流",
}

# 商店分类（用于优先级排序）：主题店 > 尺寸店 > 稀有度店 > 通用店
_THEME_SHOPS = {"Aila", "Ande", "Chronos", "Cobweb", "Colt", "Eli", "Flex", "Freiya",
                "Gaseo", "Hef", "Herma", "Kev's Armory", "Kina", "Knightshade",
                "Mr. Morland", "Nautica", "Orion", "Prospero", "Tatiana", "Tinker",
                "Tok's Clocks", "Barkun", "Midsworth", "Mittel", "Pol", "Quixel",
                "Serafina", "Vanessa", "Mak", "Pygmalien", "Dooley", "Stelle", "Jules", "Karnok"}
_TIER_SHOPS = {"Curio", "Silvia", "Goldie", "Luxe"}
_GENERIC_SHOPS = {"Jay Jay", "Valpak"}

_SHOP_CACHE = None


def _shop_data():
    global _SHOP_CACHE
    if _SHOP_CACHE is None:
        d = config.load_json(os.path.join(config.DATA_DIR, "merchants_map.json")) or {}
        _SHOP_CACHE = (d.get("item_shops") or {}, d.get("shop_cn") or {})
    return _SHOP_CACHE


def shop_advice(item_en: str, top_n: int = 3) -> str:
    """返回某物品的购买商店建议（按优先级），如：武器店·艾拉、大型店·波尔、黄金店·戈尔迪。"""
    item_shops, shop_cn = _shop_data()
    shops = item_shops.get(item_en)
    if not shops:
        return ""

    def rank(sname):
        if sname in _THEME_SHOPS:
            return 0
        if sname in _TIER_SHOPS:
            return 1
        return 2  # 通用店

    ordered = sorted(shops, key=lambda s: (rank(s["name"]), shops.index(s)))
    out = []
    for s in ordered[:top_n]:
        cn = shop_cn.get(s["name"], s["name"])
        out.append(cn)
    return "、".join(out)


_MONSTER_CACHE = None


def _monster_data():
    global _MONSTER_CACHE
    if _MONSTER_CACHE is None:
        _MONSTER_CACHE = config.load_json(os.path.join(config.DATA_DIR, "monsters_skills.json")) or []
    return _MONSTER_CACHE


# 流派类型 -> 技能关键词（用于推荐掉落契合技能的野怪）
_TYPE_SKILL_KW = {
    "灼烧流": ["灼烧", "烈焰", "燃", "火"],
    "冰冻流": ["冰冻", "冻结", "霜", "冰"],
    "中毒流": ["中毒", "毒", "剧毒"],
    "暴击流": ["暴击", "致命"],
    "治疗流": ["治疗", "回血", "生命", "再生"],
    "护盾流": ["护盾", "盾"],
    "弹药流": ["弹药", "装填", "上膛"],
    "武器流": ["武器", "伤害"],
    "控制流": ["减速", "冻结", "眩晕"],
    "药水流": ["药水"],
    "爆发流": ["爆发", "充能", "加速"],
    "水系": ["水", "浸"],
    "龙系": ["龙"],
    "成长流": ["成长", "每次", "开场"],
}


def monster_advice(build_type_cn: str, top_n: int = 2) -> str:
    """按流派类型推荐击杀掉落契合技能的野怪。返回如：击杀「血礁船长」掉「全副武装」"""
    kws = []
    for t, kw in _TYPE_SKILL_KW.items():
        if t in build_type_cn:
            kws.extend(kw)
    if not kws:
        return ""
    monsters = _monster_data()
    hits = []
    for m in monsters:
        mname = m.get("name_cn") or m.get("name_en")
        if not mname:
            continue
        for s in m.get("skills") or []:
            text = (s.get("name_cn") or "") + (s.get("desc") or "")
            if any(k in text for k in kws):
                hits.append((mname, s.get("name_cn") or s.get("name_en"), m.get("level")))
                break
    if not hits:
        return ""
    hits.sort(key=lambda h: h[2] if h[2] else 99)
    parts = []
    for name, skill, lv in hits[:top_n]:
        parts.append(f"击杀「{name}」掉「{skill}」")
    return "；".join(parts)

BOARD_LAYOUT = {
    1: "左上", 2: "左中上", 3: "中上", 4: "右中上", 5: "右上",
    6: "左下", 7: "左中下", 8: "中下", 9: "右中下", 10: "右下",
}


def _type_cn(t: str) -> str:
    if not t:
        return "未知流派"
    for k, v in TYPE_CN.items():
        if k.lower() in t.lower():
            return v
    return t


# 棋盘格位与相邻关系（The Bazaar 玩家棋盘 10 格：Socket_0-4 上排左→右，Socket_5-9 下排左→右）
# 游戏内显示 1-10 号位，日志/解析用 0-9；此处统一 0-based，输出时 +1 显示
_SOCKET_CN = {
    0: "左上", 1: "上中左", 2: "上中", 3: "上中右", 4: "右上",
    5: "左下", 6: "下中左", 7: "下中", 8: "下中右", 9: "右下",
}
_SOCKET_ADJ = {
    0: [1, 5], 1: [0, 2, 6], 2: [1, 3, 7], 3: [2, 4, 8], 4: [3, 9],
    5: [0, 6], 6: [1, 5, 7], 7: [2, 6, 8], 8: [3, 7, 9], 9: [4, 8],
}


def _sock_label(sock: int) -> str:
    """0-based 格位 -> "N号位（位置）"。"""
    return f"{sock + 1}号位（{_SOCKET_CN.get(sock, '')}）"


def placement_advice(have: list, items_db: dict, current_sockets: dict = None, missing: list = None) -> list:
    """根据已拥有组件生成摆放建议（中文）。

    - 相邻增益类物品（adjacent/neighbor）集中放中排
    - 武器/盾牌攻守相邻
    - 缺失组件：给出建议购买后摆放的格位（避开已占用）
    current_sockets: 0-based 格位 -> 物品名（gamestate board）
    返回: [("物品", "建议摆放"), ...]（元素可为纯字符串提示）
    """
    if not have and not missing:
        return []
    meta = {}
    for name in have:
        item = items_db.get(name.lower()) or items_db.get(datahub.normalize_name(name))
        if not item:
            continue
        tags = set(item.get("tags") or [])
        adj = any(re.search(r"\b(adjacent|neighbor)\b", tt, re.I) for tt in (item.get("tooltips") or []))
        meta[name] = {"tags": tags, "adj": adj}
    if not meta and not missing:
        return []

    occupied = set(int(k) for k in (current_sockets or {}).keys())
    adv = []

    def free_socket(prefer):
        """返回第一个空闲格位（优先 prefer 列表，其次任意空位）。0-based。"""
        for s in prefer:
            if s not in occupied:
                return s
        for s in range(10):
            if s not in occupied:
                return s
        return None

    # 1) 相邻增益类物品集中放中排（若建议格已被占，换相邻空闲格）
    adj_items = [n for n, m in meta.items() if m["adj"]]
    if len(adj_items) >= 2:
        s1 = free_socket([2, 7, 1, 3, 6, 8, 0, 4, 5, 9])
        if s1 is not None:
            adv.append((datahub.item_cn(adj_items[0]), f"放 {_sock_label(s1)}"))
            occupied.add(s1)
        s2 = free_socket([7, 2, 6, 8, 1, 3, 0, 4, 5, 9])
        if s2 is not None:
            adv.append((datahub.item_cn(adj_items[1]), f"放 {_sock_label(s2)}，紧贴其下方"))
            occupied.add(s2)
        adv.append("相邻增益件尽量集中在 2/3/4/7/8/9 号位，让彼此吃到加成")
    elif adj_items:
        s1 = free_socket([2, 7, 1, 3, 6, 8, 0, 4, 5, 9])
        if s1 is not None:
            adv.append((datahub.item_cn(adj_items[0]), f"放 {_sock_label(s1)}"))
            occupied.add(s1)

    # 2) 武器与护盾相邻（攻守循环）
    weapons = [n for n, m in meta.items() if "Weapon" in m["tags"]]
    shields = [n for n, m in meta.items() if "Shield" in m["tags"]]
    if weapons and shields:
        s1 = free_socket([3, 8, 2, 4, 7, 9, 0, 1, 5, 6])
        if s1 is not None:
            adv.append((datahub.item_cn(weapons[0]), f"放 {_sock_label(s1)}"))
            occupied.add(s1)
        s2 = free_socket([8, 3, 7, 9, 2, 4, 0, 1, 5, 6])
        if s2 is not None:
            adv.append((datahub.item_cn(shields[0]), f"放 {_sock_label(s2)}，与武器对角相邻"))
            occupied.add(s2)

    # 3) 缺失组件：建议补上后的摆放格位（核心件优先中间）
    if missing:
        mid = free_socket([2, 7, 1, 3, 6, 8, 0, 4, 5, 9])
        if mid is not None:
            adv.append((f"补「{datahub.item_cn(missing[0])}」", f"放 {_sock_label(mid)}"))
            occupied.add(mid)
        for extra in missing[1:3]:
            s = free_socket([7, 2, 6, 8, 1, 3, 0, 4, 5, 9])
            if s is None:
                break
            adv.append((f"补「{datahub.item_cn(extra)}」", f"放 {_sock_label(s)}"))
            occupied.add(s)

    # 4) 已占格位的物品提示其格位（只列当前流派已拥有组件，避免冗余）
    if current_sockets:
        have_norm = {datahub.normalize_name(n) for n in have}
        for sock in sorted(int(k) for k in current_sockets):
            nm = current_sockets[sock]
            if nm and nm not in ("?", "") and datahub.normalize_name(nm) in have_norm:
                adv.append((f"已有「{datahub.item_cn(nm)}」", f"在 {_sock_label(sock)}"))
                if len([a for a in adv if isinstance(a, tuple)]) >= 10:
                    break
    return adv[:10]


def _title_items(build) -> list:
    """流派标题中出现的物品（通常是核心件）。"""
    title = (build.get("title") or "").lower()
    out = []
    for it in build.get("items") or []:
        if re.search(r"\b" + re.escape(it.lower()) + r"\b", title):
            out.append(it)
    return out


def _parse_score_rank(build):
    """胜场优先排序键。"""
    win = build.get("win") or 0
    loss = build.get("loss") or 0
    return win - loss * 0.5, win


# 稀有度排序（从低到高，玩家更容易入手）
_TIER_RANK = {"Bronze": 0, "Silver": 1, "Gold": 2, "Diamond": 3, "Legendary": 4}
# 英雄别名 -> items_db heroes 字段的标准名
_HERO_CANON = {
    "mak": "Mak", "dooley": "Dooley", "vanessa": "Vanessa",
    "pygmalien": "Pygmalien", "stelle": "Stelle", "jules": "Jules", "karnok": "Karnok",
    "dragons": "TheDragons", "thedragons": "TheDragons",
}


def _item_hero_rank(item_en: str, hero: str, items_db) -> int:
    """物品对本英雄的适配度：0=本英雄专属/可用，1=通用或未知，2=其他英雄专属。"""
    if not hero:
        return 1
    item = items_db.get(item_en.lower()) or items_db.get(datahub.normalize_name(item_en))
    if not item:
        return 1
    hs = [str(h) for h in (item.get("heroes") or [])]
    if not hs:
        return 1
    if hero in hs:
        return 0
    if "Common" in hs:
        return 1
    return 2


def _item_sort_key(item_en: str, hero: str, items_db):
    """missing 排序键：(英雄适配, 稀有度从低到高, 英文名)。"""
    hero_rank = _item_hero_rank(item_en, hero, items_db)
    item = items_db.get(item_en.lower()) or items_db.get(datahub.normalize_name(item_en))
    tier = _TIER_RANK.get((item or {}).get("startingTier"), 2)  # 未知默认 Gold 档
    return (hero_rank, tier, item_en)


def _hero_note(item_en: str, hero: str, items_db) -> str:
    """非本英雄专属物品的提示后缀。"""
    if _item_hero_rank(item_en, hero, items_db) == 2:
        return "（非本英雄专属，慎选）"
    return ""


def recommend(detected_items: dict, hero: str = "mak", builds=None, top_n: int = 5,
              sockets: dict = None, stash_sockets: dict = None) -> dict:
    """detected_items: recognize.detect_items 的结果 {名称: {count, positions}}。

    sockets: 棋盘格位 -> 物品名（gamestate 的 board，键为 int socket）
    stash_sockets: 备战区格位 -> 物品名（gamestate 的 stash）

    返回:
    {
      builds: [{build, coverage, have, missing, core_missing, note}],
      best: {...},
      swaps: [{item, reason}],
      tips: [str],
      teach: [str],        # 教学步骤
      summary: str,
    }
    """
    builds = builds if builds is not None else datahub.get_builds(hero)
    if not builds:
        return {"builds": [], "best": None, "swaps": [], "tips": [], "teach": [], "summary": "暂无攻略数据，请先在“数据”页更新。"}

    items_db = datahub.get_items()
    # 中文名 -> 英文 key 映射（日志/校准返回中文名，攻略库是英文 key）
    cn2en = {}
    for _k, _it in items_db.items():
        _cn = (_it.get("nameCn") or "").strip()
        if _cn:
            cn2en.setdefault(_cn, _k)
            cn2en.setdefault(_it.get("name") or "", _k)

    def to_en(name: str) -> str:
        """把检测名统一成 items_db 的英文 key：已是英文返回本身（规范化），中文查映射。

        兼容附魔前缀（如「灼热·Cog」）：剥离「中文·」前缀后取真实物品名。
        """
        base = name
        if "·" in name:
            # 附魔前缀是中文+·，真实物品名在其后（可能英文或中文）
            _, _, rest = name.partition("·")
            if rest.strip():
                base = rest.strip()
        if re.search(r"[\u4e00-\u9fff]", base):
            return cn2en.get(base, base)
        norm = datahub.normalize_name(base)
        if norm in items_db:
            return norm
        return base

    # 阵容识别：优先使用游戏日志/卡面识别等权威来源
    authoritative = {k: v for k, v in detected_items.items() if v.get("source") in ("game", "cardname")}
    if authoritative:
        detected_items = authoritative
    # 防御：只保留词表内的真实物品（排除攻略站杂项/未知来源）。
    # 附魔物品保留原名作显示（如「护盾·Cog」），剥离附魔前缀后作匹配 key。
    normalized = {}          # display_name -> info（附魔名保留）
    display_to_key = {}      # display_name -> en_key（匹配用）
    for k, v in detected_items.items():
        en = to_en(k)
        if en in items_db or datahub.normalize_name(en) in items_db:
            normalized[k] = v
            display_to_key[k] = en if en in items_db else datahub.normalize_name(en)
    detected_items = normalized
    detected_names = set(detected_items.keys())

    def is_real_item(name: str) -> bool:
        """判断攻略库物品名是否为真实游戏物品（有 id/尺寸/英雄归属，排除 buildsite 杂项）。"""
        it = items_db.get(name.lower()) or items_db.get(datahub.normalize_name(name))
        if not it:
            return False
        # 攻略站脏数据：无 id 无 size 无 heroes，如 'bird'/'wrist'
        if it.get("id") or it.get("size") or (it.get("heroes") or []) or it.get("nameCn"):
            return True
        return False

    def cn(n):  # 中文名
        return datahub.item_cn(n)

    hero_canon = _HERO_CANON.get((hero or "").lower(), hero or "")
    # 规范化匹配集合（build items 与检测名可能大小写不同）
    # 附魔物品用剥离附魔后的 en_key 匹配（display_to_key 的值）
    detected_norm = {datahub.normalize_name(display_to_key.get(n, n)) for n in detected_names}
    # 反向映射：en_key -> 检测到的附魔原名（如 'cog' -> '护盾·Cog'）
    key_to_display = {}
    for dn in detected_names:
        k = display_to_key.get(dn, dn)
        key_to_display.setdefault(datahub.normalize_name(k), dn)

    def display_of(build_item: str) -> str:
        """build 物品名 -> 检测到的附魔原名（无附魔返回原英文名）。"""
        return key_to_display.get(datahub.normalize_name(build_item), build_item)

    scored = []
    for b in builds:
        bitems = [i for i in (b.get("items") or []) if is_real_item(i)]
        if not bitems:
            continue
        have = [i for i in bitems if datahub.normalize_name(i) in detected_norm]
        missing = [i for i in bitems if datahub.normalize_name(i) not in detected_norm]
        # 缺失件排序：优先本英雄物品，稀有度从低到高（玩家更容易补上）
        missing = sorted(missing, key=lambda i: _item_sort_key(i, hero_canon, items_db))
        coverage = len(have) / len(bitems)
        core_missing = sorted([i for i in missing if i in _title_items(b)],
                              key=lambda i: _item_sort_key(i, hero_canon, items_db))
        # 统计其他英雄专属的缺失件（无法/很难获得，应大幅降权）
        foreign_missing = [i for i in missing if _item_hero_rank(i, hero_canon, items_db) == 2]
        foreign_core = [i for i in core_missing if _item_hero_rank(i, hero_canon, items_db) == 2]
        scored.append({
            "build": b,
            "coverage": round(coverage, 2),
            "have": have,
            "missing": missing,
            "core_missing": core_missing,
            "foreign_missing": foreign_missing,
            "foreign_core": foreign_core,
            "foreign_missing_cn": [cn(i) for i in foreign_missing],
            "foreign_core_cn": [cn(i) for i in foreign_core],
            "have_cn": [cn(display_of(i)) for i in have],
            "missing_cn": [cn(i) for i in missing],
            "core_missing_cn": [cn(i) for i in core_missing],
            "rank": _parse_score_rank(b),
        })
    # 排序：已拥有组件数多者优先；核心/普通缺失件含其他英雄专属物品的流派大幅降权；
    # qiubot 天梯组合（真实统计）优先于攻略站（提高权重）；再按覆盖率、出现率、胜场
    scored.sort(key=lambda s: (
        -len(s["have"]),
        len(s["foreign_core"]),      # 核心件需其他英雄物品 -> 靠后
        len(s["foreign_missing"]),   # 普通缺失件需其他英雄物品 -> 靠后
        -s["coverage"],
        0 if s["build"].get("source") == "qiubot" else 1,   # qiubot 权重更高
        -(s["build"].get("appearance_rate") or 0),          # 天梯出现率
        -s["rank"][0],
        -s["rank"][1],
    ))
    top = scored[:top_n]

    best = top[0] if top else None

    # 换卡建议：检测到但不在任何 top 流派里的物品（输出中文名）
    top_items_norm = set()
    for s in top:
        top_items_norm.update(datahub.normalize_name(i) for i in (s["build"].get("items") or []))
    swaps = []
    for name, info in detected_items.items():
        if datahub.normalize_name(name) not in top_items_norm and info.get("count", 0) > 0:
            swaps.append({"item": name, "item_cn": cn(name), "reason": "不在当前推荐的流派组件内"})
    # 摆放建议：基于物品标签/效果的通用规则（中文名）
    tips = _placement_tips(detected_items, best, items_db)
    # 格位摆放建议：核心件居中、相邻增益集中、缺失组件给落位
    if best:
        placement = placement_advice(best["have"], items_db,
                                     current_sockets=sockets or {},
                                     missing=best["missing"])
        for item in placement:
            if isinstance(item, tuple):
                tips.append(f"摆放：{item[0]} → {item[1]}。")
            else:
                tips.append(item)

    # 教学步骤（中文名 + 商店建议）
    teach = []
    if best:
        b = best["build"]
        teach.append(f"当前最优流派：「{b['title']}」（{_type_cn(b.get('type', ''))}，胜率参考 {b.get('score') or '?'}，作者 {b.get('author') or '?'}）")
        if best["have"]:
            have_brief = "、".join(
                (cn_with_brief(display_of(i)) if idx < 2 else cn(display_of(i))) for idx, i in enumerate(best["have"]))
            teach.append(f"已拥有的组件：{have_brief}（覆盖率 {best['coverage']*100:.0f}%）")
        if best["core_missing"]:
            shop_lines = []
            for idx, en in enumerate(best["core_missing"][:3]):
                adv = shop_advice(en)
                note = _hero_note(en, hero_canon, items_db)
                name = cn_with_brief(en) if idx < 2 else cn(en)
                shop_lines.append(f"{name}{note}" + (f"（去 {adv} 买）" if adv else ""))
            teach.append("最优先补的核心件：" + "；".join(shop_lines))
        elif best["missing"]:
            shop_lines = []
            for idx, en in enumerate(best["missing"][:3]):
                adv = shop_advice(en)
                note = _hero_note(en, hero_canon, items_db)
                name = cn_with_brief(en) if idx < 2 else cn(en)
                shop_lines.append(f"{name}{note}" + (f"（去 {adv} 买）" if adv else ""))
            teach.append("还需补齐：" + "；".join(shop_lines))
        else:
            teach.append("组件已齐！注意关键件的相邻关系。")
        # 其他英雄专属缺失件提示（无法/很难获得）
        if best.get("foreign_core_cn"):
            teach.append(f"⚠ 该流派核心件含其他英雄专属物品（{'、'.join(best['foreign_core_cn'][:3])}），本英雄无法正常获得，建议优先考虑别的流派")
        elif best.get("foreign_missing_cn"):
            teach.append(f"⚠ 该流派还缺其他英雄专属物品（{'、'.join(best['foreign_missing_cn'][:3])}），获得难度较高")
        # 技能野怪推荐（契合流派类型）
        madv = monster_advice(_type_cn(b.get("type", "")))
        if madv:
            teach.append(f"技能推荐：{madv}（契合{_type_cn(b.get('type', ''))}）")
        # 格位摆放建议（含缺失件落位）
        placement = placement_advice(best["have"], items_db,
                                     current_sockets=sockets or {},
                                     missing=best["missing"])
        p_lines = []
        for item in placement:
            if isinstance(item, tuple):
                p_lines.append(f"摆放：{item[0]} → {item[1]}")
            else:
                p_lines.append(item)
        if p_lines:
            teach.append("；".join(p_lines[:4]))
        if swaps:
            teach.append(f"可考虑替换：{'、'.join(s['item_cn'] for s in swaps[:3])}")

    summary = ""
    if best:
        b = best["build"]
        shop_part = ""
        # 优先找的缺失件：跳过其他英雄专属物品（优先列本英雄可获得的）
        foreign_set = set(best.get("foreign_missing") or []) | set(best.get("foreign_core") or [])
        target_en = [i for i in (best["core_missing"] or best["missing"]) if i not in foreign_set]
        target = [cn(i) for i in target_en[:3]]
        if best["missing"]:
            first_en = next((i for i in (best["core_missing"] or best["missing"]) if i not in foreign_set),
                            None)
            if first_en:
                adv = shop_advice(first_en)
                if adv:
                    shop_part = f"（商店：{adv}）"
        if not target and best["missing"]:
            # 全部缺失件都是其他英雄物品：不推荐跟这个流派
            summary = (f"「{b['title']}」缺少的组件都是其他英雄专属物品，"
                       f"本英雄无法获得，不建议选择该流派。")
        else:
            summary = (f"建议跟「{b['title']}」：已集齐 {best['coverage']*100:.0f}% 组件"
                       f"（{len(best['have'])}/{len(b['items'])}）。"
                       + (f" 优先找：{'、'.join(target)}{shop_part}" if best["missing"] else " 组件齐全，专注升星与摆放。"))
    elif detected_items:
        summary = "没有匹配度较高的流派，建议先选定一个流派方向。"
    else:
        summary = "尚未识别到物品，请先开启监视并让游戏画面完整显示。"

    return {
        "builds": [{
            "build": s["build"], "coverage": s["coverage"], "have": s["have"],
            "missing": s["missing"], "core_missing": s["core_missing"],
            "have_cn": s["have_cn"], "missing_cn": s["missing_cn"], "core_missing_cn": s["core_missing_cn"],
            "foreign_missing_cn": s.get("foreign_missing_cn") or [],
            "foreign_core_cn": s.get("foreign_core_cn") or [],
            "note": _build_note(s, items_db),
        } for s in top],
        "best": {
            "build": best["build"], "coverage": best["coverage"], "have": best["have"],
            "missing": best["missing"], "core_missing": best["core_missing"],
            "have_cn": best["have_cn"], "missing_cn": best["missing_cn"], "core_missing_cn": best["core_missing_cn"],
            "foreign_missing_cn": best.get("foreign_missing_cn") or [],
            "foreign_core_cn": best.get("foreign_core_cn") or [],
            "have_brief": [cn_with_brief(display_of(i)) for i in best.get("have") or []],
            "missing_brief": [cn_with_brief(i) for i in best.get("missing") or []],
        } if best else None,
        "swaps": swaps,
        "tips": tips,
        "teach": teach,
        "summary": summary,
    }


def _build_note(scored, items_db):
    parts = []
    if scored["core_missing"]:
        parts.append("核心缺失")
    elif scored["missing"]:
        parts.append(f"还差 {len(scored['missing'])} 件")
    else:
        parts.append("组件齐")
    b = scored["build"]
    if b.get("score"):
        parts.append(b["score"])
    return "，".join(parts)


def _placement_tips(detected_items, best, items_db) -> list:
    tips = []
    if not best:
        return tips
    have = best["have"]
    if not have:
        tips.append("先凑齐流派组件，再考虑精细摆放。")
        return tips

    # 规则 1：核心件放中间
    core = best["core_missing"]
    core_present = [i for i in have if i in _title_items(best["build"])]
    if core_present:
        tips.append(f"核心件 {datahub.item_cn(core_present[0])} 建议放在棋盘中部（3号/8号位），便于它吃到相邻增益。")

    # 规则 2：基于物品标签的相邻关系
    tag_rules = {
        "Weapon": ("武器", "与提供伤害/暴击加成的物品相邻"),
        "Shield": ("护盾", "与武器相邻，实现攻守循环（如 28 Hour Fitness 类）"),
        "Property": ("地产", "收益类，放角落也不影响"),
        "Potion": ("药水", "配合药水流派集中摆放，吃加值"),
        "Food": ("食物", "触发类食物尽量与“使用物品触发”的件相邻"),
        "Tool": ("工具", "与加速类物品相邻可提升触发频率"),
        "Ammo": ("弹药", "给对应武器供弹，尽量贴近武器"),
    }
    used_tags = set()
    for name in have:
        item = items_db.get(name.lower()) or items_db.get(datahub.normalize_name(name))
        if not item:
            continue
        for tag in item.get("tags") or []:
            if tag in tag_rules and tag not in used_tags:
                cn, rule = tag_rules[tag]
                tips.append(f"{datahub.item_cn(name)}（{cn}）：{rule}。")
                used_tags.add(tag)
                break

    # 规则 3：工具提示里含 adjacent/neighbor 的关键件
    for name in have:
        item = items_db.get(name.lower()) or items_db.get(datahub.normalize_name(name))
        if not item:
            continue
        for tt in (item.get("tooltips") or []):
            if re.search(r"\b(adjacent|neighbor)\b", tt, re.I):
                tips.append(f"{datahub.item_cn(name)} 效果与相邻件相关，注意它周围的物品选择。")
                break

    tips.append("参考该流派的获胜截图（右键可查看大图）摆放整体布局。")
    return tips[:6]


def teach_text(rec: dict) -> str:
    return "\n".join(rec.get("teach") or ["暂无教学建议。"]) if rec else "暂无教学建议。"


# ---------------------------------------------------------------- 流派搜索 / 自选分析

def search_builds(keyword: str, hero: str, builds=None, limit: int = 50,
                  detected_items: dict = None) -> list:
    """按关键词搜索流派：匹配流派标题 / 类型 / 组件物品名（中英）。

    detected_items 给定时，为每个结果计算覆盖率（基于当前识别阵容），
    返回 [{build, items_cn, item_hits, score, coverage, have_cn, missing_cn}]。
    排序：标题命中 > 组件命中 > 类型命中；同分时覆盖率高的靠前（玩家容易凑齐）。
    """
    builds = builds if builds is not None else datahub.get_builds(hero)
    if not builds:
        return []
    kw = (keyword or "").strip().lower()
    if not kw:
        return []
    kw_norm = re.sub(r"[\s\-_]", "", kw)
    items_db = datahub.get_items()
    detected_norm = {datahub.normalize_name(n) for n in (detected_items or {})}
    results = []
    for b in builds:
        title = (b.get("title") or "").lower()
        btype = ((b.get("type") or "") + " " + " ".join(b.get("types") or [])).lower()
        bitems = [i for i in (b.get("items") or []) if i]
        # 组件名匹配：英文原名 / 中文名（去空格/连字符归一）
        item_hits = []
        for it in bitems:
            it_db = items_db.get(it.lower()) or items_db.get(datahub.normalize_name(it))
            cn = (it_db or {}).get("nameCn") or ""
            it_norm = re.sub(r"[\s\-_]", "", it.lower())
            cn_norm = re.sub(r"[\s\-_]", "", cn.lower())
            if (kw_norm and (kw_norm in it_norm or (cn_norm and kw_norm in cn_norm))) or kw in it.lower():
                item_hits.append(it)
        if kw in title:
            score = 3
        elif item_hits:
            score = 2
        elif kw in btype:
            score = 1
        else:
            continue
        # 覆盖率（基于当前识别阵容）
        have = [i for i in bitems if datahub.normalize_name(i) in detected_norm]
        coverage = len(have) / len(bitems) if bitems else 0.0
        results.append({
            "build": b,
            "items_cn": [datahub.item_cn(i) for i in bitems],
            "item_hits": [datahub.item_cn(i) for i in item_hits],
            "score": score,
            "coverage": round(coverage, 2),
            "have_cn": [datahub.item_cn(i) for i in have],
            "missing_cn": [datahub.item_cn(i) for i in bitems if datahub.normalize_name(i) not in detected_norm],
        })
    # 排序：相关度优先；同相关度覆盖率高的靠前（玩家容易凑齐）
    results.sort(key=lambda r: (-r["score"], -r["coverage"], -(r["build"].get("win") or 0)))
    return results[:limit]


def analyze_build(build: dict, detected_items: dict, hero: str,
                  sockets: dict = None, stash_sockets: dict = None) -> dict:
    """针对用户自选的单个流派做完整分析，返回与 recommend() 相同结构的 dict。

    覆盖 / 缺失 / 核心件 / 商店 / 技能 / 摆放 / 替换 全部计算，方便用户自选流派。
    """
    items_db = datahub.get_items()
    bitems = [i for i in (build.get("items") or []) if i]
    detected_names = set(detected_items.keys())
    # 附魔物品：显示名保留原名，匹配用剥离附魔后的英文 key
    display_to_key = {}
    for k in detected_names:
        base = k
        if "·" in k:
            _, _, rest = k.partition("·")
            if rest.strip():
                base = rest.strip()
        if re.search(r"[\u4e00-\u9fff]", base):
            # 中文名 -> items_db key
            enk = next((ek for ek, it in items_db.items()
                        if (it.get("nameCn") or "").strip() == base.strip()), base)
            display_to_key[k] = enk
        else:
            display_to_key[k] = base
    detected_norm = {datahub.normalize_name(display_to_key.get(n, n)) for n in detected_names}
    key_to_display = {}
    for dn in detected_names:
        k = display_to_key.get(dn, dn)
        key_to_display.setdefault(datahub.normalize_name(k), dn)

    def display_of(build_item: str) -> str:
        return key_to_display.get(datahub.normalize_name(build_item), build_item)

    hero_canon = _HERO_CANON.get((hero or "").lower(), hero or "")

    have = [i for i in bitems if datahub.normalize_name(i) in detected_norm]
    missing = [i for i in bitems if datahub.normalize_name(i) not in detected_norm]
    missing = sorted(missing, key=lambda i: _item_sort_key(i, hero_canon, items_db))
    coverage = len(have) / len(bitems) if bitems else 0
    core_missing = sorted([i for i in missing if i in _title_items(build)],
                          key=lambda i: _item_sort_key(i, hero_canon, items_db))
    foreign_missing = [i for i in missing if _item_hero_rank(i, hero_canon, items_db) == 2]

    def cn(n):
        return datahub.item_cn(n)

    best = {
        "build": build, "coverage": round(coverage, 2), "have": have, "missing": missing,
        "core_missing": core_missing,
        "have_cn": [cn(display_of(i)) for i in have], "missing_cn": [cn(i) for i in missing],
        "core_missing_cn": [cn(i) for i in core_missing],
        "foreign_missing": foreign_missing,
        "foreign_missing_cn": [cn(i) for i in foreign_missing],
    }

    # 教学
    teach = []
    teach.append(f"当前流派：「{build.get('title')}」（{_type_cn(build.get('type', ''))}，胜率参考 {build.get('score') or '?'}，作者 {build.get('author') or '?'}）")
    if have:
        have_brief = "、".join(
            (cn_with_brief(display_of(i)) if idx < 2 else cn(display_of(i))) for idx, i in enumerate(have))
        teach.append(f"已拥有的组件：{have_brief}（覆盖率 {coverage*100:.0f}%）")
    if core_missing:
        shop_lines = []
        for idx, en in enumerate(core_missing[:3]):
            adv = shop_advice(en)
            note = _hero_note(en, hero_canon, items_db)
            name = cn_with_brief(en) if idx < 2 else cn(en)
            shop_lines.append(f"{name}{note}" + (f"（去 {adv} 买）" if adv else ""))
        teach.append("最优先补的核心件：" + "；".join(shop_lines))
    elif missing:
        shop_lines = []
        for idx, en in enumerate(missing[:3]):
            adv = shop_advice(en)
            note = _hero_note(en, hero_canon, items_db)
            name = cn_with_brief(en) if idx < 2 else cn(en)
            shop_lines.append(f"{name}{note}" + (f"（去 {adv} 买）" if adv else ""))
        teach.append("还需补齐：" + "；".join(shop_lines))
    else:
        teach.append("组件已齐！注意关键件的相邻关系。")
    if foreign_missing:
        teach.append(f"⚠ 该流派还缺其他英雄专属物品（{'、'.join(best['foreign_missing_cn'][:3])}），获得难度较高")
    madv = monster_advice(_type_cn(build.get("type", "")))
    if madv:
        teach.append(f"技能推荐：{madv}（契合{_type_cn(build.get('type', ''))}）")
    placement = placement_advice(best["have"], items_db, current_sockets=sockets or {}, missing=missing)
    p_lines = []
    for item in placement:
        if isinstance(item, tuple):
            p_lines.append(f"摆放：{item[0]} → {item[1]}")
        else:
            p_lines.append(item)
    if p_lines:
        teach.append("；".join(p_lines[:4]))

    # 替换建议：检测到但不在该流派里的物品
    bitems_norm = {datahub.normalize_name(i) for i in bitems}
    swaps = [{"item": n, "item_cn": cn(n), "reason": "不在该流派组件内"}
             for n in detected_items if datahub.normalize_name(n) not in bitems_norm]

    summary = (f"「{build.get('title')}」：已集齐 {coverage*100:.0f}% 组件（{len(have)}/{len(bitems)}）。"
               + (f" 优先找：{'、'.join(best['missing_cn'][:3])}" if missing else " 组件齐全。"))

    tips = _placement_tips(detected_items, best, items_db)
    for item in placement:
        if isinstance(item, tuple):
            tips.append(f"摆放：{item[0]} → {item[1]}。")
        else:
            tips.append(item)

    return {
        "builds": [{"build": build, "coverage": best["coverage"], "have": have, "missing": missing,
                    "core_missing": core_missing, "have_cn": best["have_cn"],
                    "missing_cn": best["missing_cn"], "core_missing_cn": best["core_missing_cn"],
                    "foreign_missing_cn": best["foreign_missing_cn"], "note": _build_note(best, items_db)}],
        "best": {**best,
                 "have_brief": [cn_with_brief(display_of(i)) for i in have],
                 "missing_brief": [cn_with_brief(i) for i in missing]},
        "swaps": swaps,
        "tips": tips,
        "teach": teach,
        "summary": summary,
    }
