# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-PortraitV2 :: K2V3Generator 节点

在原 V1 基础上做两件事：
  ① 【角色联动】依据所选角色的特征（外貌 / 服装 / 气质 / 场景）组织提示词；
     特征数据全部取自插件自带的 character_pool.json + character_tags_zh.py，
     详见 k2_v3_char_link.py 顶部的「数据来源」说明。
  ② 【面板对齐】新增「内容等级」（NSFW 六档）与「权重覆盖」，命名 / 取值范围 /
     交互逻辑对齐选择器 v3.8.0：
       · 内容等级 = LEVELS(sfw/suggestive/explicit/hardcore/extreme/any) + LEVEL_ZH
       · 权重    = 轴级（WEIGHT_MIN 0.0 / MAX 3.0 / STEP 0.05）
                   命中概率 = 该选项权重 ÷ 同轴全部选项权重之和
     内容等级与 K2V3 的 SFW/NSFW 二模式桥接：sfw/suggestive → SFW，
     explicit 及以上 → NSFW（同一设置只留一个入口，故移除原「模式」控件）。

节点：
  K2V3GeneratorV2
    输入：内容等级 / 随机种子 / 每次运行重随机 / 角色联动 / 角色 / 角色预设
          / 权重覆盖 / 锁定字段(JSON)
          【v3.11】纹身面积 / 人物数量 / 百合模式
          【v3.17】第二角色
          【v3.18】展示生殖器权重 / 自慰权重 / 口交权重 / 性交权重 / 其它权重
    输出：提示词(STRING) + 自检报告(STRING) + 槽位JSON(STRING)
          【v3.11】随机种子(INT) + 元数据JSON(STRING)
          ⚠️ 新增端口一律**追加在末尾**，既有索引 0/1/2 不变，老工作流连线不丢。
          ⚠️ 新增控件同样**追加在 optional 末尾**，位置一旦插进中间，
             既有工作流按位置存的 widgets_values 会整体错位。

v3.11 新增能力的接线范式（与 V2 既有新增能力一致）：
    **控件取"不干预"值 = 传给规则层 None = 规则层根本不调用**。
    因此老工作流（没有这几个控件 → 取不到值）与新节点（默认值 = 不干预）
    都不会触发新逻辑，出题结果与 v3.10 逐字一致。
    唯一的例外是「人物数量」：用户明确要求默认单人且单人必须保证只有一人，
    所以这里默认就是"单人"（会启用单人保证），这是用户授权的定向变更。
