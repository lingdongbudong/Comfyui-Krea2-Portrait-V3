# -*- coding: utf-8 -*-
"""
角色特征标签 -> 中文自然语言 映射表

用途：角色池里的 f 字段是 Danbooru 英文标签（如 `goat horns` / `mole under eye`），
      插件正文是中文自然语言，需要把标签翻译成可读的中文短语。

策略（两级）：
  1. EXACT  — 精确映射表，人工撰写，覆盖高频标签
  2. RULE   — 正则规则兜底（如 `xxx hair` -> "xxx色的头发"）
  未命中的标签被丢弃（宁可少写，不写错）。

统一约定：映射结果都是**短语**（不带标点），由 compose() 决定如何串联。
"""

import re

# ============================================================================
# 精确映射（覆盖 100 角色池里的高频标签）
# ============================================================================
EXACT = {
    # ---- 性别/人数（由 compose 单独处理，这里只做兜底）----
    "1girl": "",
    "solo": "",

    # ---- 发型 ----
    "long hair": "长发",
    "very long hair": "及腰长发",
    "medium hair": "中长发",
    "short hair": "短发",
    "twintails": "双马尾",
    "ponytail": "马尾辫",
    "braid": "编发",
    "braided ponytail": "编成长辫的马尾",
    "hair between eyes": "额前垂着碎发",
    "hair over one eye": "一侧头发盖住半边眼睛",
    "sidelocks": "鬓角垂着两缕长发",
    "ahoge": "头顶翘着一缕呆毛",
    "hair intakes": "刘海向两侧分流",
    "parted bangs": "刘海中分",
    "crossed bangs": "刘海交叉垂落",
    "swept bangs": "刘海斜向一边",
    "blunt bangs": "齐刘海",
    "two side up": "两侧头发向上束起",
    "hair ribbon": "发间系着缎带",
    "hair ornament": "戴着头饰",
    "hairclip": "发间别着发夹",
    "hair flower": "鬓边簪着一朵花",
    "hairband": "戴着发带",

    # ---- 发色 ----
    "white hair": "银白色头发",
    "grey hair": "灰色头发",
    "gray hair": "灰色头发",
    "black hair": "黑色头发",
    "brown hair": "棕色头发",
    "light brown hair": "浅棕色头发",
    "blonde hair": "金色头发",
    "red hair": "红色头发",
    "pink hair": "粉色头发",
    "purple hair": "紫色头发",
    "blue hair": "蓝色头发",
    "green hair": "绿色头发",
    "orange hair": "橘色头发",
    "silver hair": "银色头发",
    "multicolored hair": "挑染的头发",
    "two-tone hair": "双色头发",
    "streaked hair": "挑染的发丝",

    # ---- 瞳色 ----
    "blue eyes": "蓝色的眼瞳",
    "red eyes": "红色的眼瞳",
    "purple eyes": "紫色的眼瞳",
    "green eyes": "绿色的眼瞳",
    "yellow eyes": "金色的眼瞳",
    "brown eyes": "棕色的眼瞳",
    "pink eyes": "粉色的眼瞳",
    "orange eyes": "橘色的眼瞳",
    "grey eyes": "灰色的眼瞳",
    "gray eyes": "灰色的眼瞳",
    "aqua eyes": "水蓝色的眼瞳",
    "heterochromia": "异色双瞳",

    # ---- 面部特征 ----
    "mole under eye": "眼角一颗泪痣",
    "mole on breast": "胸口一颗痣",
    "mole on face": "脸上一颗小痣",
    "pointy ears": "尖尖的耳朵",
    "fang": "露出一颗小虎牙",
    "sharp teeth": "露出锐利的牙齿",
    "eyelashes": "睫毛浓密",

    # ---- 耳/角/尾（幻想特征）----
    "horns": "头顶一对角",
    "goat horns": "头顶一对弯曲的羊角",
    "demon horns": "头顶一对恶魔角",
    "animal ears": "一对兽耳",
    "fake animal ears": "头上戴着一对兽耳",
    "rabbit ears": "一对兔耳",
    "cat ears": "一对猫耳",
    "fox ears": "一对狐耳",
    "horse ears": "一对马耳",
    "dog ears": "一对犬耳",
    "wolf ears": "一对狼耳",
    "animal ear fluff": "兽耳内侧的绒毛",
    "horse tail": "身后垂着一条马尾",
    "horse girl": "赛马娘",
    "cat tail": "身后一条猫尾",
    "fox tail": "身后一条蓬松的狐尾",
    "multiple tails": "身后多条尾巴",
    "shark tail": "身后一条鲨鱼尾",
    "shark girl": "鲨鱼女孩",
    "tail": "身后一条尾巴",
    "wings": "背后一对翅膀",
    "halo": "头顶悬着光环",

    # ---- 身材 / 体态 ----
    "large breasts": "身材丰满",
    "huge breasts": "身材极为丰满",
    "medium breasts": "身形匀停",
    "small breasts": "身形纤细",
    "flat chest": "身形清瘦",
    "cleavage": "胸前露出乳沟",
    "thighs": "大腿线条饱满",
    "thigh gap": "双腿并拢时留出缝隙",
    "wide hips": "胯部线条宽",
    "navel": "露出肚脐",
    "stomach": "露出小腹",
    "midriff": "露出腰腹",
    "collarbone": "锁骨线条清晰",
    "bare shoulders": "肩线袒露",
    "ass": "臀部曲线饱满",
    "abs": "腹部肌肉线条分明",
    "toned": "身材紧实",

    # ---- 服装（角色自带，作为锚点用）----
    "maid": "女仆装",
    "maid headdress": "女仆头饰",
    "chinese clothes": "中式服饰",
    "white dress": "白色连衣裙",
    "black dress": "黑色连衣裙",
    "school uniform": "校服",
    "sailor collar": "水手领",
    "serafuku": "水手服",
    "kimono": "和服",
    "yukata": "浴衣",
    "military uniform": "军装",
    "nurse": "护士装",
    "detached sleeves": "分离式袖套",
    "detached collar": "分离式衣领",
    "long sleeves": "长袖",
    "short sleeves": "短袖",
    "sleeveless": "无袖",
    "strapless": "抹胸式",
    "strapless leotard": "抹胸连体衣",
    "leotard": "连体衣",
    "playboy bunny": "兔女郎装",
    "black gloves": "黑色手套",
    "white gloves": "白色手套",
    "fingerless gloves": "露指手套",
    "black jacket": "黑色外套",
    "white shirt": "白衬衫",
    "thighhighs": "过膝袜",
    "white thighhighs": "白色过膝袜",
    "black thighhighs": "黑色过膝袜",
    "pantyhose": "丝袜",
    "boots": "靴子",
    "heeled boots": "高跟靴",
    "hat": "帽子",
    "witch hat": "女巫帽",
    "beret": "贝雷帽",
    "headphones": "头戴耳机",
    "glasses": "眼镜",
    "earrings": "耳环",
    "necklace": "项链",
    "choker": "颈圈",
    "ribbon": "缎带",
    "bowtie": "领结",
    "hair bow": "发间结着蝴蝶结",
    "nail polish": "指甲涂着甲油",
    "crown": "头戴王冠",
    "tiara": "头戴冠冕",
    "veil": "披着头纱",
    "cape": "披着斗篷",
    "scarf": "围着围巾",
    "armor": "穿着铠甲",
    "swimsuit": "泳装",
    "bikini": "比基尼",
    "apron": "系着围裙",
    "belt": "系着腰带",
    "skirt": "短裙",
    "pleated skirt": "百褶裙",
    "pants": "长裤",
    "shorts": "短裤",

    # ---- 气质 / 状态 ----
    "expressionless": "神情淡漠",
    "smile": "嘴角带笑",
    "open mouth": "微微张着嘴",
    "blush": "脸颊泛红",
    "sweat": "皮肤上挂着细汗",
    "tears": "眼角泛着泪光",
    "wet": "浑身湿透",
    "swimming": "",
    "fox shadow puppet": "",
}


