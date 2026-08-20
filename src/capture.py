# -*- coding: utf-8 -*-
"""屏幕捕获模块（基于 mss，多显示器支持）。

用法:
    cap = ScreenCapture()
    frame = cap.grab(region=None, monitor=0)   # 返回 RGB numpy 数组
"""
import numpy as np

_REGION_CACHE = {}


def list_monitors():
    """返回 [{index, left, top, width, height, name}]。"""
    import mss
    cls = getattr(mss, "MSS", None) or mss.mss
    with cls() as sct:
        out = []
        for i, m in enumerate(sct.monitors):
            out.append({
                "index": i,
                "left": m["left"], "top": m["top"],
                "width": m["width"], "height": m["height"],
                "name": m.get("name", f"显示器{i}"),
            })
        return out


class ScreenCapture:
    def __init__(self):
        self._sct = None
        self._last_size = None

    @property
    def sct(self):
        if self._sct is None:
            import mss
            cls = getattr(mss, "MSS", None) or mss.mss
            self._sct = cls()
        return self._sct

    def close(self):
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None

    def grab(self, region=None, monitor=0):
        """region: None=整个显示器; 或 [left, top, width, height]（虚拟屏幕坐标）。"""
        if region is None:
            ms = self.sct.monitors
            if monitor < 0 or monitor >= len(ms):
                monitor = 0
            box = ms[monitor]
        else:
            left, top, w, h = region
            box = {"left": int(left), "top": int(top), "width": int(w), "height": int(h)}
        shot = self.sct.grab(box)
        frame = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return frame[:, :, :3].copy()  # BGRA -> BGR (去掉 alpha)


def save_frame(frame_bgr, path):
    import cv2
    cv2.imwrite(path, frame_bgr)


def load_frame(path) -> np.ndarray:
    import cv2
    data = np.fromfile(path, dtype=np.uint8)  # 兼容中文路径
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"无法读取图片: {path}")
    return img


def test_capture():
    cap = ScreenCapture()
    ms = list_monitors()
    print("monitors:", ms)
    frame = cap.grab()
    print("frame shape:", frame.shape)
    save_frame(frame, "research/test_images/screen_sample.png")
    print("saved research/test_images/screen_sample.png")
    cap.close()


if __name__ == "__main__":
    test_capture()
