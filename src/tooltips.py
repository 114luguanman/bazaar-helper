# -*- coding: utf-8 -*-
"""物品效果中文翻译器：把 items_db 的英文 tooltips 翻译成中文简介 + 提取 CD。

翻译策略：
1. 内置效果短语词典（覆盖 The Bazaar 常见效果关键词）
2. 逐条 tooltip：先提 CD（Cooldown N seconds），再翻译其余效果
3. 保留数值（Heal 20 -> 治疗20）
"""
import re

# CD 提取：Cooldown 5 seconds -> 5
_CD_RE = re.compile(r"Cooldown\s+(\d+(?:\.\d+)?)\s+seconds?", re.I)
# 通用效果词典：英文短语(小写) -> 中文模板（{n} 插入数字）
_EFFECT_DICT = [
    # 触发句式（When you ...）优先（长句式先于简单词，捕获组放宽到含中文）
    (r"when you use an? ([a-z ]+?) item", "使用{n}物品时"),
    (r"when you use this", "使用此物品时"),
    (r"when you use an? ([a-z ]+?) item to the (left|right)", "使用其{m}侧{n}物品时"),
    (r"when you buy an? ([a-z ]+?)", "购买{n}时"),
    (r"when you buy this", "购买此物品时"),
    (r"when you sell this", "出售此物品时"),
    (r"when you sell an? ([a-z ]+?)", "出售{n}时"),
    (r"when you win a fight", "获胜时"),
    (r"when you lose a fight", "失败时"),
    (r"when this is destroyed", "此物品被摧毁时"),
    (r"when an? ([a-z ]+?) item is used", "使用{n}物品时"),
    (r"when (?:an?|your) ([a-z ]+?) items? (?:start|stop) flying", "当{n}物品开始/停止飞行时"),
    (r"when this stops flying", "此物品停止飞行时"),
    (r"when this starts flying", "此物品开始飞行时"),
    (r"when you visit an? ([a-z ]+?)", "访问{n}时"),
    (r"when you destroy an? ([a-z ]+?)", "摧毁{n}时"),
    (r"when you take damage", "受到伤害时"),
    (r"the first time you use this each fight", "每场战斗首次使用此物品时"),
    (r"the first time (?:this|an? ([a-z ]+?) item) is used each fight", "每场战斗首次使用{n}物品时"),
    # 时间节点
    (r"at the start of combat", "战斗开始时"),
    (r"at the start of the fight", "战斗开始时"),
    (r"at the start of each fight", "每场战斗开始时"),
    (r"at the start of each day", "每天开始时"),
    (r"at the end of each day", "每天结束时"),
    (r"at the end of each fight", "每场战斗结束时"),
    (r"for the rest of the fight", "本场剩余战斗"),
    (r"for the fight", "本场战斗"),
    # 简单数值效果
    (r"^heal\s+(\d+)", "治疗{n}"),
    (r"^shield\s+(\d+)", "获得护盾{n}"),
    (r"^burn\s+(\d+)", "施加灼烧{n}"),
    (r"^poison\s+(\d+)", "施加剧毒{n}"),
    (r"^deal\s+(\d+)\s+damage", "造成{n}伤害"),
    (r"^deal\s+(\d+)\s+damage\s+to\s+(all\s+)?enemies?", "对敌人造成{n}伤害"),
    (r"^deal\s+(\d+)\s+damage\s+to\s+(this|one)\s+enemy", "对一名敌人造成{n}伤害"),
    (r"^has\s+(\d+)\s+ammo", "弹药{n}"),
    (r"^\+(\d+)%?\s*crit\s*chance", "暴击率+{n}%"),
    (r"^\+(\d+)\s*crit\s*chance", "暴击率+{n}%"),
    # 常见机制
    (r"multicast\s+(\d+)", "多重施法{n}"),
    (r"multicast", "多重施法"),
    (r"\baccelerate\b", "加速"),
    (r"\baccelerates\b", "加速"),
    (r"\banother\b", "另一件"),
    (r"\bget\s+(\d+)\b", "获得{n}个"),
    (r"\bget\b", "获得"),
    (r"has flying", "获得飞行"),
    (r"starts flying", "初始飞行"),
    (r"charge this (\d+(?:\.\d+)?) second", "为此物品充能{n}秒"),
    (r"charge (?:an? )?([a-z ]+?) items? (\d+(?:\.\d+)?) second", "为{n}物品充能{m}秒"),
    (r"\bslow\b", "减速"),
    (r"\bfreeze\b", "冻结"),
    (r"\bhaste\b", "加速"),
    (r"\bstun\b", "眩晕"),
    (r"\blifesteal\b", "吸血"),
    (r"\bregeneration\b", "生命再生"),
    (r"\bregen\b", "生命再生"),
    (r"this has the types", "此物品获得类型"),
    (r"gain[s]? (\d+) ([a-z ]+) for the fight", "本场战斗{n}获得{m}"),
    (r"([a-z ]+?) items? gain[s]? \+?(\d+) ([a-z ]+) for the fight", "{n}物品本场战斗+{m}{k}"),
    (r"your ([a-z ]+?) gain[s]? \+?(\d+) ([a-z ]+)", "你的{n}物品+{m}{k}"),
    (r"your ([a-z ]+?) have \+?(\d+)", "你的{n}物品+{m}"),
    (r"([a-z ]+?) items? have \+?(\d+)", "{n}物品+{m}"),
    (r"deal[s]? (\d+) damage", "造成{n}伤害"),
    (r"deal[s]? damage equal to (\d+)% of your enemy's max health", "造成敌人最大生命{n}%的伤害"),
    (r"deal[s]? damage equal to", "造成等量伤害"),
    (r"heal equal to (\d+) times this item's value", "治疗=物品价值×{n}"),
    (r"equal to (\d+)% of your max health", "=最大生命{n}%"),
    (r"equal to double this item's value", "=物品价值×2"),
    (r"equal to this item's value", "=物品价值"),
    (r"equal to this item's damage", "=物品伤害"),
    (r"equal to this item's shield", "=物品护盾"),
    (r"equal to this item's heal", "=物品治疗"),
    (r"permanently destroy", "永久摧毁"),
    (r"destroy an? ([a-z ]+?) item", "摧毁{n}物品"),
    (r"transform another", "转化"),
    (r"transform this", "转化此物品"),
    (r"to the (left|right) of this", "其{m}侧"),
    (r"to the left of this", "左侧"),
    (r"to the right of this", "右侧"),
    (r"items? to the (left|right)", "左/右两侧物品"),
    (r"upgrade", "升级"),
    (r"get a ([a-z ]+?)", "获得{n}"),
    (r"gain (\d+) gold", "获得{n}金币"),
    (r"value", "价值"),
    (r"cooldown", "冷却"),
    (r"if able", "若可行"),
    (r"enchant", "附魔"),
]

