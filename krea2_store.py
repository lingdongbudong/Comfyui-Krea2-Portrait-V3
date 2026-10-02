# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-Portrait :: 历史记录与收藏（v3.6 M3）

设计目标（用户 2026-09-30 需求三：结果历史与收藏）：
  · **零依赖**：只用标准库，落盘为 UTF-8 JSON，不做数据库；
  · **不阻塞出图**：写入是"尽力而为"，任何异常都吞掉（不能因为存历史把出图搞挂）；
  · **有上限**：历史上限 200 条、收藏上限 200 条，超出按时间淘汰最旧的，
    避免文件无限膨胀（提示词文本 + 槽位 JSON 一条约 1–2 KB）；
  · **原子写**：先写临时文件再 os.replace，避免写一半断电留下坏文件。

存储位置：`<插件目录>/user_data/history.json`
  ⚠️ 放在插件目录下而不是 ComfyUI/user/：插件自包含，卸载时一起带走，
     也不会跟 ComfyUI 自己的数据目录结构耦合。

数据结构
  {
    "version": 1,
    "history": [ {"id","ts","text","seed","level","style","slots","quality"} … ],
    "favorites": [ 同上，另加 "note" ]
  }
  `id` = 时间戳 + 4 位随机，够唯一且天然按时间有序。
"""

import io
import json
import os
import random
import threading
import time

# 上限：超过就按"最旧优先"淘汰
HISTORY_MAX = 200
FAV_MAX = 200

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_data")
_FILE = os.path.join(_DIR, "history.json")
_LOCK = threading.Lock()          # 多请求并发写同一份文件时串行化


def _empty():
    return {"version": 1, "history": [], "favorites": []}


def load():
    """读整份数据；文件不存在 / 损坏一律返回空结构（不抛异常）。"""
    try:
        with io.open(_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return _empty()
        for k in ("history", "favorites"):
            if not isinstance(d.get(k), list):
                d[k] = []
        d.setdefault("version", 1)
        return d
    except Exception:
        return _empty()


def _save(d):
    """原子写：临时文件 → os.replace。失败静默（历史记录不值得影响出图）。"""
    try:
        os.makedirs(_DIR, exist_ok=True)
        tmp = _FILE + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _FILE)
        return True
    except Exception:
        return False


def _new_id():
    # 时间戳（毫秒）保证有序，4 位随机避免同毫秒撞车
    return "%d%04d" % (int(time.time() * 1000), random.randint(0, 9999))


def _trim(lst, cap):
    """超出上限时按 ts 从旧到新淘汰（保持新记录在后面）。"""
    if len(lst) <= cap:
        return lst
    return lst[-cap:]


def add_history(entry):
    """追加一条历史。entry 是任意 dict；函数会补 id / ts。"""
    if not isinstance(entry, dict):
        return None
    with _LOCK:
        d = load()
        e = dict(entry)
        e.setdefault("id", _new_id())
        e.setdefault("ts", int(time.time()))
        d["history"].append(e)
        d["history"] = _trim(d["history"], HISTORY_MAX)
        _save(d)
        return e.get("id")


def list_history(limit=50, offset=0):
    """倒序返回历史（最新在前）。"""
    d = load()
    lst = list(reversed(d["history"]))
    try:
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        limit, offset = 50, 0
    return lst[offset:offset + limit], len(d["history"])


def clear_history():
    with _LOCK:
        d = load()
        n = len(d["history"])
        d["history"] = []
        _save(d)
        return n


def add_favorite(entry):
    """收藏（可从历史里"收藏"，也可直接收藏当前）。同 id 已存在则更新。"""
    if not isinstance(entry, dict):
        return None
    with _LOCK:
        d = load()
        e = dict(entry)
        e.setdefault("id", _new_id())
        e.setdefault("ts", int(time.time()))
        d["favorites"] = [x for x in d["favorites"] if x.get("id") != e["id"]]
        d["favorites"].append(e)
        d["favorites"] = _trim(d["favorites"], FAV_MAX)
        _save(d)
        return e["id"]


def list_favorites(limit=100, offset=0):
    d = load()
    lst = list(reversed(d["favorites"]))
    try:
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        limit, offset = 100, 0
    return lst[offset:offset + limit], len(d["favorites"])


def remove_favorite(fid):
    with _LOCK:
        d = load()
        before = len(d["favorites"])
        d["favorites"] = [x for x in d["favorites"] if x.get("id") != fid]
        _save(d)
        return before - len(d["favorites"])


def get_entry(fid):
    """按 id 从历史或收藏里取一条（「复用此种子」要用它回填槽位）。"""
    d = load()
    for key in ("history", "favorites"):
        for e in d[key]:
            if e.get("id") == fid:
                return e
    return None
