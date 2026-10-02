# -*- coding: utf-8 -*-
"""
K2V3 扩展能力（v3.11 新增）—— 独立模块，不并入 krea2_v3_rules.py 主体。

为什么要独立成文件：
    krea2_v3_rules.py 是从 HTML 逐条照搬的生成核心，动它就有漂移风险。
    所有 v3.11 新能力（纹身面积 / 人物数量 / 多人·百合场景）都放这里，
    rules 只保留 4 处「读一个默认为空的字符串」的插入点 ——
    空值时代入结果与原样逐字一致，因此默认行为零变化。

统一范式（与 V2 既有新增能力一致）：
    **参数为 None / 空 = 根本不调用**，出题结果与加入该功能前逐字一致。

内容池来源：presets/k2_v3_extra_pools.json
    ⚠️ 不要去改 presets/k2_v3_pools.json —— 它由 tools/export_k2v3_pools.mjs
       从 HTML 导出，手改会被下次导出冲掉。新增池一律放 extra 文件。

【v3.17】双人段点名 —— compose_dual_head() + build_multi_segment(chars=…)，
    让双人 / 百合段的互动主语明确指向某一位角色，而不是含糊的「她」。

【v3.18】NSFW 内容项体系（取代 v3.17 的单一「NSFW 场景权重」）：
    ① 等级唯一化   —— INTENSITY_PRESETS 收敛成五档，其余等级类控件全部移除；
    ② 内容项权重   —— nsfwAct 表 + available_acts() / pick_act() / pick_act_entry()，
       实现「等级先筛可用范围、权重再定概率」；
    ③ 双人互动提前 —— build_multi_segment(act=…) 按内容项给双人段取桶，
       段本身由 rules 放到正文**最前**（用户要求"优先交代两人正在做什么"）。

【v3.19】① 内容项从 5 项扩到 **13 项**（展示生殖器 / 自慰 / 潮吹·失禁 / 手交 / 乳交 /
    足交 / 口交 / 深喉 / 性交 / 肛交 / 束缚·捆绑 / 多人 / 其它），权重上限 2.0 → 5.0；
    ② 新增 auto_act_weights()：按档位取一套权重基准，供「内容权重 · 跟随等级」开关用；
    ③ **删除负面提示词**（NEG_* 词表 + build_negative()，整条链路移除）。
"""

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXTRA_PATH = os.path.join(_HERE, "presets", "k2_v3_extra_pools.json")

_extra_cache = None


def _load():
    global _extra_cache
    if _extra_cache is None:
        try:
            with open(_EXTRA_PATH, "r", encoding="utf-8") as f:
                _extra_cache = json.load(f)
        except Exception:
            _extra_cache = {}
    return _extra_cache


def extra_pool(name):
    """取扩展池条目列表（去掉 _note / _meta 之类的说明键）。"""
    d = _load().get(name) or {}
    if isinstance(d, dict):
        out = []
        for k, v in d.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
                out.extend(v)
        return [x for x in out if isinstance(x, dict)]
    return [x for x in d if isinstance(x, dict)]


def extra_pool_group(name, level):
    """取分级池（multiScene / yuriScene）：{level: [条目]} 里的某一档。"""
    d = _load().get(name) or {}
    if not isinstance(d, dict):
        return []
    return d.get(level) or []


# 纹身覆盖面积 -> 插入正文的描述短语
# ⚠️ 「不使用」和空值都映射成空串 —— 空串意味着正文一字不改。
TATTOO_AREA_DESC = {
    "不使用": "",
    "": "",
    "小面积": "图案面积很小，",
    "中等面积": "约占半个巴掌大小，",
    "大面积": "沿肢体延伸成片，",
    "满背大面积": "自肩胛一路铺至腰际，",
}


def tattoo_area_desc(area):
    return TATTOO_AREA_DESC.get(area or "", "")


