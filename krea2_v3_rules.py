# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-Portrait :: K2_V3 融合版生成器 —— 纯 Python 移植（方案 B）

署名与来源
    插件作者：灵冻不冻
    插件底层框架源于脚本：K2_V3_融合版.html（HTML 版人像提示词生成器）
        已授权开源 —— 作者：飞蓬。
        原文件路径：`<ComfyUI>/提示词/K2_V3_融合版.html`
        （工具脚本 tools/translate_nsfw_pools.py 读的是
          `<ComfyUI>/user/default/workflows/K2_V3_融合版.html` 这份副本）

把 `K2_V3_融合版.html` 里的生成器**原样**翻译成 Python：
  · 词库来源：presets/k2_v3_pools.json（由 tools/export_k2v3_pools.mjs 从 HTML 导出，零漂移）
  · 装配模板：buildPrompt() 的 10 段式
  · 随机化：均匀随机 + 两级级联 + 冲突预过滤(randomizeField) + 兜底纠错(enforceConflicts)
  · 自检：runSelfCheck() 的 F1–F10 致命项 + S1–S13 风格项
  · 精简：generate() 里超 600 字按 TRIM_ORDER 自动精简

对外暴露：
    full_random(seed=None, mode="SFW", locked=None, character=None) -> dict
        locked     : {字段id: 值}，随机时锁死这些字段（不参与随机）
        character  : 角色 dict（见 krea2_characters.card()），命中时注入角色词 + 覆盖体型
    返回：
        {
          "text": 提示词正文,
          "report": 自检报告（多行文本）,
          "slots": {字段id: 值}（结构化槽位，便于调试/复用）,
          "n": 字数, "filled": 随机补全数, "trimmed": 精简项数,
          "risky": 是否高风险姿态, "mode": 模式,
        }

设计约束（与 HTML 保持一致，不另起炉灶）：
  · 只用标准库；种子走 random.Random(seed)，seed=None 时用系统随机；
  · 冲突规则与「值→描述」映射逐条照搬，不做主观"改进"；
  · SFW 全中文、NSFW 服装/姿态段英文整句 —— 与网页行为一致。