# 保留的英文专有名词（不翻译）——小写键
_KEEP_EN = {"weapon": "武器", "shield": "护盾", "tool": "工具", "potion": "药水",
            "food": "食物", "property": "地产", "ammo": "弹药", "friend": "伙伴",
            "apparel": "服饰", "tech": "科技", "toy": "玩具", "vehicle": "载具",
            "adjacent": "相邻", "neighbor": "相邻", "flying": "飞行", "poison": "剧毒",
            "burn": "灼烧", "damage": "伤害", "heal": "治疗", "regen": "生命再生",
            "value": "价值", "crit": "暴击", "chance": "几率", "gold": "金币",
            "max": "最大", "health": "生命", "enemy": "敌人", "small": "小型",
            "medium": "中型", "large": "大型", "non-legendary": "非传说", "legendary": "传说",
            "items": "物品", "item": "物品", "weapons": "武器", "shields": "护盾",
            "poison items": "剧毒物品", "burn items": "灼烧物品", "relic": "遗物",
            "dinosaur": "恐龙", "dinosaurs": "恐龙", "relics": "遗物", "property": "地产",
            "haste": "加速", "freeze": "冻结", "slow": "减速", "stun": "眩晕",
            "regen items": "生命再生物品", "flying items": "飞行物品",
            "weapon and tech item": "武器与科技物品",
            "accelerate": "加速", "accelerates": "加速", "another": "另一件",
            "get": "获得", "nanobots": "纳米机器人", "friends": "伙伴", "friend": "伙伴",
            "dinosaurs": "恐龙", "relics": "遗物", "vehicles": "载具", "drones": "无人机",
            "properties": "地产", "weapons": "武器", "shields": "护盾", "tools": "工具"}


