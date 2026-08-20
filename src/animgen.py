# -*- coding: utf-8 -*-
"""桌宠动画生成器：给定一张静态形象图（PNG 带透明通道），自动生成
启动 / 建议 / 教学 / 待机 四套动画（GIF + 帧清单）。

生成的动画保存在 assets/pet/anims/<pet_id>/ 下：
    startup.gif   弹入登场
    advice.gif    说话抖动 + 点头
    teach.gif     左右指点（含提示手势）
    idle.gif      轻微浮动待机

同时生成 manifest.json 描述动画参数，供悬浮窗播放。
"""
import json
import math
import os
import shutil

from PIL import Image, ImageDraw, ImageOps

from . import config

CANVAS = 256          # 画布尺寸
FPS = 12              # 默认帧率


# ---------------------------------------------------------------- 图像工具

def _remove_background(img: Image.Image) -> Image.Image:
    """去背景（仅用于动画模式）：
    - 已有 alpha 的图：原样使用
    - 无 alpha 的图（JPG 等）：只从边缘泛洪去除背景连通区域（带容差），
      不全局删白 —— 主体自身的白色部位（鱼肚/高光等）会保留。
    """
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            if px[x, y][3] != 255:
                return img  # 已有透明通道，不做处理
    try:
        from PIL import ImageDraw
        seed = px[0, 0][:3]
        # 只吃掉与边缘连通的、颜色接近边缘色的区域
        thresh = 22
        ImageDraw.floodfill(img, (0, 0), (0, 0, 0, 0), thresh=thresh)
        for corner in ((w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            c = img.getpixel(corner)
            if c[3] == 255 and sum(abs(int(c[i]) - int(seed[i])) for i in range(3)) < 60:
                ImageDraw.floodfill(img, corner, (0, 0, 0, 0), thresh=thresh)
    except Exception:
        pass
    return img


def _autocrop(img: Image.Image, margin_ratio: float = 0.09) -> Image.Image:
    """按内容包围盒裁剪并留出边距，避免贴边/留白过多。"""
    import numpy as np
    a = np.array(img)[:, :, 3]
    op = a > 10
    if not op.any():
        return img
    rows = np.where(op.any(axis=1))[0]
    cols = np.where(op.any(axis=0))[0]
    m = max(4, int(max(img.size) * margin_ratio))
    y0 = max(0, int(rows.min()) - m)
    y1 = min(img.height, int(rows.max()) + m)
    x0 = max(0, int(cols.min()) - m)
    x1 = min(img.width, int(cols.max()) + m)
    return img.crop((x0, y0, x1, y1))


def _load_pet_image(path: str) -> Image.Image:
    img = Image.open(path)
    # 大图先缩小，避免逐像素处理太慢
    if max(img.size) > 512:
        img.thumbnail((512, 512), Image.LANCZOS)
    img = _remove_background(img)
    return _autocrop(img)


def _fit(img: Image.Image, max_size: float) -> Image.Image:
    w, h = img.size
    scale = min(max_size / w, max_size / h)
    if scale < 1:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return img


def _compose(base: Image.Image, dx=0, dy=0, angle=0, scale=1.0, opacity=1.0) -> Image.Image:
    """在 CANVAS 画布上以位移/旋转/缩放绘制 base 中心（空画布，paste 即可）。"""
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    w, h = base.size
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    layer = base.resize((nw, nh), Image.LANCZOS)
    if angle:
        layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    if opacity < 1:
        layer = Image.blend(Image.new("RGBA", layer.size, (0, 0, 0, 0)), layer, opacity)
    x = int((CANVAS - layer.width) / 2 + dx)
    y = int((CANVAS - layer.height) / 2 + dy)
    canvas.paste(layer, (x, y), layer)
    return canvas


def _ease_out_back(t: float) -> float:
    """回弹缓动。"""
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _save_gif(frames, path, duration_ms):
    """保存 GIF 预览（修正透明：量化后把调色板索引0固定为透明色）。"""
    def to_gif_frame(im):
        im = im.convert("RGBA")
        alpha = im.getchannel("A")
        p = im.convert("RGB").quantize(colors=255, method=Image.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
        # 重映射：新调色板 = [透明占位黑] + 255 个量化色
        pal = p.getpalette()
        new_pal = [0, 0, 0] + list(pal[: 255 * 3])
        remap = list(range(1, 256)) + [0]
        out = p.point(remap)
        out.putpalette(new_pal)
        mask = alpha.point(lambda a: 255 if a < 128 else 0)
        out.paste(0, mask=mask)
        return out
    try:
        gf = [to_gif_frame(f) for f in frames]
        gf[0].save(path, save_all=True, append_images=gf[1:],
                   duration=duration_ms, loop=0, disposal=2, transparency=0)
    except Exception:
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration_ms, loop=0)
    return os.path.basename(path)


def _save_frames(frames, out_dir, prefix, duration_ms):
    """保存 PNG 帧序列（悬浮窗实际播放用，透明保真）。"""
    names = []
    for i, f in enumerate(frames):
        fn = f"{prefix}_{i:03d}.png"
        f.save(os.path.join(out_dir, fn))
        names.append(fn)
    return names, max(16, duration_ms // max(1, len(frames)))


# ---------------------------------------------------------------- 动画

def gen_startup(base: Image.Image, n: int = 14) -> list:
    """弹入 + 回弹 + 落地。"""
    frames = []
    for i in range(n):
        t = i / (n - 1)
        if t < 0.7:
            k = _ease_out_back(t / 0.7)
            scale = 0.3 + 0.7 * k
            dy = int((1 - k) * CANVAS * 0.5)
        else:
            tt = (t - 0.7) / 0.3
            scale = 1.0 - 0.06 * math.sin(tt * math.pi * 2)
            dy = int(4 * math.sin(tt * math.pi))
        frames.append(_compose(base, dy=dy, scale=scale))
    return frames


def gen_advice(base: Image.Image, n: int = 24) -> list:
    """说话抖动：小幅左右摇摆 + 上下点头。"""
    frames = []
    for i in range(n):
        t = i / (n - 1)
        wobble = math.sin(t * math.pi * 6)
        dx = int(3 * wobble)
        dy = int(2 * math.sin(t * math.pi * 3))
        tilt = 3 * wobble
        frames.append(_compose(base, dx=dx, dy=dy, angle=tilt))
    return frames


def gen_teach(base: Image.Image, n: int = 30) -> list:
    """指点：向右倾斜并画出手势箭头。"""
    frames = []
    for i in range(n):
        t = i / (n - 1)
        phase = 0
        if t < 0.35:
            phase = t / 0.35  # 蓄力左倾
        elif t < 0.8:
            phase = 1 + (t - 0.35) / 0.45  # 向右指点
        else:
            phase = 2 + (t - 0.8) / 0.2  # 收回
        dx = int(6 * math.sin(phase * math.pi))
        tilt = -8 + 18 * (1 if phase > 1 else phase) if phase <= 2 else -8 + 18 * (2 - phase)
        frame = _compose(base, dx=dx, angle=tilt)
        # 画指示箭头
        draw = ImageDraw.Draw(frame)
        ax = CANVAS - 34 + dx
        ay = CANVAS - 34
        draw.polygon([(ax - 16, ay + 10), (ax, ay), (ax - 16, ay - 10), (ax - 14, ay), (ax - 16, ay + 10)],
                     fill=(255, 60, 60, 230))
        frames.append(frame)
    return frames


def gen_idle(base: Image.Image, n: int = 24) -> list:
    """待机浮动。"""
    frames = []
    for i in range(n):
        t = i / (n - 1)
        dy = int(4 * math.sin(t * math.pi * 2))
        frames.append(_compose(base, dy=dy, scale=1.0 + 0.02 * math.sin(t * math.pi * 2)))
    return frames


GENERATORS = {
    "startup": (gen_startup, {"n": 14}),
    "advice": (gen_advice, {"n": 24}),
    "teach": (gen_teach, {"n": 30}),
    "idle": (gen_idle, {"n": 24}),
}


# ---------------------------------------------------------------- 主流程

def list_pets() -> list:
    """已生成的桌宠列表 [{id, name, manifest}]。"""
    out = []
    d = config.PET_ANIMS_DIR
    if not os.path.isdir(d):
        return out
    for pid in sorted(os.listdir(d)):
        mf = os.path.join(d, pid, "manifest.json")
        if os.path.exists(mf):
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    out.append({"id": pid, "manifest": json.load(f)})
            except Exception:
                continue
    return out


def generate_pet(source_path: str, pet_id: str = None, name: str = "") -> dict:
    """从源图生成全部动画。返回 manifest 字典。"""
    config.ensure_dirs()
    base = _fit(_load_pet_image(source_path), CANVAS * 0.82)
    pet_id = pet_id or os.path.splitext(os.path.basename(source_path))[0]
    out_dir = os.path.join(config.PET_ANIMS_DIR, pet_id)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    # 图标（用于列表预览）：直接用原图，不做抠图
    icon = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    raw = Image.open(source_path).convert("RGBA")
    raw.thumbnail((80, 80), Image.LANCZOS)
    icon.alpha_composite(raw, ((96 - raw.width) // 2, (96 - raw.height) // 2))
    icon.save(os.path.join(config.PET_ICONS_DIR, f"{pet_id}.png"))

    anims = {}
    for key, (fn, kw) in GENERATORS.items():
        frames = fn(base, **kw)
        gif = _save_gif(frames, os.path.join(out_dir, f"{key}.gif"), int(1000 / FPS))
        png_names, frame_ms = _save_frames(frames, out_dir, key, int(1000 / FPS * len(frames)))
        anims[key] = {"file": gif, "frames": png_names, "frame_ms": frame_ms,
                      "count": len(frames), "duration_ms": frame_ms * len(frames)}

    manifest = {
        "id": pet_id,
        "name": name or pet_id,
        "source": os.path.basename(source_path),
        "canvas": CANVAS,
        "fps": FPS,
        "anims": anims,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def generate_demo_pet():
    """生成一个内置示例桌宠（圆球+眼睛），便于首次运行即可演示。"""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((28, 48, 228, 248), fill=(255, 183, 77, 255))            # 身体
    d.ellipse((88, 118, 124, 154), fill=(40, 30, 20, 255))              # 左眼
    d.ellipse((132, 118, 168, 154), fill=(40, 30, 20, 255))             # 右眼
    d.ellipse((104, 150, 116, 162), fill=(255, 255, 255, 255))
    d.ellipse((140, 150, 152, 162), fill=(255, 255, 255, 255))
    d.arc((100, 168, 156, 210), 20, 160, fill=(120, 60, 30, 255), width=6)  # 嘴
    d.ellipse((36, 38, 92, 70), fill=(255, 120, 120, 255))              # 左腮红
    d.ellipse((164, 38, 220, 70), fill=(255, 120, 120, 255))            # 右腮红
    src = os.path.join(config.PET_INPUT_DIR, "demo_pet.png")
    img.save(src)
    return generate_pet(src, pet_id="demo_pet", name="示例桌宠")


if __name__ == "__main__":
    config.ensure_dirs()
    mf = generate_demo_pet()
    print("demo pet generated:", mf["id"])
    print("anims:", {k: v["file"] for k, v in mf["anims"].items()})
