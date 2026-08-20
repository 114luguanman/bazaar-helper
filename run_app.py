# -*- coding: utf-8 -*-
"""一键启动器：检测依赖并安装缺失项（优先装入本地 vendor/ 便携模式），然后启动主程序。

用 pythonw.exe 启动主程序，避免弹出黑色控制台窗口。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.join(ROOT, "requirements.txt")
VENDOR = os.path.join(ROOT, "vendor")


def prepare_path():
    """便携模式：本地 vendor/ 优先于全局环境。"""
    if os.path.isdir(VENDOR):
        sys.path.insert(0, VENDOR)
        os.environ["PYTHONPATH"] = VENDOR + os.pathsep + os.environ.get("PYTHONPATH", "")


def check_deps() -> list:
    missing = []
    for mod in ("PySide6", "mss", "numpy", "cv2", "PIL", "rapidocr_onnxruntime"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def find_pythonw() -> str:
    """优先找 pythonw.exe（无控制台窗口的 Python），找不到则退回 python.exe。"""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        w = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(w):
            return w
    return exe


def main():
    prepare_path()
    missing = check_deps()
    if missing:
        print("缺少依赖:", ", ".join(missing))
        print("正在安装（可能需要几分钟）…")
        cmd = [sys.executable, "-m", "pip", "install", "-r", REQ, "--upgrade"]
        if os.path.isdir(VENDOR):
            cmd += ["--target", VENDOR]
        code = subprocess.call(cmd)
        if code != 0:
            print("依赖安装失败，请手动执行: pip install -r requirements.txt")
            input("按回车退出…")
            sys.exit(1)
    os.chdir(ROOT)
    # 用 pythonw 启动主程序：无黑框
    launcher = find_pythonw()
    subprocess.Popen([launcher, os.path.join(ROOT, "main.py")])


if __name__ == "__main__":
    main()