# ----------------------------------------------------------------------
# 单人保证：识别「画面里出现了第二个人」的 nsfwPose 条目
#
# 背景（实测）：原词库 opt.nsfwPose 里有 6 个条目，名字带「双人/多人」，
# 它们的英文描述里出现了 him / two men / stranger —— 也就是画面里其实有
# 第二个人。NSFW 模式下命中率约 30%。
# 用户要求「单人时提示内必定只有一人」，所以单人模式要把这些条目光掉。
# ----------------------------------------------------------------------
_MULTI_NAME_HINTS = ("双人", "多人", "两人", "三人")
# ⚠️ 这里的判据必须**只认"画面里确实出现第二个人"**的措辞。
#    早期版本把 him/his 也算进去，结果"单人诱惑""传教士"等全被误判成多人
#    （实测 400 次里 400 次误命中）。NSFW 描述里出现 him 并不等于第二个
#    人真的在画面内，因此只保留明确写数量的说法。
_SECOND_PERSON_HINTS = (
    "two men", "three men", "two women", "three women",
    "another man", "another woman", "another person", "stranger",
    "between two", "two partners", "multiple partners",
)


def is_multi_person_item(item):
    """判断一个 nsfwPose 条目是否隐含第二个人。

    判据优先级：
      1) 名字里写了「单人」→ 一定不是多人（最高优先级，先排除误伤）
      2) 名字里写了「双人/多人/两人/三人」→ 是多人
      3) 描述里出现明确的复数人物措辞 → 是多人
    """
    if not isinstance(item, dict):
        return False
    v = item.get("v") or ""
    t = (item.get("t") or "").lower()
    if "单人" in v:
        return False
    if any(h in v for h in _MULTI_NAME_HINTS):
        return True
    return any(h in t for h in _SECOND_PERSON_HINTS)


def ensure_solo(g, nsfw_pose_items):
    """单人保证：当前 nsfwPose 若含第二人，就换成一个纯单人条目。

    只在**所有字段随机完成之后**调用，因此不会打乱后续随机序列。
    返回 True 表示确实替换过。
    """
    cur = g.v("nsfwPose")
    if not cur:
        return False
    hit = None
    for it in (nsfw_pose_items or []):
        if it.get("v") == cur:
            hit = it
            break
    if hit is None or not is_multi_person_item(hit):
        return False
    solo = [it.get("v") for it in (nsfw_pose_items or [])
            if it.get("v") and not is_multi_person_item(it)]
    if not solo:
        return False
    g.set_v("nsfwPose", g.rng.choice(solo))
    return True


# ----------------------------------------------------------------------
# 【v3.18】NSFW 内容项（展示生殖器 / 自慰 / 口交 / 性交 / 其它）
#
# 两条需求（用户 2026-10-01 明确，取代 v3.17 的单一「NSFW 场景权重」）：
#   ① 调节 NSFW 等级的功能**只保留「内容强度」一个**，其余全部移除
#      —— 抽卡严格按所选档位出对应等级的内容；
#   ② 设置页最底部给出**各内容项的权重**调节项，按权重输出对应概率。
#
# 两者的优先级（用户选定）：**等级先筛可用范围，权重再定概率**。
#   · 每项内容自带 min 档位（presets/k2_v3_extra_pools.json → nsfwAct.items）
#   · 候选 = {min 档位 ≤ 当前档位 且 权重 > 0} 的内容项
#   · 命中概率 = 该项权重 ÷ 候选权重之和；权重 0 = 该项永不出现
#
# 为什么内容项表放 JSON 而不是写死在这里：档位门槛、条目、文案都是"内容"，
#   内容放数据文件，逻辑放代码 —— 加一项内容只改 JSON，不用碰生成核心。
#
# ⚠️ 单人保证：solo 组条目画面里只有一个人，multi 组必须有第二人。
#   单人模式只从 solo 里抽（口交/性交 因此走「道具版」条目），双人模式两者都用 ——
#   否则「单人」会抽出画面里必然出现第二个人的内容。
# ----------------------------------------------------------------------
ACT_W_MIN = 0.0
# 【v3.19】上限从 2.0 提到 5.0：内容项扩到 13 项之后，要拉开"只想要某一项"这种
# 强偏好，2.0 的差距不够（13 项等权时单项约 7.7%，拉到 5.0 才能把它顶到 40% 以上）。
# ⚠️ 1.0 仍然是"标准比例"，老工作流存的 1.0/2.0 值语义不变，不需要重设。
ACT_W_MAX = 5.0
ACT_W_STEP = 0.05
ACT_W_DEFAULT = 1.0          # 1.0 = 标准比例（候选之间等权）

