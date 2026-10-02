# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-Portrait :: 角色池加载

数据来源：presets/character_pool.json（schema 3，约 1000 位女性角色）
由 tools/build_character_pool.py 从 Comfyui-Anima-Wardrobe 的
presets/character_pool.json（Danbooru 热度榜）中按「榜单排名 + 单作品配额 +
近期新角色保底」筛选生成，缩略图走确定性直链。

（兼容旧版 presets/character_100.json，读不到新池时自动回落。）

对外暴露：
    CHARACTER        : {下拉显示名: 角色dict}
    CHARACTER_NAMES  : [下拉显示名, ...]  —— 顺序即下拉顺序（热度降序）
    CHARACTER_LIST   : [角色dict, ...]    —— 原始顺序，供前端图鉴渲染
    WORK_ORDER       : [作品英文名, ...]  —— 图鉴侧栏分类顺序（按人数降序）
    WORK_ZH          : {作品英文名: 中文名}
    WORK_COLOR       : {作品英文名: 十六进制色}
    get_character(n) : 按显示名取角色 dict，取不到返回 None
    card(rec)        : 转成前端图鉴用的精简 dict
    draw(n, ...)     : 按人数抽 1-3 名角色（含「同作品 IP 优先」逻辑，v3 功能3）

角色 dict 结构：
    {
      "n": 英文原名（含作品后缀，如 "ganyu (genshin impact)"）,
      "c": 作品名（英文，如 "genshin impact"）,
      "t": 触发词（英文原名，供正文使用，符合 SKILL「人物名用英文」）,
      "p": 热度权重（供排序）,
      "h": 主发色（Danbooru 英文标签）,
      "e": 主瞳色（Danbooru 英文标签）,
      "f": [标志性 Danbooru 特征标签, ...],
      "z": 角色中文名（未收录时回落英文原名）,
      "wz": 作品中文名（未收录时回落英文原名）,
      "img": 官方缩略图直链（animadex blobs，确定性直链）,
    }

⭐ v3.0 起**不再派生「人种 / 国籍」**（用户要求：取消人种句）。
   派生字段只保留体型 / 身材 / 角色词。

以下字段在**加载时派生**：
      "en"     : 纯英文原名（去掉作品后缀，如 "ganyu"）
      "ip"     : 作品 IP 词（中文，如 "原神"）
      "word"   : 角色词 —— 正文用的完整写法，如 "ganyu（《原神》）"
      "fig"    : 身材（FIGURE 键，如 "f_large"）
      "build"  : 体型（BODY_BUILD 键，如 "高挑"）
