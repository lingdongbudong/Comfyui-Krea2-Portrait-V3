# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-Portrait :: K2_V3 角色预设存储（保存 / 加载 / 切换 / 删除）

对齐 krea2_store.py 的存储范式：纯标准库 JSON 落盘、原子写、尽力而为、有上限。
与历史 / 收藏（krea2_store）分开，单独一份文件，互不干扰。

存储位置：`<插件目录>/user_data/k2v3_presets.json`

数据结构：
  {
    "version": 1,
    "presets": [
      {
        "id": "时间戳+随机",
        "name": "用户起的名字（唯一）",
        "character": "角色显示名（如 甘雨 · 原神）或空串=不绑角色",
        "overrides": {"lens": "广角", "scene": "温泉", ...},   # 锁定的生成字段
        "note": "备注（可选）",
        "ts": 时间戳
      }, ...
    ]
  }
"""

import io
import json
import os
import random
import threading
import time

PRESET_MAX = 100

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_data")
_FILE = os.path.join(_DIR, "k2v3_presets.json")
_LOCK = threading.Lock()


def _empty():
    return {"version": 1, "presets": []}


def load():
    try:
        with io.open(_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return _empty()
        if not isinstance(d.get("presets"), list):
            d["presets"] = []
        d.setdefault("version", 1)
        return d
    except Exception:
        return _empty()


def _save(d):
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
    return "%d%04d" % (int(time.time() * 1000), random.randint(0, 9999))


def _trim(lst, cap):
    return lst[-cap:] if len(lst) > cap else lst


def list_presets():
    d = load()
    return list(reversed(d["presets"]))


def get_preset(name):
    """按 name 取一条预设（name 作主键，保存时已去重）。"""
    if not name:
        return None
    d = load()
    for p in d["presets"]:
        if p.get("name") == name:
            return p
    return None


def save_preset(name, character="", overrides=None, note="", weights="", params=None):
    """保存（同名覆盖更新，否则新增）。返回 (ok, preset_or_error)。

    【v3.13 整合】原来只存"角色 + 锁定字段"，权重方案没有对应物。
    现在同一条**方案**可以一起带走：
        weights —— 「权重覆盖」串
        params  —— 其余控件的一份快照 {控件名: 值}（内容强度/记忆/温度/边界…）
    老调用方不传这两个参数时，存进去的是空串/空表，行为与之前一致。
    """
    if not name or not str(name).strip():
        return False, {"error": "预设名不能为空"}
    name = str(name).strip()
    with _LOCK:
        d = load()
        entry = {
            "name": name,
            "character": character or "",
            "overrides": dict(overrides or {}),
            "note": note or "",
            "weights": weights or "",
            "params": dict(params or {}),
        }
        for p in d["presets"]:
            if p.get("name") == name:
                entry["id"] = p.get("id")
                entry["ts"] = int(time.time())
                p.update(entry)
                _save(d)
                return True, p
        entry["id"] = _new_id()
        entry["ts"] = int(time.time())
        d["presets"].append(entry)
        d["presets"] = _trim(d["presets"], PRESET_MAX)
        _save(d)
        return True, entry


def remove_preset(name):
    with _LOCK:
        d = load()
        before = len(d["presets"])
        d["presets"] = [p for p in d["presets"] if p.get("name") != name]
        _save(d)
        return before - len(d["presets"])


def preset_names():
    d = load()
    return [p.get("name", "") for p in d["presets"] if p.get("name")]


# ============================================================================
# 【v3.13】去重记忆（新鲜度衰减）的「最近取值」记录
#
# 为什么单独一份文件：它是**高频改写**的小状态（每次出题都写），
# 与历史/收藏那种"用户资产"混在一起容易被误清；分开存互不影响。
# 同样遵守本模块约定：纯标准库、原子写、失败静默。
# ============================================================================
_MEM_FILE = os.path.join(_DIR, "recent.json")
RECENT_PER_FIELD = 40          # 每个字段最多记 40 个最近取值


def load_recent():
    """返回 {字段 id: [最近取值…]}；文件不存在 / 损坏一律返回 {}。"""
    try:
        with io.open(_MEM_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        fields = d.get("fields") if isinstance(d, dict) else None
        if not isinstance(fields, dict):
            return {}
        return {k: list(v) for k, v in fields.items() if isinstance(v, list)}
    except Exception:
        return {}


def push_recent(picks, per_field=RECENT_PER_FIELD):
    """把本次各字段的取值并入记忆（尽力而为，失败静默）。返回是否写盘。"""
    if not isinstance(picks, dict) or not picks:
        return False
    clean = {}
    for fid, v in picks.items():
        if isinstance(v, str) and v.strip():
            clean[fid] = v.strip()
    if not clean:
        return False
    try:
        with _LOCK:
            d = {}
            try:
                with io.open(_MEM_FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                d = {}
            fields = d.get("fields")
            if not isinstance(fields, dict):
                fields = {}
            for fid, v in clean.items():
                lst = fields.get(fid)
                if not isinstance(lst, list):
                    lst = []
                lst.append(v)
                fields[fid] = lst[-per_field:]
            d["fields"] = fields
            d.setdefault("version", 1)
            os.makedirs(_DIR, exist_ok=True)
            tmp = _MEM_FILE + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _MEM_FILE)
        return True
    except Exception:
        return False


def clear_recent():
    """清空去重记忆。返回是否真的删掉了文件。"""
    try:
        with _LOCK:
            if os.path.isfile(_MEM_FILE):
                os.remove(_MEM_FILE)
                return True
        return False
    except Exception:
        return False
