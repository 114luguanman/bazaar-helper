# -*- coding: utf-8 -*-
"""物品识别模块。

主路径：OCR（RapidOCR，纯 pip 安装，含中英文模型）→ 与 926 件物品名模糊匹配。
备选路径：图标模板匹配（cv2.matchTemplate，模板放 templates/items/<名称>.png）。
"""
import difflib
import os
import re

import numpy as np

from . import config, datahub

_OCR = None
_OCR_ERROR = None
_TEMPLATE_CACHE = {}


# ---------------------------------------------------------------- OCR

def get_ocr():
    global _OCR, _OCR_ERROR
    if _OCR is not None:
        return _OCR
    if _OCR_ERROR:
        raise _OCR_ERROR
    try:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
        return _OCR
    except Exception as e:  # noqa: BLE001
        _OCR_ERROR = RuntimeError(f"OCR 引擎初始化失败（请安装 rapidocr-onnxruntime）: {e}")
        raise _OCR_ERROR


def ocr_frame(frame_bgr) -> list:
    """返回 [(text, score, box)]，box 为 4 点 [[x,y],...] 文本包围盒。"""
    try:
        engine = get_ocr()
    except RuntimeError:
        return []
    try:
        result, _ = engine(frame_bgr)
    except Exception:
        return []
    out = []
    for item in (result or []):
        try:
            box, text, score = item[0], item[1], item[2]
            out.append((str(text), float(score), box))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- 名称匹配

def _norm(s: str) -> str:
    """通用规范化：保留中文/字母/数字/空格，去标点。"""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9 ]", "", s.lower()).strip()


def _norm_zh(s: str) -> str:
    """中文名规范化：保留汉字/字母/数字，去掉标点空格。"""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s).lower()


def _box_center(box):
    try:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        return float(np.mean(xs)), float(np.mean(ys))
    except Exception:
        return None


def _build_vocab(vocabulary: dict):
    """构建双语匹配结构：英文（含无空格形式）+ 中文（nameCn -> 英文键）。"""
    en_names = sorted((n for n in vocabulary.keys() if len(n) >= 3), key=len, reverse=True)
    en_ns = {n.replace(" ", ""): n for n in en_names}
    zh_map = {}
    for key, item in vocabulary.items():
        cn = item.get("nameCn") or ""
        if re.search(r"[\u4e00-\u9fff]", cn):
            z = _norm_zh(cn)
            if z and z not in zh_map:
                zh_map[z] = key
    zh_names = sorted(zh_map.keys(), key=len, reverse=True)
    return en_names, en_ns, zh_map, zh_names


_ENCHANT_WORDS = {
    "icy", "fiery", "golden", "shiny", "heavy", "radiant", "toxic", "poisoned",
    "frozen", "burning", "swift", "sharp", "glowing", "ancient", "lunar", "solar",
}


def _match_zh(line: str, zh_map: dict, zh_names: list, raw_score: float, line_ns: str):
    """中文名匹配：精确 -> 独立子串 -> 模糊。返回 (english_key, kind)。"""
    if line in zh_map:
        return zh_map[line], "exact"
    if line_ns in zh_map:
        return zh_map[line_ns], "exact"
    for z in zh_names:
        if len(z) < 2:
            continue
        if z in line_ns and len(line_ns) <= len(z) + 4:  # 接近独立成词
            return zh_map[z], "substr"
    if len(line) >= 3:
        best, best_r = None, 0.0
        for z in zh_names[:150]:
            if abs(len(z) - len(line)) > 3:
                continue
            r = difflib.SequenceMatcher(None, z, line).ratio()
            if r > best_r:
                best, best_r = z, r
        if best and best_r >= 0.85 and raw_score >= 0.6:
            return zh_map[best], "fuzzy"
    return None, None