"""

import json
import os

# 【v3.13】体型表已从 krea2_pools.py 抽到 k2_v3_tables.py（原文件整体移除）。
# 兼容两种导入方式：包内（ComfyUI 运行时）与独立脚本（自测）
try:
    from .k2_v3_tables import FIGURE_TAG_MAP, FIGURE, BODY_BUILD
except ImportError:  # pragma: no cover
    try:
        from k2_v3_tables import FIGURE_TAG_MAP, FIGURE, BODY_BUILD
    except ImportError:
        FIGURE_TAG_MAP, FIGURE, BODY_BUILD = {}, {}, {}

_HERE = os.path.dirname(os.path.abspath(__file__))
_PRESET_NEW = os.path.join(_HERE, "presets", "character_pool.json")
_PRESET_OLD = os.path.join(_HERE, "presets", "character_100.json")

# 作品英文名 -> 中文名（兜底表；文件里没带 wz 时用）
WORK_ZH = {
    "genshin impact": "原神",
    "honkai: star rail": "崩坏：星穹铁道",
    "zenless zone zero": "绝区零",
    "blue archive": "蔚蓝档案",
    "arknights": "明日方舟",
    "azur lane": "碧蓝航线",
    "umamusume": "赛马娘",
    "fate/grand order": "FGO",
    "goddess of victory: nikke": "胜利女神：妮姬",
    "wuthering waves": "鸣潮",
    "kantai collection": "舰队Collection",
    "punishing gray raven": "战双帕弥什",
    "girls frontline": "少女前线",
    "azur promilia": "蓝色星原",
    "takt op": "宿命回响",
    "kancolle": "舰队Collection",
    "fate grand order": "FGO",
    "nikke": "胜利女神：妮姬",
    "touhou": "东方Project",
}

WORK_COLOR = {}

# 同作品优先抽取的概率（v3 功能3：人物更易抽到同 IP 作品角色）
SAME_WORK_P = 0.8


def _work_zh(c):
    return WORK_ZH.get(c, c)


def _display_name(rec):
    """下拉展示名：中文名 · 作品中文名（可检索英文原名）。

    "ganyu (genshin impact)" -> "甘雨 · 原神"
    中文名未收录时回落英文原名 -> "hatsune miku · VOCALOID"
    """
    return f"{rec.get('z') or rec.get('n', '')} · {rec.get('wz') or _work_zh(rec.get('c', ''))}"


# 公开别名：供节点/工具复用同一套展示名规则
display_name = _display_name


# ============================================================================
# v2.2 · 体型 / 身材 派生（v3.0 起不再派生「人种」）
# ============================================================================

# 作品 IP -> 默认体型（角色标签里没有体型信息时的兜底）
BUILD_WORK_DEFAULT = {
    "umamusume": "高挑",
    "blue archive": "娇小",
    "kantai collection": "娇小",
    "azur lane": "标准",
    "goddess of victory: nikke": "标准",
}

# 角色标签 -> 体型（优先级高于作品默认）
BUILD_TAG_MAP = {
    "loli": "娇小",
    "petite": "娇小",
    "child": "娇小",
    "tall": "高挑",
    "tall female": "高挑",
    "muscular": "结实",
    "toned": "结实",
    "abs": "结实",
    "plump": "丰腴",
    "thick thighs": "丰腴",
    "curvy": "丰腴",
}

# 体型 / 身材 -> 中文画面描写（正文用）——直接复用 pools 里的同一份数据，避免两处漂移
BUILD_ZH = BODY_BUILD
FIGURE_ZH = FIGURE


def _derive_build(rec):
    """体型：角色标签优先，其次作品 IP 默认，最后「标准」。"""
    for t in (rec.get("f") or []):
        k = BUILD_TAG_MAP.get((t or "").strip().lower())
        if k:
            return k
    return BUILD_WORK_DEFAULT.get(rec.get("c", ""), "标准")


def _derive_figure(rec):
    """身材：按 Danbooru 标签优先级取第一个命中的 FIGURE 键。"""
    feats = set((t or "").strip().lower() for t in (rec.get("f") or []))
    for tag, key in FIGURE_TAG_MAP.items():
        if tag in feats:
            return key
    return ""


def _derive_ip_word(rec):
    """角色词：英文原名 + 中文作品 IP，自动补全。

    "ganyu (genshin impact)" -> ("ganyu", "原神", "ganyu（《原神》）")
    """
    n = rec.get("n") or ""
    en = n.rsplit(" (", 1)[0].strip() if " (" in n else n.strip()
    ip = rec.get("wz") or _work_zh(rec.get("c", ""))
    word = f"{en}（《{ip}》）" if en and ip else (en or ip)
    return en, ip, word


# 人工覆盖表：展示名里的中文名关键字 -> 强制体型 / 身材。
# 只在「标签推导结果明显不符合角色设定」时使用，宁缺毋滥。
CHAR_OVERRIDE = {
    "纳西妲": {"build": "娇小", "fig": "胸前平坦"},
}


def _enrich(rec):
    """就地补上派生字段（幂等）。"""
    en, ip, word = _derive_ip_word(rec)
    rec["en"] = en
    rec["ip"] = ip
    rec["word"] = word
    fig = _derive_figure(rec)
    rec["fig"] = fig
    rec["build"] = _derive_build(rec)
    # 人工覆盖（按中文名关键字匹配）
    z = rec.get("z") or ""
    for key, ov in CHAR_OVERRIDE.items():
        if key and key in z:
            rec["build"] = ov.get("build", rec["build"])
            rec["fig"] = ov.get("fig", rec["fig"])
            break
    return rec


def _load():
    path = _PRESET_NEW if os.path.isfile(_PRESET_NEW) else _PRESET_OLD
    if not os.path.isfile(path):
        return {}, [], [], []
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 兼容三种结构：{"characters":[...]} / {"data":[...]} / 直接 [...]
    if isinstance(raw, dict):
        recs = raw.get("characters") or raw.get("data") or []
        works_zh_file = raw.get("works_zh") or {}
        works_color_file = raw.get("works_color") or {}
    else:
        recs, works_zh_file, works_color_file = raw, {}, {}

    # 用文件里的作品中文名 / 配色覆盖兜底表
    WORK_ZH.update(works_zh_file)
    WORK_COLOR.update(works_color_file)

    chars, names, lst = {}, [], []
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        rec.setdefault("z", rec.get("n", ""))
        rec.setdefault("wz", _work_zh(rec.get("c", "")))
        rec.setdefault("img", "")
        _enrich(rec)
        disp = _display_name(rec)
        if disp in chars:  # 极端去重
            continue
        chars[disp] = rec
        names.append(disp)
        lst.append(rec)

    # 侧栏分类顺序：按人数降序，人数相同按中文名
    cnt = {}
    for r in lst:
        cnt[r.get("c", "")] = cnt.get(r.get("c", ""), 0) + 1
    order = sorted(cnt, key=lambda k: (-cnt[k], _work_zh(k)))
    return chars, names, lst, order


CHARACTER, CHARACTER_NAMES, CHARACTER_LIST, WORK_ORDER = _load()
_LOADED_PATH = _PRESET_NEW if os.path.isfile(_PRESET_NEW) else _PRESET_OLD


def get_character(name):
    """按显示名取角色 dict；name 为空 / 「自动」时返回 None。"""
    if name in (None, "", "自动"):
        return None
    return CHARACTER.get(name)


def tags_zh(rec):
    """【v3.11】把结构化中文标签摊平成一行短语（图鉴展示 / 检索用）。

    只做展示，不参与任何提示词生成 —— 生成侧读的仍是原始英文标签 `f`。
    """
    t = rec.get("tags") or {}
    if not isinstance(t, dict):
        return []
    out = []
    for k in ("发色", "发型", "瞳色"):
        v = t.get(k)
        if v:
            out.append(v)
    for v in (t.get("特征") or []):
        if v and v not in out:
            out.append(v)
    return out


def card(rec):
    """前端图鉴用的精简卡片数据。"""
    if not rec:
        return {}
    return {
        "value": _display_name(rec),
        "zh": rec.get("z", ""),
        "en": rec.get("en", ""),
        "n": rec.get("n", ""),
        "work": rec.get("c", ""),
        "work_zh": rec.get("wz", ""),
        "work_color": WORK_COLOR.get(rec.get("c", ""), "#888888"),
        "img": rec.get("img", ""),
        "trigger": rec.get("t", ""),
        "word": rec.get("word", ""),
        "ip": rec.get("ip", ""),
        "fig": rec.get("fig", ""),
        "fig_zh": FIGURE_ZH.get(rec.get("fig", ""), ""),
        "build": rec.get("build", ""),
        "build_zh": BUILD_ZH.get(rec.get("build", ""), ""),
        "count": rec.get("p", 0),
        "hair": rec.get("h", ""),
        "eye": rec.get("e", ""),
        "tags": list(rec.get("f") or []),
        "nfeat": len(rec.get("f") or []),
        # 【v3.11】结构化中文标签（展示/检索用，不参与生成）
        "tags_zh": tags_zh(rec),
    }


def cards():
    """全部角色的卡片数据（前端一次拉取后本地筛选）。"""
    return [card(r) for r in CHARACTER_LIST]


# ============================================================================
# v3.0 功能3：按人数抽角色（同作品 IP 优先 ≈80%）
# ============================================================================

def works():
    """作品键列表（按池内顺序）。"""
    seen, out = set(), []
    for r in CHARACTER_LIST:
        c = r.get("c", "")
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _female(rec):
    """角色池本身全为女性角色，这里再按标签兜一层保险。"""
    return any("1girl" in str(t) for t in (rec.get("f") or []))


def draw(n=1, work="全部", female_only=True, rng=None, seed=None,
         exclude=None, same_work_p=SAME_WORK_P, same_ip=True):
    """按人数抽角色（v3 功能3）。

    规则：
      · n 名角色，n 由「画面人数」决定（双人=2 / 三人=3）；
      · **同作品 IP 优先**：先随机抽第一位，之后以 `same_work_p`（默认 0.8）
        的概率把候选池收敛到第一位所属作品，让多角色更容易是同一 IP 的同框；
        该作品人数不够时自动放宽到全池；
      · 已抽过的角色不重复（同一角色不在同框里出现两次）。

    返回 (picks, info)
    """
    import random as _r
    if rng is None:
        rng = _r.Random(seed) if seed is not None else _r

    n = max(1, int(n))
    pool = [r for r in CHARACTER_LIST
            if work in ("全部", "", None) or r.get("c") == work]
    if female_only:
        only_f = [r for r in pool if _female(r)]
        if only_f:
            pool = only_f
    if not pool:
        pool = list(CHARACTER_LIST)

    excl = set(exclude or ())
    picks, used_works = [], []

    def _sample(cands):
        cands = [c for c in cands
                 if c not in picks
                 and not (excl & {_display_name(c), c.get("n", ""), c.get("z", "")})]
        return rng.choice(cands) if cands else None

    for i in range(n):
        cands = pool
        if i > 0 and same_ip and used_works and rng.random() < same_work_p:
            # 同 IP 优先：收敛到前面已经抽过的作品，凑不齐就自动退回全池
            w = rng.choice(used_works)
            same = [r for r in pool if r.get("c") == w]
            if len([c for c in same
                    if c not in picks
                    and not (excl & {_display_name(c), c.get("n", ""),
                                     c.get("z", "")})]) >= 1:
                cands = same
        pick = _sample(cands) or _sample(pool)
        if pick is None:
            break
        picks.append(pick)
        used_works.append(pick.get("c", ""))

    info = {
        "n": len(picks),
        "candidates": len(pool),
        "same_work_p": same_work_p,
        "same_work": bool(len(picks) > 1
                          and len({p.get("c") for p in picks}) == 1),
    }
    return picks, info