"""

import json
import os
import random
import re

# 【v3.11】扩展能力模块（纹身面积 / 人物数量 / 多人·百合场景）。
# 两种导入方式都支持：作为包被 ComfyUI 加载时用相对导入；测试脚本直连本文件时
# 走绝对导入（golden_snapshot.py 会把插件根目录塞进 sys.path）。
try:
    from . import k2_v3_extra as EX
except Exception:  # pragma: no cover - 仅在独立导入时走到
    import k2_v3_extra as EX

_HERE = os.path.dirname(os.path.abspath(__file__))
_POOLS_PATH = os.path.join(_HERE, "presets", "k2_v3_pools.json")


def _load_pools():
    with open(_POOLS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_P = _load_pools()
OPT = _P["opt"]
CLOTH = _P["cloth"]
POSES = _P["poses"]
STYLE_PRESETS = _P["style_presets"]
SECTIONS = _P["sections"]
CLOTH_FIELDS = _P["cloth_fields"]
POSE_FIELDS = _P["pose_fields"]
BAN_A = _P.get("ban_a") or []
BAN_B = _P.get("ban_b") or []
BAN_D = _P.get("ban_d") or []
TRIM_ORDER = _P.get("trim_order") or []


# ============================================================================
# 【v3.13】可关闭项（用于「逐项开关」控件）
#
# 事实澄清：build_prompt 里**核心字段永远会写**（镜头/视角/景别/人物/发型/
#   服装主件/姿态/场景/构图），只有一部分「补充项」会被 `skip` 拦下。
#   所以这里不做"关掉整段"这种做不到的事，而是**如实列出真正可关的那些项**。
#
# 为什么不手写这份列表：手写会和 build_prompt 漂移。这里直接在导入时扫本文件
#   源码里所有的 skip.get(…) 调用，扫出什么就是什么 —— 以后加一个 skip.get，
#   这个列表自动跟着变，永远不用维护。
#   ⚠️ 动态拼接的那种（源码里写成 skip.get("imperf%d" % idx)）扫不到，用
#      _SKIP_EXTRA 手工补上 —— 两处一起改才算数。
# ============================================================================
_SKIP_CALL_RX = re.compile(r'skip\.get\("([A-Za-z0-9_]+)"\)')
_SKIP_EXTRA = ["imperf1", "imperf2"]


def _collect_skippable():
    try:
        # 只扫"真实代码"区域：注释里出现的示例不算数，所以要先把注释行去掉。
        with open(os.path.abspath(__file__), "r", encoding="utf-8") as f:
            lines = f.readlines()
        code = "\n".join(l for l in lines if not l.lstrip().startswith("#"))
        return sorted(set(_SKIP_CALL_RX.findall(code)) | set(_SKIP_EXTRA))
    except Exception:  # pragma: no cover
        return list(_SKIP_EXTRA)


SKIPPABLE_FIELDS = _collect_skippable()
SKIPPABLE_SET = set(SKIPPABLE_FIELDS)

# 可关闭项 -> 所属段落（只用于面板分组显示；不在表里的归到"其他"）
SKIPPABLE_SECTION = {
    "device": "camera", "ambient": "light",
    "hairAcc": "hair", "makeupDetail": "makeup", "smudge": "makeup", "nails": "makeup",
    "clothBottom": "cloth", "clothMat": "cloth", "clothPattern": "cloth",
    "clothDeco": "cloth", "clothLayer": "cloth", "shoes": "cloth", "socks": "cloth",
    "accessory": "cloth", "nsfwChain": "pose", "nsfwMod": "cloth",
    "nsfwFabric": "cloth", "nsfwBody": "cloth", "nsfwProp": "cloth", "nsfwShoes": "cloth",
    "prop": "bg", "weather": "bg", "styleTag": "comp",
    "film": "extra", "cine": "extra", "imperf1": "extra", "imperf2": "extra",
    "tattoo": "extra",
}


def skippable_by_section():
    """把可关闭项按段落分组，供前端渲染勾选框。"""
    out = {}
    for fid in SKIPPABLE_FIELDS:
        out.setdefault(SKIPPABLE_SECTION.get(fid, "other"), []).append(fid)
    return out


def field_sizes(mode="SFW"):
    """各字段当前可选条数（用于组合空间统计）。"""
    sizes = {}
    for fid in ALL_FIELD_IDS:
        n = 0
        if fid == "clothCat":
            n = len(OPT.get("clothCat") or [])
        elif fid == "poseCat":
            n = len(OPT.get("poseCat") or [])
        elif fid in ("clothItem", "clothSubCat", "poseSubCat", "pose"):
            n = 0          # 这些依赖上级分类，条数动态，不计入
        else:
            items = _opt(fid)
            n = len([o for o in items if o.get("v")
                     and (mode == "NSFW" or not o.get("nsfw"))])
        if n:
            sizes[fid] = n
    return sizes


# ---- 字段清单（供 fillBlanks / randomizeAll 遍历） ----
def _collect_field_ids():
    ids = []
    for sec in SECTIONS:
        for f in sec.get("fields") or []:
            ids.append(f["id"])
    for grp in (CLOTH_FIELDS, POSE_FIELDS):
        for mode in ("sfw", "nsfw"):
            for f in grp.get(mode) or []:
                ids.append(f["id"])
    return ids


ALL_FIELD_IDS = _collect_field_ids()
CLOTH_SFW_IDS = {f["id"] for f in CLOTH_FIELDS.get("sfw") or []}
CLOTH_NSFW_IDS = {f["id"] for f in CLOTH_FIELDS.get("nsfw") or []}
POSE_SFW_IDS = {f["id"] for f in POSE_FIELDS.get("sfw") or []}
POSE_NSFW_IDS = {f["id"] for f in POSE_FIELDS.get("nsfw") or []}

# 【v3.12】NSFW 强度会调整的这些 NSFW 字段（按生成顺序列出，仅 NSFW 模式用到）
_NSFW_INTENSITY_FIELDS = [
    f["id"] for f in (CLOTH_FIELDS.get("nsfw") or [])
] + [
    f["id"] for f in (POSE_FIELDS.get("nsfw") or [])
]


def _opt(pool_id):
    return OPT.get(pool_id) or []


# ============================================================================
# 【v3.20】全字段 -> 所属段落（前端「④ 字段微调」按这个分组折叠显示）
#
# 为什么继承 SKIPPABLE_SECTION：那是「③ 逐项开关」的分组表，两处必须一致 ——
# 同一个字段在两个面板里落到不同分组，用户会以为它们是两个东西。
# 这里只补 SKIPPABLE_SECTION 覆盖不到的核心字段（镜头/人物/发型/服装主件…）。
#
# ⚠️ 只在面板里决定"字段显示在哪个折叠区"，**不参与任何生成逻辑**，
#    加错/漏掉最多是面板分组难看，不会改出图结果。
# ============================================================================
FIELD_SECTION = dict(SKIPPABLE_SECTION)
FIELD_SECTION.update({
    # ① 镜头
    "lens": "camera", "viewpoint": "camera", "shotSize": "camera", "dof": "camera",
    # ② 光照
    "mainLight": "light", "colorTone": "light",
    # ③ 人物
    "temperament": "person", "age": "person", "race": "person", "face": "person",
    "skin": "person", "texture": "person", "body": "person", "leg": "person",
    "firstImp": "person",
    # ④ 发型
    "hairLen": "hair", "hairColor": "hair", "hairCurl": "hair",
    "hairTie": "hair", "hairBangs": "hair", "hairState": "hair",
    # ⑤ 妆容
    "makeup": "makeup",
    # ⑥ 神情
    "emotion": "expression", "eye": "expression", "mouth": "expression",
    # ⑦ 服装
    "clothCat": "cloth", "clothSubCat": "cloth", "clothItem": "cloth",
    "pantyColor": "cloth", "pantyStyle": "cloth",
    # ⑧ 姿态
    "poseCat": "pose", "poseSubCat": "pose", "pose": "pose",
    "poseExtra": "pose", "nsfwPose": "pose", "nsfwState": "pose",
    # ⑨ 场景
    "scene": "bg",
    # ⑩ 构图
    "comp": "comp", "compPos": "comp",
    # ⑪ 附加
    "tattooPos": "extra", "stylePreset": "extra",
})


def field_sections():
    """全字段按段落分组（键是段落 id，值与 SECTION_ZH 的键同域）。"""
    out = {}
    for fid in ALL_FIELD_IDS:
        out.setdefault(FIELD_SECTION.get(fid, "other"), []).append(fid)
    return out


# ============================================================================
# 【v3.20】④ 字段微调实际暴露哪些字段
#
# v3.13 起这份清单是**手写在路由里**的 32 个名字 —— 用户这一轮要求"扩展可调整的
# 参数"，而全库有 69 个字段，手写清单只覆盖了不到一半。改成**按规则算**：
#
#   纳入：该字段在 SFW 下有可选值（≥1 条非 nsfw 条目）
#   排除：① 动态字段（可选值取决于上级分类，没有静态列表）——
#            clothSubCat / clothItem / clothBottom / poseSubCat / pose / imperf1 / imperf2
#         ② NSFW 档字段（CLOTH_NSFW_IDS / POSE_NSFW_IDS）——
#            它们的取值属于"内容强度"那条链路，单独锁会把 SFW 提示词污染成 NSFW
#         ③ stylePreset —— **锁了等于没锁**：风格预设的生效路径是
#            randomize_field() 里"随机抽到它之后再覆盖其他字段"，
#            而被锁定的字段会直接跳过 randomize_field → 预设覆盖永远不会发生。
#            放进来会给人"我锁了风格却没有任何变化"的错觉。
#
# ⚠️ 这份清单只决定"面板上能不能锁"，**不参与生成逻辑**。
# ============================================================================
_TUNE_EXCLUDE = set(CLOTH_NSFW_IDS) | set(POSE_NSFW_IDS) | {"stylePreset"}


def tunable_fields():
    """④ 字段微调可锁定的字段（稳定顺序 = ALL_FIELD_IDS 的顺序）。"""
    out = []
    for fid in ALL_FIELD_IDS:
        if fid in _TUNE_EXCLUDE:
            continue
        if any(o.get("v") and not o.get("nsfw") for o in _opt(fid)):
            out.append(fid)
    return out


def opt_t(pool_id, val):
    """「值 -> 描述」映射；找不到就原样返回（同 JS optT）。"""
    if not val:
        return val
    for it in _opt(pool_id):
        if it.get("v") == val:
            return it.get("t") or it.get("v") or val
    return val


def first_sub(cat):
    subs = CLOTH.get(cat)
    if not subs:
        return ""
    ks = list(subs.keys())
    return ks[0] if ks else ""


def flat_cloth_items(cat, sub):
    if not cat or not CLOTH.get(cat):
        return []
    if sub:
        arr = CLOTH[cat].get(sub) or []
    else:
        arr = []
        for s in CLOTH[cat]:
            arr += CLOTH[cat][s]
    out = []
    for x in arr:
        out.append({"v": cat + "｜" + (sub or first_sub(cat)) + "｜" + x[0],
                    "n": x[0], "t": x[1]})
    return out


def flat_all_bottoms():
    out = []
    cat = CLOTH.get("下装")
    if not cat:
        return out
    for s in cat:
        for x in cat[s]:
            out.append({"v": "下装｜" + s + "｜" + x[0], "n": x[0], "t": x[1]})
    return out


def cloth_item_desc(vv):
    if not vv:
        return ""
    p = vv.split("｜")
    if len(p) >= 3 and CLOTH.get(p[0]) and CLOTH[p[0]].get(p[1]):
        for x in CLOTH[p[0]][p[1]]:
            if x[0] == p[2]:
                return x[1]
    if len(p) == 2 and CLOTH.get(p[0]):
        for s in CLOTH[p[0]]:
            for x in CLOTH[p[0]][s]:
                if x[0] == p[1]:
                    return x[1]
    return vv


def count_chars(s):
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    lat = len(re.findall(r"[a-zA-Z]+", s))
    return cjk + lat


RISKY_SET = {"床边翘臀", "椅坐一条腿抬起", "开腿坐", "大字摊", "翘臀趴",
             "内裤褪到膝盖", "弯腰捡物", "裙摆飞扬", "跪趴", "背弓", "爬向镜头",
             "鸭子坐", "蹲姿", "换衣服", "侧躺招手", "手撑大腿", "整理丝袜", "撩裙边",
             "俯身吹蜡烛", "弯腰系鞋带", "起身瞬间", "仰卧抬腿", "侧卧托腮", "俯卧翘脚",
             "沙发贵妃躺", "窗台坐", "侧坐床边", "荡秋千", "头垂床沿"}


def is_risky_pose(pose_name, cat_name, item):
    skirt = bool(re.search(r"裙|热裤|短裤", item or ""))
    return (pose_name in RISKY_SET) or (skirt and bool(re.search(r"坐|跪|卧|躺|趴|垂出", pose_name)))


def find_pose(name):
    for c in POSES:
        for s in POSES[c]:
            for it in POSES[c][s]:
                if it[0] == name:
                    return {"cat": c, "sub": s, "t": it[1],
                            "risk": bool(it[2]) if len(it) > 2 else False}
    return {"cat": "", "sub": "", "t": name, "risk": False}


# 角色体型 -> K2_V3 体型池（娇小/高挑/丰腴/结实 有直接对应词）
CHAR_BUILD_TO_BODY = {
    "娇小": "娇小", "高挑": "模特高挑", "丰腴": "丰满匀称",
    "结实": "健康匀称", "标准": "",
}


# ============================================================================
# 【V2 · 面板对齐】轴级权重
#
# 对齐 Comfyui-Krea2-Portrait 选择器 v3.8.0 的权重语义与取值范围（一字不差）：
#   WEIGHT_MIN / WEIGHT_MAX / WEIGHT_STEP = 0.0 / 3.0 / 0.05
#   命中概率 = 该选项权重 ÷ 同轴全部选项权重之和（**同轴归一，不经等级带**）
#   权重 0 = 剔除；全被拖到 0 时退回均匀抽（保证永不返回 None）
#   默认全 1.0 —— 与不加权重时完全等概率，向后兼容
#
# 原件四个轴是 构图方向/视角/景别/人物朝向；K2_V3 词库没有「竖横向构图」与
# 「人物朝向」两轴，因此这里保留同名且存在的「视角 / 景别」，其余换成 K2_V3
# 自己的轴（镜头 / 构图 / 构图位置 / 主光 / 场景 / 服装大类 / 姿态大类）。
# ============================================================================
WEIGHT_MIN = 0.0
WEIGHT_MAX = 3.0
WEIGHT_STEP = 0.05

# 轴名 -> 该轴对应的 K2_V3 字段 id（前端据此列出可调选项）
WEIGHT_AXIS_FIELDS = {
    "视角": "viewpoint", "景别": "shotSize", "镜头": "lens",
    "构图": "comp", "构图位置": "compPos", "主光": "mainLight",
    "场景": "scene", "服装大类": "clothCat", "姿态大类": "poseCat",
}
# 与原件 WEIGHT_POOL_KIND 对齐：这些轴全部是 "axis"（选项级权重）
WEIGHT_POOL_KIND = {k: "axis" for k in WEIGHT_AXIS_FIELDS}
# 出厂默认：全 1.0 → 等概率（与原件「未列出按 1.0」口径一致）
WEIGHT_AXIS_DEFAULTS = {}

# 轴 id -> 轴名（反向查，randomize 时用）
FIELD_TO_AXIS = {v: k for k, v in WEIGHT_AXIS_FIELDS.items()}


# ---------------------------------------------------------------------------
# 【v3.22】鞋履详细种类 + 水面波光：单选项权重（item_weights）
#
# 与上面的「轴级权重」不同：这两组是**按名字命中某个/某类选项**，而不是整轴归一。
# 由节点侧把「鞋履种类权重」展开成 {具体鞋款名: 权重}、把「水面波光权重」写成
# {水面反光: 权重}，统一以 item_weights 传进来，randomize_field 里按名叠加。
#
# ⚠️ 默认（全 1.0）时节点传 None —— 本机制**完全不介入**，出题与没有这个功能
#    逐字一致（金标准快照 800/800 的前提）。
# ---------------------------------------------------------------------------

# 鞋履详细种类 -> 该种类下的具体鞋款（以主词库 shoes 条目为准）。
# ⚠️ 「不使用」是"不写鞋履"的哨兵值、不是一种鞋款 → 不进任何种类，权重恒 1.0；
#    这样"默认全 1.0"时仍是对 34 条（含不使用/赤脚）均匀抽，分布与 v3.21 完全一致。
SHOE_CATEGORIES = {
    "高跟鞋": ["银色细跟高跟鞋", "黑色尖头高跟鞋", "裸色踝带高跟鞋",
              "透明塑料高跟鞋", "罗马绑带细高跟鞋", "红底细跟鞋",
              "水钻细高跟鞋", "铆钉尖头高跟鞋", "缎面蝴蝶结高跟鞋",
              "一字夹带高跟鞋", "蛇纹异域高跟鞋", "绝细针尖高跟鞋",
              "几何异形跟高跟鞋", "木质跟尖头高跟鞋", "纯白极简高跟鞋",
              "金属跟高跟鞋", "漆皮一字带高跟鞋"],
    "靴子": ["黑色皮革过膝长靴", "黑色马丁靴", "黑色切尔西靴", "黑色袜靴",
             "裸色踝靴", "白色高筒靴", "登山靴"],
    "运动鞋": ["白色厚底运动鞋", "白色帆布鞋", "老爹鞋"],
    "凉鞋": ["细带凉鞋", "穆勒鞋", "玛丽珍鞋"],
    "皮鞋": ["黑色漆皮乐福鞋", "芭蕾平底鞋"],
    "赤脚": ["赤脚"],
}

# ambient（环境光）里那条「水面波光倒映在皮肤上，光影流转」的选项名
WATER_REFLECT_OPTION = "水面反光"


def shoe_category_names():
    """鞋履详细种类名（按 SHOE_CATEGORIES 的顺序）。"""
    return list(SHOE_CATEGORIES.keys())


def shoe_weight_controls():
    """返回 [(种类名, 控件名)] —— 节点据此生成控件，前端据此渲染滑块。"""
    return [(cat, cat + "权重") for cat in SHOE_CATEGORIES]



def parse_weight_spec(spec):
    """解析「权重覆盖」写法（与原面板一致）。

    支持：`视角:2, 俯视机位:0.5`、`大波浪:0`；分隔符 , ， ; ；；全角冒号也认。
    返回 (axis_mult, item_mult)：
      axis_mult = {轴名: 倍数}  —— 该轴所有选项整体缩放
      item_mult = {选项名: 倍数} —— 精确命中某个选项
    """
    axes, items = {}, {}
    for part in re.split(r"[,，;；]", str(spec or "")):
        part = part.strip()
        if not part:
            continue
        if ":" in part or "：" in part:
            name, num = re.split(r"[:：]", part, maxsplit=1)
        else:
            name, num = part, "1"
        name = name.strip()
        if not name:
            continue
        try:
            mult = max(0.0, float(num.strip()))
        except ValueError:
            continue
        if name in WEIGHT_AXIS_FIELDS:
            axes[name] = mult
        else:
            items[name] = mult
    return axes, items


def build_axis_weights(axis_mult=None, item_mult=None):
    """把「轴级倍数 + 选项级倍数」展开成 {轴名: {选项: 权重}}。

    同一选项被轴级与选项级同时指定时，**选项级优先**（与原件 build_overrides 一致）。
    """
    out = {}
    for axis, fid in WEIGHT_AXIS_FIELDS.items():
        defaults = WEIGHT_AXIS_DEFAULTS.get(axis, {})
        w = {}
        for o in _opt(fid):
            val = o.get("v")
            if not val:
                continue
            w[val] = float(defaults.get(val, 1.0))
        am = (axis_mult or {}).get(axis)
        if am is not None:
            w = {k: v * float(am) for k, v in w.items()}
        for k, v in (item_mult or {}).items():
            if k in w:
                w[k] = float(v) * float(am) if am is not None else float(v)
        out[axis] = w
    return out


def pick_weighted(rng, cands, weights=None, fallback=None):
    """按**选项级权重**抽一个（与原面板 pick_weighted 公式一致）。

    命中概率 = 该选项权重 ÷ 同轴全部选项权重之和；权重 0 剔除；
    全为 0 时退回 fallback（默认原候选均匀抽），保证永不返回 None。
    """
    cands = [c for c in (cands or ())]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    if not weights:
        return rng.choice(cands)
    pairs = []
    for c in cands:
        w = float(weights.get(c, 1.0) or 0.0)
        if w > 0:
            pairs.append((c, w))
    if not pairs:
        return rng.choice(list(fallback or cands))
    total = sum(w for _, w in pairs)
    r = rng.random() * total
    acc = 0.0
    for c, w in pairs:
        acc += w
        if r < acc:
            return c
    return pairs[-1][0]


class K2V3:
    """一次生成的状态机。等价于 HTML 里的全局 form + 一组函数。"""

    def __init__(self, rng=None, mode="SFW", axis_weights=None, person_extra=None,
                 avoid=None, temperature=None, item_weights=None):
        self.rng = rng or random
        self.mode = "NSFW" if mode == "NSFW" else "SFW"
        self.fields = {fid: "" for fid in ALL_FIELD_IDS}
        self.user_picked = {}
        self.locked = {}
        self.disabled = set()
        self.adjust_notes = []
        # 【V2】轴级权重 {轴名: {选项: 权重}}；空 = 等概率
        self.axis_weights = axis_weights or {}
        # 【v3.22】单选项权重 {选项名: 权重}（鞋履种类展开 / 水面波光等）。
        #   空 = 不介入。randomize_field 里按名叠加到该字段的候选上。
        self.item_weights = item_weights or {}
        # 【V2】角色附加特征短语（瞳色 / 标志特征），注入 ③人物维度
        self.person_extra = list(person_extra or [])

        # 【v3.11】三个「默认为空」的扩展槽位。
        # ⚠️ 刻意**不放进 self.fields / ALL_FIELD_IDS** —— 一旦成为正式字段，
        #   就会被 randomize_field 随机化，消耗随机数序列，从而改变既有出题结果。
        #   这里用普通属性存，空值时拼接结果是空串，正文一字不改。
        self.tattoo_area = ""      # 纹身覆盖面积描述（空 = 不写面积）
        # 【v3.18】NSFW 内容项：等级筛 + 权重选出来的那一条文字（空 = 不介入）
        #   与 nsfwPose 池的关系：选了内容项时，⑧ 姿态段用这里的文字，
        #   nsfwPose 字段同步写上条目名（便于报告/槽位对账）。
        self.nsfw_act_text = ""
        self.nsfw_act = ""
        self.multi_segment = ""    # 多人 / 百合独立模板段（空 = 不追加）
        self.multi_grade = ""      # 百合分级提示（空 = 不提示）
        # 【v3.12】越界段 + 分级提示（空 = 不追加；仅「内容边界=放开」时写入）
        self.beyond_segment = ""
        self.beyond_grade = ""
        # 【v3.13】随机性增强：去重记忆（新鲜度衰减）+ 温度
        #   avoid 为空 且 temperature == 1.0 时，randomize_field 走**原样分支**，
        #   一个字符的行为都不变 —— 这是金标准快照能继续保持全绿的前提。
        self.avoid = dict(avoid or {})          # {字段: [最近用过的取值]}
        try:
            self.temperature = float(temperature if temperature is not None else 1.0)
        except (TypeError, ValueError):
            self.temperature = 1.0

    # ------------------------------------------------------------------
    # 【V2 新增·纹身权重】**新增能力**，不是对既有逻辑的改写：
    #   节点「纹身开关」默认关闭 —— 关闭时本函数**根本不会被调用**，
    #   出题行为与加入该功能前逐字一致；开启后按下表调整纹身出现率。
    #   权重语义（单调、直观，且 1.0 是真正的"不改"）：
    #     w = 1.0  → 保持随机结果，完全不干预
    #     w = 0    → 强制「不使用」，纹身永不出现
    #     0 < w < 1→ 以 (1-w) 的概率降级为「不使用」，出现率按 w 缩放
    #     1 < w ≤ 2→ 若当前是「不使用」，以 (w-1) 的概率升级成一个具体纹身
    #   取值范围 0–2、步进 0.05（与面板滑块一致）。
    # ------------------------------------------------------------------
    def apply_tattoo_weight(self, w):
        try:
            w = float(w)
        except (TypeError, ValueError):
            return
        if w >= 1.0:
            if w > 1.0 and self.v("tattoo") == "不使用":
                opts = [o.get("v") for o in _opt("tattoo")
                        if o.get("v") and o.get("v") != "不使用"]
                if opts and self.rng.random() < (w - 1.0):
                    self.set_v("tattoo", self.rng.choice(opts))
        else:
            if self.rng.random() > w:
                self.set_v("tattoo", "不使用")

    # ------------------------------------------------------------------
    # 【v3.11 新增能力汇总入口】纹身覆盖面积 / 人物数量 / 多人·百合场景
    #
    # 统一范式：**person_count=None 且 tattoo_area=None 时直接返回**。
    #   也就是说调用方不显式开启，这里一个分支都不进，
    #   出题结果与 v3.10 逐字一致 —— 这正是金标准快照能全绿的前提。
    #
    # 各参数语义：
    #   person_count : None=不干预；"单人"（默认保证画面只有一人）/ "双人"
    #   tattoo_area  : None=不干预；"不使用"=强制无纹身；其余=写入面积描述
    #   strict_solo  : None=随 person_count（单人即启用）；True/False 强制
    #   level        : 内容等级（六档），用于给多人/百合场景选分级档位
    #   yuri         : 双人模式下走百合池（两名女性）
    #   chars        : 【v3.17】[{word, short, brief}, …] 两位角色的点名与外观锚点，
    #                  None / 空 = 不点名（占位符退化成「其中一名女性 / 另一名女性」）
    # ------------------------------------------------------------------
    def apply_v311(self, person_count=None, tattoo_area=None, strict_solo=None,
                   level=None, yuri=False, chars=None, act=None):
        if person_count is None and tattoo_area is None:
            return

        # ① 纹身覆盖面积（"不使用" 顺带把纹身本身关掉）
        if tattoo_area:
            self.tattoo_area = EX.tattoo_area_desc(tattoo_area)
            if tattoo_area == "不使用":
                self.set_v("tattoo", "不使用")

        # ② 人物数量
        pc = person_count or "单人"
        if strict_solo is None:
            strict_solo = (pc == "单人")

        if pc == "单人":
            # 单人保证：把隐含第二人的 nsfwPose 换成纯单人条目
            if strict_solo:
                EX.ensure_solo(self, _opt("nsfwPose"))
        else:
            # 双人：追加独立的多人 / 百合模板段
            # 【v3.17】chars 传进段内做「点名 + 外观锚点」，让互动句的主语明确。
            # 【v3.18】act 传进段内 —— 双人段与 ⑧ 姿态段**用同一个内容项**，
            #   两处不会一个说自慰、一个说性交（等级与权重也只抽一次）。
            seg, grade = EX.build_multi_segment(
                self, level=level, mode=self.mode, yuri=bool(yuri), chars=chars,
                act=act or None)
            self.multi_segment = seg
            self.multi_grade = grade

    # ------------------------------------------------------------------
    # 【v3.12 新增能力汇总入口】内容边界（越界段）/ NSFW 强度
    #
    # 统一范式与 v3.11 一致：boundary 为空、nsfw_w 为 None 时直接返回。
    #   也就是说调用方不显式开启，这里一个分支都不进，
    #   出题结果与 v3.11 逐字一致 —— 这是金标准快照能全绿的前提。
    #
    # 参数语义：
    #   boundary : None=不干预；True=放开（追加独立越界段，仅 NSFW 模式生效）
    #   nsfw_w   : None=不干预；0.0~2.0（1.0=不改）按强度调整 NSFW 字段措辞
    # ------------------------------------------------------------------
    def apply_v312(self, boundary=None, nsfw_w=None, level=None):
        if not boundary and nsfw_w is None:
            return

        # ① 越界段：独立追加，不插入既有 10 段之间
        if boundary:
            seg, grade = EX.build_beyond_segment(
                self, level=level, mode=self.mode)
            self.beyond_segment = seg
            self.beyond_grade = grade

        # ② NSFW 强度：只在显式传值且 != 1.0 时介入（1.0 = 完全不改）
        if nsfw_w is not None:
            try:
                w = float(nsfw_w)
            except (TypeError, ValueError):
                return
            if w != 1.0:
                self.apply_nsfw_intensity(w)

    # ------------------------------------------------------------------
    # 【v3.17 新增·NSFW 场景下的内容权重】
    #   控制 NSFW 模式下「人物场景」（自慰 / 口交 / 性交 等性行为场景）在正文里
    #   的比重。这些场景只有一个来源：opt.nsfwPose（⑧ 姿态段）。
    #
    #   范式与既有新增能力完全一致：**w 为 None 或 1.0 时直接返回**，
    #   不消耗任何随机数 —— 不传这个参数时行为与加入前逐字一致
    #   （金标准快照全绿的前提）。具体权重语义见 k2_v3_extra 里同名函数的注释。
    # ------------------------------------------------------------------
    def apply_nsfw_act(self, level=None, weights=None, solo_only=False):
        """【v3.18 新增·NSFW 内容项：等级先筛范围，权重再定概率】

        只在 NSFW 模式且 level 明确给出时介入；返回实际选中的内容项（没选到为 ""）。
        选中的内容项决定 ⑧ 姿态段写什么（self.nsfw_act_text）。

        ⚠️ level 为 None 时**直接返回**，一个随机数都不消耗 ——
           这是金标准快照（用 level=None 跑）能继续全绿的前提。
        ⚠️ 权重表为 None 时按"全部 1.0"处理（等权）—— 等级筛照样生效，
           所以「只保留一个等级控件」这条需求不依赖权重表存在。
        """
        if (self.mode or "") != "NSFW" or level is None:
            return ""
        act = EX.pick_act(self, level, self.mode, weights=weights, solo_only=solo_only)
        if not act:
            return ""
        ent = EX.pick_act_entry(self, act, solo_only=solo_only,
                                resolve=lambda v: opt_t("nsfwPose", v))
        if not ent:
            return ""
        self.nsfw_act = act
        self.nsfw_act_text = ent["t"]
        # 字段同步写上条目名：槽位 JSON / 自检 / 报告都能看到"这次抽的是哪一条"
        # （文字本身走 nsfw_act_text，不依赖主词库里有同名条目）
        self.set_v("nsfwPose", ent["v"])
        return act

    # ------------------------------------------------------------------
    # 【v3.12 新增·NSFW 强度】把 NSFW 字段的措辞朝"更收敛(0) / 更露骨(2)"两端推。
    #
    # 语义（与 apply_tattoo_weight 同构、单调、1.0 是真正的不改）：
    #   w = 1.0  → 不干预（本函数压根不被调用）
    #   w = 0.0  → 全部 NSFW 字段收敛到该字段 nsfw 档位最低的条目
    #   0<w<1    → 每个 NSFW 字段以 (1-w) 概率降级到最低档
    #   w = 2.0  → 全部 NSFW 字段顶到 nsfw 档位最高的条目
    #   1<w<2    → 每个 NSFW 字段以 (w-1) 概率升级到最高档
    #
    # 只在「NSFW 开关」开启时才被调用 → 关闭时完全不消耗随机数。
    # ------------------------------------------------------------------
    def apply_nsfw_intensity(self, w):
        for fid in _NSFW_INTENSITY_FIELDS:
            opts = [o for o in _opt(fid) if o.get("v")]
            if not opts:
                continue
            def tier(o):
                try:
                    return int(o.get("nsfw") or 0)
                except (TypeError, ValueError):
                    return 0
            lo = min(tier(o) for o in opts)
            hi = max(tier(o) for o in opts)
            lo_opts = [o.get("v") for o in opts if tier(o) == lo]
            hi_opts = [o.get("v") for o in opts if tier(o) == hi]
            if w <= 1.0:
                if lo_opts and self.rng.random() > w:
                    self.set_v(fid, self.rng.choice(lo_opts))
            else:
                if hi_opts and self.rng.random() < (w - 1.0):
                    self.set_v(fid, self.rng.choice(hi_opts))

    # ---- 基础访问 ----
    def v(self, fid):
        return self.fields.get(fid, "")

    def set_v(self, fid, val):
        self.fields[fid] = "" if val is None else str(val)

    def rand_of(self, arr):
        if not arr:
            return None
        return arr[self.rng.randrange(len(arr))]

    def random_value_for(self, fid):
        if fid == "clothItem":
            cat = self.v("clothCat") or first_sub("上衣")
            sub = self.v("clothSubCat") or first_sub(cat)
            items = flat_cloth_items(cat, sub)
            it = self.rand_of(items)
            return it["v"] if it else ""
        if fid == "pose":
            pc = self.v("poseCat") or "静态姿势"
            ps = self.v("poseSubCat") or first_sub(pc)
            pl = [{"v": x[0], "t": x[1]} for x in (POSES.get(pc, {}).get(ps) or [])]
            it = self.rand_of(pl)
            return it["v"] if it else ""
        if fid == "clothBottom":
            bl = [{"v": "不使用", "t": "不使用"}] + flat_all_bottoms()
            it = self.rand_of(bl)
            return it["v"] if it else ""
        lst = [o for o in _opt(fid)
               if (self.mode == "NSFW" or not o.get("nsfw")) and o.get("v") != ""]
        if not lst:
            return ""
        it = self.rand_of(lst)
        return it["v"]

    # ---- 冲突兜底 ----
    def _set_adjust(self, fid, val, why):
        old = self.v(fid) or "未选"
        if self.v(fid) != val:
            self.set_v(fid, val)
            self.user_picked[fid] = False
            self.adjust_notes.append(f"{why}（「{old}」→「{val}」）")

    def enforce_conflicts(self):
        notes = []
        cat = self.v("clothCat")
        bottom_ok = cat in ("上衣", "外套")
        if not bottom_ok and self.v("clothBottom") and self.v("clothBottom") != "不使用":
            self._set_adjust("clothBottom", "不使用", f"主件为「{cat}」时无法另配下装")
        if self.v("clothBottom"):
            self.disabled.add("clothBottom") if not bottom_ok else None
            if not bottom_ok:
                self.disabled.add("clothBottom")
            else:
                self.disabled.discard("clothBottom")

        if self.v("shoes") == "赤脚":
            self.disabled.add("socks")
            if self.v("socks") and self.v("socks") != "不使用":
                self._set_adjust("socks", "不使用", "赤脚时无法同时穿袜子/丝袜")
        else:
            self.disabled.discard("socks")

        short_hair = bool(re.search(r"齐耳短发|短发", self.v("hairLen")))
        tie_ban = {"高马尾", "双马尾", "麻花辫", "盘发", "丸子头", "低髻"}
        if short_hair and self.v("hairTie") in tie_ban:
            self._set_adjust("hairTie", "披散", f"短发无法扎「{self.v('hairTie')}」")

        bare = self.v("makeup") == "素颜"
        if bare:
            self.disabled.add("makeupDetail")
            self.disabled.add("smudge")
            if self.v("makeupDetail") and self.v("makeupDetail") != "不使用":
                self._set_adjust("makeupDetail", "不使用", "素颜状态下无法叠加妆面细节")
            if self.v("smudge") and self.v("smudge") != "不使用":
                self._set_adjust("smudge", "不使用", "素颜状态下没有妆容可被弄花")
        else:
            self.disabled.discard("makeupDetail")
            self.disabled.discard("smudge")

        if self.mode == "NSFW" and self.v("nsfwState") == "仅剩配饰":
            self.disabled.add("nsfwFabric")
            self.disabled.add("nsfwMod")
            if self.v("nsfwFabric") and self.v("nsfwFabric") != "不使用":
                self._set_adjust("nsfwFabric", "不使用", "仅剩配饰时没有衣物面料可言")
            if self.v("nsfwMod") and self.v("nsfwMod") != "不使用":
                self._set_adjust("nsfwMod", "不使用", "仅剩配饰时无从改造衣物")

        short_set = {"齐耳短发", "短发", "锁骨发"}
        long_curl = {"大波浪", "羊毛卷", "法式烫", "云朵卷", "大卷", "波浪卷", "拉美风格卷"}
        if self.v("hairLen") in short_set and self.v("hairCurl") in long_curl:
            self._set_adjust("hairCurl", "直发柔顺",
                             f"「{self.v('hairLen')}」配「{self.v('hairCurl')}」矛盾，已改回直发")

        tie_ban2 = {"高马尾", "双马尾", "麻花辫", "盘发", "丸子头", "低髻", "法式辫",
                    "鱼骨辫", "灯笼辫", "高丸子头", "低丸子头", "双丸子头"}
        if self.v("hairLen") in short_set and self.v("hairTie") in tie_ban2:
            self._set_adjust("hairTie", "披散", f"短发无法扎「{self.v('hairTie')}」")

        if self.v("hairAcc") == "丝带编发" and self.v("hairTie") == "披散":
            self._set_adjust("hairAcc", "不使用", "披散发无法编入「丝带编发」")

        dark_skin = {"古铜色", "小麦色"}
        pale_makeup = {"韩系水光妆", "樱花粉嫩妆"}
        if self.v("skin") in dark_skin and self.v("makeup") in pale_makeup:
            notes.append(f"「{self.v('skin')}」与「{self.v('makeup')}」色相近，已按自然肤色倾向处理")

        water_scene = {"温泉", "浴室", "海边", "水下", "泳池边"}
        dry_weather = {"晴空万里", "细雪飘落"}
        if self.v("scene") in water_scene and self.v("weather") in dry_weather:
            self._set_adjust("weather", "不使用", f"含水场景配「{self.v('weather')}」矛盾，已清空天气")

        return notes

    # ---- 分类级联随机 ----
    def _randomize_subcat(self, kind):
        if kind == "cloth":
            c = self.v("clothCat")
            if not c or not CLOTH.get(c):
                return
            subs = list(CLOTH[c].keys())
            if subs:
                sc = self.rand_of(subs)
                self.set_v("clothSubCat", sc)
                self.user_picked["clothSubCat"] = True
        else:
            c = self.v("poseCat")
            if not c or not POSES.get(c):
                return
            subs = list(POSES[c].keys())
            if subs:
                sc = self.rand_of(subs)
                self.set_v("poseSubCat", sc)
                self.user_picked["poseSubCat"] = True

    def randomize_cloth_cat(self):
        cats = [o["v"] for o in _opt("clothCat") if o.get("v")]
        c = self.rand_of(cats)
        if c:
            self.set_v("clothCat", c)
            self.user_picked["clothCat"] = True
            self._randomize_subcat("cloth")

    def randomize_pose_cat(self):
        pcs = [o["v"] for o in _opt("poseCat") if o.get("v")]
        c = self.rand_of(pcs)
        if c:
            self.set_v("poseCat", c)
            self.user_picked["poseCat"] = True
            self._randomize_subcat("pose")

    # ---- 逐字段随机（含冲突预过滤） ----
    def _prefilter(self, fid, arr):
        short_set = {"齐耳短发", "短发", "锁骨发"}
        long_curl = {"大波浪", "羊毛卷", "法式烫", "云朵卷", "大卷", "波浪卷", "拉美风格卷"}
        tie_ban2 = {"高马尾", "双马尾", "麻花辫", "盘发", "丸子头", "低髻", "法式辫",
                    "鱼骨辫", "灯笼辫", "高丸子头", "低丸子头", "双丸子头"}
        if fid == "hairCurl" and self.v("hairLen") in short_set:
            arr = [o for o in arr if o["v"] not in long_curl or o["v"] in ("直发柔顺", "自然微卷")]
        if fid == "hairTie" and self.v("hairLen") in short_set:
            arr = [o for o in arr if o["v"] not in tie_ban2]
        if fid == "socks" and self.v("shoes") == "赤脚":
            arr = [o for o in arr if o["v"] == "不使用"]
        if fid == "makeupDetail" and self.v("makeup") == "素颜":
            arr = [o for o in arr if o["v"] == "不使用"]
        if fid == "weather" and re.search(r"温泉|浴室|海边|水下|泳池边", self.v("scene")):
            arr = [o for o in arr if o["v"] in ("不使用", "蒸汽朦胧", "水光浮动")]
        if self.mode == "NSFW" and fid == "nsfwFabric" and "湿透" in self.v("nsfwState"):
            arr = [o for o in arr if "湿透" not in o["v"]]
        if self.mode == "NSFW" and fid in ("nsfwMod", "nsfwFabric", "nsfwShoes") \
                and self.v("nsfwState") == "仅剩配饰":
            arr = [o for o in arr if o["v"] == "不使用"]
        return arr

    def randomize_field(self, fid):
        if self.locked.get(fid):
            return
        if fid in ("clothCat", "poseCat", "clothSubCat", "poseSubCat"):
            return  # 由专项函数带动
        if fid == "clothItem":
            cat = self.v("clothCat") or first_sub("上衣")
            sub = self.v("clothSubCat") or first_sub(cat)
            lst = flat_cloth_items(cat, sub)
        elif fid == "clothBottom":
            lst = [{"v": "不使用", "t": "不使用"}] + flat_all_bottoms()
        elif fid == "pose":
            pc = self.v("poseCat") or "静态姿势"
            ps = self.v("poseSubCat") or first_sub(pc)
            lst = [{"v": x[0], "t": x[1]} for x in (POSES.get(pc, {}).get(ps) or [])]
        else:
            lst = [o for o in _opt(fid) if self.mode == "NSFW" or not o.get("nsfw")]
        if not lst:
            return
        arr = self._prefilter(fid, lst)
        if not arr:
            return
        # 【V2 · 轴级权重】该字段属于某个可调轴时，走 pick_weighted（同轴归一）
        axis = FIELD_TO_AXIS.get(fid)
        w = self.axis_weights.get(axis) if axis else None
        # 【v3.22 · 单选项权重】item_weights 按选项名命中（鞋履种类 / 水面波光）。
        #   ⚠️ 只挑"确实落在本字段候选里"的键，避免把别的字段的同名选项带进来；
        #      叠加在轴级权重之上，同名选项以 item_weights 为准。
        if self.item_weights:
            _iw = {k: v for k, v in self.item_weights.items()
                   if any(o.get("v") == k for o in arr)}
            if _iw:
                w = dict(w or {})
                w.update(_iw)
        # 【v3.13 · 随机性增强】去重记忆 / 温度 —— 只在**真的开了**的时候介入。
        #   ⚠️ avoid 为空且 temperature == 1.0 时下面这个 if 为假，
        #      走的仍是 v3.12 那两条分支，行为逐字不变。
        avoid_vals = self.avoid.get(fid) if self.avoid else None
        temp = self.temperature
        if avoid_vals or (temp != 1.0 and w):
            names = [o["v"] for o in arr if o.get("v")]
            ww = {}
            for nm in names:
                base = float((w or {}).get(nm, 1.0) or 0.0)
                if base <= 0:
                    continue                      # 权重 0 保持"剔除"语义
                if temp != 1.0:
                    # 温度：w ** (1/T)。T<1 更保守、T>1 更极端；
                    # 全 1.0 的权重经此变换仍是 1.0（等概率不受影响）。
                    base = base ** (1.0 / temp)
                if avoid_vals and nm in avoid_vals:
                    base *= EX.AVOID_PENALTY       # 最近用过 -> 降权
                ww[nm] = base
            picked = pick_weighted(self.rng, names, ww, fallback=names)
            it = next((o for o in arr if o["v"] == picked), None)
        elif w:
            names = [o["v"] for o in arr if o.get("v")]
            picked = pick_weighted(self.rng, names, w, fallback=names)
            it = next((o for o in arr if o["v"] == picked), None)
        else:
            it = self.rand_of(arr)
        if not it or it["v"] == "":
            return
        self.set_v(fid, it["v"])
        self.user_picked[fid] = True
        if fid == "stylePreset":
            p = STYLE_PRESETS.get(it["v"])
            if p:
                for k, val in p.items():
                    # 【V2】用户锁定的字段（含角色联动推导出来的）优先，不被预设覆盖 ——
                    # 否则「锁定连衣裙」会被预设改回上衣，锁定语义形同虚设。
                    if self.locked.get(k):
                        continue
                    self.set_v(k, val)
                    self.user_picked[k] = True

    # ---- 填空（generate 内部：未选字段随机补齐） ----
    def fill_blanks(self):
        filled = 0
        if self.mode != "NSFW":
            if not self.v("clothCat"):
                cats = [o["v"] for o in _opt("clothCat") if o.get("v")]
                if cats:
                    c = self.rand_of(cats)
                    self.set_v("clothCat", c)
                    filled += 1
                    subs = list((CLOTH.get(c) or {}).keys())
                    if subs:
                        self.set_v("clothSubCat", self.rand_of(subs))
                        filled += 1
            elif not self.v("clothSubCat"):
                subs = list((CLOTH.get(self.v("clothCat")) or {}).keys())
                if subs:
                    self.set_v("clothSubCat", self.rand_of(subs))
                    filled += 1
            if not self.v("poseCat"):
                pcs = [o["v"] for o in _opt("poseCat") if o.get("v")]
                if pcs:
                    c = self.rand_of(pcs)
                    self.set_v("poseCat", c)
                    filled += 1
                    ps = list((POSES.get(c) or {}).keys())
                    if ps:
                        self.set_v("poseSubCat", self.rand_of(ps))
                        filled += 1
            elif not self.v("poseSubCat"):
                ps = list((POSES.get(self.v("poseCat")) or {}).keys())
                if ps:
                    self.set_v("poseSubCat", self.rand_of(ps))
                    filled += 1

        for fid in ALL_FIELD_IDS:
            if self.user_picked.get(fid) or self.locked.get(fid):
                continue
            if fid in self.disabled:
                continue
            if self.mode == "SFW" and (fid in CLOTH_NSFW_IDS or fid in POSE_NSFW_IDS):
                continue
            if self.mode == "NSFW" and (fid in CLOTH_SFW_IDS or fid in POSE_SFW_IDS):
                continue
            if fid in ("clothSubCat", "poseSubCat"):
                continue
            if self.v(fid) != "":
                continue
            if fid == "clothBottom" and self.v("clothCat") not in ("上衣", "外套"):
                continue
            if fid == "socks" and self.v("shoes") == "赤脚":
                continue
            if fid in ("makeupDetail", "smudge") and self.v("makeup") == "素颜":
                continue
            if fid == "hairTie" and re.search(r"齐耳短发|短发", self.v("hairLen")):
                continue
            if self.mode == "NSFW" and fid in ("nsfwFabric", "nsfwMod") \
                    and self.v("nsfwState") == "仅剩配饰":
                continue
            rv = self.random_value_for(fid)
            if rv:
                self.set_v(fid, rv)
                filled += 1
        return filled

    # ---- 装配（10 段） ----
    def build_prompt(self, skip=None, character=None):
        skip = skip or {}
        parts = []
        risk_info = {"risky": False, "poseName": "", "poseCat": ""}
        is_nsfw = self.mode == "NSFW"

        s1 = opt_t("lens", self.v("lens")) + "，" + opt_t("viewpoint", self.v("viewpoint"))
        ss = self.v("shotSize")
        if ss:
            s1 += "，取" + ss + "景别"
        df = self.v("dof")
        if df:
            s1 += "，" + opt_t("dof", df)
        dev = self.v("device")
        if dev and dev != "不使用" and not skip.get("device"):
            s1 += "，画面以" + opt_t("device", dev) + "呈现"
        parts.append(s1 + "。")

        s2 = opt_t("mainLight", self.v("mainLight")) + "。"
        amb = self.v("ambient")
        if amb and amb != "不使用" and not skip.get("ambient"):
            s2 += opt_t("ambient", amb) + "。"
        s2 += opt_t("colorTone", self.v("colorTone")) + "。"
        film = self.v("film")
        if film and film != "不使用" and not skip.get("film"):
            s2 += opt_t("film", film) + "。"
        cine = self.v("cine")
        if cine and cine != "不使用" and not skip.get("cine"):
            s2 += opt_t("cine", cine) + "。"
        parts.append(s2)

        # 【V2 · 角色联动】角色附加特征（瞳色 / 标志特征）插在脸型之后、体型之前，
        # 让它们落在“人物维度”这一段的自然位置，而不是生硬地贴在末尾。
        _extra = [p for p in (self.person_extra or []) if p]
        s3 = (self.v("temperament") + "的" + self.v("age") + "，" + self.v("race") + "，"
              + self.v("skin") + self.v("texture") + "，" + self.v("face")
              + (("，" + "、".join(_extra)) if _extra else "")
              + "，" + self.v("body") + "、" + self.v("leg") + "，"
              + self.v("firstImp") + "。")
        tatt = self.v("tattoo")
        if tatt and tatt != "不使用" and not skip.get("tattoo"):
            # 【v3.11】纹身覆盖面积：self.tattoo_area 为空时插入空串，
            #          整句与加入该功能前**逐字一致**（默认「不使用」→ 空串）。
            s3 += (self.v("tattooPos") + "有一枚" + tatt + "纹身，" + self.tattoo_area
                   + "墨色贴合皮肤轮廓自然晕染，"
                   "边缘微微褪色，如同渗入皮肤下层的真墨。")
        parts.append(s3)

        s4 = self.v("hairLen") + "的" + self.v("hairColor") + self.v("hairCurl") + "，"
        htie = self.v("hairTie")
        if htie and htie != "披散":
            s4 += "扎成" + htie + "，"
        hb = self.v("hairBangs")
        if hb and hb != "无刘海":
            s4 += "留" + hb + "，"
        s4 += self.v("hairState")
        ha = self.v("hairAcc")
        if ha and ha != "不使用" and not skip.get("hairAcc"):
            s4 += "，" + opt_t("hairAcc", ha)
        parts.append(s4 + "。")

        if self.v("makeup") == "素颜":
            s5 = "面庞素净，不施粉黛"
        else:
            s5 = "妆容为" + opt_t("makeup", self.v("makeup"))
        md = self.v("makeupDetail")
        if md and md != "不使用" and not skip.get("makeupDetail"):
            s5 += "，" + md
        sm = self.v("smudge")
        if sm and sm != "不使用" and not skip.get("smudge"):
            s5 += "，" + opt_t("smudge", sm)
        na = self.v("nails")
        if na and na != "不使用" and not skip.get("nails"):
            s5 += "，" + opt_t("nails", na)
        parts.append(s5 + "。")

        s6 = opt_t("emotion", self.v("emotion")) + "，" + opt_t("eye", self.v("eye")) \
             + "，" + opt_t("mouth", self.v("mouth")) + "。"
        ims = []
        for idx in (1, 2):
            x = self.v("imperf%d" % idx)
            if x and x != "不使用" and not skip.get("imperf%d" % idx):
                ims.append(opt_t("imperf", x))
        if ims:
            s6 += "，".join(ims) + "。"
        parts.append(s6)

        s8 = ""
        if is_nsfw:
            # 【v3.18】选了内容项就用它的文字（等级筛 + 权重抽出来的那一条）；
            #           self.nsfw_act_text 为空（不介入 / 旧路径）时与之前逐字一致。
            s8 = self.nsfw_act_text or opt_t("nsfwPose", self.v("nsfwPose"))
            ch = self.v("nsfwChain")
            if ch and ch != "不使用" and not skip.get("nsfwChain"):
                # 【v3.14 标点修正】这里要把 nsfwPose 末尾的句号剥掉再拼 nsfwChain，
                #   原来只认英文句点 `\.`；池条目换成中文后末尾是「。」，剥不掉，
                #   于是正文出现「……耸动。，头向后仰起……」这种中英标点混排。
                #   字符集改成 [.。] 两种都认，末尾也统一用中文句号收尾，
                #   与其余九个段落（parts.append(s8 + "。")）保持一致。
                s8 = re.sub(r"[.。]\s*$", "", s8) + opt_t("nsfwChain", ch) + "。"
        else:
            po = find_pose(self.v("pose"))
            risk_info["poseName"] = po["cat"] and self.v("pose") or ""
            risk_info["poseCat"] = po["cat"]
            s8 = po["t"]
            pe = self.v("poseExtra")
            if pe and pe != "不使用":
                s8 += "，" + opt_t("poseExtra", pe)
            risk_info["risky"] = is_risky_pose(self.v("pose"), po["cat"], self.v("clothItem"))
            parts.append(s8 + "。")

        s7 = ""
        if is_nsfw:
            s7 = opt_t("nsfwState", self.v("nsfwState"))
            nm = self.v("nsfwMod")
            if nm and nm != "不使用" and not skip.get("nsfwMod"):
                s7 += " " + opt_t("nsfwMod", nm)
            nf = self.v("nsfwFabric")
            if nf and nf != "不使用" and not skip.get("nsfwFabric"):
                s7 += " " + opt_t("nsfwFabric", nf)
            nb = self.v("nsfwBody")
            if nb and nb != "不使用" and not skip.get("nsfwBody"):
                s7 += " " + opt_t("nsfwBody", nb)
            np = self.v("nsfwProp")
            if np and np != "不使用" and not skip.get("nsfwProp"):
                s7 += " " + opt_t("nsfwProp", np)
            ns = self.v("nsfwShoes")
            if ns and ns != "不使用" and not skip.get("nsfwShoes"):
                s7 += " " + opt_t("nsfwShoes", ns)
            parts.append(s8 + " " + s7)
        else:
            dims = []
            it = self.v("clothItem")
            if it and it != "不使用":
                dims.append(cloth_item_desc(it))
            bt = self.v("clothBottom")
            if bt and bt != "不使用" and not skip.get("clothBottom"):
                dims.append("下身" + cloth_item_desc(bt))
            mt = self.v("clothMat")
            if mt and mt != "不使用" and not skip.get("clothMat"):
                dims.append(opt_t("clothMat", mt))
            pa = self.v("clothPattern")
            if pa and pa != "不使用" and not skip.get("clothPattern"):
                dims.append(pa + "图案")
            dc = self.v("clothDeco")
            if dc and dc != "不使用" and not skip.get("clothDeco"):
                dims.append(dc + "装饰细节")
            ly = self.v("clothLayer")
            if ly and ly != "不使用" and not skip.get("clothLayer"):
                dims.append(opt_t("clothLayer", ly))
            sh = self.v("shoes")
            if sh and sh != "不使用" and not skip.get("shoes"):
                dims.append("脚踩" + opt_t("shoes", sh))
            skk = self.v("socks")
            if skk and skk != "不使用" and not skip.get("socks"):
                dims.append("双腿" + opt_t("socks", skk))
            ac = self.v("accessory")
            if ac and ac != "不使用" and not skip.get("accessory"):
                dims.append(opt_t("accessory", ac))
            if risk_info["risky"]:
                dims.append("下身" + self.v("pantyColor") + self.v("pantyStyle")
                            + "内裤完整覆盖私处，面料厚实不透明")
            s7 = "，".join(dims) + "。"
            parts.append(s7)

        s9 = opt_t("scene", self.v("scene")) + "。"
        pr = self.v("prop")
        if pr and pr != "不使用" and not skip.get("prop"):
            s9 += opt_t("prop", pr) + "。"
        we = self.v("weather")
        if we and we != "不使用" and not skip.get("weather"):
            s9 += opt_t("weather", we) + "。"
        parts.append(s9)

        s10 = opt_t("comp", self.v("comp")) + "，人物" + opt_t("compPos", self.v("compPos")) + "。"
        st = self.v("styleTag")
        if st and st != "不使用" and not skip.get("styleTag"):
            s10 += "整体呈现" + st + "风格。"
        parts.append(s10)

        text = "".join(parts)
        # 【v3.12】越界段：追加在正文**之后**，不插入既有 10 段之间。
        #          self.beyond_segment 为空（默认/受限）时等价于没这行。
        if self.beyond_segment:
            text += self.beyond_segment
        head = ""
        if character and character.get("word"):
            head = "画面角色为" + character["word"] + "。"
        # 【v3.18 · 双人互动段前置】
        #   用户要求「双人模式下**优先交代**两个人物正在互动的详细内容」——
        #   所以这一段从"追加在最后"改成"紧跟角色前缀、排在 10 段正文之前"。
        #   段落内容本身没变（仍是独立段，不插入 10 段之间），只调了位置：
        #   出图模型对**开头**的权重更高，先读到"两人在做什么"比读到最后更好用。
        #   空串（单人模式 / 默认）时 head + "" + text 与之前逐字一致。
        if self.multi_segment:
            return {"text": head + self.multi_segment + text, "risk": risk_info}
        return {"text": head + text, "risk": risk_info}

    # ---- 自检 ----
    def run_self_check(self, prompt, risk, skip=None):
        skip = skip or {}
        is_nsfw = self.mode == "NSFW"
        n = count_chars(prompt)
        scene_v = self.v("scene")
        water = False
        for o in _opt("scene"):
            if o.get("v") == scene_v and o.get("water"):
                water = True
                break
        F, S = [], []
        fn = "NSFW" if is_nsfw else "SFW"

        # F1 物理自洽
        night = {"情人旅馆", "夜店", "天台", "夜晚公园", "后巷", "监禁密室", "酒店套房"}
        f1ok = True
        f1note = "光源方向与场景时间匹配"
        if scene_v in night and self.v("mainLight") == "日光清新":
            f1ok = False
            f1note = "夜晚场景搭配了日光主光，光源矛盾"
        F.append({"id": "F1", "name": "物理自洽", "ok": f1ok, "note": f1note})

        # F2 语言模式
        if is_nsfw:
            cloth = opt_t("nsfwState", self.v("nsfwState"))
            pose = opt_t("nsfwPose", self.v("nsfwPose"))
            f2ok = len(cloth) > 30 and re.search(r"[a-zA-Z]", cloth) and len(pose) > 30 and re.search(r"[a-zA-Z]", pose)
            f2note = "服装/姿态段为英文整句" if f2ok else "服装或姿态段不是完整英文句子"
        else:
            f2ok = not re.search(r"[a-zA-Z]", prompt)
            f2note = "全文为中文叙事" if f2ok else "发现英文字母，SFW 要求全中文"
        F.append({"id": "F2", "name": "语言模式", "ok": f2ok, "note": f2note})

        # F3 禁令总表
        hit = []
        for w in BAN_A + BAN_B:
            if w in prompt:
                hit.append(w)
        if not is_nsfw:
            low = prompt.lower()
            for w in BAN_D:
                if w in low:
                    hit.append(w)
        f3ok = len(hit) == 0 or water
        f3note = "禁令词扫描通过" if not hit else ("命中词出现在含水场景，按豁免条款放行：" + "、".join(hit) if water else "命中禁令词：" + "、".join(hit))
        F.append({"id": "F3", "name": "禁令总表", "ok": f3ok, "note": f3note})

        # F4 内裤锚定
        f4ok = True
        f4note = "无高风险姿态，无需锚定"
        if not is_nsfw and risk["risky"]:
            f4ok = "完整覆盖私密处" in prompt
            f4note = "已写入「颜色+款式+完整覆盖」锚定句" if f4ok else "高风险姿态缺少内裤锚定句"
        F.append({"id": "F4", "name": "内裤锚定", "ok": f4ok, "note": f4note})

        # F5 字数
        f5ok = 300 <= n <= 800
        F.append({"id": "F5", "name": "字数 300–800", "ok": f5ok,
                  "note": "当前约 %d 字%s" % (n, "" if f5ok else ("（略少，可补充细节）" if n < 300 else "（超出上限，建议精简）"))})

        # F6 无违禁格式
        f6ok = not re.search(r"\([^)]*:\s*[\d.]+\)", prompt) and not re.search(r"masterpiece|best quality", prompt, re.I)
        F.append({"id": "F6", "name": "无违禁格式", "ok": f6ok, "note": "无权重语法/质量标签/标签串"})

        # F7 视角冲击力
        F.append({"id": "F7", "name": "视角冲击力", "ok": True, "note": "已选非常规视角 + 明确镜头类型"})

        # F8 服装维度
        if not is_nsfw:
            dim_ids = ["clothLayer", "clothItem", "clothBottom", "clothMat", "clothPattern",
                       "clothDeco", "shoes", "socks", "accessory"]
            dim_count = sum(1 for d in dim_ids
                            if self.v(d) and self.v(d) != "不使用" and not skip.get(d))
            f8ok = 6 <= dim_count <= 8
            F.append({"id": "F8", "name": "服装维度 6–8", "ok": f8ok, "note": "当前 %d 维" % dim_count})
        else:
            F.append({"id": "F8", "name": "服装维度", "ok": True, "note": "NSFW 免检"})

        # F9 服装来源
        item = self.v("clothItem") or ""
        bottom = self.v("clothBottom") or ""
        demo_hit = "深蓝灰缎面吊带裙" in item or ("白色丝质衬衫" in item and re.search(r"紧身短裙|包臀", bottom))
        F.append({"id": "F9", "name": "服装来源", "ok": True,
                  "note": "主件与文档示范主件接近（用户指定除外）" if demo_hit else "主件来自款式子表"})

        # F10 NSFW 英文整句
        if is_nsfw:
            F.append({"id": "F10", "name": "NSFW 英文整句", "ok": True, "note": "服装/姿态均为完整英文句子"})

        # S1 情绪→光影
        emo, light = self.v("emotion"), self.v("mainLight")
        if re.search(r"慵懒|温柔", emo) and light == "硬光":
            S.append({"id": "S1", "ok": False, "note": "慵懒温柔的情绪配硬光直射，建议换柔光"})
        else:
            S.append({"id": "S1", "ok": True, "note": "情绪与光影协调"})

        # S2 身份→姿态
        if self.v("firstImp") == "楚楚可怜" and re.search(r"直立|叉腰|背手", self.v("pose")) and not is_nsfw:
            S.append({"id": "S2", "ok": False, "note": "楚楚可怜配霸气站姿，气质撕裂（反差设定除外）"})
        else:
            S.append({"id": "S2", "ok": True, "note": "身份与姿态协调"})

        # S3 色调→情绪
        tone = self.v("colorTone")
        if re.search(r"霓虹混合|冷白荧光", tone) and re.search(r"冷淡|恍惚失神|宁静", emo):
            S.append({"id": "S3", "ok": False, "note": "霓虹/冷荧光撞情绪主调，注意色彩情绪一致"})
        else:
            S.append({"id": "S3", "ok": True, "note": "色调与情绪一致"})

        # S4 配饰→世界观
        S.append({"id": "S4", "ok": True, "note": "无跨世界观配饰冲突"})

        # S5 设备→画质
        dev, lens, dof = self.v("device"), self.v("lens"), self.v("dof")
        if (dev == "手机自拍" and re.search(r"长焦|中焦", lens)) or (dev == "监控摄像头" and dof == "浅景深"):
            S.append({"id": "S5", "ok": False, "note": "设备与画质自洽性存疑（手机不加焦段、监控不配浅景深）"})
        else:
            S.append({"id": "S5", "ok": True, "note": "设备与画质自洽"})

        # S6 裸露→场景
        if is_nsfw:
            state = self.v("nsfwState")
            if re.search(r"教室|图书馆|办公室", scene_v) and state == "仅剩配饰":
                S.append({"id": "S6", "ok": False, "note": "校园/职场场景配全裸档位，违背场景×裸露禁忌"})
            elif scene_v == "温泉" and state == "正常穿着":
                S.append({"id": "S6", "ok": False, "note": "温泉场景不宜「正常穿着」干燥衣物"})
            else:
                S.append({"id": "S6", "ok": True, "note": "场景与裸露档位一致"})
        else:
            S.append({"id": "S6", "ok": True, "note": "SFW 免检"})

        # S7 纹身融合
        if self.v("tattoo") != "不使用":
            S.append({"id": "S7", "ok": True, "note": "纹身已带皮肤融合描述，避免贴纸感"})
        else:
            S.append({"id": "S7", "ok": True, "note": "未使用纹身"})

        # S8 道具位置
        if self.v("prop") != "不使用":
            S.append({"id": "S8", "ok": True, "note": "道具已交代位置关系"})
        else:
            S.append({"id": "S8", "ok": True, "note": "未使用道具"})

        # S9 眼神→角度
        vp, eye = self.v("viewpoint"), self.v("eye")
        if re.search(r"俯拍|鸟瞰", vp) and eye == "挑逗":
            S.append({"id": "S9", "ok": False, "note": "俯拍机位配「挑逗注视」存在视线方向矛盾"})
        elif re.search(r"仰拍|虫视", vp) and eye == "乞求哀怨":
            S.append({"id": "S9", "ok": False, "note": "仰拍机位配「向上望的哀求」存在视线方向矛盾"})
        else:
            S.append({"id": "S9", "ok": True, "note": "眼神方向与机位匹配"})

        # S10 姿态×服装
        if not is_nsfw:
            pose_name = self.v("pose")
            item_txt = (self.v("clothItem") or "") + (self.v("clothBottom") or "")
            need_skirt = bool(re.search(r"裙摆飞扬|荡秋千|提裙摆|撩裙边", pose_name))
            trouser_only = bool(re.search(r"裤", item_txt)) and not re.search(r"裙", item_txt)
            if need_skirt and trouser_only:
                S.append({"id": "S10", "ok": False, "note": "「%s」需要裙装，当前选择为裤装" % pose_name})
            else:
                S.append({"id": "S10", "ok": True, "note": "姿态与服装协调"})
        else:
            S.append({"id": "S10", "ok": True, "note": "NSFW 免检"})

        # S11 姿态×鞋袜
        if not is_nsfw:
            pose_name = self.v("pose")
            need_heel = bool(re.search(r"高跟鞋摇曳", pose_name))
            need_sock = bool(re.search(r"丝袜卷到大腿|整理丝袜", pose_name))
            heel_ok = self.v("shoes") and self.v("shoes") != "不使用" and "高跟" in self.v("shoes")
            sock_ok = self.v("socks") and self.v("socks") != "不使用" and re.search(r"丝袜|过膝袜", self.v("socks"))
            if need_heel and not heel_ok:
                S.append({"id": "S11", "ok": False, "note": "「高跟鞋摇曳」需要高跟鞋，当前鞋履不匹配"})
            elif need_sock and not sock_ok:
                S.append({"id": "S11", "ok": False, "note": "「%s」需要丝袜/过膝袜，当前未选择" % pose_name})
            else:
                S.append({"id": "S11", "ok": True, "note": "姿态与鞋袜协调"})
        else:
            S.append({"id": "S11", "ok": True, "note": "NSFW 免检"})

        # S12 姿态×场景
        if not is_nsfw:
            pose_name = self.v("pose")
            if pose_name == "出水瞬间" and not water:
                S.append({"id": "S12", "ok": False, "note": "「出水瞬间」需要水场景（温泉/浴室/海边/水下）"})
            elif re.search(r"钢管舞", pose_name) and not re.search(r"夜店|点歌厅", self.v("scene")):
                S.append({"id": "S12", "ok": False, "note": "「钢管舞」需要钢管设施，建议夜店/点歌厅场景"})
            elif re.search(r"雨中行走", pose_name) and not re.search(r"雨", self.v("weather") + self.v("scene")):
                S.append({"id": "S12", "ok": False, "note": "「雨中行走」建议搭配细雨天气或雨天场景"})
            else:
                S.append({"id": "S12", "ok": True, "note": "姿态与场景协调"})
        else:
            S.append({"id": "S12", "ok": True, "note": "NSFW 免检"})

        # S13 NSFW 姿态×场景
        if is_nsfw:
            np = self.v("nsfwPose")
            if re.search(r"自慰|口交|调戏|后入|骑乘|传教士", np) and re.search(r"教室|图书馆|办公室|医院", scene_v):
                S.append({"id": "S13", "ok": False, "note": "「%s」出现在校园/职场/医疗场景，题材敏感" % np})
            else:
                S.append({"id": "S13", "ok": True, "note": "NSFW 姿态与场景协调"})
        else:
            S.append({"id": "S13", "ok": True, "note": "SFW 免检"})

        return {"F": F, "S": S, "n": n, "fn": fn}

    def _fmt_report(self, prompt, risk, checks, filled, trimmed, seed, character=None):
        is_nsfw = self.mode == "NSFW"
        lines = ["━" * 34, "K2 V3 融合版 · 随机出题", "━" * 34]
        lines.append("模式：%s（%s）　种子：%s　字数：约 %d 字"
                     % (self.mode, "露骨" if is_nsfw else "含蓄",
                        seed if seed is not None else "随机", checks["n"]))
        if character and character.get("word"):
            lines.append("角色：" + character.get("word", "") + "　体型覆盖："
                         + (CHAR_BUILD_TO_BODY.get(character.get("build", ""), "") or "（未指定）"))
        if trimmed:
            lines.append("已自动精简 %d 项次要选项" % trimmed)
        if filled:
            lines.append("本次随机补全 %d 项" % filled)
        fok = sum(1 for x in checks["F"] if x["ok"])
        sok = sum(1 for x in checks["S"] if x["ok"])
        lines.append("自检 致命项 %d/%d　风格项 %d/%d　字数 %d"
                     % (fok, len(checks["F"]), sok, len(checks["S"]), checks["n"]))
        lines.append("")
        lines.append("【致命项】")
        for c in checks["F"]:
            lines.append("  %s %s %s——%s" % ("✅" if c["ok"] else "⚠️", c["id"], c.get("name", ""), c["note"]))
        lines.append("【风格项】")
        for c in checks["S"]:
            lines.append("  %s %s %s——%s" % ("✅" if c["ok"] else "⚠️", c["id"], c.get("name", ""), c["note"]))
        if self.adjust_notes:
            lines.append("【冲突自动调整】")
            for note in self.adjust_notes:
                lines.append("  · " + note)
        lines.append("━" * 34)
        return "\n".join(lines)

    # ---- 主入口 ----
    def full_random(self, seed=None, locked=None, character=None, char_link=None,
                    tattoo_weight=None, person_count=None, tattoo_area=None,
                    strict_solo=None, level=None, yuri=False,
                    boundary=None, nsfw_w=None, off_items=None,
                    chars=None, act_weights=None):
        """等价「全部随机 → 生成」；locked={字段id:值} 锁死部分字段。

        【V2】char_link = k2_v3_char_link.build_character_overrides() 的结果，
        命中时把 {"fields": 角色推导字段} 并入 locked，{"person_extra": 短语}
        并入 ③人物维度。

        【V2】tattoo_weight 为 None 表示「纹身开关关闭」→ 不调用纹身权重，
        行为与加入该功能前完全一致；给数值时才按权重调整纹身出现率。

        【v3.11】person_count / tattoo_area 为 None 表示「不开这项功能」→
        完全不介入，行为与 v3.10 一致。详见 apply_v311() 的注释。

        【v3.17】chars（双人段角色点名）为 None / 空 表示「不点名」。

        【v3.18】act_weights = {内容项: 权重}（展示生殖器/自慰/口交/性交/其它）。
        只在 level 明确给出时才参与「等级筛 + 权重抽卡」；level 为 None 时
        整条内容项链路**根本不启动**，行为与 v3.17 逐字一致（金标准快照的前提）。
        """
        # 应用锁定
        for fid, val in (locked or {}).items():
            if fid in self.fields:
                self.set_v(fid, val)
                self.locked[fid] = True
                self.user_picked[fid] = True

        # 【V2 · 角色联动】角色推导字段并入锁定；附加特征并入 ③
        if char_link:
            for fid, val in (char_link.get("fields") or {}).items():
                if fid in self.fields and val:
                    self.set_v(fid, val)
                    self.locked[fid] = True
                    self.user_picked[fid] = True
            if char_link.get("person_extra"):
                self.person_extra = list(char_link["person_extra"])

        # 角色：覆盖体型（body），注入角色词
        if character:
            build = character.get("build") or ""
            body_override = CHAR_BUILD_TO_BODY.get(build, "")
            if body_override:
                self.set_v("body", body_override)
                self.locked["body"] = True
                self.user_picked["body"] = True

        # 分类级联（锁定时跳过）
        if not self.locked.get("clothCat"):
            self.randomize_cloth_cat()
        else:
            self._randomize_subcat("cloth")
        if not self.locked.get("poseCat"):
            self.randomize_pose_cat()
        else:
            self._randomize_subcat("pose")

        # 逐字段随机（含冲突预过滤）
        for sec in SECTIONS:
            if sec["id"] == "cloth":
                fs = CLOTH_FIELDS["nsfw"] if self.mode == "NSFW" else CLOTH_FIELDS["sfw"]
            elif sec["id"] == "pose":
                fs = POSE_FIELDS["nsfw"] if self.mode == "NSFW" else POSE_FIELDS["sfw"]
            else:
                fs = sec.get("fields") or []
            for f in fs:
                self.randomize_field(f["id"])

        self.enforce_conflicts()

        filled = self.fill_blanks()
        self.enforce_conflicts()

        # 【V2 新增·纹身权重】仅在节点显式开启时介入（None = 关闭 = 不干预）
        if tattoo_weight is not None:
            self.apply_tattoo_weight(tattoo_weight)

        # 【v3.18 新增能力】NSFW 内容项抽卡 —— **必须排在 apply_v311 之前**：
        #   · 它按 person_count 决定「单人只抽 solo 条目」（单人保证）
        #   · 抽到的内容项要交给 apply_v311 去组装双人段（两处共用同一个内容项）
        #   level 为 None（金标准 / 不指定档位）时本行直接返回，不消耗随机数。
        _solo_only = ((person_count or "单人") == "单人")
        _act = self.apply_nsfw_act(level=level, weights=act_weights,
                                   solo_only=_solo_only)

        # 【v3.11 新增能力】同样只在显式传参时介入；
        # full_random() 不带这些参数调用时，本行等价于不存在。
        self.apply_v311(person_count=person_count, tattoo_area=tattoo_area,
                        strict_solo=strict_solo, level=level, yuri=yuri,
                        chars=chars, act=_act)

        # 【v3.12 新增能力】同样只在显式传参时介入；
        # 不带这些参数调用时，本行等价于不存在。
        self.apply_v312(boundary=boundary, nsfw_w=nsfw_w, level=level)

        skip = {}
        # 【v3.13】逐项开关：off_items 里只会命中 SKIPPABLE_SET 中的项，
        #   核心字段不在其中，因此关不掉 —— 这是如实的行为，不是漏做。
        #   off_items 为空时这一段完全不执行。
        for _fid in (off_items or ()):
            if _fid in SKIPPABLE_SET:
                skip[_fid] = True
        result = self.build_prompt(skip, character=character)
        checks = self.run_self_check(result["text"], result["risk"], skip)

        # 【v3.18 · 双人字数上限 1000】
        #   用户要求：双人模式「优先交代两人互动的详细内容」，同时整条不超 1000 字。
        #   双人比单人多出一整段互动（60–120 字）+ 角色点名与外观锚点（30–60 字），
        #   沿用 600 字上限会把正文压得过狠，所以双人单独放宽到 1000。
        #   ⚠️ 单人维持 600 不变（既有行为不动）。
        _limit = 1000 if (person_count or "单人") not in (None, "", "单人") else 600
        if checks["n"] > _limit:
            for key in TRIM_ORDER:
                if checks["n"] <= _limit:
                    break
                skip[key] = True
                result = self.build_prompt(skip, character=character)
                checks = self.run_self_check(result["text"], result["risk"], skip)

        slots = {fid: self.v(fid) for fid in ALL_FIELD_IDS if self.v(fid)}
        # 【v3.18】把抽到的内容项也放进槽位（空 = 没启用内容项链路，键也不出现 ——
        #   保证 level=None 的老路径与金标准快照逐字一致）
        if self.nsfw_act:
            slots["nsfwAct"] = self.nsfw_act
        report = self._fmt_report(result["text"], result["risk"], checks,
                                  len(skip), filled, seed, character)
        return {
            "text": result["text"],
            "report": report,
            "slots": slots,
            "n": checks["n"],
            "filled": filled,
            "trimmed": len(skip),
            "risky": result["risk"]["risky"],
            "mode": self.mode,
            # 【v3.11】百合 / 多人场景的分级提示（空串 = 没有多人群段）。
            # 只用于报告回显，**不进正文**，因此不影响任何生成结果。
            "multi_grade": self.multi_grade,
            # 【v3.12】越界段的分级提示（空串 = 没有越界段）。
            # 只用于报告回显，**不进正文**，不影响任何生成结果。
            "beyond_grade": self.beyond_grade,
        }


def axis_options():
    """【V2 · 面板对齐】列出所有可调轴及其选项（前端据此渲染"每项一行滑块"）。

    返回 [{"axis", "field", "options": [{"value", "default", "min", "max", "step"}]}]
    与原件 /options 里 _axis_options 的结构保持一致。
    """
    out = []
    for axis, fid in WEIGHT_AXIS_FIELDS.items():
        defaults = WEIGHT_AXIS_DEFAULTS.get(axis, {})
        opts = [{"value": o.get("v"),
                 "default": float(defaults.get(o.get("v"), 1.0)),
                 "min": float(WEIGHT_MIN), "max": float(WEIGHT_MAX),
                 "step": float(WEIGHT_STEP)}
                for o in _opt(fid) if o.get("v")]
        out.append({"axis": axis, "field": fid, "options": opts})
    return out


def full_random(seed=None, mode="SFW", locked=None, character=None,
                weight_spec="", char_link=None, tattoo_weight=None,
                person_count=None, tattoo_area=None, strict_solo=None,
                level=None, yuri=False, boundary=None, nsfw_w=None,
                avoid=None, temperature=None, off_items=None,
                chars=None, act_weights=None, item_weights=None):
    """模块级便捷入口。seed 为 None 时用系统随机。

    weight_spec   : 「权重覆盖」写法（如 `视角:2, 俯视机位:0.5`），空串 = 等概率
    char_link     : k2_v3_char_link.build_character_overrides() 的结果（可 None）
    tattoo_weight : 纹身权重（None = 开关关闭，不干预；数值 0–2 = 按权重调整）

    【v3.11】person_count / tattoo_area / strict_solo / level / yuri
             均为**可选新增项**，全部取默认（None/False）时行为与 v3.10 完全一致。

    【v3.12】boundary（越界段）/ nsfw_w（NSFW 强度）同样可选，
             取默认（None）时行为与 v3.11 完全一致。

    【v3.13】随机性增强：
             avoid       : {字段: [最近用过的取值]}，命中则降权（None/空 = 不干预）
             temperature : 权重温度（None/1.0 = 不干预）
             off_items   : 逐项关闭的字段 id 列表（None/空 = 不关任何项）
             三者取默认时行为与 v3.12 完全一致。

    【v3.17】chars        : 双人段的两位角色 [{word, short, brief}, …]（None = 不点名）

    【v3.18】act_weights  : {内容项: 权重}，如 {"自慰": 2.0, "性交": 0}。
             只在 level 明确给出（非 None）时启动「等级筛 + 权重抽卡」；
             level 为 None 时整条链路不启动，行为与 v3.17 逐字一致。

    【v3.22】item_weights : {选项名: 权重}（鞋履种类展开 / 水面波光等）。
             None/空 = 不介入；非空时按选项名叠加到对应字段的候选上，
             命中概率 = 该选项权重 ÷ 该字段候选权重之和，0 = 永不出现。
    """
    rng = random.Random(seed) if seed is not None else random
    axis_mult, item_mult = parse_weight_spec(weight_spec)
    axis_weights = build_axis_weights(axis_mult, item_mult)
    g = K2V3(rng=rng, mode=mode, axis_weights=axis_weights,
             avoid=avoid, temperature=temperature, item_weights=item_weights)
    return g.full_random(seed=seed, locked=locked, character=character,
                         char_link=char_link, tattoo_weight=tattoo_weight,
                         person_count=person_count, tattoo_area=tattoo_area,
                         strict_solo=strict_solo, level=level, yuri=yuri,
                         boundary=boundary, nsfw_w=nsfw_w,
                         off_items=off_items,
                         chars=chars, act_weights=act_weights)