def _match_en(line: str, en_names: list, en_ns: dict, vocabulary: dict, raw_score: float, line_ns: str):
    """英文名匹配（收紧版）：精确 -> 无空格精确 -> 独立子串 -> 模糊。"""
    if line in vocabulary:
        return line, "exact"
    if line_ns in en_ns:
        return en_ns[line_ns], "exact"
    # 子串：允许词尾数量词("x2")或附魔前缀("Icy Magic Carpet")，拒绝长句
    for n in en_names:
        if len(n) < 5:
            continue
        if n in line:
            idx = line.index(n)
            head = line[:idx].strip()
            tail = line[idx + len(n):].strip()
            if (not head or (len(head.split()) <= 2 and head.split()[0] in _ENCHANT_WORDS)) and (
                    not tail or re.fullmatch(r"[a-z]{0,3}\d{0,2}", tail)):
                return n, "substr"
            break  # en_names 按长度降序，取最长命中
    # 无空格子串（如 RunicDaggers）
    if len(line_ns) >= 5:
        for n in en_names:
            ns = n.replace(" ", "")
            if len(ns) >= 5 and ns in line_ns and len(line_ns) - len(ns) <= 4:
                return n, "substr"
    # 模糊：仅较长行、高阈值
    if len(line) >= 6 and raw_score >= 0.6:
        best, best_r = None, 0.0
        for n in en_names[:200]:
            if abs(len(n) - len(line)) > 4:
                continue
            r = difflib.SequenceMatcher(None, n, line).ratio()
            if r > best_r:
                best, best_r = n, r
        if best and best_r >= 0.9:
            return best, "fuzzy"
    return None, None


def match_items_from_ocr(ocr_lines, vocabulary: dict, min_score: float = 0.86) -> dict:
    """把 OCR 文本行匹配到物品名（支持中英文）。

    vocabulary: {规范化名: item_dict}
    返回 {英文显示名: {count, positions, source}}
    """
    en_names, en_ns, zh_map, zh_names = _build_vocab(vocabulary)
    found = {}

    for text, score, box in ocr_lines:
        line = _norm(text)
        if not line or not re.search(r"[a-z\u4e00-\u9fff]", line):
            continue
        if len(line) > 40:
            continue
        line_ns = line.replace(" ", "")
        if re.search(r"[\u4e00-\u9fff]", line):
            key, kind = _match_zh(line, zh_map, zh_names, score, line_ns)
        else:
            key, kind = _match_en(line, en_names, en_ns, vocabulary, score, line_ns)
        if not key:
            continue
        conf = min(1.0, score * (0.9 if kind == "exact" else 0.75 if kind == "substr" else 0.6))
        if conf < 0.45:
            continue
        entry = found.setdefault(vocabulary[key]["name"], {"count": 0, "positions": [], "source": "ocr"})
        entry["count"] += 1
        c = _box_center(box)
        if c:
            entry["positions"].append((c[0], c[1], round(conf, 3)))
    return found


# ---------------------------------------------------------------- 模板匹配（可选）

def load_templates():
    """加载 templates/items/*.png -> {名称: 模板图(BGR)}。"""
    if _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE
    import cv2
    d = config.TEMPLATES_DIR
    if not os.path.isdir(d):
        return {}
    for fn in sorted(os.listdir(d)):
        if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            name = os.path.splitext(fn)[0]
            img = cv2.imread(os.path.join(d, fn), cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            _TEMPLATE_CACHE[name] = img
    return _TEMPLATE_CACHE


def match_items_by_template(frame_bgr, threshold: float = 0.72) -> dict:
    """多尺度模板匹配；返回 {名称: {count, positions, source}}。"""
    import cv2
    templates = load_templates()
    if not templates:
        return {}
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    found = {}
    for name, tmpl in templates.items():
        t = tmpl
        if t.shape[-1] == 4:
            t = cv2.cvtColor(t, cv2.COLOR_BGRA2BGR)
        tg = cv2.cvtColor(t, cv2.COLOR_BGR2GRAY)
        th, tw = tg.shape[:2]
        best = None
        for scale in (1.0, 0.85, 0.7, 1.15):
            w, h = int(tw * scale), int(th * scale)
            if w < 12 or h < 12:
                continue
            resized = cv2.resize(tg, (w, h), interpolation=cv2.INTER_AREA)
            if w > gray.shape[1] or h > gray.shape[0]:
                continue
            res = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            if best is None or maxv > best[0]:
                best = (maxv, maxloc, scale)
        if best and best[0] >= threshold:
            maxv, (x, y), scale = best
            cx, cy = x + tw * scale / 2, y + th * scale / 2
            entry = found.setdefault(name, {"count": 0, "positions": [], "source": "template"})
            entry["count"] += 1
            entry["positions"].append((cx, cy, round(float(maxv), 3)))
    return found


# ---------------------------------------------------------------- 卡图识别（模板）

_DESC_CACHE = {"mtime": None, "descs": None}


def _icon_descriptors():
    """加载 templates/items/*.png 为特征描述子（48x48 灰度 + 8x8 颜色块，带缓存）。"""
    import cv2
    d = config.TEMPLATES_DIR
    if not os.path.isdir(d):
        return {}
    try:
        mtime = max(os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d))
    except OSError:
        mtime = 0
    if _DESC_CACHE["mtime"] == mtime and _DESC_CACHE["descs"] is not None:
        return _DESC_CACHE["descs"]
    descs = {}
    for fn in sorted(os.listdir(d)):
        if fn.lower().endswith(".png"):
            name = os.path.splitext(fn)[0]
            try:
                img = cv2.imdecode(np.fromfile(os.path.join(d, fn), dtype=np.uint8), cv2.IMREAD_COLOR)
            except Exception:
                continue
            if img is None:
                continue
            descs[name] = _frame_descriptor(img)
    _DESC_CACHE["mtime"] = mtime
    _DESC_CACHE["descs"] = descs
    return descs