# 档位序号（与 nsfwAct._rank 一致）；「全年龄 / 暗示」在 NSFW 语义下就是"没有性行为"
LEVEL_RANK = ("sfw", "suggestive", "explicit", "hardcore", "extreme")


def nsfw_act_items():
    """内容项表（保持 JSON 里的顺序 —— 顺序即设置页滑块的排列顺序）。"""
    d = _load().get("nsfwAct") or {}
    return [x for x in (d.get("items") or [])
            if isinstance(x, dict) and x.get("act")]


def nsfw_act_labels():
    return {x["act"]: (x.get("label") or x["act"]) for x in nsfw_act_items()}


def nsfw_act_names():
    return [x["act"] for x in nsfw_act_items()]


def nsfw_act_min(act):
    for x in nsfw_act_items():
        if x["act"] == act:
            return x.get("min") or "explicit"
    return None


def level_rank(level, mode="SFW"):
    """档位 -> 序号（0=全年龄 … 4=极端）；认不出来按 mode 兜底。"""
    lv = (level or "").strip().lower()
    if lv in LEVEL_RANK:
        return LEVEL_RANK.index(lv)
    return 4 if (mode or "") == "NSFW" else 0


def act_weight(act, weights):
    """取某项的权重；没给权重表 = 全部标准权重 1.0（不干预）。"""
    if not weights:
        return ACT_W_DEFAULT
    try:
        v = weights.get(act, ACT_W_DEFAULT)
        return max(0.0, float(ACT_W_DEFAULT if v is None else v))
    except (TypeError, ValueError):
        return ACT_W_DEFAULT


def available_acts(level, mode="SFW", weights=None):
    """等级先筛：返回当前档位可用、且权重大于 0 的内容项名（保持表内顺序）。"""
    r = level_rank(level, mode)
    out = []
    for x in nsfw_act_items():
        if level_rank(x.get("min"), "NSFW") > r:
            continue
        if act_weight(x["act"], weights) <= 0:
            continue
        out.append(x["act"])
    return out


def act_entries(act, solo_only=False):
    """取某内容项的条目；solo_only=True 时只取画面里只有一个人的条目。"""
    for x in nsfw_act_items():
        if x["act"] != act:
            continue
        out = list(x.get("solo") or [])
        if not solo_only:
            out += list(x.get("multi") or [])
        return [e for e in out if isinstance(e, dict) and e.get("v")]
    return []


def pick_act(g, level, mode="SFW", weights=None, solo_only=False):
    """按「等级筛 + 权重归一」抽一个内容项；返回 act 名，无可选项时 None。"""
    acts = [a for a in available_acts(level, mode, weights)
            if (not solo_only) or act_entries(a, solo_only=True)]
    ws = [act_weight(a, weights) for a in acts]
    total = sum(ws)
    if not acts or total <= 0:
        return None
    r = g.rng.random() * total
    acc = 0.0
    for a, w in zip(acts, ws):
        acc += w
        if r <= acc:
            return a
    return acts[-1]


def pick_act_entry(g, act, solo_only=False, resolve=None):
    """在某内容项里均匀抽一条。resolve(v) 用于给「只写了名字」的条目补描述。"""
    items = act_entries(act, solo_only=solo_only) or act_entries(act, solo_only=False)
    if not items:
        return None
    e = g.rng.choice(items)
    v = e.get("v") or ""
    t = (e.get("t") or "").strip()
    if not t and resolve:
        t = (resolve(v) or "").strip()
    return {"v": v, "t": t or v, "act": act}