# ============================================================================
# 正则规则兜底
# ============================================================================
# 兜底补充：颜色类 / 渐变类标签（避免落到规则兜底拼出洋泾浜）
EXACT.update({
    "gradient hair": "渐变色挑染的长发",
    "colored inner hair": "内层挑染的头发",
    "single braid": "编成一条辫子",
    "hair bun": "盘起的发髻",
    "double bun": "两侧各盘一个小丸子",
    "side ponytail": "侧扎的马尾",
    "low twintails": "低双马尾",
    "short twintails": "短双马尾",
    "hair spread out": "长发在身后铺开",
    "messy hair": "略显凌乱的头发",
    "wet hair": "被水打湿的头发",
    "floating hair": "发丝在空气里轻轻飘起",
    "hair rings": "发间缀着环状装饰",
    "horns": "头顶生着角",
    "animal ears": "头顶一对兽耳",
    "cat ears": "头顶一对猫耳",
    "fox ears": "头顶一对狐耳",
    "rabbit ears": "头顶一对兔耳",
    "wolf ears": "头顶一对狼耳",
    "elf ears": "尖长的精灵耳",
    "pointy ears": "尖尖的耳朵",
    "wings": "背后生着一对翅膀",
    "feathered wings": "背后展开的羽翼",
    "demon wings": "背后一对恶魔翼",
    "tail": "身后拖着一条尾巴",
    "multiple tails": "身后拖着好几条尾巴",
    "halo": "头顶悬着一轮光环",
    "fangs": "唇边露出小小的尖牙",
    "fang": "唇边露出一颗小尖牙",
    "mole under eye": "眼下一颗小痣",
    "mole under mouth": "嘴边一颗小痣",
    "freckles": "鼻梁上散着淡淡的雀斑",
    "eyepatch": "一只眼睛蒙着眼罩",
    "heterochromia": "异色双瞳",
    "sharp teeth": "露出一口细密的尖牙",
    "expressionless": "神情平静无波",
    "bandages": "身上缠着绷带",
    "scar": "身上有一道旧疤",
    "thick eyebrows": "眉毛浓密",
    "makeup": "化着精致的妆",
    "eyeshadow": "眼妆晕开",
    "red lips": "唇色是浓红",
    "lipstick": "唇上涂着口红",
})