def _frame_descriptor(img):
    """48x48 灰度归一化 + 8x8 分块颜色均值 -> 组合特征向量。"""
    import cv2
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (48, 48), interpolation=cv2.INTER_AREA).astype(np.float32)
    small -= small.mean()
    n = np.linalg.norm(small)
    if n > 1e-6:
        small /= n
    # 颜色块特征：每个 8x8 块的三通道均值
    small_c = cv2.resize(img, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float32)
    color = small_c.reshape(-1, 3).T.reshape(-1)  # 8*8*3 = 192
    color -= color.mean()
    n2 = np.linalg.norm(color)
    if n2 > 1e-6:
        color /= n2
    return np.concatenate([small.reshape(-1), color])


def _merge_rects(rects, iou_thresh: float = 0.35):
    """合并重叠/嵌套的矩形（保留较大者）。"""
    rects = [r for r in rects if r[2] > 0 and r[3] > 0]
    rects.sort(key=lambda r: r[2] * r[3], reverse=True)
    kept = []
    for r in rects:
        x, y, w, h = r
        overlap = False
        for k in kept:
            kx, ky, kw, kh = k
            ix = max(0, min(x + w, kx + kw) - max(x, kx))
            iy = max(0, min(y + h, ky + kh) - max(y, ky))
            inter = ix * iy
            if inter / (w * h) > iou_thresh or inter / (kw * kh) > iou_thresh:
                overlap = True
                break
        if not overlap:
            kept.append(r)
    return kept


