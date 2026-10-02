# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-Portrait-V3

Krea2 工作流专用的摄影人物提示词自动生成插件（V3 主线）。
**纯本地、无大模型、模板填空式**：把 60+ 个随机池里的中文条目按固定 10 段模板
装配成一段完整提示词，配角色联动、内容分级、轴级权重、可复现种子。

================================================================================
署名与来源
================================================================================
· 插件作者：**灵冻不冻**
· 插件底层框架源于脚本：K2_V3_融合版.html（HTML 版人像提示词生成器）
  已授权开源 —— 作者：飞蓬。
  本插件的词库与生成逻辑均由该 HTML 脚本移植而来：
    - 词库  → presets/k2_v3_pools.json（由 tools/export_k2v3_pools.mjs 从 HTML 导出，零漂移）
    - 逻辑  → krea2_v3_rules.py（10 段装配 / 级联随机 / 冲突纠错 / 自检 / 精简 的 Python 移植）
  词库的**唯一真源**仍是那份 HTML：改词库要**同时**改 HTML 与 JSON，
  只改 JSON 会在下次导出时被冲回。

================================================================================
v3.13 结构收敛（本次大版本的核心工作）
================================================================================
V3 是 V2 的**减法重构**，不是又加一层。V2 目录里同时躺着 V1 的整套实现
（legacy 路由 / legacy 面板 / legacy 节点 / legacy 生成核心），它们**不参与 V3 出题**
却占了约 500 KB 代码 —— 全部移除。现在只剩一条主线：

    前端  js/k2v3_gen.js          （出题面板，8 个 DOM 分区）
          js/k2v3_prompt_edit.js  （预览编辑节点面板）
    节点  k2_v3_node.py           K2V3GeneratorV2   —— 出题
               k2_v3_prompt_edit_node.py K2V3PromptEdit  —— 预览与编辑
    核心  krea2_v3_rules.py       ← 生成逻辑（自包含，只读 presets/k2_v3_pools.json）
    扩展  k2_v3_extra.py          ← v3.11+ 新增能力（独立模块，不并入核心）
    数据  k2_v3_tables.py         内容等级 / 体型表
          character_tags_zh.py    Danbooru 标签 -> 中文
          krea2_characters.py     角色池读取
          k2_v3_char_link.py      角色特征联动
          k2_v3_store.py          角色方案 / 权重方案存储
          krea2_store.py          历史 / 收藏存储
          presets/*.json          词库与角色池

⚠️ 已移除（连同它们的用途）：
    nodes.py              V1 的 5 个节点（ENABLE_LEGACY_KREA2 开关一并取消）
    krea2_prompt_rules.py V1 的生成核心（V3 走 krea2_v3_rules.py，两套本来就不同）
    krea2_fixtext.py      运行期中文守卫（V3 靠池子**预翻译**实现纯中文）
    krea2_events.py       事件池（只被 legacy 链引用）
    krea2_pools.py        V1 词库总表（V3 只需要其中 7 张小表 → k2_v3_tables.py）
    krea2_presets_zh.py   V1 选择器预设中文表（只服务 legacy 面板）
    js_legacy/            V1 前端大面板（192 KB ×2，且两份 md5 相同）
    presets/character_100.json      旧版回落池（永不触发）
    presets/character_thumbs.json   只被已归档的 tools 用
    presets/anima_presets*.json     只服务 legacy /options 路由

⚠️ 节点内部 ID 保持不变（K2V3GeneratorV2 等），只改**显示名**。
   原因：ComfyUI 工作流是按节点 ID 存的，改 ID 会让所有既有工作流打不开。
   显示名已全部改成 V3 品牌，用户看到的是 V3。

================================================================================
================================================================================
v3.24.1 修复（清掉「保存图片」在用户可见文案里的残留）
================================================================================
v3.24 删节点时只 grep 了 `K2V3SaveImageMeta` 与「保存节点」两个关键词，
漏了「**保存图片**」这个写法在 `*.py` 里的出现 —— 当时只扫了 README 与 js/。
结果节点 tooltip 里还留着「可接给 ★ K2V3 保存图片 写进 PNG 元数据」，
鼠标悬停就能看到，属于**用户可见的错误**。本次全部改掉：

    · 输出端口 tooltip「随机种子」（两处）
      → 改为「关掉每次运行重随机后改它可精确复现」
    · 节点 DESCRIPTION 里的「配合 ★ K2V3 保存图片 写入 PNG」
      → 改为「接原生 SaveImage 出图时，插件会自动把完整正文写进 PNG 元数据」
    · 生成函数里的一处注释同步改掉
    · `k2_v3_prompt_edit_node.py` 文件头「接给 CLIP Text Encode / 保存节点」
      → 「/ 原生 SaveImage」
    · 顺手把 v3.24 段末尾那条 78 个 `=` 的分隔线补齐成 80 个

⚠️ 教训：**删一个东西时，要按「用户可见文案里的所有叫法」去扫**，
   不能只扫类名与单个关键词；`.py` 里的 tooltip 同样是用户可见的。
   可用：
       grep -rn '保存图片\\|保存节点\\|K2V3SaveImageMeta' --include=*.py .

================================================================================
v3.24 新增（移除「保存图片」节点 —— 插件定位收敛为「只出提示词」）
================================================================================
**改动**：彻底移除 `K2V3SaveImageMeta`（显示名「★ Krea2-Portrait-V3 · 保存图片」）。
插件由 **4 节点收敛为 3 节点**：出题 / 提示词预览与编辑 / 提示词导出。

**为什么**：插件只负责**生成提示词**，不生成、也不保存图片与影像；
原先「保存图片」节点会把种子与提示词写进 PNG 元数据，属于出图侧职责，
与本插件定位冲突，故整体移除。删除前已核查：**没有任何工作流引用该节点**
（全部含 K2V3 节点的工作流里，该节点出现 0 次），因此不影响任何既有工作流。
需要元数据的场景改用**原生 `SaveImage`** —— 出题节点仍会在运行时把正文注入
`extra_pnginfo`（见 `k2_v3_pngmeta.py`），原生 SaveImage 照样能把它写进 PNG。

**同步清理**：
    · `__init__.py` 的 import / NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS / K2V3_NODES
    · `k2_v3_pngmeta.build_parameters()` —— 它只服务该节点（拼 A1111 风格 parameters），
      一并移除；PNG 注入相关的其余函数**保留**，仍由出题 / 编辑节点调用。
    · 自测：新增 6 条「已移除且无残留」断言，替换原保存节点的键名断言。
    · README / 总览的节点表、目录结构、链路说明。

⚠️ 源文件 `k2_v3_meta_node.py` **未物理删除**，而是移到
   `S:/AI_workV5/_retired/Comfyui-Krea2-Portrait-V3_v3.24.0_已移除的保存节点/`，
   将来若要恢复可从那里取回。

================================================================================
v3.23 新增（署名文案更新：已授权开源 · 作者：飞蓬）
================================================================================
署名文案（用户给定）：
    旧：插件底层框架源于脚本：K2_V3_融合版.html（HTML 版人像提示词生成器）
        —— 更新日志内有“飞蓬”字样，作者未署名，故仅注明来源文件名。
    新：插件底层框架源于脚本：K2_V3_融合版.html（HTML 版人像提示词生成器）
        已授权开源 —— 作者：飞蓬。
口径（已与用户确认）：全角冒号「：」；整句**纯文本、不加粗**（连同文件名在内，
也不再套 **加粗** 或 `反引号`），保证六处落点逐字一致。

替换落点（六处署名位置 + 附加位置）：
    __init__.py 文件头 · krea2_v3_rules.py 文件头 · k2_v3_node.DESCRIPTION（第一行）·
    js/k2v3_gen.js 免责声明 · README.md（头部 / 免责要点 / 来源表「作者」行 /
    界面署名位置）· 总览文档头部 · k2_v3_pngmeta.py 文件头 · MEMORY.md。
测试同步：selftest 的 _srcs 增列 k2_v3_pngmeta.py（四处→五处），
    新增「同一句完整新文案（忽略折行逐字一致）」与「旧文案零残留」两条断言；
    UI 冒烟改为校验「已授权开源 + 飞蓬」且**不含**「作者未署名」。
⚠️ v3.22 的历史变更记录里**引用旧文案**作为存档，按用户要求**刻意保留不动**。

================================================================================
v3.22 新增（鞋履详细种类权重 + 水面波光权重 + 署名文本更新）
================================================================================
① 设置面板新增「⑤b 外观细节权重」区：
   - 鞋履详细种类权重：高跟鞋 / 靴子 / 运动鞋 / 凉鞋 / 皮鞋 / 赤脚 六种，
     每种一个权重滑块（0–3 / step .05 / 默认 1.00）。命中的鞋款权重 = 该种类权重，
     「不使用（不写鞋履）」恒为 1.00；权重 0 = 该种类永不出现。
   - 水面波光权重：控制「水面反光」（水面波光倒映在皮肤上，光影流转）这条
     环境光效果的出现概率。
   ⚠️ 实现：规则层新增 item_weights（按选项名叠加），节点把种类权重展开成
     {鞋款名: 权重} 再传入；**全 1.00 时传 None → 完全不介入**，出题逐字不变。
② 署名文本更新：由「底层源与脚本…原作者未查找到」改为
   「插件底层框架源于脚本…更新日志内有"飞蓬"字样，作者未署名…」，
   六处署名落点（__init__ / rules / node DESCRIPTION / 免责声明 / README / 总览）
   一并替换。

================================================================================
v3.21 新增（修复：PNG 元数据里的提示词不完整）
================================================================================
**问题**（用户报障）：出图后看图工具里的「提示词」只剩 `bl00m,`，不完整。
**根因**（解剖图片后确认）：**不是截断，是完整正文一个字都没进元数据** ——
这条链路里所有承载文本的 widget 都被转成了输入连线，`widgets_values` 全是空串：
    CLIPTextEncode#1133 的 text · StringConcatenate#1135 的 string_a/string_b ·
    PrimitiveStringMultiline#1004 的 value · K2V3PromptEdit#1281 的提示词
而出题节点的正文预览框又是 `serialize:false` 的 DOM widget —— 于是文本只在
运行时内存里流动，**从未落到任何可序列化的位置**。看图工具拿不到，就退而取了
链上唯一"有字"的节点（LoRA 触发词 bl00m + 拼接分隔符）。

**修法**：新增 `k2_v3_pngmeta.py`，把正文注入 `extra_pnginfo["workflow"]`。
`extra_pnginfo` 在同一次执行里被所有节点共享，而原生 SaveImage 是在自己执行时才
`json.dumps` 它 —— 所以**当次出图就带上，且不用改用户的工作流**。

· 写入位置（9 处）：正向 CLIPTextEncode 的 text · K2V3PromptEdit / Export 的提示词框 ·
  中间 PrimitiveString* 的 value 与 StringConcatenate 的 string_a/string_b
  （补中间节点是为了让"只读 widget 值、自己逐段拼链"的工具也能拼出完整串）。
· `widgets_values` 与 `widgets_values_named` **两份都写**
  （0.37 的前端优先读后者，只写一份会出现两套值不一致）。
· **安全边界**：`workflow`（UI JSON）只用于展示，随便写；
  `prompt`（API JSON）是执行用的图，所以**只写惰性键** `k2v3_prompt`
  —— CLIPTextEncode 的 `inputs.text` 保持连线不改字面量（它的真值由第三方节点
  决定，改成字面量会把触发词丢掉、直接改变出图）。
· K2V3SaveImageMeta **对齐原生**：`prompt`/`workflow` 归位原生语义
  （不再占用 `prompt`、不再把真 prompt 改名成 `prompt_workflow`），
  插件信息统一 `k2v3_` 前缀，另补一份 A1111 风格 `parameters` 给只认它的工具兜底。
· 新增 `tools/inspect_png_metadata.py`：纯标准库查看任意 PNG 的元数据，
  并可用 `--probe` 给旧图注入标记文本、验证自己的看图工具到底读不读得到。
· 出题节点 / 提示词编辑节点各新增两个 hidden 输入（EXTRA_PNGINFO / PROMPT）——
  hidden 输入不是控件，**不占控件位、不影响既有工作流**，所以本次**不需要迁移脚本**。

================================================================================
v3.20 新增（角色外观一致性 + ④ 字段微调扩展 + 历史收藏复制放大）
================================================================================
① **角色外观锁定**（新控件，追加在 optional 末尾，**默认开**）
   用户要求："确保角色设定后，每次自动重随生成的提示词中，角色特征保持一致，
   包括发色、发型、瞳孔颜色。" 而 v3.19 之前的实测结果**并不一致**，根因有三：
     · 推导只覆盖 发色/发长/扎法/刘海 —— **卷度(13档)与发态(11档)没锁**，
       而这两项在 ④ 段里是被硬拼进正文的，肉眼可见地每次不同；
     · 整条外观推导挂在「角色联动」下面，联动一关外观就全随机；
     · 「联动强度 < 1.0」按比例裁掉推导字段，也可能把发型裁掉。
   修法：新增 CL.build_appearance_lock()，**独立于联动与联动强度**，
   只负责把"这个人长什么样"钉死（发色/发长/卷度/发态/扎法/刘海 + 瞳色）：
     有标签 → 用标签；扎法/刘海缺标签 → 取中性值（= 正文里不写这两句，不杜撰）；
     其余缺标签 → 用「角色名 crc32 派生种子」从词库固定取一个。
   ⚠️ 必须用 crc32 而不是内置 hash()：后者每进程随机加盐，重启一次外观就变。
   ⚠️ 空串 / 缺失一律归一成 True（老工作流兼容，见 generate() 里的 _app_raw）。
   ⚠️ 合并顺序：必须在「联动强度」裁剪**之后**（否则比例一调低就被裁掉）。

② **④ 字段微调扩展为"点选 + 手输 + 搜索 + 批量"**
   v3.13 只有「下拉选字段 → 摇一个」，看不到候选、也不能指定值。现在：
   候选值点选（点一下即锁定）· 手输任意值 · 按段落分组折叠 · 搜索筛选 ·
   🔒 锁定当前整套值 / 🎲 重摇全部已锁 / 🗑 清空全部锁定。
   数据源：新增 krea2_v3_rules.FIELD_SECTION（全 69 个字段的段落归属，
   **继承 SKIPPABLE_SECTION**，保证与「逐项开关」的分组口径一致不漂移），
   由 /krea2-v3/fields 一并返回。
   配套：ui 消息新增 k2v3_slots（最近一次的槽位表），
   「锁定当前整套值」靠它才能把**整条结果**钉死，而不是只锁 UI 上看得见的那几个。
   ⚠️ 写进去的仍是同一个「锁定字段」控件 —— 这一节**没有碰生成逻辑**。

③ **历史 / 收藏：快捷复制 + 放大查看 + 搜索筛选**
   列表每条加「📋 复制提示词」（完整全文，不是那 160 字预览）与「🔍 放大查看」；
   放大 = 叠加第二层弹窗（openTextView，z-index 更高），全文可就地编辑再复制，
   并显示种子/等级/角色/字数等元数据；顶部搜索按 角色/正文/种子/内容项 过滤。

④ **注释与使用说明更新**：README.md 重写为完整使用说明（功能总览 + 快速上手 +
   七个功能区逐个怎么用 + 外观一致性章节 + 历史收藏 + 铁律 + 六件套命令）。
   注意：v3.20 只**在末尾追加**了一个控件，输出端口没动 ——
   旧的 6 条连线与 widgets_values 都不用迁移。

================================================================================
v3.19 新增（取消负面提示词 + 内容项扩到 13 项 + 内容权重跟随等级）
================================================================================
① **取消负面提示词**：不再生成、不再输出。删掉了 NEG_* 词表 / build_negative()、
   第 6 个输出端口、导出节点的同名输入与落盘字段、面板的文本框与按钮。
   ⚠️ 端口是**按索引**连线的 —— 删中间那个会让后面的「候选列表」前移一位，
      所以本次**必须**跑 tools/fix_workflows_v319.py 修正既有工作流（已跑，有备份）。
② **⑦ 内容项 5 → 13 项**：展示生殖器 / 自慰 / 潮吹·失禁 / 手交 / 乳交 / 足交 /
   口交 / 深喉 / 性交 / 肛交 / 束缚·捆绑 / 多人 / 其它。
   档位阶梯：露骨 6 项 → 强露骨 +4 → 极端 +3。权重上限 2.0 → **5.0**。
③ **新增「内容权重跟随等级」开关**（默认关 = 纯手动，逐字不变）：
   开启后按当前档位自动套用一套权重基准（露骨偏自慰、强露骨偏口交、极端偏性交），
   ⑦ 区滑块置灰只读并显示基准值；关掉立刻回到手动值（手动值不被覆盖）。
④ 单人保证延伸到新内容项：手交 / 乳交 / 足交 / 深喉 / 多人 这五项必须有第二人，
   单人模式下**自动排除**；其余项都有「单人可见」的条目。

================================================================================
v3.18 新增（NSFW 等级唯一化 + 内容项权重 + 双人互动前置）
================================================================================
① **等级只保留「内容强度」一个控制项**（五档：全年龄/暗示/露骨/强露骨/极端）。
   「内容等级（六档）/ 内容边界（越界开关）/ NSFW开关 / NSFW强度」整体退休 ——
   界面撤掉、逻辑不再读取，越界描写并入「极端」档。
   ⚠️ 控件位**保留不删**：ComfyUI 按位置存 widgets_values，删中间任意一个都会让
      所有既有工作流的控件值整体错位。旧工作流的「内容强度 = 手动（分别设置）」
      会回落到旧「内容等级」的值（兼容桥），不会静默掉档。
② 设置页**最底部**新增五项内容权重：展示生殖器 / 自慰 / 口交 / 性交 / 其它。
   规则是「**等级先筛可用范围、权重再定概率**」：露骨起可用展示生殖器与自慰，
   强露骨加口交，极端加性交；候选之间的概率 = 权重 ÷ 候选权重之和，0 = 永不出现。
   数据在 presets/k2_v3_extra_pools.json → nsfwAct；逻辑在 k2_v3_extra.pick_act()。
③ 单人保证延伸到内容项：solo 组条目画面里只有一个人，multi 组必须有第二人；
   单人模式只从 solo 里抽（口交 / 性交 因此走「道具版」条目）。
④ 双人互动段**前置**：从"追加在正文最后"改成"紧跟角色前缀、排在 10 段之前"，
   先交代两人正在做什么，再写镜头 / 人物 / 场景。双人字数上限放宽到 1000
   （单人仍是 600，共同以自检报告的「字数」口径为准）。
⑤ 设置面板每个功能区都加了**折叠按钮**（① 内容强度 … ⑦ NSFW 内容权重），
   默认只展开内容强度，一屏看完全部功能区名。
⑥ 单人模式行为零变化：level 未指定时内容项链路根本不启动，
   金标准快照 800/800 逐字一致；新增控件默认 1.00 亦不改变既有出题。
"""

import os

from .k2_v3_node import K2V3Generator
from .k2_v3_prompt_edit_node import K2V3PromptEdit
from .k2_v3_export_node import K2V3PromptExport

__version__ = "3.24.1"

# ============================================================================
# 节点注册（V3 共三个）
#
# 三条链路各自独立，可以只用其中一条：
#   ① 只出题（不要图）   K2V3GeneratorV2 → K2V3PromptExport
#   ② 出题 + 手改        K2V3GeneratorV2 → K2V3PromptEdit → K2V3PromptExport
#   ③ 出图（经典）       K2V3GeneratorV2 → CLIPTextEncode → … → SaveImage（原生）
#   ④ 出图 + 顺手存词    K2V3GeneratorV2 → K2V3PromptEdit ─┬→ CLIPTextEncode → …
#                                                          └→ K2V3PromptExport
# ============================================================================
NODE_CLASS_MAPPINGS = {
    "K2V3GeneratorV2": K2V3Generator,        # ID 保持不变：旧工作流兼容
    "K2V3PromptEdit": K2V3PromptEdit,
    # 【v3.14】提示词导出：插件自有输出端点，让出题链路可以脱离出图独立跑完
    "K2V3PromptExport": K2V3PromptExport,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "K2V3GeneratorV2": "★ Krea2-Portrait-V3 · 随机出题（角色联动 + 等级/权重）",
    "K2V3PromptEdit": "★ Krea2-Portrait-V3 · 提示词预览与编辑",
    "K2V3PromptExport": "★ Krea2-Portrait-V3 · 提示词导出（txt / md / json）",
}

# 三个节点都属于「主功能」，分组名统一到 V3
MAIN_NODES = ("K2V3GeneratorV2",)
SPARE_NODES = ()
K2V3_NODES = ("K2V3GeneratorV2", "K2V3PromptEdit", "K2V3PromptExport")

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY",
           "MAIN_NODES", "SPARE_NODES", "K2V3_NODES"]

# ============================================================================
# HTTP 路由命名空间统一为 /krea2-v3/*
#
# 为什么换前缀：V1、V2 各自用过不同的旧前缀。V3 自成一体，
# 用 /krea2-v3/* 既不含混也不与任何旧版本撞名（旧版本即使被重新启用也不会顶掉）。
# 本文件只注册「角色图鉴」一条 —— 其余出题相关路由在 k2_v3_node.py 里。
# 旧 V1/V2 的 11 条 legacy 路由（options/preview/random-character/presets/
# history/favorites/entry）与 V2 的接口重复，已全部移除。
# ============================================================================
ROUTE_PREFIX = "/krea2-v3"


def _register_routes():
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return

    routes = PromptServer.instance.routes

    from .krea2_characters import (
        CHARACTER_NAMES, WORK_ORDER, WORK_ZH, WORK_COLOR, cards,
    )

    @routes.get(ROUTE_PREFIX + "/characters")
    async def _characters(request):
        """角色图鉴数据：中文名 + 作品分类 + 缩略图。"""
        return web.json_response({
            "version": __version__,
            "count": len(CHARACTER_NAMES),
            "works": [{"key": w,
                       "zh": WORK_ZH.get(w, w),
                       "color": WORK_COLOR.get(w, "#888888"),
                       "count": sum(1 for c in cards() if c["work"] == w)}
                      for w in WORK_ORDER],
            "characters": cards(),
        })

    # ------------------------------------------------------------------
    # 【v3.16】角色缩略图：**由插件自己发图**
    #
    # 为什么必须走本地：远程图片直链（animadex / safebooru）在浏览器侧要看系统代理，
    # 与后端不是同一条路，国内网络下经常加载不出来。改成本地路径后，
    # 浏览器请求的是 127.0.0.1:8188 —— 和出图服务同源，断网也能显示。
    #
    # 用 FileResponse 而不是读进内存：图很多（上千张），逐个走磁盘更省内存，
    # 而且 aiohttp 会自动带 ETag / Last-Modified，浏览器能缓存。
    # 文件不存在就 404 —— 前端 onerror 退回首字色块，这是预期行为
    #（返回通用占位图反而会让人误以为"图配错了"）。
    # ------------------------------------------------------------------
    from . import k2_v3_thumbs as TH

    @routes.get(ROUTE_PREFIX + "/thumb/{tail:.*}")
    async def _thumb(request):
        path = TH.resolve(request.match_info.get("tail", ""))
        if not path:
            raise web.HTTPNotFound()
        resp = web.FileResponse(path)
        # FileResponse 在 aiohttp 3.14 里不猜 MIME（默认 octet-stream），显式给对
        resp.content_type = TH.content_type(path)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp


_register_routes()

# 出题 / 方案 / 历史等相关路由（在 k2_v3_node.py 内定义）
try:
    from . import k2_v3_node as _K2V3
    _K2V3.register_routes()
except Exception:
    pass


# ============================================================================
# 重复安装自检（"刷新后随机加载两套界面"的预防针）
#
# 背景：ComfyUI 用**未排序**的 os.listdir 枚举 custom_nodes，同名节点后加载者
#       静默覆盖；前端 JS 也用未排序 glob 枚举，两份同名 JS 谁先注册谁赢。
#       所以只要存在两份未停用的同族插件，加载结果必然不确定。
#
# 处理：启动时自检并告警（不抛异常、不影响启动）。只有以 .disabled 结尾的
#       目录会被 ComfyUI 跳过（官方机制）。
# ============================================================================
def check_duplicate_install():
    """返回与自身冲突的、仍处于启用状态的同族插件目录名列表。"""
    import os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    parent = _os.path.dirname(here)
    mine = _os.path.basename(here)
    dups = []
    for name in _os.listdir(parent):
        if name == mine or name.endswith(".disabled"):
            continue
        p = _os.path.join(parent, name)
        if not _os.path.isdir(p):
            continue
        # 同族：目录名以 Comfyui-Krea2-Portrait 开头，且是能加载的插件
        if not name.startswith("Comfyui-Krea2-Portrait"):
            continue
        if _os.path.isfile(_os.path.join(p, "__init__.py")) and (
                _os.path.isfile(_os.path.join(p, "k2_v3_node.py"))
                or _os.path.isfile(_os.path.join(p, "nodes.py"))):
            dups.append(name)
    return dups


try:
    import logging as _logging
    _dups = check_duplicate_install()
    _logging.info("[Comfyui-Krea2-Portrait-V3] v%s 已加载，注册 %d 个节点：%s",
                  __version__, len(NODE_CLASS_MAPPINGS), list(NODE_CLASS_MAPPINGS))
    if _dups:
        _logging.warning(
            "[Comfyui-Krea2-Portrait-V3] ⚠️ 检测到其他未停用的同族插件：%s。"
            "这会导致节点与前端 JS 被随机覆盖（刷新后界面不稳定）。"
            "请把不需要的那份目录重命名为以 .disabled 结尾后重启 ComfyUI。", _dups)
except Exception:
    pass