"""

import json
import random

from .k2_v3_tables import LEVELS, LEVEL_ZH, LEVEL_BAND_ZH
from .krea2_characters import CHARACTER_NAMES, get_character, card
from . import krea2_v3_rules as V3
from . import k2_v3_store as PS
from . import k2_v3_char_link as CL
from . import k2_v3_extra as EX
# 【v3.21】把生成出的正文写进 PNG 元数据（注入 extra_pnginfo 里的 workflow）
from . import k2_v3_pngmeta as PM

AUTO = "自动"
NO_PRESET = "不使用"

# 【v3.11】纹身面积：选项取自 presets/k2_v3_extra_pools.json 的 tattooArea，
# 前面再补一个「跟随随机」——它对应传给规则层的 None（= 不干预）。
# ⚠️ 默认值取「跟随随机」而不是「不使用」：默认就要零变化，
#    "不使用" 会真的把纹身关掉，那样默认行为就变了。
AREA_AUTO = "跟随随机"
TATTOO_AREAS = [AREA_AUTO] + [x.get("v") for x in EX.extra_pool("tattooArea") if x.get("v")]

# 【v3.11】人物数量：默认单人（用户要求）。
PERSON_COUNTS = [x.get("v") for x in EX.extra_pool("personCount") if x.get("v")] or ["单人", "双人"]
PERSON_DEFAULT = "单人"

# 【v3.18】NSFW 内容项 -> 权重控件名。
#   控件名 = 内容项名 + "权重"（展示生殖器权重 / 自慰权重 / 口交权重 / 性交权重 / 其它权重），
#   一一对应，不在别处再写一遍字符串 —— 内容项表改了这里自动跟着改。
ACT_WEIGHT_CTL = {a: a + "权重" for a in EX.nsfw_act_names()}

# 【v3.19】「内容权重 · 跟随等级」开关的控件名。
#   开启后按档位自动套用权重基准（替代手动滑块），默认关闭 = 纯手动。
ACT_AUTO_CTL = "内容权重跟随等级"

# 【v3.22】鞋履详细种类权重 + 水面波光权重 —— 控件名。
#   ⚠️ 与 NSFW 内容项权重同构：控件名 = 种类名 + "权重"，一一对应，
#      种类表（V3.SHOE_CATEGORIES）改了这里自动跟着改，不在别处再写字符串。
SHOE_WEIGHT_CTL = {cat: cat + "权重" for cat in V3.shoe_category_names()}
WATER_WEIGHT_CTL = "水面反光权重"
# 取值范围与默认值：与轴级权重（WEIGHT_MIN/MAX/STEP）统一，0 = 永不出现，1.00 = 标准。
SHOE_W_MIN = V3.WEIGHT_MIN
SHOE_W_MAX = V3.WEIGHT_MAX
SHOE_W_STEP = V3.WEIGHT_STEP
SHOE_W_DEFAULT = 1.0

# 【v3.14 清理】这里原本有个模块级 CATEGORY 变量，但节点类用的是下面的
#   SECTION_NAME（第 229 行 CATEGORY = SECTION_NAME），它从来没被读到过 ——
#   属于 v3.10 收敛时漏下的死变量，已删除。分组请看 SECTION_NAME。

# 内容等级 -> K2V3 二模式（K2V3 只有 SFW / NSFW 两档，这里做桥接）
LEVEL_TO_MODE = {
    "sfw": "SFW", "suggestive": "SFW",
    "explicit": "NSFW", "hardcore": "NSFW", "extreme": "NSFW", "any": "NSFW",
}

# ============================================================================
# 【UI 对齐】节点分类 / 控件说明
#   分组名 = 插件名 / 组名，两级。V3 已把 V1/V2 的旧模块整批移除，
#   不再需要「加后缀区分同族插件」那套写法，这里直接换成 V3 品牌名。
# ============================================================================
SECTION_NAME = "Krea2-Portrait-V3/主要功能"

# ============================================================================
# 【UI 对齐】控件中文说明（tooltip）
#   与 Krea2-Portrait 的 nodes.TIP 同构：集中在一处维护，由 with_tooltips() 注入，
#   鼠标停在控件上就能看到，不用翻文档。
#   ⚠️ 这里只改「展示」，任何条目都不影响生成逻辑本身。
# ============================================================================
TIP = {
    # 【v3.18 退休】以下四个控件已不再是可操作的入口 —— 界面全部撤掉，
    #   值只在一种情况下还会被读到：老工作流的「内容强度」是旧值「手动（分别设置）」
    #   （不是有效档位），此时回落到「内容等级」以保证升级后不静默掉档。
    #   ⚠️ 控件本身**留在原位不删**：ComfyUI 按位置存 widgets_values，
    #      删掉中间任意一个都会让所有既有工作流的控件值整体错位。
    "内容等级": "【v3.18 起退休】等级控制已收敛到「内容强度」一个控件。"
                "此控件的值只在「内容强度」为旧值「手动（分别设置）」时作为兼容兜底被读取。",
    "内容边界": "【v3.18 起退休】越界描写已并入「内容强度 = 极端」档，不再是独立开关。",
    "NSFW开关": "【v3.18 起退休】NSFW 措辞强度已由「内容强度」档位统一决定。",
    "NSFW强度": "【v3.18 起退休】同上 —— 措辞强度随「内容强度」档位走。",
    "角色": "绑定角色：按该角色的发色 / 发长 / 扎法 / 体型 / 服装大类 / 气质 / 场景"
            "组织提示词。可打开节点面板「👤 角色图鉴」浏览缩略图点选。",
    "角色联动": "开启后按所选角色的特征组织内容；角色没有的特征仍交给随机，"
                "不会凭空杜撰。关闭则完全随机（仅保留角色名）。",
    "每次运行重随机": "开启后每次 Queue 运行都会重新随机出一条新提示词"
                      "（内部靠 IS_CHANGED 绕过 ComfyUI 的结果缓存）。",
    "随机种子": "固定种子可复现同一条提示词 —— 需要配合「每次运行重随机」关闭才生效。"
                "填 0 且关闭重随机时，用种子 0 出固定结果。",
    "角色预设": "选择已保存的角色预设一键切换（预设里含角色 + 锁定的生成字段）。"
                "可在节点面板「🗂 角色预设」里保存 / 加载 / 删除。",
    "权重覆盖": "轴级权重，写法 `视角:2, 俯视机位:0.5, 大波浪:0`，逗号分隔。"
                "命中概率 = 该选项权重 ÷ 同轴全部选项权重之和；0 = 永不出现；"
                "整轴同乘一个倍数不改变分布。可在「⚙️ 设置」里用滑块调。",
    "锁定字段": "把某些维度固定下来，其余仍随机。JSON 写法："
                "{\"lens\":\"广角\",\"scene\":\"温泉\"}。优先级高于角色预设。",
    "纹身开关": "默认**关闭** —— 关闭时纹身完全交给随机，出题结果与本控件无关。"
                "开启后才按下方「纹身权重」调整纹身出现率。",
    "纹身权重": "仅在「纹身开关」开启时生效。1.0 = 保持随机结果不改；"
                "0 = 纹身永不出现；(0,1) 按比例降低出现率；(1,2] 提高出现率。"
                "取值 0–2，步进 0.05。",
    "纹身面积": "纹身覆盖身体面积。**跟随随机（默认）= 本控件不介入**，出题结果"
                "与没有这个控件时完全一致；选「不使用」= 强制无纹身；"
                "其余档位会在纹身描述里写入对应的面积说法。",
    # 【v3.17】控件入口已迁移到「⚙️ 设置 → 子级交互」，这里只说语义，
    #          不再写「在下方内容设定一行」之类的界面位置（位置会变，语义不会）。
    "人物数量": "**单人（默认）**：保证画面里只有一个人 —— NSFW 词库里带「双人/多人」"
                "的姿势条目会被换成纯单人条目。"
                "**双人**：在正文之后追加一段独立的双人互动场景（按内容等级分档）。"
                "⚠️ 这是 v3.11 起唯一的定向输出变更，用户明确要求。"
                "入口：插件面板「⚙️ 设置」面板最底部的「子级交互」区。",
    "百合模式": "仅在「人物数量 = 双人」时生效：强制走百合场景池（两名女性），"
                "并按分级附带分级提示（全年龄/暗示/露骨/强露骨/极端）。"
                "⚠️ **双人 + NSFW 档本来就会自动走百合**（用户要求：SFW 出正常互动、"
                "NSFW 出百合），这个开关的作用是**在 SFW 档也强制百合**。",
    "内容权重跟随等级": "**新增（v3.19）**：开启后，⑦ 里那 13 个内容项权重不再由滑块决定，"
                        "而是**按当前「内容强度」档位自动算出一套基准** —— 档位越高越偏向该档的"
                        "招牌内容（露骨偏自慰、强露骨偏口交、极端偏性交）。\n"
                        "默认**关闭** = 滑块说了算，出题结果与没有这个开关时逐字一致。\n"
                        "开启时 ⑦ 区的滑块会置灰只读，并显示自动基准值；关掉立刻回到手动值"
                        "（滑块里存的值不会被覆盖）。\n"
                        "入口：插件面板「⚙️ 设置」→「⑦ NSFW 内容权重」区顶部。",
    # ---- v3.20 新增 ----
    "角色外观锁定": "**默认开启**：只要指定了角色，就把「这个人长什么样」钉死 ——\n"
                    "发色 · 发长 · 卷度 · 发态 · 扎法 · 刘海 · 瞳孔颜色。\n"
                    "同一个角色不管重随多少次、换种子、重启 ComfyUI，这些描述都完全一样。\n"
                    "⚠️ 它**独立于「角色联动」**：联动关掉（不想被锁服装 / 场景 / 气质）时，"
                    "外观依然保持一致。\n"
                    "取值口径：角色有标签 → 用标签；标签缺失时：扎法 / 刘海取中性值"
                    "（= 正文里不写这两句，不杜撰），发色 / 发长 / 卷度 / 发态 / 瞳色 按"
                    "「角色名派生」固定取一个（同一角色永远一样，跨角色仍有变化）。\n"
                    "关闭 = 外观交回随机引擎，每次重随都可能换发色 / 卷度 / 瞳色。",
    # ---- v3.17 新增 ----
    "第二角色": "双人模式下的第二位角色。两位角色会各自点名写进双人/百合段，"
                "互动句的主语替换成真实角色名 —— 双人画面里不会出现「她…她」的指代歧义。"
                "第一位角色（「角色」控件）仍是正文主体的特征来源；"
                "留「自动」则双人段只用「其中一名女性 / 另一名女性」称呼，不点名。"
                "入口：插件面板「⚙️ 设置」→「子级交互」，或「👤 角色图鉴」顶部切到「角色 B」。",
    # ---- v3.13 新增；v3.18 起升级为**唯一的等级控制项** ----
    "内容强度": "**【v3.18 起这是唯一的 NSFW 等级控制项】**五档：全年龄 / 暗示 / "
                "露骨 / 强露骨 / 极端。抽卡严格按所选档位出对应等级的内容：\n"
                "· 全年龄 / 暗示 → SFW：正文走 SFW 池，不写 ⑧ 姿态段，无性行为；\n"
                "· 露骨 → 可出现「展示生殖器」「自慰」；\n"
                "· 强露骨 → 再加「口交」；\n"
                "· 极端 → 再加「性交」，并自动追加越界描写。\n"
                "各项内容在可用范围内按「权重调节项」分配概率。"
                "入口：插件面板「⚙️ 设置」最顶部。",
    "联动强度": "角色特征的采用比例。1.0（默认）= 全部采用；0.5 = 只采用一半的"
                "角色推导字段，其余交回随机；0 = 只保留角色名，特征全随机。",
    "去重记忆": "**提升随机性质量的关键旋钮**。记录每个字段最近用过的取值，"
                "生成时把它们降权，避免连续抽到同一个场景/道具。"
                "0（默认）= 关闭，完全不干预；推荐日常用 3–6。",
    "多样性温度": "对权重做幂运算：<1 更保守（集中）、>1 更极端（发散）。"
                  "只有在「权重覆盖」或「去重记忆」让权重变得不均匀时才看得出效果；"
                  "1.0（默认）= 不干预。",
    "关闭可选项": "逐项关闭正文里的**补充项**（共 28 项：道具/天气/鞋袜/配饰/"
                  "纹身/胶片/瑕疵…）。留空 = 一项都不关。"
                  "⚠️ 镜头/人物/发型/服装主件/姿态/场景/构图这些**核心项关不掉**。",
    "批次数": "一次生成几条候选，在第 7 个输出端口「候选列表」里给出。"
              "1（默认）= 只生成一条，行为与之前完全一致。",
    "槽位JSON": "（输出）本次各字段的结构化取值，便于调试与对账。",
    "随机种子": "（输出）本次实际使用的种子；关掉「每次运行重随机」后改它可精确复现某一条。",
    "元数据JSON": "（输出）本次出题的元数据（种子 / 完整提示词 / 等级 / 角色 / 人物数量 / "
                  "纹身面积 / 百合 / 插件名），可直接写进图片元数据。",
}


# ============================================================================
# 【v3.19】13 个内容项权重的 tooltip —— **统一生成**，不再手写。
#
# 为什么改成生成：v3.18 手写那 5 条，这一轮内容项扩到 13 项时直接漏了 8 条
#   （自检里"每个控件都有 tooltip"当场报红）。生成式写法的好处是
#   **内容项表改了这里自动跟着改**，不会再出现"加了项忘了写说明"。
# ============================================================================
ACT_TIP_DESC = {
    "展示生殖器": "把私处暴露在镜头前。单人模式用单人可见的条目。",
    "自慰": "自我刺激。单人模式只抽单人条目（含道具版），不会带出第二个人。",
    "潮吹·失禁": "高潮射出体液 / 失控排泄。单人可达。",
    "手交": "用手刺激对方 —— 需要第二人，**单人模式自动排除**。",
    "乳交": "用胸部夹合 —— 需要第二人，**单人模式自动排除**。",
    "足交": "用脚刺激对方 —— 需要第二人，**单人模式自动排除**。",
    "口交": "口部对性器的接触。单人模式自动改用单人可见的条目（道具版）。",
    "深喉": "吞到喉咙深处 —— 需要第二人，**单人模式自动排除**。",
    "性交": "含各类插入体位的交合描写，另含单人道具版。",
    "肛交": "后庭插入，含单人道具版与双人体位。",
    "束缚·捆绑": "绳索 / 缎带固定肢体，单人自缚与双人牵引都有条目。",
    "多人": "三人及以上同时在场 —— 需要第二人以上，**单人模式自动排除**。",
    "其它": "保守姿态（诱惑 / 展示 / 暴露）与非插入式挑逗，抽不到上面那些时的兜底。",
}
for _act in EX.nsfw_act_names():
    _min_zh = {"sfw": "全年龄", "suggestive": "暗示", "explicit": "露骨",
               "hardcore": "强露骨", "extreme": "极端"}.get(
        EX.nsfw_act_min(_act), "露骨")
    TIP[ACT_WEIGHT_CTL[_act]] = (
        "【%s档起可用】%s\n"
        "权重 0 = 该项永不出现；1.00（默认）= 标准比例；上限 %.2f = 大幅提高占比。"
        "概率 = 该项权重 ÷ 当前档位下**全部可用项**的权重之和。\n"
        "入口：插件面板「⚙️ 设置」→「⑦ NSFW 内容权重」。"
        % (_min_zh, ACT_TIP_DESC.get(_act, ""), EX.ACT_W_MAX))


# ============================================================================
# 【v3.22】鞋履详细种类权重 + 水面波光权重 的 tooltip —— 同样**自动生成**。
#   种类表改了（加/删鞋款种类）这里自动跟上，不手写清单。
# ============================================================================
SHOE_TIP_DESC = {
    "高跟鞋": "细跟/尖头/踝带等细高跟类（%d 款）。" % len(V3.SHOE_CATEGORIES["高跟鞋"]),
    "靴子": "过膝靴/马丁靴/切尔西靴/袜靴/踝靴/高筒靴/登山靴（%d 款）。" % len(V3.SHOE_CATEGORIES["靴子"]),
    "运动鞋": "厚底运动鞋/帆布鞋/老爹鞋（%d 款）。" % len(V3.SHOE_CATEGORIES["运动鞋"]),
    "凉鞋": "细带凉鞋/穆勒鞋/玛丽珍鞋（%d 款）。" % len(V3.SHOE_CATEGORIES["凉鞋"]),
    "皮鞋": "乐福鞋/芭蕾平底鞋（%d 款）。" % len(V3.SHOE_CATEGORIES["皮鞋"]),
    "赤脚": "不穿鞋、光脚（%d 款）。" % len(V3.SHOE_CATEGORIES["赤脚"]),
}
for _cat in V3.shoe_category_names():
    TIP[SHOE_WEIGHT_CTL[_cat]] = (
        "**鞋履详细种类权重**：控制「%s」这类鞋款在随机出题里被抽中的概率。\n"
        "该种类下共有 %d 款：%s。\n"
        "权重 0 = 该种类永不出现；1.00（默认）= 标准比例；上限 %.2f = 概率大幅提高。\n"
        "概率口径：该种类里每一款鞋的权重都等于这个值，「不使用（不写鞋履）」恒为 1.00。\n"
        "入口：插件面板「⚙️ 设置」→「⑤b 外观细节权重」。"
        % (_cat, len(V3.SHOE_CATEGORIES[_cat]),
           "、".join(V3.SHOE_CATEGORIES[_cat][:5]) + ("…" if len(V3.SHOE_CATEGORIES[_cat]) > 5 else ""),
           SHOE_W_MAX))
TIP[WATER_WEIGHT_CTL] = (
    "**水面波光权重**：控制「水面反光」（水面波光倒映在皮肤上，光影流转）这条"
    "环境光效果在随机出题里被抽中的概率。\n"
    "权重 0 = 永不出现；1.00（默认）= 标准比例；上限 %.2f = 概率大幅提高。\n"
    "入口：插件面板「⚙️ 设置」→「⑤b 外观细节权重」。"
    % SHOE_W_MAX)


def with_tooltips(types):
    """把 TIP 里的中文说明注入 INPUT_TYPES 的每个控件（与 Krea2-Portrait 同构）。

    只补 "tooltip" 键，不动控件类型 / 选项 / 默认值 —— 纯展示层。
    """
    out = {}
    for section, controls in (types or {}).items():
        out[section] = {}
        for name, spec in (controls or {}).items():
            if isinstance(spec, tuple) and len(spec) == 2 and isinstance(spec[1], dict):
                opt = dict(spec[1])
                if name in TIP and "tooltip" not in opt:
                    opt["tooltip"] = TIP[name]
                out[section][name] = (spec[0], opt)
            else:
                out[section][name] = spec
    return out


class K2V3Generator:
    """K2 V3 融合版 · 随机出题（角色联动 + 等级 / 权重）。"""

    @classmethod
    def INPUT_TYPES(cls):
        # 【UI 对齐】仿 Krea2-Portrait 的分组：
        #   required = 每次出题都要碰的主设定；optional = 进阶 / 不常用的。
        # ⚠️ 控件名沿用既有命名（内容等级 / 随机种子 / 角色 / 权重覆盖 …），
        #    一个都不改 —— 改名会让老工作流里已保存的控件值按名字匹配不上而丢失。
        return with_tooltips({
            "required": {
                "内容等级": (list(LEVELS), {"default": "sfw"}),
                "角色": ([AUTO] + list(CHARACTER_NAMES), {"default": AUTO}),
                "角色联动": ("BOOLEAN", {"default": True}),
                "每次运行重随机": ("BOOLEAN", {"default": True}),
                "随机种子": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF}),
            },
            "optional": {
                "角色预设": ([NO_PRESET] + PS.preset_names(), {"default": NO_PRESET}),
                "权重覆盖": ("STRING", {"default": "", "multiline": True}),
                "锁定字段": ("STRING", {"default": "", "multiline": True}),
                # 【新增】纹身：开关默认关闭（关闭 = 行为与加入前一致）+ 权重 1.0（= 不改）
                "纹身开关": ("BOOLEAN", {"default": False}),
                "纹身权重": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0,
                                       "step": 0.05}),
                # 【v3.11 新增】纹身面积 / 人物数量 / 百合模式
                # 默认值都取「不干预」那一档（人物数量除外 —— 用户要求默认单人）。
                "纹身面积": (TATTOO_AREAS, {"default": AREA_AUTO}),
                "人物数量": (PERSON_COUNTS, {"default": PERSON_DEFAULT}),
                "百合模式": ("BOOLEAN", {"default": False}),
                # 【v3.12 新增 / v3.18 退休】内容边界 / NSFW开关 / NSFW强度
                #   ⚠️ 保留控件位**不删**（只从界面撤掉、逻辑不再读取）。
                #      ComfyUI 按位置存 widgets_values：这三个在中间，
                #      删掉会让后面所有控件的值整体前移一位 ——
                #      老工作流打开后「角色」会变成「随机种子」这种级别的事故。
                "内容边界": (["受限（跟随等级）", "放开（越界）"],
                              {"default": "受限（跟随等级）"}),
                "NSFW开关": ("BOOLEAN", {"default": False}),
                "NSFW强度": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0,
                                         "step": 0.05}),
                # 【v3.13 新增】可控性 + 随机性增强 —— 默认值一律取「不干预」
                "内容强度": ([k for k, _ in EX.INTENSITY_PRESETS],
                              {"default": EX.INTENSITY_DEFAULT}),
                "联动强度": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0,
                                        "step": 0.05}),
                "去重记忆": ("INT", {"default": 0, "min": 0, "max": 20}),
                "多样性温度": ("FLOAT", {"default": 1.0, "min": 0.5, "max": 2.0,
                                          "step": 0.05}),
                "关闭可选项": ("STRING", {"default": "", "multiline": True}),
                "批次数": ("INT", {"default": 1, "min": 1, "max": 8}),
                # ------------------------------------------------------------------
                # 【v3.17 新增】双人第二位角色
                # 【v3.18 新增】NSFW 内容项权重（设置页最底部那一排滑块）
                #
                # ⚠️ 一律**追加在 optional 末尾**：ComfyUI 按位置存 widgets_values，
                #    插在中间会让所有既有工作流的控件值整体错位（值全乱）。
                #    默认值都是"不干预 / 标准比例"：自动 / 1.0。
                #
                # ⚠️ v3.17 那个单一的「NSFW场景权重」已按用户要求取消，
                #    由下面这一组**按内容项分开**的权重取代（同一个位置的控件
                #    换成了 5 个 —— 只有 v3.17 期间存过的工作流会丢这一个值）。
                # ------------------------------------------------------------------
                "第二角色": ([AUTO] + list(CHARACTER_NAMES), {"default": AUTO}),
                **{ACT_WEIGHT_CTL[a]: ("FLOAT", {"default": EX.ACT_W_DEFAULT,
                                                 "min": EX.ACT_W_MIN,
                                                 "max": EX.ACT_W_MAX,
                                                 "step": EX.ACT_W_STEP})
                   for a in EX.nsfw_act_names()},
                # 【v3.19】内容权重是否跟随等级（默认关 = 纯手动，逐字不变）
                ACT_AUTO_CTL: ("BOOLEAN", {"default": False}),
                # ------------------------------------------------------------------
                # 【v3.20 新增】角色外观锁定（默认**开**）
                #
                # 为什么要有它：用户实测反馈"角色设定后每次重随，发色/发型/瞳色
                # 都会变"。根因有两个（详见 k2_v3_char_link.py 文件头）：
                #   ① 外观推导整个挂在「角色联动」下面，联动一关外观就全随机；
                #   ② 推导只覆盖 发色/发长/扎法/刘海，**卷度与发态没锁**，
                #      而这两项在 ④ 段里是被硬拼进正文的，肉眼可见地每次不同。
                # 本开关独立于「角色联动」与「联动强度」，只负责把"这个人长什么样"
                # 钉死 —— 联动关了（不想要被锁服装/场景）也能保持外观一致。
                #
                # ⚠️ 默认 True，但**老工作流的控件值缺失/为空串时必须也归一成 True**：
                #    ComfyUI 对已存在的控件但值缺失会传空串，而 `bool("")` 是 False
                #    —— 不做归一就会出现"新工作流一致、老工作流不一致"的诡异差异。
                #    归一逻辑见 generate() 里的 _app_raw 那几行。
                # ------------------------------------------------------------------
                "角色外观锁定": ("BOOLEAN", {"default": True}),
                # ------------------------------------------------------------------
                # 【v3.22 新增】鞋履详细种类权重 + 水面波光权重。
                #
                # ⚠️ 一律**追加在 optional 末尾**（ComfyUI 按位置存 widgets_values，
                #    插中间会让旧工作流控件值整体错位）。默认全 1.00 = 不干预，
                #    出题结果与没有这些控件时逐字一致（金标准 800/800 前提）。
                #    默认值归一逻辑见 generate()（空串/缺失 → 1.00）。
                # ------------------------------------------------------------------
                **{SHOE_WEIGHT_CTL[c]: ("FLOAT", {"default": SHOE_W_DEFAULT,
                                                   "min": SHOE_W_MIN,
                                                   "max": SHOE_W_MAX,
                                                   "step": SHOE_W_STEP})
                   for c in V3.shoe_category_names()},
                WATER_WEIGHT_CTL: ("FLOAT", {"default": SHOE_W_DEFAULT,
                                              "min": SHOE_W_MIN,
                                              "max": SHOE_W_MAX,
                                              "step": SHOE_W_STEP}),
            },
            # ------------------------------------------------------------------
            # 【v3.21】hidden 输入 —— 用来把正文写进 PNG 元数据。
            #
            # ⚠️ hidden 输入**不是控件**：它不出现在节点界面上、不进 widgets_values、
            #    不占控件位，所以对既有工作流零影响（老工作流的控件值不会错位）。
            #    这也是为什么修这个 bug 不需要动用户的工作流、不需要迁移脚本。
            #
            # EXTRA_PNGINFO：同一次执行里所有节点共享的同一个 dict，
            #               里面装着 workflow（UI JSON）；SaveImage 会把它写进 PNG。
            # PROMPT      ：这一次提交的 API 图，用于写"惰性键"（见 k2_v3_pngmeta）。
            # ------------------------------------------------------------------
            "hidden": {
                "extra_pnginfo": "EXTRA_PNGINFO",
                "prompt": "PROMPT",
            },
        })

    # 【UI 对齐】输出端口仿 Krea2-Portrait（提示词 / 报告 / 结构化槽位）。
    # ⚠️ 顺序上新增端口一律**追加在末尾**：既有工作流里
    #    提示词=0、自检报告=1、槽位JSON=2 的连线索引保持不变，不会被顶掉。
    # 【v3.19】去掉「负面提示词」端口 —— 插件不再生成也不输出任何负面词。
    # ⚠️ 端口是**按索引**连线的：删掉中间那个会把后面的「候选列表」前移一位，
    #    既有工作流里那条连线会失配。所以这次改完**必须同步修正工作流文件**
    #    （已随本版一起处理，见 tools/fix_workflows_v319.py）。
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT", "STRING", "STRING")
    RETURN_NAMES = ("提示词", "自检报告", "槽位JSON", "随机种子", "元数据JSON",
                    "候选列表")
    OUTPUT_TOOLTIPS = ("随机生成的提示词正文，接 CLIP Text Encode。",
                       "自检报告：等级 / 角色联动 / 权重 / 致命项 / 风格项 / 字数。",
                       "本次各字段的结构化取值（JSON），便于调试与对账。",
                       "本次实际使用的随机种子；关掉「每次运行重随机」后改它可精确复现。",
                       "出题元数据 JSON（种子 / 完整提示词 / 等级 / 角色 / 人物数量 / "
                       "纹身面积 / 百合 / 插件名），用于写入图片元数据。",
                       "【v3.13】批次数 > 1 时的候选提示词列表（JSON 数组）。")
    FUNCTION = "generate"
    CATEGORY = SECTION_NAME
    # 【v3.20】署名与来源放最前面，并顺手修掉三处已经过时的说明：
    #   「内容等级六档」→ 现为「内容强度」五档（内容等级已退休）；
    #   原本写的「v3.17 新增 NSFW 场景下的内容权重」→ 该控件 v3.19 已被 13 项内容权重取代；
    #   「子级交互」区 → v3.18 起改名「⑥ 人物与角色」。
    DESCRIPTION = (
        "【K2 V3 融合版 · V2】随机出题节点。\n"
        "作者：灵冻不冻　｜　插件底层框架源于脚本：K2_V3_融合版.html"
        "（HTML 版人像提示词生成器）已授权开源 —— 作者：飞蓬。\n"
        "· 10 段装配，每次运行自动全随机，冲突自动纠错、超限自动精简（单人 600 / 双人 1000 字）；\n"
        "· 角色联动：按角色特征（外貌/服装/气质/场景）组织内容；\n"
        "· v3.20 角色外观锁定（默认开）：同一角色的发色/发型/卷度/瞳色 每次重随都一致；\n"
        "· 内容强度五档（全年龄/暗示/露骨/强露骨/极端）+ 轴级权重；\n"
        "· 角色图鉴浏览 + 角色预设（保存 / 加载 / 切换 / 删除）；\n"
        "· v3.11：纹身面积 / 人物数量（单人保证 · 双人独立场景段）· 百合分级；\n"
        "· v3.17：双人可分别指定两位角色并点名；SFW 出正常互动、NSFW 出百合；\n"
        "· v3.19：取消负面提示词；⑦ 新增 13 项 NSFW 内容权重（等级先筛范围、权重再定概率）；\n"
        "· v3.20：④ 字段微调支持候选点选 / 手输 / 分组折叠 / 搜索 / 批量锁定；\n"
        "· 单人／双人 与角色设定统一收进「⚙️ 设置」面板的「⑥ 人物与角色」区；\n"
        "· 第 4/5 个输出端口直接给出种子与元数据；接原生 SaveImage 出图时，\n"
        "  插件会自动把完整正文写进 PNG 元数据（见 k2_v3_pngmeta.py）。"
    )

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        """跳过 ComfyUI 的自动类型校验 —— 这是**必须**的兼容手段。

        原因（用户实测踩过）：本节点的控件是"末尾追加式"演进的，老工作流的
        `widgets_values` 比现在的控件少，缺的那几位会以空串 `""` 传进来。
        而 ComfyUI 对 FLOAT 控件的自动校验会直接拒绝空串：

            Failed to convert an input value to a FLOAT value:
            NSFW场景权重, , could not convert string to float: ''
            → invalid prompt: Prompt outputs failed validation

        表现是**整个工作流跑不起来**（不是出题出错），而且看起来像插件坏了。
        v3.18 起统一在这里放行，取值全部由 generate() 自己做容错归一化
        （空串/None/非法值一律落到"不干预 / 标准比例"），
        所以放行不会把错误藏起来，只是把"拒绝执行"变成"按默认值执行"。

        ⚠️ 定义成 `**kwargs` 形式是刻意的：ComfyUI 看到 varkw 就会把所有
           输入的校验都交给这个函数，不再逐个做类型检查。
        """
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        if bool(kwargs.get("每次运行重随机", True)):
            return random.randrange(1 << 31)
        return 0

    def generate(self, **kw):
        # 【兼容】老版本用过「模式」（V1 的 K2V3Generator），新版统一为「内容等级」。
        # 这里做一次键名兜底，保证老参数传进来不会静默失效。
        # ⚠️ 只做键名映射，不做任何取值/逻辑变更。
        if "内容等级" not in kw and "模式" in kw:
            kw["内容等级"] = ("explicit" if str(kw.get("模式")) == "NSFW" else "sfw")

        level = kw.get("内容等级", "sfw")
        mode = LEVEL_TO_MODE.get(level, "SFW")
        seed_val = int(kw.get("随机种子", 0) or 0)
        reroll = bool(kw.get("每次运行重随机", True))
        link_on = bool(kw.get("角色联动", True))
        char_name = kw.get("角色", AUTO)
        preset_name = kw.get("角色预设", NO_PRESET)
        weight_spec = (kw.get("权重覆盖") or "").strip()
        lock_json = (kw.get("锁定字段") or "").strip()

        # ------------------------------------------------------------------
        # 【v3.18 · 内容强度 = 唯一的 NSFW 等级控制项】
        #
        # 背景：v3.13~v3.17 期间，能有"调节露骨程度"效果的一共有四个控件
        #   —— 内容等级（六档）/ 内容边界（越界）/ NSFW开关+NSFW强度 / 内容强度（总旋钮）。
        #   用户明确要求收敛成一个，理由是**它们会互相打架**。
        #
        # v3.18 起：
        #   · 等级只认「内容强度」五档（全年龄/暗示/露骨/强露骨/极端）；
        #   · 「内容边界」的越界段并入**极端**档（极端 = 含越界），不再是独立开关；
        #   · 「NSFW开关 / NSFW强度」整体移除（其效果已被等级覆盖）。
        #
        # ⚠️ 兼容桥：老工作流里「内容强度」是「手动（分别设置）」—— 不是有效档位，
        #   level_from_intensity() 返回空串，于是**回落到旧的「内容等级」控件值**。
        #   这不是把旧控件重新变成可操作入口（界面上早就看不到它了），
        #   只是让升级前存的工作流不至于悄悄从 NSFW 掉成 SFW。
        # ------------------------------------------------------------------
        #   ⚠️ 「内容强度」取不到值（程序化调用 / 极老的工作流没这个控件）时
        #      也走兼容兜底，而不是直接按默认「全年龄」—— 否则显式传进来的
        #      「内容等级」会被静默忽略，老调用方（含自测与「模式」旧键）会莫名掉档。
        intensity_name = kw.get("内容强度") or ""
        _lv_direct = EX.level_from_intensity(intensity_name) if intensity_name else ""
        if _lv_direct:
            level = _lv_direct
            intensity_legacy = False
        else:
            level = kw.get("内容等级", "sfw") or "sfw"
            intensity_legacy = True
        mode = LEVEL_TO_MODE.get(level, "SFW")
        # 报告里要显示"档位从哪来" —— 兜底时 intensity_name 可能是空串
        intensity_shown = intensity_name or LEVEL_ZH.get(level, level)

        if reroll:
            seed = random.randrange(1 << 31)
        else:
            seed = seed_val

        # 预设加载（角色 + 锁定字段）
        preset = PS.get_preset(preset_name) if preset_name and preset_name != NO_PRESET else None
        locked = dict(preset.get("overrides") or {}) if preset else {}
        if preset and char_name == AUTO and preset.get("character"):
            char_name = preset["character"]

        # 锁定字段 JSON（优先级高于预设）
        if lock_json:
            try:
                extra = json.loads(lock_json)
                if isinstance(extra, dict):
                    locked.update(extra)
            except Exception:
                pass

        # 角色 -> card；角色联动 -> 特征覆盖
        character = None
        char_link = None
        if char_name and char_name != AUTO:
            rec = get_character(char_name)
            if rec:
                character = card(rec)
                if link_on:
                    char_link = CL.build_character_overrides(character)

        # ------------------------------------------------------------------
        # 【v3.13 · 联动强度】按比例采用角色推导出的字段（1.0 = 全采用 = 不干预）。
        #   原来这是布尔：要么全用，要么完全随机，中间态缺失。
        #   取前 k 个是**确定性**的（不消耗随机数），所以 1.0 时与之前逐字一致。
        # ------------------------------------------------------------------
        try:
            link_ratio = float(kw.get("联动强度")
                               if kw.get("联动强度") is not None else 1.0)
        except (TypeError, ValueError):
            link_ratio = 1.0
        if char_link and 0.0 <= link_ratio < 1.0:
            _flds = list((char_link.get("fields") or {}).items())
            _k = max(0, int(round(len(_flds) * link_ratio)))
            char_link = dict(char_link, fields=dict(_flds[:_k]))

        # ------------------------------------------------------------------
        # 【v3.20 · 角色外观锁定】把"这个人长什么样"钉死（发色/发长/卷度/发态/
        #   扎法/刘海 + 瞳色），保证同一角色不管重随多少次、不管联动怎么设，
        #   写进正文的外观描述都是同一套。实现见 CL.build_appearance_lock()。
        #
        #   ⚠️ 三个顺序约束，动任何一条都会让它失效：
        #     ① 必须在「联动强度」裁剪**之后**合并 —— 否则比例一调低就被裁掉；
        #     ② 独立于 link_on —— 联动关掉时外观照样锁（这正是本功能的意义）；
        #     ③ 空串 / 缺失一律归一成 True（老工作流兼容，见 INPUT_TYPES 注释）。
        # ------------------------------------------------------------------
        _app_raw = kw.get("角色外观锁定", None)
        app_lock_on = True if _app_raw in (None, "") else bool(_app_raw)
        app_notes = []
        # 报告要分两条写（「角色联动」与「角色外观锁定」是两件事），
        # 所以合并前先把联动自己的 notes 留一份 —— 合并后两者会串在一起。
        _link_notes = list((char_link or {}).get("notes") or [])
        if character is not None and app_lock_on:
            app = CL.build_appearance_lock(character)
            if app.get("fields") or app.get("person_extra"):
                char_link = CL.merge_appearance(char_link, app)
                app_notes = list(app.get("notes") or [])

        # 【新增·纹身】开关关闭时传 None（规则侧完全不介入）；开启时传权重值
        tattoo_w = float(kw.get("纹身权重", 1.0)) if bool(kw.get("纹身开关", False)) else None

        # ------------------------------------------------------------------
        # 【v3.11 · 纹身面积 / 人物数量 / 百合】
        #   统一走「取不到 or 取到'不干预'值 → 传 None」的口径。
        #   ⚠️ 人物数量是唯一例外：默认"单人"，且单人会启用单人保证 ——
        #      这是用户明确要求的定向变更（"开启单人提示内必定只有一人"）。
        # ------------------------------------------------------------------
        area_raw = (kw.get("纹身面积") or AREA_AUTO)
        tattoo_area = None if area_raw == AREA_AUTO else area_raw
        person_count = (kw.get("人物数量") or PERSON_DEFAULT)

        # ------------------------------------------------------------------
        # 【v3.17 · 双人模式：两位角色 + 按等级自动百合】
        #
        # 三条新规则（用户明确要求，全部只在 person_count=双人 时生效）：
        #   ① 第二位角色由「第二角色」控件单独指定，两位都按
        #      「全名（英文原名 + 中文作品）+ 短名 + 外观锚点」组织；
        #   ② 输出按等级分流：SFW 档 → 通用多人互动段；NSFW 档 → 百合段；
        #   ③ 段内**点名**（互动句主语换成真实角色名），消除「她…她」的指代歧义。
        #
        # ⚠️ 单人模式（默认）下 dual_chars 恒为 None、yuri 恒取控件值，
        #    所有行为与 v3.16 完全一致 —— 这是"完全保留现有单人模式"的落点。
        # ------------------------------------------------------------------
        yuri_manual = bool(kw.get("百合模式", False))
        yuri_auto = (person_count != "单人") and (mode == "NSFW")
        yuri = yuri_manual or yuri_auto

        char2_name = (kw.get("第二角色") or AUTO)
        dual_chars = None
        if person_count != "单人":
            rec2 = get_character(char2_name) if char2_name != AUTO else None
            card2 = card(rec2) if rec2 else None
            if character or card2:
                dual_chars = [
                    {"word": (character or {}).get("word", ""),
                     "short": (character or {}).get("en", ""),
                     "brief": CL.character_brief(character) if character else ""},
                    {"word": (card2 or {}).get("word", ""),
                     "short": (card2 or {}).get("en", ""),
                     "brief": CL.character_brief(card2) if card2 else ""},
                ]

        # ------------------------------------------------------------------
        # 【v3.18 · NSFW 内容项权重】
        #   设置页最底部的五个调节项：展示生殖器 / 自慰 / 口交 / 性交 / 其它。
        #   规则层按「等级先筛可用范围、权重再定概率」抽卡（见 EX.pick_act）。
        #
        #   ⚠️ 默认 1.0 = 标准比例。整张权重表**总是**传下去，
        #      因为"抽卡"这件事本身是等级驱动的（权重只决定"在可用项里谁更容易中"）；
        #      真正让行为与旧版一致的是 level —— 只有明确了档位才走这条链路。
        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # 【v3.19 · 「内容权重 · 跟随等级」自动档】
        #   开关**默认关闭** → 用滑块里用户自己的值（与没有这个开关时逐字一致）。
        #   开启 → 按当前档位取一套权重基准（EX.auto_act_weights），
        #   于是"内容强度"这一个控件就同时决定了「哪些内容够得着」和
        #   「够得着的里面谁更容易中」，出题与所选分级自然匹配。
        #   ⚠️ 只覆盖 act_weights，不动滑块里存的值 —— 关掉开关立刻回到手动值。
        # ------------------------------------------------------------------
        act_auto = bool(kw.get(ACT_AUTO_CTL, False))
        if act_auto:
            act_weights = EX.auto_act_weights(level, mode)
        else:
            act_weights = {}
            for _act in EX.nsfw_act_names():
                _ctl = ACT_WEIGHT_CTL.get(_act)
                _raw = kw.get(_ctl) if _ctl else None
                try:
                    act_weights[_act] = float(EX.ACT_W_DEFAULT if _raw is None else _raw)
                except (TypeError, ValueError):
                    act_weights[_act] = EX.ACT_W_DEFAULT

        # ------------------------------------------------------------------
        # 【v3.22 · 鞋履详细种类权重 + 水面波光权重】
        #   收集两个控件组 → 展开成 {选项名: 权重} 的 item_weights，交给规则层按名叠加。
        #   ⚠️ 只保留**非默认（≠1.00）**的项：全 1.00 时 item_weights 为 None，
        #      规则层完全不介入 → 出题与没有这组控件时逐字一致（金标准前提）。
        #   ⚠️ 空串/缺失一律归一成 1.00（ComfyUI 对老工作流缺位传空串）。
        # ------------------------------------------------------------------
        shoe_cat_weights = {}
        for _cat in V3.shoe_category_names():
            _raw = kw.get(SHOE_WEIGHT_CTL[_cat])
            try:
                shoe_cat_weights[_cat] = float(SHOE_W_DEFAULT if _raw in (None, "") else _raw)
            except (TypeError, ValueError):
                shoe_cat_weights[_cat] = SHOE_W_DEFAULT
        _raw_water = kw.get(WATER_WEIGHT_CTL)
        try:
            water_weight = float(SHOE_W_DEFAULT if _raw_water in (None, "") else _raw_water)
        except (TypeError, ValueError):
            water_weight = SHOE_W_DEFAULT

        item_weights = {}
        for _cat, _w in shoe_cat_weights.items():
            if abs(_w - SHOE_W_DEFAULT) > 1e-9:
                for _nm in V3.SHOE_CATEGORIES.get(_cat, []):
                    item_weights[_nm] = _w
        if abs(water_weight - SHOE_W_DEFAULT) > 1e-9:
            item_weights[V3.WATER_REFLECT_OPTION] = water_weight
        item_weights = item_weights or None

        # ------------------------------------------------------------------
        # 【v3.13 · 纹身冲突可视化（修复）】
        #   事实：apply_tattoo_weight() 先跑、apply_v311() 后跑，所以
        #   「面积=不使用」会**静默压过**「权重=2.0」（实测 200 次出纹身 0 次）。
        #   这里不改执行顺序（那会动到生成逻辑），而是把冲突**说出来**：
        #   报告头明确写「已被面积强制关闭，权重未生效」，前端也把权重控件置灰。
        # ------------------------------------------------------------------
        tattoo_conflict = bool(tattoo_w is not None and tattoo_area == "不使用")

        # 【v3.18】越界段不再是独立开关 —— 它是「极端」档的定义之一。
        #   「内容边界 / NSFW开关 / NSFW强度」三个控件整体退休（值不再被读取）。
        boundary_on = (level == EX.INTENSITY_BOUNDARY_LEVEL)
        nsfw_on = False
        nsfw_w = None

        # ------------------------------------------------------------------
        # 【v3.13 · 随机性增强】去重记忆 / 温度 / 逐项关闭
        #   全部默认取「不干预」值：深度 0 → avoid=None；温度 1.0；关闭列表为空。
        # ------------------------------------------------------------------
        try:
            dedupe_depth = int(kw.get("去重记忆", 0) or 0)
        except (TypeError, ValueError):
            dedupe_depth = 0
        avoid = None
        if dedupe_depth > 0:
            try:
                avoid = EX.build_avoid(PS.load_recent(), dedupe_depth)
            except Exception:
                avoid = None
        try:
            temperature = float(kw.get("多样性温度")
                                if kw.get("多样性温度") is not None else 1.0)
        except (TypeError, ValueError):
            temperature = 1.0
        off_items = EX.parse_off_items(kw.get("关闭可选项"))
        try:
            batch_n = int(kw.get("批次数", 1) or 1)
        except (TypeError, ValueError):
            batch_n = 1
        batch_n = max(1, min(8, batch_n))

        result = V3.full_random(seed=seed, mode=mode, locked=locked,
                                character=character, weight_spec=weight_spec,
                                char_link=char_link, tattoo_weight=tattoo_w,
                                person_count=person_count, tattoo_area=tattoo_area,
                                level=level, yuri=yuri,
                                boundary=boundary_on, nsfw_w=nsfw_w,
                                avoid=avoid, temperature=temperature,
                                off_items=off_items,
                                chars=dual_chars, act_weights=act_weights,
                                item_weights=item_weights) or {}

        # 【v3.11 · 分级提示】百合双人场景会带回分级提示，拼到报告里给人看
        grade = (result.get("multi_grade") or "")
        # 【v3.12 · 越界分级】越界段也会带回分级提示
        beyond_grade = (result.get("beyond_grade") or "")
        # 【v3.18】本次抽中的 NSFW 内容项（空 = 没走内容项链路）
        act_picked = (result.get("slots") or {}).get("nsfwAct") or ""

        # 【v3.11 · 元数据】种子 + 完整提示词是硬要求，其余附加。
        # 输出成第 5 个端口，也放进 ui 供面板回显；
        # 接原生 SaveImage 时，正文由 k2_v3_pngmeta 注入 extra_pnginfo 落进 PNG。
        meta = {
            "seed": seed,
            "prompt": result["text"],
            "level": level,
            "level_zh": LEVEL_ZH.get(level, level),
            "mode": result["mode"],
            "character": "" if char_name == AUTO else char_name,
            "person_count": person_count,
            "tattoo_area": area_raw,
            "yuri": yuri,
            # 【v3.17】双人相关的可复现信息（角色 B / 是否自动百合）
            "character2": "" if char2_name == AUTO else char2_name,
            "yuri_auto": bool(yuri_auto),
            # 【v3.18】内容项权重 + 本次抽中的内容项
            "act_weights": dict(act_weights),
            "act_auto": act_auto,
            "act": act_picked,
            # 【v3.22】鞋履种类权重 + 水面波光权重（「一键复用」要能完整还原）
            "shoe_weights": dict(shoe_cat_weights),
            "water_weight": water_weight,
            "intensity": intensity_shown,
            "grade": grade,
            "boundary": boundary_on,
            "beyond_grade": beyond_grade,
            "plugin": "Comfyui-Krea2-PortraitV2",
        }

        # 【新增·历史】写一条历史记录（尽力而为：任何异常都不影响出图，也不改输出）
        try:
            from . import krea2_store as HS
            HS.add_history({
                "text": result["text"][:4000],
                "seed": seed, "level": level, "mode": result["mode"],
                "character": char_name if char_name != AUTO else "",
                "locked": locked, "weight": weight_spec,
                "tattoo_on": tattoo_w is not None,
                # 【v3.11】历史里也带上新参数，「一键复用」才能完整还原
                "person_count": person_count,
                "tattoo_area": area_raw,
                "yuri": yuri,
                # 【v3.17】角色 B / 自动百合 / NSFW 场景权重 —— 「一键复用」才能完整还原
                "character2": "" if char2_name == AUTO else char2_name,
                "yuri_auto": bool(yuri_auto),
                "act_weights": dict(act_weights),
                "act_auto": act_auto,
                # 【v3.20】外观锁定开关 —— 复用时要一并还原，否则"复用回来又不一致"
                "app_lock": bool(app_lock_on),
                # 【v3.22】鞋履种类权重 + 水面波光权重
                "shoe_weights": dict(shoe_cat_weights),
                "water_weight": water_weight,
                "grade": grade,
                "boundary": boundary_on,
                "beyond_grade": beyond_grade,
                # 【v3.13】
                # 【v3.18】intensity 就是唯一等级控件，复用只认它
                "intensity": intensity_shown,
                "linked_ratio": link_ratio,
                "dedupe": dedupe_depth,
                "temperature": temperature,
                "off_items": list(off_items),
                "n": result["n"], "node": "K2V3GeneratorV2",
            })
        except Exception:
            pass

        # ------------------------------------------------------------------
        # 【v3.13 · 去重记忆】把本次各字段取值并入记忆，供下次降权。
        #   只在开启了记忆时才写（默认 0 = 不写，避免无谓的文件 IO）。
        #   尽力而为：任何异常都不影响出图，也不改输出。
        # ------------------------------------------------------------------
        if dedupe_depth > 0:
            try:
                _picks = {k: v for k, v in (result.get("slots") or {}).items()
                          if isinstance(v, str) and v.strip()}
                PS.push_recent(_picks)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # 【v3.13 · 组合空间统计】把"池子到底够不够大"变成可看的数字。
        #   纯报告信息，不进正文，不影响任何生成结果。
        # ------------------------------------------------------------------
        _n_opts, _combos = EX.combo_space(V3.field_sizes(result["mode"]))

        # 报告头部补上等级 / 角色联动 / 权重信息
        head = ["模式：%s（内容强度 %s → 档位 %s=%s%s）"
                % (result["mode"], intensity_shown,
                   level, LEVEL_ZH.get(level, level),
                   "，等级带：%s" % LEVEL_BAND_ZH.get(level, "—")
                   if LEVEL_BAND_ZH.get(level) else "")]
        if intensity_legacy:
            head.append("⚠ 内容强度不是有效档位（读到的值：「%s」）→ "
                        "已回落到旧「内容等级」控件的值。"
                        "把它改成 全年龄/暗示/露骨/强露骨/极端 之一即可用新档位。"
                        % (intensity_name or "（空）"))
        # 【v3.18】抽卡结果：这一次落在哪个内容项、当前档位下还有哪些可选
        if result["mode"] == "NSFW":
            _avail = EX.act_hint(level, "NSFW", act_weights)
            if act_picked:
                head.append("NSFW 内容项：%s（本次抽中）　可选：%s"
                            % (act_picked, _avail or "—"))
            else:
                head.append("⚠ NSFW 内容项：当前档位下没有可用项（权重全为 0？）"
                            "　可选：%s" % (_avail or "—"))
        # 【v3.20】外观锁定单独报一条：它是"同一角色重随不变"的直接证据，
        #   需要能一眼看出「这次到底锁了哪几项、每项是标签推的还是派生补的」。
        if character:
            if app_notes:
                head.append("角色外观锁定：%s" % "；".join(app_notes))
            elif not app_lock_on:
                head.append("⚠ 角色外观锁定：已关闭 → 发色 / 发型 / 瞳色 每次重随都会变"
                            "（打开它可保证同一角色外观一致）")
        if _link_notes:
            head.append("角色联动：%s" % "；".join(_link_notes))
        elif character and link_on:
            head.append("角色联动：已绑定 %s，但未推导到可用特征（按随机处理）"
                        % character.get("word", ""))
        elif character:
            head.append("角色联动：已关闭（服装 / 场景 / 气质交给随机；外观仍由"
                        "「角色外观锁定」负责）")
        if character and link_ratio < 1.0:
            head.append("联动强度：%.0f%%（只采用前 %d 个角色推导字段，其余交回随机）"
                        % (link_ratio * 100, len(char_link.get("fields") or {})))
        if weight_spec:
            head.append("权重覆盖：%s" % weight_spec)
        # 【v3.11】人物数量 / 纹身面积 / 百合 回显到报告头（不进正文，不影响出图）
        # 【v3.17】双人时补上「走哪条池 + 是否自动百合 + 第二位角色」
        head.append("人物数量：%s%s" % (person_count,
                                        "（百合）" if (yuri and person_count != "单人") else ""))
        if person_count != "单人":
            head.append("双人模式：%s%s" % (
                "百合段（两名女性）" if yuri else "通用多人互动段",
                "· 自动（NSFW 档固定走百合）" if (yuri_auto and not yuri_manual) else ""))
            # 【v3.18】双人的字数上限与单人不同，明确写出来（免得以为精简坏了）
            head.append("字数上限：1000（双人；单人仍为 600）")
            if char2_name != AUTO:
                head.append("第二位角色：%s" % char2_name)
            elif character or dual_chars:
                head.append("第二位角色：未指定（段内用「另一名女性」称呼）")
        # 【v3.18】非默认权重回显：只在用户真改过某一项时才写，保持报告干净
        # 【v3.19】开启「跟随等级」时，权重是自动算的 —— 直接说明来源，
        #   并把自动基准里非零的项列出来，方便核对"这一档到底会出什么"。
        if act_auto:
            _nz = [(a, act_weights.get(a, 0.0)) for a in EX.nsfw_act_names()
                   if act_weights.get(a, 0.0) > 0]
            head.append("内容权重：**跟随等级**（%s）%s" % (
                intensity_shown,
                ("　自动基准：" + "、".join("%s×%.2f" % (a, w) for a, w in _nz))
                if _nz else "　（该档不产生性行为内容）"))
        else:
            _w_changed = {a: w for a, w in act_weights.items()
                          if abs(float(w) - float(EX.ACT_W_DEFAULT)) > 1e-9}
            if _w_changed:
                head.append("NSFW 内容项权重：%s" % "、".join(
                    "%s×%.2f" % (a, _w_changed[a]) for a in EX.nsfw_act_names()
                    if a in _w_changed))
        # 【v3.22】鞋履种类权重 + 水面波光：只在真改过时才写，保持报告干净
        _shoe_changed = {c: w for c, w in shoe_cat_weights.items()
                         if abs(w - SHOE_W_DEFAULT) > 1e-9}
        if _shoe_changed or abs(water_weight - SHOE_W_DEFAULT) > 1e-9:
            _bits = ["%s×%.2f" % (c, _shoe_changed[c]) for c in V3.shoe_category_names()
                     if c in _shoe_changed]
            if abs(water_weight - SHOE_W_DEFAULT) > 1e-9:
                _bits.append("水面波光×%.2f" % water_weight)
            head.append("外观细节权重：%s" % "、".join(_bits))
        if tattoo_area:
            head.append("纹身面积：%s" % tattoo_area)
        # 【v3.13 修复】把纹身三路径的冲突**说出来**，不再静默
        if tattoo_conflict:
            head.append("⚠ 纹身：已被「面积=不使用」强制关闭，"
                        "「纹身权重 %.2f」本次未生效" % (tattoo_w or 0.0))
        if grade:
            head.append("分级提示：%s" % grade)
        # 【v3.18】越界段已并入「极端」档 —— 不再是独立开关
        if boundary_on and result["mode"] == "NSFW":
            head.append("越界段：极端档自动启用%s"
                        % ("（" + beyond_grade + "）" if beyond_grade else ""))
        if dedupe_depth > 0:
            head.append("去重记忆：最近 %d 次（已对用过的取值降权）" % dedupe_depth)
        if abs(temperature - 1.0) > 1e-9:
            head.append("多样性温度：%.2f" % temperature)
        if off_items:
            head.append("已关闭可选项：%s" % "、".join(off_items))
        head.append("组合空间：单条可选 %d 项，理论组合约 %s 种" % (_n_opts, _combos))
        # ------------------------------------------------------------------
        # 【v3.21】把这一次的正文写进 PNG 元数据。
        #
        # 背景（用户实测报障）：出图后看图工具里的「提示词」只剩 `bl00m,`
        #   —— 解剖确认不是截断，而是**完整正文一个字都没进元数据**：这条链路
        #   上所有承载文本的 widget 都处于"被转成输入连线"的状态，`widgets_values`
        #   全是空串，文本只在运行时内存里流动（详见 k2_v3_pngmeta.py 的模块说明）。
        #
        # 做法：写进 hidden 输入 EXTRA_PNGINFO 拿到的那个 dict。它在**同一次执行里
        #   被所有节点共享**，而原生 SaveImage 是在自己执行时才 json.dumps 它 ——
        #   所以这里写完，当次那张图就带上了，**不用改用户的工作流**。
        #
        # ⚠️ 三个必须守住的点：
        #   ① 取值范围只在"插件自己产出的正文"，绝不碰第三方节点的已声明输入
        #      （改了会真的改变执行结果，见模块头「安全边界」）；
        #   ② 永不抛异常 —— 元数据写不进去是小事，把出图搞挂是大事；
        #   ③ 不传 extra_pnginfo（API 提交、或老版本前端）时静默跳过，行为与从前一致。
        # ------------------------------------------------------------------
        PM.merge_metadata_stats(kw.get("extra_pnginfo"), kw.get("prompt"),
                                result["text"], report_head=head)

        report = "\n".join(head) + "\n" + result["report"]

        text = result["text"]
        # ------------------------------------------------------------------
        # 【v3.13 · 批量候选】批次数 > 1 时多跑几条，放在第 7 个端口。
        #   第 1 条仍就是主输出（种子不变 → 与批次数=1 时逐字一致）。
        # ------------------------------------------------------------------
        candidates = [text]
        for _i in range(1, batch_n):
            try:
                _r = V3.full_random(seed=seed + _i * 7919, mode=mode, locked=locked,
                                    character=character, weight_spec=weight_spec,
                                    char_link=char_link, tattoo_weight=tattoo_w,
                                    person_count=person_count, tattoo_area=tattoo_area,
                                    level=level, yuri=yuri,
                                    boundary=boundary_on, nsfw_w=nsfw_w,
                                    avoid=avoid, temperature=temperature,
                                    off_items=off_items,
                                    chars=dual_chars, act_weights=act_weights,
                                item_weights=item_weights) or {}
                if _r.get("text"):
                    candidates.append(_r["text"])
            except Exception:
                break

        meta["intensity"] = intensity_shown
        meta["dedupe"] = dedupe_depth
        meta["temperature"] = temperature
        meta["link_ratio"] = link_ratio
        meta["off_items"] = list(off_items)
        meta["batch"] = batch_n
        meta["app_lock"] = bool(app_lock_on)
        slots_json = json.dumps(result.get("slots") or {}, ensure_ascii=False, indent=2)
        meta_json = json.dumps(meta, ensure_ascii=False, indent=2)
        cand_json = json.dumps(candidates, ensure_ascii=False, indent=2)
        # 【v3.20】多带一路 ui 数据：最近一次的槽位表。
        #   ⚠️ 这是 **ui 消息**不是输出端口 —— 只给前端面板用，不改端口个数、
        #      不影响任何既有连线。前端「④ 字段微调 → 🔒 锁定当前整套值」需要它
        #      （没有槽位表就只能锁 UI 上看得见的那几个，锁不全）。
        return {"ui": {"k2v3_text": [text], "k2v3_report": [report],
                       "k2v3_meta": [meta],
                       "k2v3_slots": [result.get("slots") or {}],
                       "k2v3_candidates": [candidates]},
                "result": (text, report, slots_json, seed, meta_json,
                           cand_json)}


NODE_CLASS_MAPPINGS = {"K2V3GeneratorV2": K2V3Generator}
NODE_DISPLAY_NAME_MAPPINGS = {
    "K2V3GeneratorV2": "★ Krea2-Portrait-V3 · 随机出题",
}


# ============================================================================
# HTTP 路由：角色预设 CRUD + 轴级权重选项（供面板渲染滑块）
# ============================================================================

def register_routes():
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return

    routes = PromptServer.instance.routes

    @routes.get("/krea2-v3/presets")
    async def _presets_list(request):
        return web.json_response({"ok": True, "presets": PS.list_presets()})

    @routes.post("/krea2-v3/presets")
    async def _presets_save(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        ok, res = PS.save_preset(
            name=data.get("name", ""),
            character=data.get("character", ""),
            overrides=data.get("overrides"),
            note=data.get("note", ""),
            # 【v3.13 整合】方案里现在可以一起带走「权重覆盖 + 其余控件快照」
            weights=data.get("weights", ""),
            params=data.get("params"),
        )
        if ok:
            return web.json_response({"ok": True, "preset": res,
                                      "names": PS.preset_names()})
        return web.json_response({"ok": False, "error": res.get("error", "保存失败")})

    # ---- v3.13：字段选项表（逐字段重掷 / 可关闭项清单 用）----
    @routes.get("/krea2-v3/fields")
    async def _fields(request):
        """返回每个字段的可选值 + 可关闭项清单。

        前端「逐字段重掷」直接用这里的选项写进「锁定字段」——
        这样重掷**完全不碰生成逻辑**，只是改一个既有控件的值。
        """
        off = V3.skippable_by_section()
        return web.json_response({
            "ok": True,
            # 【v3.20】字段清单改成**按规则算**（V3.tunable_fields()），不再手写。
            #   起因：v3.13 手写的 32 个名字只覆盖了 69 个字段的不到一半，
            #   而用户这一轮明确要求"扩展可调整的参数"。规则与排除理由见该函数。
            "fields": {fid: [{"v": o.get("v"), "t": o.get("t") or o.get("v")}
                             for o in V3._opt(fid) if o.get("v") and not o.get("nsfw")]
                       for fid in V3.tunable_fields()},
            "skippable": off,
            "skippable_zh": EX.SECTION_ZH,
            # 【v3.20】④ 字段微调用：全字段按段落分组 + 段落中文名。
            #   前端据此把 60+ 个字段折成「① 镜头 … ⑪ 附加」十一个折叠区，
            #   不必把一长条下拉一口氣铺开。
            "field_sections": V3.field_sections(),
            "section_zh": EX.SECTION_ZH,
        })

    # ---- v3.13：去重记忆的清空 ----
    @routes.post("/krea2-v3/memory/clear")
    async def _memory_clear(request):
        return web.json_response({"ok": True, "cleared": PS.clear_recent()})

    @routes.get("/krea2-v3/memory")
    async def _memory_get(request):
        d = PS.load_recent()
        return web.json_response({
            "ok": True,
            "fields": len(d),
            "total": sum(len(v) for v in d.values()),
        })

    @routes.post("/krea2-v3/presets/remove")
    async def _presets_remove(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        n = PS.remove_preset(data.get("name", ""))
        return web.json_response({"ok": n > 0, "removed": n, "names": PS.preset_names()})

    @routes.get("/krea2-v3/axes")
    async def _axes(request):
        """轴级权重可调项（对齐原面板 /options 里 _axis_options 的结构）。"""
        return web.json_response({
            "ok": True,
            "min": V3.WEIGHT_MIN, "max": V3.WEIGHT_MAX, "step": V3.WEIGHT_STEP,
            "axes": V3.axis_options(),
        })

    # ---- 历史 / 收藏（复用 krea2_store，与出题逻辑完全解耦）----
    @routes.get("/krea2-v3/history")
    async def _history(request):
        try:
            from . import krea2_store as HS
        except Exception:
            return web.json_response({"ok": False, "items": [], "total": 0})
        limit = int(request.query.get("limit", 30))
        items, total = HS.list_history(limit=limit, offset=0)
        return web.json_response({"ok": True, "items": items, "total": total})

    @routes.get("/krea2-v3/favorites")
    async def _fav_list(request):
        try:
            from . import krea2_store as HS
        except Exception:
            return web.json_response({"ok": False, "items": [], "total": 0})
        items, total = HS.list_favorites(limit=100, offset=0)
        return web.json_response({"ok": True, "items": items, "total": total})

    @routes.post("/krea2-v3/favorites")
    async def _fav_add(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            from . import krea2_store as HS
        except Exception:
            return web.json_response({"ok": False})
        # 从历史里收藏时带走原 id，便于"一键复用"取回完整参数
        fid = HS.add_favorite(data or {})
        return web.json_response({"ok": True, "id": fid})

    @routes.post("/krea2-v3/favorites/remove")
    async def _fav_remove(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            from . import krea2_store as HS
        except Exception:
            return web.json_response({"ok": False, "removed": 0})
        n = HS.remove_favorite((data or {}).get("id", ""))
        return web.json_response({"ok": n > 0, "removed": n})

    @routes.get("/krea2-v3/entry")
    async def _entry(request):
        """「一键复用」用：按 id 取回完整条目（含 seed / locked / character）。"""
        try:
            from . import krea2_store as HS
        except Exception:
            return web.json_response({"ok": False}, status=404)
        e = HS.get_entry(request.query.get("id") or "")
        if not e:
            return web.json_response({"ok": False}, status=404)
        return web.json_response({"ok": True, "entry": e})

    @routes.get("/krea2-v3/levels")
    async def _levels(request):
        return web.json_response({
            "ok": True, "levels": list(LEVELS),
            "zh": LEVEL_ZH, "band": LEVEL_BAND_ZH,
            "to_mode": LEVEL_TO_MODE,
        })

    # ---- v3.11：纹身面积 / 人物数量 选项 + 百合分级说明（供面板渲染） ----
    #      【v3.17】补上 NSFW 场景权重的取值范围 / 判据说明，
    #      以及「双人 + NSFW 自动百合」这条规则的文案（前端要如实告诉用户）。
    @routes.get("/krea2-v3/extra")
    async def _extra(request):
        j = _load_extra_json()
        yuri = (j.get("yuriScene") or {})
        grade_note = yuri.get("_grade_note") or {}
        beyond = (j.get("beyondScene") or {})
        return web.json_response({
            "ok": True,
            "tattoo_areas": list(TATTOO_AREAS),
            "tattoo_area_auto": AREA_AUTO,
            "person_counts": list(PERSON_COUNTS),
            "person_default": PERSON_DEFAULT,
            "grade_note": grade_note,
            "boundary_choices": ["受限（跟随等级）", "放开（越界）"],
            "boundary_default": "受限（跟随等级）",
            "beyond_note": beyond.get("_boundary_note") or "",
            "beyond_grade_note": beyond.get("_grade_note") or {},
            "nsfw_w_min": 0.0, "nsfw_w_max": 2.0, "nsfw_w_step": 0.05,
            "dual_auto_yuri": "双人 + NSFW 档 → 自动走百合（SFW 档走通用多人互动）",
            # 【v3.18】NSFW 内容项：档位阶梯 / 滑块范围 / 每档可用项
            "act_min": EX.ACT_W_MIN, "act_max": EX.ACT_W_MAX, "act_step": EX.ACT_W_STEP,
            "act_default": EX.ACT_W_DEFAULT,
            "act_names": EX.nsfw_act_names(),
            "act_labels": EX.nsfw_act_labels(),
            "act_mins": {a["act"]: a.get("min") for a in EX.nsfw_act_items()},
            "act_notes": EX.nsfw_act_note("_grade_note"),
            "act_level_zh": EX.nsfw_act_note("_level_zh"),
            "act_level_rank": list(EX.LEVEL_RANK),
            "act_auto_table": {lv: EX.auto_act_weights(lv, "NSFW")
                               for lv in EX.LEVEL_RANK},
            "act_auto_note": EX.auto_act_note(),
            "act_auto_ctl": ACT_AUTO_CTL,
            "intensity_levels": [{"name": k, "level": (v or {}).get("level")}
                                 for k, v in EX.INTENSITY_PRESETS],
            "intensity_default": EX.INTENSITY_DEFAULT,
            "beyond_level": EX.INTENSITY_BOUNDARY_LEVEL,
            # 【v3.22】鞋履详细种类权重 + 水面波光权重（前端「⑤b 外观细节权重」区）
            "shoe_min": SHOE_W_MIN, "shoe_max": SHOE_W_MAX, "shoe_step": SHOE_W_STEP,
            "shoe_default": SHOE_W_DEFAULT,
            "shoe_categories": [
                {"name": c, "ctl": SHOE_WEIGHT_CTL[c], "items": V3.SHOE_CATEGORIES[c]}
                for c in V3.shoe_category_names()
            ],
            "water_option": {
                "name": "水面波光倒映在皮肤上", "value": V3.WATER_REFLECT_OPTION,
                "ctl": WATER_WEIGHT_CTL,
            },
        })


def _load_extra_json():
    """读 presets/k2_v3_extra_pools.json（只读，失败返回空 dict）。"""
    try:
        import os
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "presets", "k2_v3_extra_pools.json")
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