def auto_act_weights(level, mode="SFW"):
    """【v3.19】「内容权重 · 跟随等级」开启时，按档位取一套权重基准。

    数据在 presets/k2_v3_extra_pools.json → nsfwActAuto（档位 -> {内容项: 权重}）。
    表里没列出的内容项返回 0（该档不出现）—— 所以自动档**同时**表达了
    "哪些够得着"和"够得着的里面谁更容易中"，一个控件定完。

    ⚠️ 只在开关开启时被调用；关闭时用户滑块说了算，行为与没有这个开关逐字一致。
    """
    d = _load().get("nsfwActAuto") or {}
    lv = (level or "").strip().lower()
    if lv not in LEVEL_RANK:
        lv = "sfw" if (mode or "") != "NSFW" else "extreme"
    table = d.get(lv) or {}
    out = {}
    for a in nsfw_act_names():
        try:
            out[a] = float(table.get(a, 0.0) or 0.0)
        except (TypeError, ValueError):
            out[a] = 0.0
    return out


def auto_act_note():
    """自动基准表的说明文案（前端 ⑦ 区与报告都用它，避免两处各写一遍）。"""
    d = _load().get("nsfwActAuto") or {}
    return d.get("_note") or ""


def act_hint(level, mode="SFW", weights=None):
    """给报告用的一句话：当前档位下哪些内容项可用、权重各是多少。"""
    acts = available_acts(level, mode, weights)
    if not acts:
        return ""
    return "、".join("%s×%.2f" % (a, act_weight(a, weights)) for a in acts)


def nsfw_act_note(key):
    d = _load().get("nsfwAct") or {}
    return d.get(key) or {}


# ----------------------------------------------------------------------
# 多人 / 百合场景：独立模板段
#
# 这是用户明确授权放宽的部分：新增**独立**的多人模板段与内容池，
# 而不是改写原有 10 段式模板。单人模式（默认）下该段恒为空串 → 一字不改。
#
# 【v3.17】段内改成「点名 + 外观锚点 + 互动」三段式：
#   · 两位角色各自点名（英文原名 + 中文作品），互动句的主语用 {A}/{B} 占位符
#     替换成真实名字 —— 双人画面里「她…她」的指代从此不存在；
#   · 外观锚点（发色 / 发长 / 扎法）让两个人一眼可区分；
#   · 没选角色时占位符退化成「其中一名女性 / 另一名女性」，依然无歧义。
# ----------------------------------------------------------------------
_LEVEL_KEYS = ("sfw", "suggestive", "explicit", "hardcore", "extreme", "any")


def _norm_level(level, mode):
    """把等级归一化到池里的档位键；认不出来就按 mode 兜底。"""
    lv = (level or "").strip().lower()
    if lv in _LEVEL_KEYS:
        return lv
    if lv in ("全年龄",):
        return "sfw"
    if lv in ("暗示",):
        return "suggestive"
    if lv in ("露骨",):
        return "explicit"
    if lv in ("强露骨",):
        return "hardcore"
    if lv in ("极端",):
        return "extreme"
    return "explicit" if mode == "NSFW" else "sfw"


def _dual_parts(chars):
    """chars -> (全名A, 全名B, 短名A, 短名B, 锚点A, 锚点B)，缺的一律取空串。

    全名 = `角色词`（如 `ganyu（《原神》）`），只在开场白里出现**一次**；
    短名 = 英文原名（如 `ganyu`），互动句里用它 —— 互动句一句里往往要点名两次，
    每处都带作品名会又长又难读，而且正文有 600 字上限。
    """
    items = list(chars or [])
    a = items[0] if len(items) > 0 and isinstance(items[0], dict) else {}
    b = items[1] if len(items) > 1 and isinstance(items[1], dict) else {}
    wa = (a.get("word") or "").strip()
    wb = (b.get("word") or "").strip()
    return (wa, wb,
            (a.get("short") or "").strip() or wa,
            (b.get("short") or "").strip() or wb,
            (a.get("brief") or "").strip(), (b.get("brief") or "").strip())


