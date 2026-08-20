# -*- coding: utf-8 -*-
"""一键开源发布脚本：把本项目推送到 GitHub。

用法（双击「发布到GitHub.bat」或命令行执行）：
    python publish_github.py

流程：
1. 获取 GitHub Personal Access Token（优先读环境变量 GITHUB_TOKEN，否则提示输入）
2. 验证 Token 并自动获取你的 GitHub 用户名
3. 用 GitHub API 创建仓库（已存在则跳过；公开/私有可配置）
4. git 推送当前代码到仓库
5. 把 Token 保存进 Windows 凭据管理器（git credential-manager），
   以后直接 `git push` 更新，无需再输入凭据 —— 一劳永逸

需要 Token：https://github.com/settings/tokens
勾选权限：repo（完整仓库读写）。
"""
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

# Windows 控制台 GBK 编码不支持 emoji，统一 UTF-8 输出
if getattr(sys.stdout, "encoding", "") and sys.stdout.encoding.lower().startswith("gb"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO_NAME = "bazaar-helper"           # 仓库名（可改）
REPO_DESC = "《大巴扎》(The Bazaar) 萌新辅助工具：日志驱动物品识别 + 多源流派推荐 + DeepSeek娘桌宠"
PRIVATE = False                        # True=私有仓库，False=公开

API = "https://api.github.com"


def log(msg):
    print(f"[发布] {msg}")


def get_token() -> str:
    """获取 GitHub Token。"""
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    # 交互输入（隐藏回显）
    try:
        import getpass
        tok = getpass.getpass("请输入 GitHub Personal Access Token（https://github.com/settings/tokens 生成，勾选 repo 权限）: ").strip()
    except Exception:
        tok = input("请输入 GitHub Personal Access Token: ").strip()
    if not tok:
        log("未提供 Token，已取消。")
        sys.exit(1)
    return tok


def api(url: str, token: str, method: str = "GET", body: dict = None):
    """调用 GitHub API。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "bazaar-helper-publish")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode()).get("message", "")
        except Exception:
            pass
        return {"_error": f"HTTP {e.code}: {detail}"}


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    token = get_token()

    # 1) 验证 token + 获取用户名
    log("验证 Token…")
    user = api(f"{API}/user", token)
    if user.get("_error") or not user.get("login"):
        log(f"Token 无效：{user.get('_error') or '未知错误'}。请检查权限与网络。")
        sys.exit(1)
    username = user["login"]
    log(f"已登录：{username}")

    # 2) 检查仓库是否已存在
    repo_full = f"{username}/{REPO_NAME}"
    log(f"检查仓库 {repo_full}…")
    exist = api(f"{API}/repos/{repo_full}", token)
    if exist.get("_error") and "Not Found" in exist["_error"]:
        log(f"创建仓库 {REPO_NAME}（{'公开' if not PRIVATE else '私有'}）…")
        created = api(f"{API}/user/repos", token, "POST", {
            "name": REPO_NAME,
            "description": REPO_DESC,
            "private": PRIVATE,
            "auto_init": False,
        })
        if created.get("_error"):
            log(f"创建失败：{created['_error']}")
            sys.exit(1)
        log(f"仓库创建成功：{created.get('html_url')}")
    elif exist.get("_error"):
        log(f"检查仓库出错：{exist['_error']}")
        sys.exit(1)
    else:
        log(f"仓库已存在：{exist.get('html_url')}")

    # 3) 配置 git 远程（清除旧 origin 避免冲突）
    subprocess.run(["git", "remote", "remove", "origin"], capture_output=True)
    remote_url = f"https://github.com/{repo_full}.git"
    subprocess.run(["git", "remote", "add", "origin", remote_url], check=True)
    log(f"已设置远程: {remote_url}")

    # 4) 推送（带 token 一次，成功后凭据管理器接管）
    push_url = f"https://x-access-token:{token}@{remote_url.removeprefix('https://')}"
    log("推送代码到 GitHub…")
    r = subprocess.run(["git", "push", "-u", push_url, "main"], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"推送失败：\n{r.stderr[-500:]}")
        sys.exit(1)
    log("推送成功！")

    # 5) 保存凭据到 Windows 凭据管理器（一劳永逸：以后 git push 直接可用）
    log("保存凭据（以后 git push 无需再输入）…")
    try:
        cred = f"https://{username}:{token}@github.com"
        # git credential approve 写入凭据管理器
        approve = subprocess.run(
            ["git", "credential", "approve"],
            input=f"protocol=https\nhost=github.com\nusername={username}\npassword={token}\n\n",
            capture_output=True, text=True, timeout=10)
        log("凭据已保存到 Windows 凭据管理器。以后更新代码：git add . && git commit -m 'xxx' && git push")
    except Exception as e:
        log(f"凭据保存提示：{e}（不影响已推送，下次 push 需手动输入）")

    log(f"\n✅ 全部完成！仓库地址：https://github.com/{repo_full}")
    log("后续更新流程：")
    log("  1. git add .")
    log("  2. git commit -m '更新说明'")
    log("  3. git push")


if __name__ == "__main__":
    main()