def detect_cards(frame_bgr, max_cards: int = 24) -> list:
    """检测卡牌矩形：彩色稀有度边框 + 尺寸/宽高比/暗色卡面过滤 UI 元素。返回 [(x,y,w,h), ...]。"""
    import cv2
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0].astype(np.int16)
    s = hsv[:, :, 1].astype(np.int16)
    v = hsv[:, :, 2].astype(np.int16)
    gold = ((h >= 12) & (h <= 45) & (s > 70) & (v > 110))
    silver = ((s < 70) & (v > 160) & (v < 250))
    bronze = ((h < 22) & (s > 80) & (v > 100))
    mask = cv2.bitwise_or(cv2.bitwise_or(gold, silver), bronze).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cands = []
    for c in contours:
        x, y, w, hh = cv2.boundingRect(c)
        if w < 80 or hh < 80:
            continue
        ar = w / hh
        if ar < 0.5 or ar > 1.05:
            continue  # 卡牌宽高比：接近正方形或略高（排除长条按钮/横幅）
        # 暗色卡面校验（卡面应明显暗于 UI 面板）
        inner = frame_bgr[int(y + hh * 0.15):int(y + hh * 0.85), int(x + w * 0.10):int(x + w * 0.90)]
        if inner.size == 0:
            continue
        mean_v = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)[:, :, 2].mean()
        if mean_v > 140:
            continue
        cands.append((int(x), int(y), int(w), int(hh)))
    cands = _merge_rects(cands)
    # 网格/尺寸一致性校验：保留尺寸接近主体的簇（去除零星 UI 元素）
    if len(cands) >= 4:
        sizes = sorted(r[2] * r[3] for r in cands)
        med = sizes[len(sizes) // 2]
        kept = [r for r in cands if med * 0.4 <= r[2] * r[3] <= med * 2.2]
        if len(kept) >= 3:
            cands = kept
    cands.sort(key=lambda r: (r[1] // 60, r[0]))
    return cands[:max_cards]


def _ocr_card_name(card_bgr):
    """对卡面整体 OCR，返回匹配到的物品名（中文名优先）。无匹配返回 None。"""
    import cv2
    h, w = card_bgr.shape[:2]
    if h < 30 or w < 30:
        return None
    big = cv2.resize(card_bgr, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    try:
        lines = ocr_frame(big)
    except Exception:
        return None
    if not lines:
        return None
    matched = match_items_from_ocr(lines, datahub.get_items(), min_score=0.8)
    if not matched:
        return None
    # 取置信度最高的（卡面上的名字行）
    best_name, best_conf = None, 0.0
    for name, info in matched.items():
        conf = info["positions"][0][2] if info.get("positions") else 0.0
        if conf > best_conf:
            best_name, best_conf = name, conf
    return best_name, best_conf


# 候选图标区裁剪（宽度占比, 上边距占比, 高度占比）——多尺寸搜索适配不同卡面布局
_CROP_PRESETS = [
    (0.50, 0.03, 0.62), (0.58, 0.04, 0.60), (0.65, 0.05, 0.58),
    (0.72, 0.05, 0.58), (0.80, 0.05, 0.58), (0.88, 0.04, 0.62),
]


def _locate_icon_box(card_bgr):
    """在卡面内用边缘能量定位图标区域（避开边框与底部文字）。返回 (x0,y0,x1,y1) 卡内坐标。"""
    import cv2
    gray = cv2.cvtColor(card_bgr, cv2.COLOR_BGR2GRAY)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    h, w = gray.shape
    x0s, x1s = int(w * 0.07), int(w * 0.93)
    y0s, y1s = int(h * 0.04), int(h * 0.70)
    sub = lap[y0s:y1s, x0s:x1s]
    thr = sub > (sub.mean() + 0.8 * sub.std())
    rows = np.where(thr.any(axis=1))[0]
    cols = np.where(thr.any(axis=0))[0]
    if len(rows) < 4 or len(cols) < 4:
        return None
    y0, y1 = int(rows.min()) + y0s, int(rows.max()) + y0s
    x0, x1 = int(cols.min()) + x0s, int(cols.max()) + x0s
    m = max(2, int(min(w, h) * 0.02))
    return (max(x0s, x0 - m), max(y0s, y0 - m), min(x1s, x1 + m), min(y1s, y1 + m))


def _card_icon_scores(frame_bgr, box, mat, margin=0.04):
    """计算图标区与所有模板的相关分，返回 (idx, score, second_score)。"""
    import cv2
    x0, y0, x1, y1 = box
    x1 = min(frame_bgr.shape[1], x1)
    y1 = min(frame_bgr.shape[0], y1)
    if x1 - x0 < 12 or y1 - y0 < 12:
        return None
    icon = frame_bgr[y0:y1, x0:x1]
    d = _frame_descriptor(icon)
    scores = mat @ d
    idx = int(np.argmax(scores))
    best = float(scores[idx])
    if margin > 0:
        rest = np.delete(scores, idx)
        second = float(rest.max()) if rest.size else best
    else:
        second = best
    return idx, best, second


# ---------------------------------------------------------------- ORB 特征匹配

_ORB_STATE = {"orb": None, "bf": None, "names": [], "ready": False}


def _orb_init():
    """初始化 ORB 特征匹配器（模板图 -> ORB 特征，带缓存）。"""
    if _ORB_STATE["ready"]:
        return True
    import cv2
    d = config.TEMPLATES_DIR
    if not os.path.isdir(d):
        return False
    try:
        orb = cv2.ORB.create(nfeatures=1500)
        names, train = [], []
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith(".png"):
                name = os.path.splitext(fn)[0]
                img = cv2.imdecode(np.fromfile(os.path.join(d, fn), np.uint8), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                kps, desc = orb.detectAndCompute(img, None)
                if desc is None or len(desc) == 0:
                    desc = np.zeros((1, 32), dtype=np.uint8)
                names.append(name)
                train.append(desc)
        if not names:
            return False
        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        bf.add(train)
        bf.train()
        _ORB_STATE.update({"orb": orb, "bf": bf, "names": names, "ready": True})
        return True
    except Exception:
        return False


def match_card_orb(card_bgr, min_matches: int = 6) -> tuple:
    """ORB 特征匹配单张卡。返回 (名称, 匹配数) 或 None。"""
    import cv2
    if not _orb_init():
        return None
    gray = cv2.cvtColor(card_bgr, cv2.COLOR_BGR2GRAY)
    kps, desc = _ORB_STATE["orb"].detectAndCompute(gray, None)
    if desc is None or len(desc) == 0:
        return None
    try:
        matches = _ORB_STATE["bf"].knnMatch(desc, k=2)
    except Exception:
        return None
    counts = {}
    for m in matches:
        if len(m) == 2 and m[0].distance < 0.7 * m[1].distance:
            name = _ORB_STATE["names"][m[0].imgIdx]
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    top = max(counts.items(), key=lambda kv: kv[1])
    if top[1] < min_matches:
        return None
    return top


def match_card_icon(card_bgr, threshold: float = 0.75, margin: float = 0.08):
    """识别单张卡：① 卡面 OCR（若有名字）② ORB 特征匹配 ③ 图标描述子兜底。返回 (名称, 置信度, 来源)。"""
    # 1) 卡面 OCR（部分界面卡上有名字）
    hit = _ocr_card_name(card_bgr)
    if hit:
        return hit[0], hit[1], "cardname"
    h, w = card_bgr.shape[:2]
    # 2) ORB 特征匹配（整卡 + 上中部裁剪）
    orb_res = match_card_orb(card_bgr)
    if orb_res is None and h > 40 and w > 40:
        crop = card_bgr[int(h * 0.10):int(h * 0.75), int(w * 0.15):int(w * 0.85)]
        if crop.size:
            orb_res = match_card_orb(crop)
    if orb_res:
        name, cnt = orb_res
        # 匹配数转置信度（粗略）：20+ 特征=高置信
        conf = min(1.0, 0.5 + cnt / 60.0)
        return name, round(conf, 3), "orb"
    # 3) 图标描述子兜底
    descs = _icon_descriptors()
    if not descs:
        return None
    names = list(descs.keys())
    mat = np.stack([descs[n] for n in names]).reshape(len(names), -1)
    best_score, best_name = -1.0, None
    box = _locate_icon_box(card_bgr)
    if box:
        s = _card_icon_scores(card_bgr, box, mat, margin=margin)
        if s:
            idx, sc, second = s
            if sc - second >= margin or margin <= 0:
                best_score, best_name = sc, names[idx]
    if best_score < threshold:
        for wf, tf, hf in _CROP_PRESETS:
            iw, ih = int(w * wf), int(h * hf)
            if iw < 20 or ih < 20:
                continue
            ix = int((w - iw) / 2)
            iy = int(h * tf)
            s = _card_icon_scores(card_bgr, (ix, iy, ix + iw, iy + ih), mat, margin=margin)
            if s and s[1] > best_score:
                best_score, best_name = s[1], names[s[0]]
    if best_name and best_score >= threshold:
        return best_name, round(best_score, 3), "template"
    return None


def match_cards(frame_bgr, rects: list, threshold: float = 0.75, margin: float = 0.08) -> dict:
    """对每张卡：① 卡面名字 OCR（最可靠）② 图标模板兜底。返回 {名称: {...}}。"""
    import cv2
    descs = _icon_descriptors()
    names = list(descs.keys())
    mat = np.stack([descs[n] for n in names]).reshape(len(names), -1) if names else None
    results = {}
    for (x, y, w, h) in rects:
        card = frame_bgr[y:y + h, x:x + w]
        # 1) 卡面名字 OCR
        hit = _ocr_card_name(card)
        if hit:
            name, conf = hit
            entry = results.setdefault(name, {"count": 0, "positions": [], "source": "cardname"})
            entry["count"] += 1
            entry["positions"].append((x + w / 2.0, y + h / 2.0, round(conf, 3)))
            continue
        # 2) 图标模板兜底
        if mat is None:
            continue
        best_score, best_name, best_pos = -1.0, None, (x + w / 2.0, y + h / 2.0)
        box = _locate_icon_box(card)
        if box:
            abs_box = (x + box[0], y + box[1], x + box[2], y + box[3])
            s = _card_icon_scores(frame_bgr, abs_box, mat, margin=margin)
            if s:
                idx, sc, second = s
                if sc - second >= margin or margin <= 0:
                    best_score, best_name = sc, names[idx]
                    best_pos = ((abs_box[0] + abs_box[2]) / 2.0, (abs_box[1] + abs_box[3]) / 2.0)
        if best_score < threshold:
            for wf, tf, hf in _CROP_PRESETS:
                iw, ih = int(w * wf), int(h * hf)
                if iw < 20 or ih < 20:
                    continue
                ix = int(x + (w - iw) / 2)
                iy = int(y + h * tf)
                s = _card_icon_scores(frame_bgr, (ix, iy, ix + iw, iy + ih), mat, margin=margin)
                if s and s[1] > best_score:
                    best_score, best_name = s[1], names[s[0]]
                    best_pos = (ix + iw / 2.0, iy + ih / 2.0)
        if best_name and best_score >= threshold:
            entry = results.setdefault(best_name, {"count": 0, "positions": [], "source": "template"})
            entry["count"] += 1
            entry["positions"].append((best_pos[0], best_pos[1], round(best_score, 3)))
    return results


def debug_card_view(frame_bgr, rects: list, results: dict = None, out_path: str = None):
    """生成卡牌检测标注图（诊断用）。"""
    import cv2
    anno = frame_bgr.copy()
    for x, y, w, h in rects:
        cv2.rectangle(anno, (x, y), (x + w, y + h), (0, 255, 0), 2)
        if results:
            # 找落在该卡内的识别结果
            cx, cy = x + w / 2, y + h / 2
            for name, info in results.items():
                for px, py, conf in info["positions"]:
                    if abs(px - cx) < w * 0.5 and abs(py - cy) < h * 0.5:
                        cv2.putText(anno, f"{name} {conf:.2f}", (x + 4, y + 14),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    if out_path:
        cv2.imencode(".png", anno)[1].tofile(out_path)  # 兼容中文路径
    return anno


# ---------------------------------------------------------------- YOLO 卡牌检测

_YOLO_STATE = {"sess": None, "path": None}


def _yolo_session():
    """加载 YOLO 卡牌检测模型（data/models/best.onnx）。"""
    path = os.path.join(config.DATA_DIR, "models", "best.onnx")
    if not os.path.exists(path):
        return None
    if _YOLO_STATE["sess"] is None or _YOLO_STATE["path"] != path:
        try:
            import onnxruntime as ort
            _YOLO_STATE["sess"] = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            _YOLO_STATE["path"] = path
        except Exception:
            _YOLO_STATE["sess"] = None
    return _YOLO_STATE["sess"]


def yolo_detect_cards(frame_bgr, conf_thr: float = 0.45, card_class: int = 2, max_cards: int = 24) -> list:
    """用 YOLO 模型检测屏幕上的卡牌。返回 [(x, y, w, h), ...]（原图坐标）。"""
    import cv2
    sess = _yolo_session()
    if sess is None:
        return []
    h, w = frame_bgr.shape[:2]
    scale = 640 / max(h, w)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    blob = (canvas[:, :, ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
    try:
        out = sess.run(None, {"images": blob})[0]
    except Exception:
        return []
    pred = out[0]
    boxes = pred[:4].T
    cls = pred[4:].T
    maxs = cls.max(axis=1)
    cids = cls.argmax(axis=1)
    keep = (maxs > conf_thr) & (cids == card_class)
    boxes, maxs = boxes[keep], maxs[keep]
    rects = []
    for b, sc in zip(boxes, maxs):
        x1 = max(0, int((b[0] - b[2] / 2) / scale))
        y1 = max(0, int((b[1] - b[3] / 2) / scale))
        x2 = min(w, int((b[0] + b[2] / 2) / scale))
        y2 = min(h, int((b[1] + b[3] / 2) / scale))
        if x2 - x1 < 30 or y2 - y1 < 30:
            continue
        rects.append((x1, y1, x2 - x1, y2 - y1, float(sc)))
    # 手动 NMS（按置信度降序去重叠）
    rects.sort(key=lambda r: -r[4])
    kept = []
    for r in rects:
        x, y, ww, hh, _sc = r
        dup = False
        for k in kept:
            kx, ky, kw, kh = k[:4]
            ix = max(0, min(x + ww, kx + kw) - max(x, kx))
            iy = max(0, min(y + hh, ky + kh) - max(y, ky))
            inter = ix * iy
            if inter / (ww * hh) > 0.3 or inter / (kw * kh) > 0.3:
                dup = True
                break
        if not dup:
            kept.append(r)
    return [(r[0], r[1], r[2], r[3]) for r in kept][:max_cards]


# ---------------------------------------------------------------- 综合入口

def detect_items(frame_bgr, cfg: dict | None = None) -> dict:
    """识别画面中的物品：YOLO 卡牌定位 + 卡面/图标识别 + OCR。返回 {名称: {count, positions, source}}。"""
    cfg = cfg or config.load_config()
    result = {}
    if cfg.get("template_enabled", True):
        try:
            rects = yolo_detect_cards(frame_bgr)
            if not rects:
                rects = detect_cards(frame_bgr)  # 兜底：颜色检测
            result.update(match_cards(frame_bgr, rects))
        except Exception:
            pass
    if cfg.get("ocr_enabled", True):
        try:
            lines = ocr_frame(frame_bgr)
            result.update(match_items_from_ocr(lines, datahub.get_items()))
        except Exception:
            pass
    return result


def detect_player_board(frame_bgr, cfg: dict | None = None) -> dict:
    """只扫玩家棋盘区域（屏幕下半部分，The Bazaar 玩家棋盘/备战区在下方、对手在上方）。

    用于补充日志识别不到的事件/技能获得的卡（日志只记录商店购买）。
    返回 {名称: {count, positions, source: "board"}}。若识别区域未配置则返回空。
    """
    cfg = cfg or config.load_config()
    region = cfg.get("board_region")  # [left, top, width, height]（全屏坐标，可经校准设置）
    if not region or len(region) != 4:
        return {}
    import cv2
    fh, fw = frame_bgr.shape[:2]
    x0 = max(0, int(region[0]))
    y0 = max(0, int(region[1]))
    x1 = min(fw, int(region[0] + region[2]))
    y1 = min(fh, int(region[1] + region[3]))
    if x1 - x0 < 80 or y1 - y0 < 80:
        return {}
    sub = frame_bgr[y0:y1, x0:x1]
    rects = yolo_detect_cards(sub)
    if not rects:
        rects = detect_cards(sub)
    if not rects:
        return {}
    results = match_cards(sub, rects, threshold=0.80)  # 更高的阈值：仅确认度高的才补充
    out = {}
    for name, info in results.items():
        entry = out.setdefault(name, {"count": 0, "positions": [], "source": "board"})
        entry["count"] += info["count"]
        entry["positions"].extend((px + x0, py + y0, conf) for px, py, conf in info["positions"])
    return out


def names_of(detected: dict) -> list:
    """检测到的物品显示名列表（按出现次数）。"""
    return sorted(detected.keys(), key=lambda k: -detected[k]["count"])


if __name__ == "__main__":
    import sys
    img_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not img_path:
        print("用法: python -m src.recognize <图片路径>")
        sys.exit(0)
    from .capture import load_frame
    frame = load_frame(img_path)
    print("图片:", img_path, frame.shape)
    res = detect_items(frame)
    for name, info in sorted(res.items(), key=lambda kv: -kv[1]["count"]):
        print(f"  {name} x{info['count']}  pos={info['positions'][:2]}")
    if not res:
        print("  (未识别到物品)")