def compose_dual_head(yuri, chars):
    """双人段的「点名 + 外观锚点」开场白。

    返回 (开场白, 占位符 {A} 的替换文本, 占位符 {B} 的替换文本)。
    ⚠️ {A}/{B} 的替换文本用**短名**（英文原名），不用带作品名的全名 ——
       互动句里常常一句点名两次，全名会让这句话又长又难读。
    ⚠️ 兜底文本是「其中一名女性 / 另一名女性」而不是「她」——
       中文的「她」在双人画面里无法区分指代，这正是要消灭的歧义。
    """
    wa, wb, sa, sb, ba, bb = _dual_parts(chars)
    A = sa or ("其中一名女性" if yuri else "其中一人")
    B = sb or ("另一名女性" if yuri else "另一人")

    # ⚠️ 标点口径：角色词以「）》」这类全角字符收尾，后面不能再跟半角空格
    #    （「ganyu（《原神》） 与」这种就是标点混排）。所以开场白用顿号连接、
    #    「是」紧跟角色词 —— 与既有句首前缀「画面角色为ganyu（《原神》）。」同风格。
    if wa and wb:
        head = (("两名女性同框：%s、%s。" % (wa, wb)) if yuri
                else ("画面中的两个人是：%s、%s。" % (wa, wb)))
    elif wa or wb:
        one = wa or wb
        head = (("两名女性同框，其中一位是%s。" % one) if yuri
                else ("画面中有两个人，其中一位是%s。" % one))
    else:
        head = "画面中有两名女性。" if yuri else "画面中有两个人。"

    # 外观锚点：让两个人一眼可区分。缺数据就不写（宁可少写，不杜撰）。
    if ba and bb:
        head += "%s 留着%s，%s 留着%s。" % (A, ba, B, bb)
    elif ba:
        head += "%s 留着%s。" % (A, ba)
    elif bb:
        head += "%s 留着%s。" % (B, bb)
    return head, A, B


def build_multi_segment(g, level=None, mode="SFW", yuri=False, chars=None, act=None):
    """生成多人互动场景段（独立段）。

    返回 (文本, 分级提示)。双人模式且池里有内容时才非空。
    yuri=True 走百合池（两名女性），否则走通用多人池。
    chars=[{word, brief}, {word, brief}]：两位角色的点名与外观锚点（可 None）。
    act ：【v3.18】指定的内容项（展示生殖器 / 自慰 / 口交 / 性交 / 其它）。
         给了就**按内容项取桶**（由调用方先做「等级筛 + 权重」），
         没给则退回按等级取桶（v3.17 及以前的行为）。

    ⚠️ chars 为 None/空时，占位符退化成「其中一名女性 / 另一名女性」，
       正文仍然可读且无歧义 —— 不会因为没选角色就写不出段落。
    """
    lv = _norm_level(level, mode)

    # 「不限」等级：五档里随便挑一档，再在该档里挑条目
    if lv == "any":
        keys = [k for k in _LEVEL_KEYS if k != "any"]
        lv = g.rng.choice(keys)

    pool_name = "yuriScene" if yuri else "multiScene"
    if act:
        # 【v3.18】按内容项取桶；某一侧没有这一项就退到另一侧，再不行回落到等级桶
        items = extra_pool_group(pool_name, act) or \
            extra_pool_group("yuriScene" if not yuri else "multiScene", act)
    else:
        items = extra_pool_group(pool_name, lv)
    # 低档位没有内容时向上取最近的一档（如 sfw 缺就试 suggestive）
    if not items:
        for k in _LEVEL_KEYS:
            if k == "any":
                continue
            items = extra_pool_group(pool_name, k)
            if items:
                break
    if not items:
        return "", ""

    pick = g.rng.choice(items)
    frag = pick.get("t") or pick.get("v") or ""
    if not frag:
        return "", ""

    # 分级提示：百合按用户要求「按 NSFW 分级展示并附带分级提示」
    grade = pick.get("grade") or _GRADE_ZH.get(lv, "")
    if yuri and not grade:
        grade = _GRADE_ZH.get(lv, "")

    # 【v3.17】点名 + 外观锚点 + 互动句（{A}/{B} 换成真实角色名）
    head, name_a, name_b = compose_dual_head(yuri, chars)
    frag = frag.replace("{A}", name_a).replace("{B}", name_b)
    return head + frag + "。", grade