# 允许拼进「xxx色头发」的颜色词白名单
# （否则 `gradient hair` 会被兜底规则硬拼成「gradient色头发」这种洋泾浜）
COLOR_WORDS = {
    "black", "white", "red", "blue", "green", "purple", "pink", "brown",
    "grey", "gray", "yellow", "orange", "silver", "gold", "blonde", "blond",
    "aqua", "turquoise", "violet", "lavender", "magenta", "cyan", "crimson",
    "scarlet", "maroon", "navy", "teal", "mint", "peach", "cream", "ivory",
    "beige", "platinum", "rose", "copper", "ash", "dark", "light",
}

RULES = [
    # xxx hair 已在上表覆盖大部分，这里兜底罕见色（只认颜色词）
    (re.compile(r"^(\w+) hair$"),
     lambda m: f"{m.group(1)}色头发" if m.group(1) in COLOR_WORDS else None),
    (re.compile(r"^(\w+) eyes$"),
     lambda m: f"{m.group(1)}色的眼瞳" if m.group(1) in COLOR_WORDS else None),
    (re.compile(r"^(\w+) (thighhighs|pantyhose|socks)$"),
     lambda m: f"{m.group(1)}色{m.group(2)}" if m.group(1) in COLOR_WORDS else None),
    (re.compile(r"^(\w+) gloves$"),
     lambda m: f"{m.group(1)}色手套" if m.group(1) in COLOR_WORDS else None),
    # 纯颜色词 —— 【v3.5 修正】原来拼成 "black色" 这种半截中文，
    # 与「提示词必须中文输出」的要求相冲，且会被中文守卫判定为失败而整词删掉。
    # 这里改为直接的汉语颜色词，翻译结果才真正可用。
    (re.compile(r"^(black|white|red|blue|green|purple|pink|brown|grey|gray|"
                r"yellow|orange|silver|gold|blonde|cyan|aqua|violet|magenta)"
                r"$"),
     lambda m: _COLOR_ZH.get(m.group(1), "")),
]

# 纯颜色词 -> 汉语颜色
_COLOR_ZH = {
    "black": "黑色", "white": "白色", "red": "红色", "blue": "蓝色",
    "green": "绿色", "purple": "紫色", "pink": "粉色", "brown": "棕色",
    "grey": "灰色", "gray": "灰色", "yellow": "黄色", "orange": "橙色",
    "silver": "银色", "gold": "金色", "blonde": "金黄色",
    "cyan": "青色", "aqua": "水蓝色", "violet": "紫罗兰色",
    "magenta": "品红色",
}


