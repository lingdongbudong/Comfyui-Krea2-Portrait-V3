# -*- coding: utf-8 -*-
"""角色缩略图**本地缓存**：插件自己发图，不依赖任何外部域名。

## 为什么要有这个模块（v3.16）

v3.15 之前，角色记录的 `img` 直接写的是**远程图片直链**（先是 animadex，后来换成
safebooru）。这在浏览器侧有两个问题：

1. **国内网络下不可靠** —— 浏览器走的是系统代理，和 ComfyUI 后端（走 `ProxyHandler({})`）
   不是同一条路。后端测得通，不代表用户浏览器能加载。表现出来就是"图鉴一片空"。
2. **远程站可能挡热链** —— 就算第一次能加载，也可能随时被 403。

所以改成：**下载到插件目录、由 ComfyUI 自己发**。
`img` 字段存的是**相对本站的路径**（`/krea2-v3/thumb/<作品>/<slug>.webp`），
浏览器请求的是 `127.0.0.1:8188` —— 与出图服务同一个源，**断网也能显示**。

## 目录约定

```
thumbs/
    star-rail/    acheron.webp  ...
    zenless/      ...
    ww/           ...
    nikke/        ...
```

文件缺失时路由返回 404，前端 `onerror` 会退回首字色块 —— 这是**预期行为**，
比返回一张"通用占位图"好：占位图会让人误以为图配错了。
"""
import mimetypes
import os
import re

# Windows 的 mimetypes 库里**没有 webp**，先补上（供本模块的 content_type() 用）。
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/png", ".png")


def content_type(path):
    """按扩展名给出 MIME。

    ⚠️ 为什么必须显式设置，而不是交给 aiohttp 自己猜：
       aiohttp 3.14 的 `web.FileResponse` **不做任何 MIME 猜测** ——
       它继承 StreamResponse 的默认值，一律返回 `application/octet-stream`
       （实测确认：`prepare()` 里根本没有 guess_type 相关代码）。
       浏览器靠嗅探仍能渲染图片，但 MIME 不规范，
       所以这里自己算好、再赋给 response.content_type。
    """
    ext = os.path.splitext(path or "")[1].lower()
    return mimetypes.guess_type("x" + ext)[0] or "application/octet-stream"

HERE = os.path.dirname(os.path.abspath(__file__))
THUMB_DIR = os.path.join(HERE, "thumbs")

# 允许的扩展名（下载器目前只产出 webp，留 jpg/png 以备换源）
_ALLOW_EXT = (".webp", ".jpg", ".jpeg", ".png")

# 作品目录名：只用小写字母数字和短横，避免中文/冒号带来的编码麻烦
_SAFE = re.compile(r"^[a-z0-9][a-z0-9_\-/]*$")


def thumb_rel_path(game_key, slug, ext=".webp"):
    """拼出**磁盘相对路径**（也是 URL 里 `/thumb/` 之后那一段）。"""
    g = (game_key or "").strip().lower()
    s = (slug or "").strip().lower()
    if not _SAFE.match(g) or not s or ".." in s:
        return ""
    if not _SAFE.match(s):
        s = re.sub(r"[^a-z0-9_\-]", "-", s)
    if ext not in _ALLOW_EXT:
        ext = ".webp"
    return "%s/%s%s" % (g, s, ext)


def thumb_url(game_key, slug, ext=".webp"):
    """给角色记录用的 `img` 值：本站相对路径。取不到就返回空串。"""
    rel = thumb_rel_path(game_key, slug, ext)
    return ("/krea2-v3/thumb/" + rel) if rel else ""


def resolve(rel):
    """把 URL 里的相对路径解析成磁盘绝对路径。

    安全要点：**必须**校验解析结果仍在 THUMB_DIR 之内 ——
    否则 `/thumb/../../etc/passwd` 这类请求就能读到任意文件。
    用 `os.path.realpath` 而不是 `normpath`，因为软链接也能绕过朴素检查。
    """
    rel = (rel or "").strip().lstrip("/")
    if not rel or ".." in rel or "\x00" in rel:
        return None
    base = os.path.realpath(THUMB_DIR)
    full = os.path.realpath(os.path.join(base, rel))
    if not (full == base or full.startswith(base + os.sep)):
        return None
    if os.path.splitext(full)[1].lower() not in _ALLOW_EXT:
        return None
    return full if os.path.isfile(full) else None


def count_cached():
    """统计本地已缓存多少张图（用于自检与报告）。"""
    n = 0
    if os.path.isdir(THUMB_DIR):
        for _root, _dirs, files in os.walk(THUMB_DIR):
            n += sum(1 for f in files if os.path.splitext(f)[1].lower() in _ALLOW_EXT)
    return n


def dir_size_mb():
    total = 0
    if os.path.isdir(THUMB_DIR):
        for root, _dirs, files in os.walk(THUMB_DIR):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return round(total / 1048576.0, 1)