_GRADE_ZH = {
    "sfw": "全年龄",
    "suggestive": "暗示",
    "explicit": "露骨",
    "hardcore": "强露骨",
    "extreme": "极端",
    "any": "不限",
}


def grade_zh(level, mode="SFW"):
    return _GRADE_ZH.get(_norm_level(level, mode), "")


# ----------------------------------------------------------------------
# v3.12 越界内容：独立的更高强度 NSFW 段（内容边界 = 放开 时才启用）
#
# 与多人/百合段完全同一范式：独立模板段 + 独立内容池，
# 默认（受限）下本函数**根本不会被调用**，正文一字不改。
# ----------------------------------------------------------------------
_BEYOND_GRADE_ZH = {
    "explicit": "露骨", "hardcore": "强露骨", "extreme": "极端",
}


def build_beyond_segment(g, level=None, mode="SFW"):
    """生成越界内容段（独立段，追加在正文之后）。

    返回 (文本, 分级提示)。仅在 mode == NSFW 且池里有内容时才非空。
    """
    if (mode or "") != "NSFW":
        return "", ""
    lv = _norm_level(level, mode)
    if lv in ("sfw", "suggestive", "any"):
        # 越界池只做 explicit 及以上；低档/不限时按 NSFW 兜底到露骨
        lv = "explicit"
    items = extra_pool_group("beyondScene", lv)
    if not items:
        for k in ("explicit", "hardcore", "extreme"):
            items = extra_pool_group("beyondScene", k)
            if items:
                lv = k
                break
    if not items:
        return "", ""
    pick = g.rng.choice(items)
    frag = pick.get("t") or pick.get("v") or ""
    if not frag:
        return "", ""
    grade = pick.get("grade") or _BEYOND_GRADE_ZH.get(lv, "")
    seg = "越界描写：" + frag + "。"
    return seg, grade


# ============================================================================
# v3.13 新增能力
# ============================================================================

# ---------------------------------------------------------------------------
# ① 去重记忆（新鲜度衰减）—— 提升随机性质量的核心
#
# 问题：63 个池 / 949 条看起来很多，但纯均匀随机在**同一次会话里很容易连续
#       抽到同一个场景/道具**（生日悖论），出图观感就是"怎么老是这个"。
# 做法：节点侧记录每个字段最近用过的 N 个取值，生成时把这些取值降权。
#       avoid 为空 → 规则层完全不介入 → 与加入前逐字一致。
# ---------------------------------------------------------------------------
AVOID_PENALTY = 0.15      # 命中"最近用过"时权重乘这个系数


def build_avoid(fields_recent, depth):
    """{字段: [最近取值…]} 取最近 depth 个 -> 规则层用的 avoid 表。

    depth <= 0 或没有记录时返回 None（= 不干预）。
    """
    if not depth or depth <= 0:
        return None
    out = {}
    for fid, vals in (fields_recent or {}).items():
        take = list(vals or [])[-int(depth):]
        if take:
            out[fid] = take
    return out or None


# ---------------------------------------------------------------------------
# ② （已移除）负面提示词
#
# 【v3.19 用户要求】"取消负面提示词的输出，插件运行时不生成也不输出任何负面提示词"。
#   这里原本有 NEG_BASE / NEG_PHOTO / NEG_NSFW 三张词表与 build_negative()；
#   连同出题节点的输出端口、导出节点的输入与落盘字段、面板的文本框与按钮，
#   一整套链路都已删除。**不要再把它加回来** —— 阴性词表与"只写画面里有的"
#   这条正文口径本来就是两套相反的东西，混在一起容易互相污染。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ③ 组合空间统计 —— 让"池子到底够不够大"变成可看的数字
# ---------------------------------------------------------------------------
def combo_space(field_sizes, capped=1e15):
    """由各字段可选条数算组合空间。

    返回 (可选条目总数, 组合数文本)。组合数超过 capped 时用科学计数法。
    """
    total = 0
    prod = 1
    for _fid, n in (field_sizes or {}).items():
        n = int(n or 0)
        if n <= 0:
            continue
        total += n
        prod *= n
        if prod > capped * 1000:
            prod = int(capped * 1000)   # 防止天文数字撑爆整数
    if prod >= 10000:
        s = "%.2e" % float(prod)
    else:
        s = str(prod)
    return total, s


