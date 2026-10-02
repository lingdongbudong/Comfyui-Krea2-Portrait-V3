# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-PortraitV2 :: 角色联动提示词（角色特征 → K2V3 各字段）

═══════════════════════════════════════════════════════════════════
★ 角色特征数据来源（全部来自插件自带数据，未联网检索、未杜撰）
═══════════════════════════════════════════════════════════════════
1. `presets/character_pool.json`（1086 位女性角色，schema 3）
   每条角色自带：
     f : [Danbooru 英文特征标签]  —— 1086/1086 条都有（发型/发色/瞳色/服装/配饰/身材…）
     h : 主发色英文（1022/1086 条有）
     e : 主瞳色英文（1033/1086 条有）
     c / wz : 作品（英文 / 中文）
     z  : 角色中文名
   由 tools/build_character_pool.py 从 Anima-Wardrobe 的 Danbooru 热度榜筛选生成。
2. `character_tags_zh.py`
   Danbooru 英文标签 -> 中文短语 的两级映射（EXACT 精确表 + RULES 正则兜底），
   并提供 `translate_features()`、`HAIR_COLOR_MAP / HAIR_STYLE_MAP / EYE_COLOR_MAP`。
3. `krea2_characters.py` 在加载时派生的结构化字段：
     build（体型：娇小/高挑/标准/结实/丰腴，标签优先、作品默认兜底）
     fig  （身材键，FIGURE_TAG_MAP 命中）

本模块做的唯一一件事：把上面这些**已有数据**翻译成 K2V3 融合版词库的取值，
让提示词按角色的外貌 / 服装 / 气质 / 场景来组织；角色没有的特征一律不猜，
交给随机引擎（宁可随机，不写错）。

对外：
    build_character_overrides(card, translate_limit=6) -> {
        "fields": {K2V3字段: 值},   # 确定性推导出的，随机时锁死
        "person_extra": [中文短语],  # 注入 ③人物维度（瞳色 + 未被结构化覆盖的标志特征）
        "notes": [推导说明]         # 写进自检报告，便于核对"用了角色哪些特征"
    }
    character_brief(card) -> "金色过胸长发"   # 【v3.17】双人段区分两位角色的一句话锚点
    build_appearance_lock(card) -> 同结构    # 【v3.20】只含"外貌锚点"，保证重随不变

═══════════════════════════════════════════════════════════════════
★ 【v3.20】为什么要单独再做一个 build_appearance_lock()
═══════════════════════════════════════════════════════════════════
用户要求："确保角色设定后，每次自动重随生成的提示词中，角色特征保持一致，
包括发色、发型、瞳孔颜色。"

而 v3.19 之前的实测结果是**不一致的**，原因有三个：
  ① 上面 build_character_overrides() 只推导了 hairColor / hairLen / hairTie /
     hairBangs，**没推导 hairCurl（卷度，13 档）与 hairState（发态，11 档）**
     —— 这两项每次重随都在变（同一个甘雨，三次分别是「直发柔顺 / 直发柔顺 /
     微卷蓬松」）。④ 段里它们是硬拼进正文的，所以肉眼可见地不一致。
  ② 整条外观推导挂在「角色联动」开关下面。联动一关（用户可能只是不想要被锁
     服装 / 场景），**发色、发长、瞳色就全变成随机的**。
  ③ 「联动强度 < 1.0」按比例裁掉推导字段，也可能把发型裁掉。

所以本函数独立于「角色联动」与「联动强度」，只负责一件事：
**把"这个人长什么样"钉死**，保证同一个角色不管重随多少次、不管联动怎么设，
写进正文的外观描述都是同一套。

三个取值口径（按"是否杜撰"排序，越靠前越可信）：
  ┌ 有标签          → 一律用标签（最可信）
  ├ 无标签·显眼特征 → 取**中性值**（扎法=披散 / 刘海=无刘海）。
  │                   这两个中性值在 ④ 段的写法是"整句不写"（见 build_prompt：
  │                   `if htie and htie != "披散"`），所以效果是**不杜撰**，
  │                   同时又是**确定的** —— 一致性与"不写错"两头都占住。
  └ 无标签·质感项   → 用**角色名派生种子**从词库固定取一个（卷度 / 发态 /
                       发色 / 发长 / 瞳色）。这几项是"风格纹理"，抽到哪个
                       都成立，但必须每次一样 —— 派生种子保证跨进程、跨重随、
                       跨重启都稳定（用 zlib.crc32，不用内置 hash()：
                       Python 的 hash() 每个进程的随机盐不同，跨进程不一致）。
