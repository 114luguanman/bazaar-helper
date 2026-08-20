# -*- coding: utf-8 -*-
"""数据层：物品图鉴（howbazaar.gg API）+ 流派攻略（bazaar-builds.net WordPress API）。

全部使用标准库实现抓取，具备本地缓存；离线时回退到缓存数据。
"""
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from . import config

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

ITEMS_API = "https://howbazaar.gg/api/items"
CODEX_ITEMS_URL = "https://cdn.jsdelivr.net/gh/TooYoung010/bazaar-codex@main/src/data/items.json"
WP_BASE = "https://bazaar-builds.net/wp-json/wp/v2"
QIUBOT_BASE = "https://bazaarqiubot.com"

# 巴扎丘Bot 英雄中文名 -> 应用 hero key（含新角色"双龙"）
QIUBOT_HERO_MAP = {
    "凡妮莎": "vanessa", "杜利": "dooley", "马克": "mak", "皮格": "pygmalien",
    "斯黛拉": "stelle", "朱尔斯": "jules", "卡诺克": "karnok", "双龙": "dragons",
}
QIUBOT_HERO_CN = {v: k for k, v in QIUBOT_HERO_MAP.items()}

HEROES = ["mak", "vanessa", "dooley", "pygmalien", "stelle", "jules", "karnok", "dragons"]
HERO_CN = {
    "mak": "玛克(Mak)", "vanessa": "凡妮莎(Vanessa)", "dooley": "杜利(Dooley)",
    "pygmalien": "猪马莲(Pygmalien)", "stelle": "斯黛尔(Stelle)",
    "jules": "朱尔斯(Jules)", "karnok": "卡诺克(Karnok)", "dragons": "双龙(The Dragons)",
}


class FetchError(Exception):
    pass


def _http_get_json(url: str, retries: int = 3, timeout: int = 40):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise FetchError(f"请求失败: {url} -> {last}")


def _http_get_bytes(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- 物品图鉴

_ITEM_TAG_STOP = {
    "bazaar", "builds", "build", "guide", "guides", "patch", "patches", "season",
    "update", "updates", "account settings", "news", "tools", "database", "hero",
    "mak", "vanessa", "dooley", "pygmalien", "stelle", "jules", "karnok",
    "gameplay", "meta", "tips", "tier list", "signup", "discord", "twitter", "steam",
}


def _item_tag_like(name: str) -> bool:
    if not name or len(name) < 3 or len(name) > 40:
        return False
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9'\-\. ]*$", name):
        return False
    low = name.lower()
    if low in _ITEM_TAG_STOP:
        return False
    if any(w in low for w in (" build", " guide", " patch", " season", " update")):
        return False
    return True


def _fetch_item_tags(refresh: bool = False) -> dict:
    """攻略站 tag（物品名词汇，含 howbazaar 缺失的基础物品）。返回 {tag_id: [name, count]}。"""
    if not refresh and os.path.exists(config.TAGS_CACHE_PATH):
        return config.load_json(config.TAGS_CACHE_PATH) or {}
    tags = {}
    page = 1
    while page <= 40:
        batch = _http_get_json(f"{WP_BASE}/tags?per_page=100&page={page}&_fields=id,name,count&orderby=count&order=desc")
        if not batch:
            break
        for t in batch:
            tags[t["id"]] = [t.get("name"), t.get("count", 0)]
        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.2)
    config.ensure_dirs()
    with open(config.TAGS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False)
    config.invalidate(config.TAGS_CACHE_PATH)
    return tags