# ---------------------------------------------------------------------------
# ④ 内容强度 —— 【v3.18 起是**唯一**的 NSFW 等级控制项】
#
# v3.13 引入它时只是"总旋钮"，旁边还并存着 内容等级 / 内容边界 / NSFW 强度 /
# 越界开关 —— 四个控件都在管"多露骨"，互相打架（用户明确点名要收敛）。
# v3.18 起只剩这一个：五档，去掉「手动（分别设置）」。
#
# 每档的完整含义（抽卡就是按这个走）：
#   全年龄 / 暗示  → SFW 模式：正文走 SFW 池，不写 ⑧ 姿态段，无性行为；
#   露骨          → 可出现「展示生殖器」「自慰」；
#   强露骨        → 再加「口交」；
#   极端          → 再加「性交」，并自动追加越界描写（越界不再是独立开关）。
#
# ⚠️ 兼容：老工作流的「内容强度」值是「手动（分别设置）」—— 那不是这里的任何一档，
#    intensity_map() 会返回 None，节点层据此**回落到旧的「内容等级」控件值**
#    （见 k2_v3_node.generate()）。读法见注释，不是把旧控件重新变成可操作的入口。
# ---------------------------------------------------------------------------
INTENSITY_PRESETS = [
    ("全年龄", {"level": "sfw"}),
    ("暗示", {"level": "suggestive"}),
    ("露骨", {"level": "explicit"}),
    ("强露骨", {"level": "hardcore"}),
    ("极端", {"level": "extreme"}),
]
INTENSITY_DEFAULT = "全年龄"

# 「极端」档自动追加越界段（原来由独立的「内容边界」控件控制）
INTENSITY_BOUNDARY_LEVEL = "extreme"


def intensity_map(name):
    """内容强度档位 -> {level}；不是有效档位（如旧值「手动」）返回 None。"""
    for k, v in INTENSITY_PRESETS:
        if k == name:
            return v
    return None


def level_from_intensity(name):
    """档位名 -> level 键（sfw/suggestive/…）；认不出来返回空串。"""
    m = intensity_map(name)
    return (m or {}).get("level") or ""


# ---------------------------------------------------------------------------
# ⑤ 逐项开关 —— 关掉正文里的**补充项**（原来只有风格/景别能关）
#
# 事实澄清：build_prompt 里核心字段（镜头/人物/发型/服装主件/姿态/场景/构图）
#   永远会写，只有 28 个"补充项"能被 skip 拦下。所以这里如实做成"逐项开关"，
#   不假装能关掉整段。默认空 → 一项都不关 → 与加入前逐字一致。
#   可关闭项的**权威清单**在 krea2_v3_rules.SKIPPABLE_FIELDS（自动扫描得出）。
# ---------------------------------------------------------------------------
SECTION_ZH = {
    "camera": "① 镜头", "light": "② 光照", "person": "③ 人物", "hair": "④ 发型",
    "makeup": "⑤ 妆容", "expression": "⑥ 神情", "cloth": "⑦ 服装", "pose": "⑧ 姿态",
    "bg": "⑨ 场景", "comp": "⑩ 构图", "extra": "⑪ 附加",
}


def parse_off_items(raw):
    """解析「关闭可选项」控件 -> 字段 id 列表。

    接受两种写法：
      · JSON 数组：["prop", "weather"]
      · 逗号 / 顿号 / 空格分隔：prop, weather
    空串或解析失败一律返回 []（= 一项都不关）。
    ⚠️ 这里只做解析，**是否真的能关由规则层的 SKIPPABLE_SET 决定**。
    """
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            d = json.loads(s)
            if isinstance(d, list):
                return [str(x).strip() for x in d if str(x).strip()]
        except Exception:
            return []
    return [x.strip() for x in re.split(r"[,，、;；\s]+", s) if x.strip()]


def format_off_items(items):
    """字段 id 列表 -> 存进控件的紧凑写法。"""
    return ", ".join(sorted(set(items or [])))