def to_zh(tag):
    """单个标签 -> 中文短语；未命中返回 None。"""
    t = (tag or "").strip().lower()
    if not t:
        return None
    if t in EXACT:
        v = EXACT[t]
        return v or None
    for rx, fn in RULES:
        m = rx.match(t)
        if m:
            v = fn(m)
            if v:
                return v
    return None


# 语义去重：同一概念若被多个标签命中（如 goat horns + horns），
# 只保留第一个（更具体的那个）。key 是概念名，value 是标签集合。
_SEMANTIC_GROUPS = {
    "horns": {"horns", "goat horns", "demon horns", "dragon horns",
              "small horns", "curled horns"},
    "ears": {"animal ears", "cat ears", "fox ears", "rabbit ears",
             "dog ears", "wolf ears", "pointy ears", "elf ears",
             "fluffy ears", "ear fluff"},
    "wings": {"wings", "feathered wings", "demon wings", "bat wings"},
    "tail": {"tail", "cat tail", "fox tail", "demon tail", "wolf tail",
             "multiple tails", "fluffy tail", "huge tail"},
    "halo": {"halo", "angel halo", "glowing halo"},
    "sidelocks": {"sidelocks", "hair between eyes", "forehead"},
    "ahoge": {"ahoge", "antenna hair"},
    "gloves": {"gloves", "black gloves", "white gloves", "fingerless gloves"},
    # 身材：只保留一个（更具体的先出现）
    "bust": {"medium breasts", "large breasts", "huge breasts",
             "small breasts", "flat chest", "cleavage"},
    "hairlen": {"long hair", "very long hair", "short hair", "medium hair",
                "absurdly long hair"},
}


def _sem_key(tag):
    """返回该标签所属的语义组名；不属于任何组则返回 None。"""
    for gname, members in _SEMANTIC_GROUPS.items():
        if tag in members:
            return gname
    return None


def translate_features(tags, limit=6):
    """
    批量翻译特征标签，返回中文短语列表。
    会自动跳过空串、按语义组去重（避免"羊角 + 角"这类重复）、
    限制条数（避免正文过长）。
    """
    out = []
    seen = set()          # 已出现的文本
    used_groups = set()   # 已占用的语义组
    for t in tags or []:
        z = to_zh(t)
        if not z:
            continue
        if z in seen:
            continue
        # 语义组去重：同一概念只保留先出现（更具体）的那个
        g = _sem_key((t or "").strip().lower())
        if g and g in used_groups:
            continue
        out.append(z)
        seen.add(z)
        if g:
            used_groups.add(g)
        if len(out) >= limit:
            break
    return out


# 用于从特征标签里提取结构化槽位（发色/发型/瞳色）——供"映射到现有模块"用
HAIR_COLOR_MAP = {
    "white hair": "铂金白", "silver hair": "银白", "grey hair": "银白", "gray hair": "银白",
    "black hair": "自然黑", "brown hair": "深棕", "light brown hair": "栗色",
    "blonde hair": "亚麻金", "red hair": "酒红", "pink hair": "粉棕",
    "purple hair": "蓝黑", "blue hair": "蓝黑", "green hair": "深棕",
    "orange hair": "栗色",
}

HAIR_STYLE_MAP = {
    "long hair": "长直发", "very long hair": "长直发", "medium hair": "微卷中长发",
    "short hair": "短碎发", "twintails": "双马尾", "ponytail": "高马尾",
    "braid": "编发", "braided ponytail": "编发",
}

EYE_COLOR_MAP = {
    "blue eyes": "灰蓝瞳", "aqua eyes": "湖蓝瞳", "red eyes": "绯红瞳",
    "purple eyes": "紫罗兰瞳", "green eyes": "翡翠绿瞳",
    "yellow eyes": "琥珀瞳", "brown eyes": "深棕瞳", "pink eyes": "紫罗兰瞳",
    "orange eyes": "琥珀瞳", "grey eyes": "深灰瞳", "gray eyes": "深灰瞳",
}