def _fetch_codex_items(refresh: bool = False) -> list:
    """bazaar-codex 中文图鉴（含 nameCn 中文名、icon 链接），缓存到本地。"""
    if not refresh and os.path.exists(config.CODEX_CACHE_PATH):
        return config.load_json(config.CODEX_CACHE_PATH) or []
    data = _http_get_json(CODEX_ITEMS_URL)
    config.ensure_dirs()
    with open(config.CODEX_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    config.invalidate(config.CODEX_CACHE_PATH)
    return data


def _merge_cn_names(items: dict, codex: list):
    """把 codex 的中文名（nameCn）按 id/英文名合并进物品词表。"""
    by_id, by_name = {}, {}
    for it in codex:
        cid = it.get("id")
        name = (it.get("name") or "").strip()
        cn = (it.get("nameCn") or it.get("nameDisplay") or "").strip()
        if cid:
            by_id[cid] = cn
        if name:
            by_name.setdefault(normalize_name(name), cn)
    for key, item in items.items():
        cn = ""
        if item.get("id") and item["id"] in by_id:
            cn = by_id[item["id"]]
        elif key in by_name:
            cn = by_name[key]
        item["nameCn"] = cn or item.get("nameCn") or ""
    return items


def fetch_items(refresh: bool = False) -> dict:
    """返回 {规范化名称: item}。howbazaar.gg 物品库 + 攻略站 tag 物品名合并。"""
    if not refresh and os.path.exists(config.ITEMS_PATH):
        return config.load_json(config.ITEMS_PATH) or {}
    raw = _http_get_json(ITEMS_API)
    data = raw.get("data", raw if isinstance(raw, list) else [])
    items = {}
    for it in data:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        tooltips = {}
        for tier, info in (it.get("tiers") or {}).items():
            tt = info.get("tooltips") or []
            if tt:
                tooltips[tier] = tt
        best_tier_tt = tooltips.get("Diamond") or tooltips.get("Gold") or tooltips.get("Silver") or tooltips.get("Bronze") or []
        items[normalize_name(name)] = {
            "name": name,
            "id": it.get("id"),
            "size": it.get("size"),
            "heroes": it.get("heroes") or [],
            "tags": it.get("tags") or [],
            "startingTier": it.get("startingTier"),
            "tooltips": best_tier_tt,
            "all_tooltips": tooltips,
        }
    # 合并攻略站 tag 中像物品名的条目（补全 howbazaar 缺失的基础物品）
    try:
        tags = _fetch_item_tags(refresh)
        for _tid, (name, count) in tags.items():
            n = normalize_name(name or "")
            if not n or n in items or not count or count < 1:
                continue
            if _item_tag_like(name):
                items[n] = {"name": name, "source": "buildsite", "build_count": count,
                            "size": None, "heroes": [], "tags": [], "tooltips": []}
    except FetchError:
        pass
    # 合并 bazaar-codex 中文名
    try:
        _merge_cn_names(items, _fetch_codex_items(refresh))
    except FetchError:
        pass
    config.ensure_dirs()
    with open(config.ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    config.invalidate(config.ITEMS_PATH)
    return items


def get_items() -> dict:
    items = config.load_json(config.ITEMS_PATH)
    if items is None:
        try:
            items = fetch_items(refresh=False)
        except FetchError:
            items = {}
    return items or {}


def item_cn(name: str) -> str:
    """物品中文名；无中文名时回退英文原名。支持附魔前缀（「致命·Cog」→「致命·齿轮」）。"""
    if not name:
        return name
    # 附魔前缀：中文·真实名 -> 翻译真实名后拼回
    if "·" in name:
        prefix, _, rest = name.partition("·")
        rest = rest.strip()
        if rest:
            rest_cn = item_cn(rest)
            if rest_cn and rest_cn != rest:
                return f"{prefix}·{rest_cn}"
            return name
    if re.search(r"[\u4e00-\u9fff]", name):
        return name  # 已是中文
    items = get_items()
    item = items.get(normalize_name(name))
    if item and item.get("nameCn"):
        return item["nameCn"]
    return name


def item_cn_list(names) -> list:
    return [item_cn(n) for n in (names or [])]


def normalize_name(name: str) -> str:
    """规范化物品名用于匹配：小写、去标点、压缩空白。"""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


# ---------------------------------------------------------------- 英雄分类

def fetch_heroes(refresh: bool = False) -> dict:
    """返回 {hero: {id,name,slug,count}}。"""
    if not refresh and os.path.exists(config.HEROES_PATH):
        return config.load_json(config.HEROES_PATH) or {}
    heroes = {}
    for hero in HEROES:
        try:
            res = _http_get_json(f"{WP_BASE}/categories?search={hero}&per_page=100")
        except FetchError:
            continue
        # 优先 <hero>-builds 分类（实际存放流派）；其次 slug==hero 且有帖子数的分类
        def pick():
            pref = [c for c in res if c.get("slug") == f"{hero}-builds"]
            if pref:
                return pref[0]
            for c in res:
                if c.get("slug") == hero and c.get("count", 0) > 0:
                    return c
            return None
        c = pick()
        if c:
            heroes[hero] = {"id": c["id"], "name": c["name"], "slug": c["slug"], "count": c.get("count", 0)}
    config.ensure_dirs()
    with open(config.HEROES_PATH, "w", encoding="utf-8") as f:
        json.dump(heroes, f, ensure_ascii=False, indent=1)
    config.invalidate(config.HEROES_PATH)
    return heroes


def get_heroes() -> dict:
    heroes = config.load_json(config.HEROES_PATH)
    if heroes is None:
        try:
            heroes = fetch_heroes(refresh=False)
        except FetchError:
            heroes = {}
    return heroes or {}


# ---------------------------------------------------------------- 流派攻略

def _parse_score(title: str):
    m = re.search(r"\b(\d+)-(\d+)\b", title)
    if m:
        return f"{m.group(1)}-{m.group(2)}", int(m.group(1)), int(m.group(2))
    return None, None, None


def _parse_author(title: str):
    # 作者通常在 " - 昵称" 之后（分隔符前后带空格，避免误匹配 10-3 这类分数连字符）
    m = re.search(r"\s[-–—]\s+([A-Za-z0-9_\-\. ]{2,30}?)\s*$", title)
    if m:
        return m.group(1).strip()
    m = re.search(r"\bBuild\s+([A-Za-z][A-Za-z0-9_\-\. ]{1,29}?)\s*$", title)
    return m.group(1).strip() if m else None


def fetch_builds(hero: str, refresh: bool = False) -> list:
    """抓取指定英雄的全部流派，归一化后缓存到 data/builds.json。

    返回元素:
    {hero, title, date, link, slug, score, win, loss, author, type, items: [名], categories}
    """
    hero = hero.lower()
    cache = config.load_json(config.BUILDS_PATH)
    if isinstance(cache, dict):
        cache = cache.get(hero)
    if not refresh and cache:
        return cache

    heroes = get_heroes()
    info = heroes.get(hero)
    if not info:
        raise FetchError(f"未找到英雄 {hero} 的分类信息，请先“更新数据”")
    cat_id = info["id"]

    posts = []
    page = 1
    while page <= 30:
        batch = _http_get_json(
            f"{WP_BASE}/posts?categories={cat_id}&per_page=100&page={page}"
            "&_fields=id,date,modified,slug,title,link,categories,tags,excerpt"
        )
        if not batch:
            break
        posts.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.3)

    # tag id -> 物品名
    tag_ids = sorted({t for p in posts for t in (p.get("tags") or [])})
    tagmap = {}
    for i in range(0, len(tag_ids), 90):
        chunk = tag_ids[i:i + 90]
        try:
            tags = _http_get_json(f"{WP_BASE}/tags?include={','.join(map(str, chunk))}&per_page=100&_fields=id,name")
            tagmap.update({t["id"]: t["name"] for t in tags})
        except FetchError:
            pass

    # category id -> 流派类型
    cat_ids = sorted({c for p in posts for c in (p.get("categories") or [])})
    catmap = {}
    for i in range(0, len(cat_ids), 90):
        chunk = cat_ids[i:i + 90]
        try:
            cats = _http_get_json(f"{WP_BASE}/categories?include={','.join(map(str, chunk))}&per_page=100&_fields=id,name,slug")
            catmap.update({c["id"]: c["name"] for c in cats})
        except FetchError:
            pass

    builds = []
    for p in posts:
        title = (p.get("title") or {}).get("rendered", "") if isinstance(p.get("title"), dict) else str(p.get("title") or "")
        title = re.sub(r"&#\d+;", " - ", title)
        title = re.sub(r"<[^>]+>", "", title).strip()
        score, win, loss = _parse_score(title)
        items = [tagmap[t] for t in (p.get("tags") or []) if t in tagmap]
        raw_types = [catmap[c] for c in (p.get("categories") or []) if c in catmap and catmap[c].lower() not in ("builds", hero)]
        archetypes = [t for t in raw_types if "build" in t.lower()] or raw_types
        excerpt = re.sub(r"<[^>]+>", " ", (p.get("excerpt") or {}).get("rendered", "") if isinstance(p.get("excerpt"), dict) else "").strip()
        builds.append({
            "hero": hero,
            "title": title,
            "date": p.get("date"),
            "link": p.get("link"),
            "slug": p.get("slug"),
            "score": score,
            "win": win,
            "loss": loss,
            "author": _parse_author(title),
            "type": archetypes[0] if archetypes else "",
            "types": raw_types,
            "items": items,
            "excerpt": excerpt[:400],
        })

    all_builds = config.load_json(config.BUILDS_PATH) or {}
    all_builds[hero] = builds
    config.ensure_dirs()
    with open(config.BUILDS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_builds, f, ensure_ascii=False, indent=1)
    config.invalidate(config.BUILDS_PATH)
    return builds


def get_builds(hero: str) -> list:
    """获取英雄流派：bazaar-builds 攻略 + 巴扎丘Bot 天梯组合（合并，qiubot 权重更高）。"""
    hero = hero.lower()
    cache = config.load_json(config.BUILDS_PATH)
    builds = cache.get(hero) if isinstance(cache, dict) else (cache or [])
    if not isinstance(builds, list):
        builds = []
    # 合并巴扎丘Bot 天梯组合（真实统计，优先排序）
    try:
        qb = qiubot_builds(hero)
        if qb:
            # 去重：qiubot 在前（权重高）
            qb_slugs = {b.get("slug") for b in qb}
            builds = [b for b in builds if b.get("slug") not in qb_slugs] or builds
            builds = qb + [b for b in builds if b.get("source") != "qiubot"]
    except Exception:
        pass
    return builds or []


# ---------------------------------------------------------------- 巴扎丘Bot（qiubot）数据源

QIUBOT_CACHE_PATH = os.path.join(config.DATA_DIR, "qiubot_comp.json")


def _qiubot_http_get(url: str, timeout: int = 25):
    """qiubot 请求（带 UA + 重试 + 限流保护）。"""
    import urllib.parse
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:  # 限流：等待后重试
                time.sleep(2.5)
                last = FetchError("巴扎丘Bot 请求过于频繁，请稍后再试")
                continue
            raise FetchError(f"巴扎丘Bot HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            last = FetchError(f"巴扎丘Bot 请求失败: {e}")
    raise last or FetchError("巴扎丘Bot 请求失败")


def fetch_qiubot_comp(hero: str, refresh: bool = False) -> dict:
    """抓取巴扎丘Bot 的组合数据（真实天梯统计，含核心组合层/变体/配置）。

    hero 为应用 key（mak/dooley/.../dragons）。返回 {hero, hero_zh, layers, total_runs}。
    """
    hero = hero.lower()
    cache = config.load_json(QIUBOT_CACHE_PATH) or {}
    if not refresh and cache.get(hero):
        return cache[hero]
    cn = QIUBOT_HERO_CN.get(hero, hero)
    import urllib.parse
    url = f"{QIUBOT_BASE}/api/comp?hero=" + urllib.parse.quote(cn)
    try:
        data = _qiubot_http_get(url)
        if isinstance(data, dict) and data.get("layers"):
            cache[hero] = data
            config.ensure_dirs()
            with open(QIUBOT_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
            config.invalidate(QIUBOT_CACHE_PATH)
            return data
    except FetchError:
        pass
    return cache.get(hero) or {}


def qiubot_comp_to_builds(comp: dict, hero: str) -> list:
    """把 qiubot comp 数据转成完整流派 build（推荐用）。

    只取 **完整配置层（configs，5-7 件完整卡组）**——它们是真正的"整个流派"。
    核心组合/变体（2-4 件搭配）属于"物品搭配分析"，不参与流派推荐。
    """
    builds = []
    layers = comp.get("layers") or []
    for li, layer in enumerate(layers):
        core = [c.get("name_en") or c.get("name_zh") for c in (layer.get("core_cards") or []) if (c.get("name_en") or c.get("name_zh"))]
        if not core:
            continue
        for vi, vart in enumerate(layer.get("l2_variants") or []):
            vcore = [c.get("name_en") or c.get("name_zh") for c in (vart.get("core_cards") or []) if (c.get("name_en") or c.get("name_zh"))]
            confs = vart.get("configs") or []
            for ci, cfg in enumerate(confs):
                cards = [c.get("name_en") or c.get("name_zh") for c in (cfg.get("cards") or []) if (c.get("name_en") or c.get("name_zh"))]
                if not cards:
                    continue
                crate = cfg.get("appearance_rate") or 0
                base_name = "+".join(vcore) if vcore else "+".join(core)
                builds.append({
                    "hero": hero,
                    "title": f"[天梯] {base_name}·完整阵容",
                    "date": "", "link": QIUBOT_BASE,
                    "slug": f"qiubot-{hero}-{li}-v{vi}-c{ci}",
                    "score": f"出现率{crate*100:.2f}%",
                    "win": int(crate * 10000), "loss": max(1, int(10000 - crate * 10000)),
                    "author": "巴扎丘Bot", "type": "天梯流派", "types": ["天梯流派"],
                    "items": cards, "excerpt": f"完整阵容，出现率 {crate*100:.2f}%",
                    "source": "qiubot", "appearance_rate": crate,
                    "core_cards": core,  # 保留核心组合信息供展示
                })
    return builds


def qiubot_core_comps(hero: str) -> list:
    """qiubot 核心组合层（物品搭配分析用）：[{core, rate, count, variants}]。"""
    comp = fetch_qiubot_comp(hero)
    out = []
    for layer in comp.get("layers") or []:
        core = [c.get("name_zh") or c.get("name_en") for c in (layer.get("core_cards") or []) if (c.get("name_zh") or c.get("name_en"))]
        if not core:
            continue
        out.append({
            "core": core,
            "rate": layer.get("appearance_rate") or 0,
            "count": layer.get("count") or 0,
            "variants": len(layer.get("l2_variants") or []),
        })
    return out


def qiubot_partner(card_name: str, days: str = "") -> dict:
    """巴扎丘Bot 物品搭配查询：某物品最常一同使用的卡 + 10连胜概率。

    返回 {card_name, target_total, by_appear: [...], by_winrate: [...]}。
    """
    import urllib.parse
    url = f"{QIUBOT_BASE}/api/partner?card=" + urllib.parse.quote(card_name)
    if days:
        url += "&days=" + urllib.parse.quote(days)
    try:
        return _qiubot_http_get(url)
    except FetchError:
        return {"_error": "搭配查询失败"}


def qiubot_builds(hero: str, refresh: bool = False) -> list:
    """获取英雄的 qiubot 组合 build 列表（含缓存）。"""
    comp = fetch_qiubot_comp(hero, refresh)
    if not comp:
        return []
    return qiubot_comp_to_builds(comp, hero)


def build_thumbnail_url(slug: str):
    """获胜截图（og:image）URL，用于展示参考摆放。"""
    return f"https://bazaar-builds.net/wp-content/uploads/og-{slug}.png"  # 兜底模式，见 fetch_build_image


def fetch_build_image(slug: str, out_path: str) -> bool:
    """尝试下载流派页 og:image 截图（后台调用，超时较短避免长时间阻塞）。"""
    import urllib.parse
    # 通过页面 meta 拿真实图 URL
    try:
        page_url = f"https://bazaar-builds.net/{slug}/"
        html = _http_get_bytes(page_url, timeout=8).decode("utf-8", "replace")
        m = re.search(r'property="og:image"\s+content="([^"]+)"', html) or re.search(r'content="([^"]+)"[^>]*property="og:image"', html)
        img_url = m.group(1) if m else None
        if not img_url:
            return False
        data = _http_get_bytes(img_url, timeout=12)
        with open(out_path, "wb") as f:
            f.write(data)
        return len(data) > 1000
    except Exception:
        return False


def refresh_all(hero: str | None = None) -> dict:
    """一键刷新。返回 {items: n, builds: n, heroes: n}。"""
    result = {}
    items = fetch_items(refresh=True)
    result["items"] = len(items)
    heroes = fetch_heroes(refresh=True)
    result["heroes"] = len(heroes)
    if hero:
        builds = fetch_builds(hero, refresh=True)
        result["builds"] = len(builds)
    return result


if __name__ == "__main__":
    hero = sys.argv[1] if len(sys.argv) > 1 else "mak"
    print("items:", len(get_items()))
    print("heroes:", get_heroes())
    bs = fetch_builds(hero, refresh=True)
    print(f"builds({hero}):", len(bs))
    for b in bs[:5]:
        print(" -", b["title"], "| items:", b["items"])