"""

import random
import re
import zlib

try:
    from . import character_tags_zh as TZ
except ImportError:  # 独立脚本导入兜底
    import character_tags_zh as TZ

# ============================================================================
# 1. 发色：Danbooru 发色标签 / h 字段 -> K2V3 hairColor 池
#    （K2V3 取值：黑发/黑茶色/深棕/浅棕/栗色/奶茶色/奶茶灰棕/亚麻色/金色/
#      白金色/灰蓝色/渐变染/渐变粉/挑染/挂耳染/银灰色/雾霾蓝/樱花粉/
#      红棕色/墨绿色/紫灰色）
# ============================================================================
HAIR_COLOR_TO_K2V3 = {
    "black hair": "黑发", "black": "黑发",
    "brown hair": "深棕", "brown": "深棕",
    "light brown hair": "浅棕", "auburn hair": "红棕色",
    "blonde hair": "金色", "blonde": "金色",
    "white hair": "白金色", "white": "白金色", "platinum blonde": "白金色",
    "silver hair": "银灰色", "silver": "银灰色",
    "grey hair": "银灰色", "gray hair": "银灰色", "grey": "银灰色",
    "red hair": "红棕色", "red": "红棕色", "orange hair": "挂耳染", "orange": "挂耳染",
    "pink hair": "樱花粉", "pink": "樱花粉",
    "purple hair": "紫灰色", "purple": "紫灰色",
    "blue hair": "雾霾蓝", "blue": "雾霾蓝", "aqua hair": "灰蓝色", "aqua": "灰蓝色",
    "green hair": "墨绿色", "green": "墨绿色",
    "gradient hair": "渐变染", "two-tone hair": "挑染",
    "multicolored hair": "渐变染", "streaked hair": "挂耳染",
}

# ============================================================================
# 2. 发长 / 扎法 / 刘海
# ============================================================================
HAIR_LEN_TO_K2V3 = {
    "very long hair": "及腰长发",
    "absurdly long hair": "极长及臀长发",
    "long hair": "过胸长发",
    "medium hair": "及肩中长发",
    "shoulder-length hair": "齐肩发",
    "short hair": "短发",
    "very short hair": "齐耳短发",
    "bob cut": "齐耳短发",
}
HAIR_TIE_TO_K2V3 = {
    "twintails": "双马尾", "twin braids": "双马尾",
    "ponytail": "高马尾", "side ponytail": "侧马尾", "low ponytail": "低马尾",
    "braid": "麻花辫", "braided ponytail": "麻花辫", "french braid": "法式辫",
    "fishtail braid": "鱼骨辫",
    "bun": "丸子头", "hair bun": "丸子头", "double bun": "双丸子头",
    "low bun": "低髻", "updo": "盘发", "drill hair": "公主头",
    "half updo": "半扎", "messy bun": "松散发髻",
    "lamp bun": "灯笼辫", "high bun": "高丸子头",
}
HAIR_BANGS_TO_K2V3 = {
    "blunt bangs": "齐刘海",
    "parted bangs": "中分",
    "swept bangs": "斜刘海",
    "side bangs": "侧分",
    "hair intakes": "八字刘海",
    "crossed bangs": "碎刘海",
    "long bangs": "空气刘海",
    "bralette of hair": "眉上短刘海",
}

# ============================================================================
# 2b.【v3.20】卷度 / 发态的标签映射
#     只认**明确**的卷直标签。实测全库 2165 个角色里只有 2.5% 带这类标签，
#     所以这里能命中就命中，命不中一律走"角色名派生种子"（见 build_appearance_lock），
#     不猜具体卷法（把"波浪"猜成"羊毛卷"比不猜更糟）。
# ============================================================================
HAIR_CURL_TO_K2V3 = {
    "straight hair": "直发柔顺",
    "wavy hair": "波浪卷",
    "curly hair": "大卷",
    "very curly hair": "大卷",
    "drill hair": "公主切",
    "messy hair": "慵懒卷",
}
HAIR_STATE_TO_K2V3 = {
    "flipped hair": "发尾外翻",
    "flipped ends": "发尾外翻",
    "messy hair": "凌乱蓬松",
    "wet hair": "微湿贴肤",
}

# ============================================================================
# 2c.【v3.20】「无标签时取中性值」的清单
#     中性值 = 在 ④ 段里**整句不写**的那两个取值（见 krea2_v3_rules.build_prompt：
#       `if htie and htie != "披散"` / `if hb and hb != "无刘海"`）。
#     所以写进去的副作用是"少描述一句"，而不是"多描述一句" —— 与插件
#     「只写画面里有的、宁可随机不写错」的口径一致。
# ============================================================================
HAIR_TIE_NEUTRAL = "披散"
HAIR_BANGS_NEUTRAL = "无刘海"

# 派生种子要跳过的字段：这两个用中性值，不参与"随机取一个"
_NEUTRAL_FIELDS = {"hairTie", "hairBangs"}

# 写报告用的字段中文名（只覆盖本模块会推导的字段，与前端 FIELD_ZH 口径一致）
FIELD_ZH_NAME = {
    "hairColor": "发色", "hairLen": "发长", "hairCurl": "卷度",
    "hairTie": "扎法", "hairBangs": "刘海", "hairState": "发态",
    "body": "体型", "clothCat": "服装大类", "clothSubCat": "服装次级",
    "scene": "场景", "temperament": "气质",
}

# ============================================================================
# 3. 服装：Danbooru 标签 -> K2V3 服装两级（大类, 次级分类）
#    ⚠️ 只锁定「大类 / 次级分类」，具体款式仍由 K2V3 随机挑 ——
#       既不凭空杜撰角色没穿的衣服，也不会把款式写死。
# ============================================================================
CLOTH_TAG_TO_CAT = {
    # 连衣裙
    "dress": ("连衣裙", None), "sundress": ("连衣裙", "短款"),
    "long dress": ("连衣裙", "长款"), "ball gown": ("连衣裙", "礼服"),
    "evening gown": ("连衣裙", "礼服"), "wedding dress": ("连衣裙", "礼服"),
    # 传统服饰
    "kimono": ("传统服饰", "和服"), "cheongsam": ("传统服饰", "旗袍"),
    "qipao": ("传统服饰", "旗袍"), "china dress": ("传统服饰", "旗袍"),
    "hanfu": ("传统服饰", "汉服"),
    # 制服
    "school uniform": ("职业制服", "校服"), "serafuku": ("职业制服", "校服"),
    "sailor uniform": ("职业制服", "校服"), "office lady": ("职业制服", "职业装"),
    "suit": ("职业制服", "职业装"), "business suit": ("职业制服", "职业装"),
    "maid": ("职业制服", "女仆家政"), "maid uniform": ("职业制服", "女仆家政"),
    "nurse": ("职业制服", "职业装"),
    # 外套
    "jacket": ("外套", "夹克"), "coat": ("外套", "大衣"),
    "trench coat": ("外套", "大衣"), "blazer": ("外套", "风衣披肩"),
    "cardigan": ("外套", "夹克"), "hoodie": ("外套", "夹克"),
    # 上衣
    "shirt": ("上衣", "衬衫"), "blouse": ("上衣", "衬衫"),
    "white shirt": ("上衣", "衬衫"),
    "sweater": ("上衣", "针织毛衣"), "knitwear": ("上衣", "针织毛衣"),
    "camisole": ("上衣", "内搭吊带"), "tank top": ("上衣", "内搭吊带"),
    "tube top": ("上衣", "内搭吊带"),
    # 下装
    "skirt": ("下装", "裙装"), "miniskirt": ("下装", "裙装"),
    "pleated skirt": ("下装", "裙装"), "pants": ("下装", "裤装"),
    "jeans": ("下装", "裤装"), "shorts": ("下装", "裤装"),
    # 内衣 / 睡衣
    "lingerie": ("内衣/睡衣", "内衣"), "underwear": ("内衣/睡衣", "内衣"),
    "bra": ("内衣/睡衣", "内衣"), "panties": ("内衣/睡衣", "内衣"),
    "pajamas": ("内衣/睡衣", "睡衣"), "nightgown": ("内衣/睡衣", "睡裙"),
    "bikini": ("内衣/睡衣", "内衣"), "swimsuit": ("内衣/睡衣", "内衣"),
}

# ============================================================================
# 4. 场景：Danbooru 标签 / 作品关键词 -> K2V3 scene 池
#    （只在标签明确指向场景时锁定，否则交给随机）
# ============================================================================
SCENE_TAG_TO_K2V3 = {
    "classroom": "教室", "school": "教室", "school uniform": "教室",
    "library": "图书馆", "bookstore": "书店",
    "office": "办公室", "office lady": "办公室",
    "hospital": "医院", "nurse": "医院",
    "beach": "海边", "ocean": "海边", "swimsuit": "泳池边", "pool": "泳池边",
    "bathhouse": "温泉", "onsen": "温泉", "hot spring": "温泉", "bath": "浴室",
    "shrine": "神社", "torii": "神社", "japanese room": "和室", "tatami": "和室",
    "rooftop": "天台", "night sky": "天台", "sky": "天台",
    "cafe": "咖啡馆", "coffee": "咖啡馆",
    "train": "电车", "train interior": "电车",
    "car": "车内", "convenience store": "便利店",
    "gym": "健身房", "art studio": "画室", "museum": "美术馆",
    "snow": "雪地", "sakura": "樱花树下", "cherry blossoms": "樱花树下",
    "bamboo": "竹林", "forest": "森林深处", "desert": "沙漠公路",
    "night club": "夜店", "bar": "夜店", "karaoke": "点歌厅",
    "elevator": "电梯间", "balcony": "露天阳台", "flower shop": "花房",
    "music room": "音乐教室",
}

# ============================================================================
# 5. 气质：Danbooru 标签 -> K2V3 temperament 池
#    只映射含义明确的少数标签，其余交给随机（宁可随机，不乱贴标签）
# ============================================================================
TEMP_TAG_TO_K2V3 = {
    "gentle smile": "温柔", "smile": "甜美", "blush": "清纯",
    "loli": "可爱", "child": "呆萌", "expressionless": "高冷",
    "smug": "飒爽", "confident": "飒爽", "smirk": "魅惑",
    "sleepy": "慵懒", "yawn": "慵懒", "glasses": "文艺",
    "milf": "成熟", "older woman": "成熟",
    "tomboy": "活泼", "energetic": "活泼",
    "sad": "空灵", "crying": "清纯",
    "serious": "坚毅", "angry": "坚毅",
    "mysterious": "神秘", "ethereal": "不食人间烟火",
    "elegant": "高贵", "princess": "名媛", "gothic": "叛逆",
}


def _norm_tags(card):
    """统一取标签列表，全部小写去空格。"""
    tags = card.get("tags") or card.get("f") or []
    return [str(t).strip().lower() for t in tags if t]


def _first_hit(tags, mapping):
    """按映射表的键在标签里找第一个命中（映射表顺序即优先级）。"""
    for key, val in mapping.items():
        if key in tags:
            return val
    return None


def character_brief(card):
    """【v3.17】一句话外观锚点（发色 + 发长 + 扎法）—— 双人段用来区分两位角色。

    为什么单独做一个"轻量版"而不是复用 build_character_overrides()：
      后者会把标签过一遍翻译表、推导服装/场景/气质等一堆字段，而双人段
      只需要一句"长什么样"的锚点。正文额度有限，锚点越短越好。

    只读角色已有的结构化标签，**不猜任何东西**；取不到就返回空串
    （调用方据此不写这句，宁可少写也不杜撰）。
    """
    if not card:
        return ""
    tags = _norm_tags(card)
    hc = _first_hit(tags, HAIR_COLOR_TO_K2V3)
    if not hc and card.get("hair"):
        hc = HAIR_COLOR_TO_K2V3.get(str(card["hair"]).strip().lower())
    hl = _first_hit(tags, HAIR_LEN_TO_K2V3)
    ht = _first_hit(tags, HAIR_TIE_TO_K2V3)
    s = "".join(x for x in (hc, hl) if x)
    if ht:
        s += ("，" if s else "") + "扎成" + ht
    return s


# ============================================================================
# 【v3.20】外观锁定 —— 保证同一角色每次重随写出的外观描述**逐字相同**
# ============================================================================
# 要锁的字段与取值口径（详见文件头注释）：
#   hairColor / hairLen : 标签 → 派生随机
#   hairCurl / hairState: 标签 → 派生随机（④ 段里硬拼，不给"不写"这个选项）
#   hairTie   / hairBangs: 标签 → 中性值（披散 / 无刘海 = ④ 段里整句不写）
#   eye（瞳色）        : 标签 → 派生随机，作为短语注入 ③ 人物维度
_APPEARANCE_FIELDS = ("hairColor", "hairLen", "hairCurl",
                      "hairTie", "hairBangs", "hairState")


def _stable_key(card):
    """取一个跨进程稳定的角色标识 —— 派生种子的种子。

    ⚠️ 为什么不用内置 hash()：Python 对 str 的 hash 每个进程带不同随机盐
       （PYTHONHASHSEED），同一角色在"这次出图"和"重启之后"会得到不同的种子，
       表现就是"重启一次外观就变了"。zlib.crc32 是确定性的，跨进程一致。
    """
    for k in ("en", "word", "zh", "value", "trigger"):
        v = str(card.get(k) or "").strip()
        if v:
            return v.lower()
    return ""


def _stable_rng(card):
    """由角色名派生的固定随机源（同一个角色 → 永远同一个序列）。"""
    return random.Random(zlib.crc32(_stable_key(card).encode("utf-8")))


def _pool_values(fid):
    """从主词库取某字段的候选值（稳定顺序）。

    ⚠️ 延迟导入 krea2_v3_rules：本模块在插件包初始化早期就被 import，
       顶层导入会形成循环依赖（rules 会反过来 import 本模块）。
    """
    try:
        from . import krea2_v3_rules as V3
    except ImportError:  # 独立脚本导入兜底
        import krea2_v3_rules as V3
    try:
        return [o["v"] for o in V3._opt(fid) if o.get("v")]
    except Exception:
        return []


def _stable_pick(card, fid):
    """从词库确定性地取一个值；取不到返回空串（调用方据此不写这一项）。"""
    vals = _pool_values(fid)
    if not vals:
        return ""
    return _stable_rng(card).choice(vals)


def build_appearance_lock(card):
    """【v3.20】把角色的外貌锚点推导成一套**固定**的字段值。

    返回结构与 build_character_overrides() 一致：
        {"fields": {...}, "person_extra": [...], "notes": [...]}
    只含外貌（发色 / 发长 / 卷度 / 发态 / 扎法 / 刘海 + 瞳色），
    **不含**服装 / 场景 / 气质 —— 那些属于「角色联动」，关掉联动就该随机。

    ⚠️ 与「联动强度」无关：调用方必须在按比例裁剪之后**再**合并本函数的结果，
       否则联动强度调到 0.5 时外观会被裁掉一半，反而更不一致。
    """
    if not card:
        return {"fields": {}, "person_extra": [], "notes": []}

    tags = _norm_tags(card)
    fields, extra, notes = {}, [], []

    def put(fid, val, how):
        if val:
            fields[fid] = val
            notes.append("%s→%s（%s）" % (FIELD_ZH_NAME.get(fid, fid), val, how))

    # ── 发色 ──────────────────────────────────────────────────────
    hc = _first_hit(tags, HAIR_COLOR_TO_K2V3)
    if not hc and card.get("hair"):
        hc = HAIR_COLOR_TO_K2V3.get(str(card["hair"]).strip().lower())
    if hc:
        put("hairColor", hc, "标签")
    else:
        put("hairColor", _stable_pick(card, "hairColor"), "角色名派生（无标签）")

    # ── 发长 ──────────────────────────────────────────────────────
    hl = _first_hit(tags, HAIR_LEN_TO_K2V3)
    if hl:
        put("hairLen", hl, "标签")
    else:
        put("hairLen", _stable_pick(card, "hairLen"), "角色名派生（无标签）")

    # ── 卷度 / 发态：④ 段里必然被写出来，所以必须给一个确定值 ──────────
    hc2 = _first_hit(tags, HAIR_CURL_TO_K2V3)
    put("hairCurl", hc2 or _stable_pick(card, "hairCurl"),
        "标签" if hc2 else "角色名派生（无标签）")
    hs = _first_hit(tags, HAIR_STATE_TO_K2V3)
    put("hairState", hs or _stable_pick(card, "hairState"),
        "标签" if hs else "角色名派生（无标签）")

    # ── 扎法 / 刘海：无标签时给中性值 —— ④ 段会因此**整句不写** ─────────
    ht = _first_hit(tags, HAIR_TIE_TO_K2V3)
    put("hairTie", ht or HAIR_TIE_NEUTRAL, "标签" if ht else "无标签→不写扎法")
    hb = _first_hit(tags, HAIR_BANGS_TO_K2V3)
    put("hairBangs", hb or HAIR_BANGS_NEUTRAL, "标签" if hb else "无标签→不写刘海")

    # ── 瞳色（K2V3 没有瞳色池，作为短语注入 ③ 人物维度）───────────────
    eye_zh = ""
    for key, val in (TZ.EYE_COLOR_MAP or {}).items():
        if key in tags:
            eye_zh = val
            break
    if not eye_zh and card.get("eye"):
        eye_zh = TZ.EYE_COLOR_MAP.get("%s eyes" % str(card["eye"]).strip().lower(), "")
    if eye_zh:
        extra.append(eye_zh)
        notes.append("瞳色→%s（标签）" % eye_zh)
    else:
        # 全库 14.2% 的角色没有瞳色标签。这里用派生值补上 —— 用户明确要求
        # 「瞳孔颜色」也要一致；补的值是确定性的，不会每次重随换一个颜色。
        vals = [v for _, v in sorted((TZ.EYE_COLOR_MAP or {}).items()) if v]
        if vals:
            eye_zh = _stable_rng(card).choice(vals)
            extra.append(eye_zh)
            notes.append("瞳色→%s（角色名派生，无标签）" % eye_zh)

    return {"fields": fields, "person_extra": extra, "notes": notes}


def merge_appearance(base, app):
    """把外观锁定并入（可能为 None 的）联动结果；镜像不重复、notes 追加。

    为什么要单独一个函数：调用点（k2_v3_node）需要"没有联动结果时也能合并"，
    而 dict 手工拼三处容易漏字段。这里集中一次，语义只有一句 ——
    **外观结果覆盖联动结果里同名项**（两者同源同值，重复时以外观版为准，
    因为外观版保证"缺标签也补齐"，覆盖面更全）。
    """
    if not app or not (app.get("fields") or app.get("person_extra")):
        return base
    if not base:
        return {"fields": dict(app.get("fields") or {}),
                "person_extra": list(app.get("person_extra") or []),
                "notes": list(app.get("notes") or [])}
    f = dict(base.get("fields") or {})
    f.update(app.get("fields") or {})
    ex = list(base.get("person_extra") or [])
    for p in (app.get("person_extra") or []):
        if p not in ex:
            ex.append(p)
    return {"fields": f, "person_extra": ex,
            "notes": list(base.get("notes") or []) + list(app.get("notes") or [])}


def build_character_overrides(card, translate_limit=6):
    """把角色特征翻译成 K2V3 字段覆盖 + 附加短语。

    card : krea2_characters.card(rec) 的结果（含 tags/hair/eye/build/fig/word/zh/en）
    """
    if not card:
        return {"fields": {}, "person_extra": [], "notes": []}

    tags = _norm_tags(card)
    fields, extra, notes = {}, [], []

    # ── 外貌 · 发色 ──────────────────────────────────────────────
    hc = _first_hit(tags, HAIR_COLOR_TO_K2V3)
    if not hc and card.get("hair"):
        hc = HAIR_COLOR_TO_K2V3.get(str(card["hair"]).strip().lower())
    if hc:
        fields["hairColor"] = hc
        notes.append("发色→%s（标签）" % hc)

    # ── 外貌 · 发长 / 扎法 / 刘海 ───────────────────────────────
    hl = _first_hit(tags, HAIR_LEN_TO_K2V3)
    if hl:
        fields["hairLen"] = hl
        notes.append("发长→%s（标签）" % hl)
    ht = _first_hit(tags, HAIR_TIE_TO_K2V3)
    if ht:
        fields["hairTie"] = ht
        notes.append("发型→%s（标签）" % ht)
    hb = _first_hit(tags, HAIR_BANGS_TO_K2V3)
    if hb:
        fields["hairBangs"] = hb
        notes.append("刘海→%s（标签）" % hb)

    # ── 外貌 · 体型 / 身材 ──────────────────────────────────────
    try:
        # 复用 rules 里的映射，避免两处漂移
        from . import krea2_v3_rules as V3
        body_map = V3.CHAR_BUILD_TO_BODY
    except Exception:
        body_map = {"娇小": "娇小", "高挑": "模特高挑", "丰腴": "丰满匀称",
                    "结实": "健康匀称", "标准": ""}
    build = card.get("build") or ""
    body = body_map.get(build, "")
    if body:
        fields["body"] = body
        notes.append("体型→%s（%s）" % (body, build))

    # ── 外貌 · 瞳色（K2V3 无瞳色池，作为附加短语注入 ③）──────────
    eye_zh = ""
    for key, val in (TZ.EYE_COLOR_MAP or {}).items():
        if key in tags:
            eye_zh = val
            break
    if not eye_zh and card.get("eye"):
        eye_zh = TZ.EYE_COLOR_MAP.get("%s eyes" % str(card["eye"]).strip().lower(), "")
    if eye_zh:
        extra.append(eye_zh)
        notes.append("瞳色→%s（标签）" % eye_zh)

    # ── 服装（只锁大类 / 次级分类，款式仍随机）────────────────────
    cat_sub = _first_hit(tags, CLOTH_TAG_TO_CAT)
    if cat_sub:
        cat, sub = cat_sub
        fields["clothCat"] = cat
        notes.append("服装大类→%s（标签）" % cat)
        if sub:
            fields["clothSubCat"] = sub
            notes.append("服装次级→%s" % sub)

    # ── 场景 ────────────────────────────────────────────────────
    sc = _first_hit(tags, SCENE_TAG_TO_K2V3)
    if sc:
        fields["scene"] = sc
        notes.append("场景→%s（标签）" % sc)

    # ── 气质 ────────────────────────────────────────────────────
    tp = _first_hit(tags, TEMP_TAG_TO_K2V3)
    if tp:
        fields["temperament"] = tp
        notes.append("气质→%s（标签）" % tp)

    # ── 其余标志特征 -> 中文短语（补进 ③）──
    # ⚠️ 两道过滤（与插件「纯中文 + 只写画面里有的」口径一致）：
    #   ① 译文里仍带英文字母的（如 "aqua色头发"）一律丢弃 —— 宁可少写，不写残句；
    #   ② 与已锁定的结构化字段重复的一律丢弃（瞳色已单独写、发长/扎法/刘海同理），
    #      否则会出现「灰蓝瞳、水蓝色的眼瞳、蓝色的眼瞳」这种同义反复。
    try:
        feats = TZ.translate_features(tags, limit=translate_limit) or []
    except Exception:
        feats = []
    mapped_vals = set(v for v in (hc, hl, ht, hb, body) if v)
    added = []
    for f in feats:
        if not f or re.search(r"[a-zA-Z]", f):
            continue
        if eye_zh and ("瞳" in f or "眼瞳" in f or "眼睛" in f):
            continue
        # 发色类短语（"棕色头发"/"银白色头发"）与已锁定的 hairColor 可能来自同一角色的
        # 多个发色标签 —— 两者同时写进正文会自相矛盾（"黑发…棕色头发"），一律丢弃。
        if hc and re.search(r"(头发|发色)", f):
            continue
        if any(f in mv or mv in f for mv in mapped_vals):
            continue
        if f in added:
            continue
        if len(extra) >= 4:
            break
        extra.append(f)
        added.append(f)
    if added:
        notes.append("标志特征→%s" % "、".join(added))

    return {"fields": fields, "person_extra": extra, "notes": notes}