def extract_cd(tooltips: list) -> str:
    """提取 CD（秒）。无 CD 返回 ''。"""
    for t in tooltips or []:
        m = _CD_RE.search(t)
        if m:
            v = float(m.group(1))
            return f"{v:g}秒"
    return ""


def _translate_phrase(phrase: str) -> str:
    """翻译短语：整体查表，否则逐词查表，未翻译的保留原文。"""
    p = phrase.strip()
    if not p:
        return p
    if p.lower() in _KEEP_EN:
        return _KEEP_EN[p.lower()]
    words = p.split()
    if len(words) <= 3:
        translated = []
        for w in words:
            lw = w.lower().strip(".,;:()")
            if lw in _KEEP_EN:
                translated.append(_KEEP_EN[lw])
            else:
                translated.append(w)
        return "".join(translated)
    return p


def translate_tooltip(text: str) -> str:
    """翻译单条英文 tooltip 为中文简介（尽力翻译）。"""
    t = (text or "").strip()
    if not t:
        return ""
    # 提 CD（单独处理）
    if _CD_RE.search(t):
        return ""  # CD 由 extract_cd 单独输出
    out = t
    for pat, tmpl in _EFFECT_DICT:
        def repl(m, _tmpl=tmpl):
            groups = m.groups()
            if groups:
                filled = _tmpl
                for gi, g in enumerate(groups):
                    val = g
                    if not re.fullmatch(r"\d+(?:\.\d+)?", g):
                        # 逐词翻译：整个短语先查表，再按单词查表
                        val = _translate_phrase(g)
                    key = {0: "n", 1: "m", 2: "k"}.get(gi, str(gi))
                    filled = filled.replace("{" + key + "}", val)
                return filled
            return _tmpl
        out = re.sub(pat, repl, out, flags=re.I)
    # 清理多余空格/括号/残留英文小词
    out = re.sub(r"\s+", " ", out).strip()
    out = out.replace(" ,", ",").replace(" 。", "。")
    out = out.replace("(s)", "").replace(" second", "秒").replace(" seconds", "秒")
    # 修复重复中文词（物品物品 -> 物品）
    out = re.sub(r"(物品){2,}", "物品", out)
    out = re.sub(r"(伤害){2,}", "伤害", out)
    out = re.sub(r"(护盾){2,}", "护盾", out)
    # 删除残留的英文虚词/结构性小词（保留数字与已翻译部分）
    for w in ["this item's", "this item", "this ", "your ", "an ", "a ", "the ", "to ",
              "items ", "item ", "and ", "for ", "each ", "have ", "gain ", "gains ",
              "when ", "you ", "use ", "with ", "on ", "of ", "equal to ", "then "]:
        out = re.sub(r"\b" + w.strip() + r"\b", " ", out, flags=re.I)
    out = re.sub(r"\s+", " ", out).strip()
    out = out.strip(" .;，；。")
    if not re.search(r"[\u4e00-\u9fff]", out):
        return text
    return out


def item_effect_summary(item: dict, max_lines: int = 3) -> str:
    """物品效果中文简介（不含 CD）。返回如 "治疗20；获得护盾20；多重施法…"。"""
    tts = item.get("tooltips") or []
    if not tts:
        return ""
    parts = []
    for t in tts:
        if _CD_RE.search(t):
            continue
        cn = translate_tooltip(t)
        if cn and cn not in parts:
            parts.append(cn)
        if len(parts) >= max_lines:
            break
    return "；".join(parts[:max_lines])


def item_brief(item: dict) -> str:
    """物品完整简介：CD + 效果。返回如 "CD 5秒 · 治疗20；获得护盾20"。无 CD 则只写效果。"""
    cd = extract_cd(item.get("tooltips") or [])
    eff = item_effect_summary(item)
    bits = []
    if cd:
        bits.append(f"CD {cd}")
    if eff:
        bits.append(eff)
    return " · ".join(bits)


if __name__ == "__main__":
    from . import datahub
    items = datahub.get_items()
    for k in ["cargo shorts", "wrist warrior", "ignition core", "yoyo", "28 hour fitness"]:
        it = items.get(k) or {}
        print(f"{it.get('nameCn')}: {item_brief(it)}")
