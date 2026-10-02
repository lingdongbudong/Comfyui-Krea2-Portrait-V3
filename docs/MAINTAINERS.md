# 维护者指南 · Comfyui-Krea2-Portrait-V3

> 面向**改这个插件代码的人**。只使用插件的话看 [USAGE.md](USAGE.md) 就够了。

---

## 一、改代码前必读：六条铁律

违反其中任何一条，代价都是**用户的工作流坏掉**或**生成结果悄悄变了**。

### 1. 新增能力必须「默认不干预」

参数为 `None` / `1.0` / 开关为 `False` 时，**根本不调用**新方法。

> 「默认不干预」≠「默认关」：例如「角色外观锁定」默认 **True**，
> 但它只在**选了角色**时才介入 —— 所以没指定角色的用例结果逐字不变。
> 判据是「该功能是否在这个用例的输入范围内生效」，不是「开关是否为 False」。

### 2. 新增内容只能「独立追加段」

新内容写进 `multi_segment` / `beyond_segment` 之类**独立段**，
**不得插进既有 10 段之间** —— 插进去会改变所有既有提示词的段落顺序。

### 3. 控件 / 输出端口一律追加在末尾

ComfyUI 按**位置**存 `widgets_values`、按**索引**连 link。

- ⚠️ **要退休某个控件时只撤界面、不删控件位**（删中间任意一个 = 旧工作流值整体错位）。
- ⚠️ **追加控件后必须确认节点有 `VALIDATE_INPUTS`**：老工作流的 `widgets_values`
  比现控件短，缺的位传**空串**，而 ComfyUI 对 `FLOAT` 控件的自动校验会拒绝空串 →
  **整个工作流跑不起来**（报 `Failed to convert an input value to a FLOAT value`）。
- ⚠️ **布尔控件默认 True 时，空串/缺失必须归一成 True**（`bool("")` 是 `False`）——
  否则会出现"新工作流一致、老工作流不一致"。
- ✅ **只在末尾追加**（如 v3.20 那次）**不需要**跑迁移脚本：缺的末位由 ComfyUI 取默认值。

### 4. `WEIGHT_AXIS_FIELDS` 不能加轴

加轴会打破金标准快照（所有既有提示词的分布都变了）。

### 5. 新功能做成独立节点 / 独立路由

不要合并进既有函数；组件类的 tooltip / 清单**尽量自动生成**（遍历内容表），
手写的清单在扩项时必漏（v3.18 手写 5 条，v3.19 扩到 13 项时漏了 8 条）。

**5b. 改「元数据」相关逻辑前，先读 `k2_v3_pngmeta.py` 的文件头** ——
里面写清了哪些能写、哪些绝对不能碰（`prompt` 是**执行用的图**，改它已声明的输入
会真的改变执行结果）。另外新版前端有 `widgets_values` **和** `widgets_values_named`
两份，只改一份就会出现两套值不一致，而它优先读 named。

### 6. 外观类的确定性值必须跨进程稳定

派生种子用 `zlib.crc32`，**不要用内置 `hash()`** —— 后者每个进程加不同的随机盐，
重启一次外观就变了。

**7. 改「用户可见文案」时要按所有叫法去扫。**
删掉一个节点的 class 不代表删干净了：`.py` 里的 tooltip、节点 `DESCRIPTION`、
注释里可能还留着它的**显示名**（踩过：v3.24 删「保存图片」节点时，
`K2V3SaveImageMeta` 扫干净了，但 tooltip 里还写着「★ K2V3 保存图片」）。
正确扫法：

```bash
grep -rn '保存图片\|保存节点\|K2V3SaveImageMeta' --include=*.py .
```

---

## 二、目录结构

```
__init__.py               包入口：节点注册 + 角色图鉴 / 缩略图路由
k2_v3_node.py             出题节点（控件 / 出题 / 方案 / 字段与历史路由）
k2_v3_prompt_edit_node.py 提示词预览与编辑
k2_v3_export_node.py      提示词导出（txt / md / json）
k2_v3_pngmeta.py          把正文注入 extra_pnginfo（让原生 SaveImage 写进 PNG）
krea2_v3_rules.py         ★ 生成核心（自包含，只读 presets/k2_v3_pools.json）
                          ＋ FIELD_SECTION 全字段分区表
k2_v3_extra.py            扩展能力（纹身/人数/百合/NSFW内容项与权重/越界/随机性）
k2_v3_tables.py           内容等级 / 体型表
character_tags_zh.py      Danbooru 标签 → 中文短语
krea2_characters.py       角色池读取与卡片
k2_v3_char_link.py        角色联动 + 外观锁定（build_appearance_lock）
k2_v3_store.py            方案 / 去重记忆存储
krea2_store.py            历史 / 收藏存储
js/k2v3_gen.js            出题面板（设置面板 / 角色图鉴 / 历史 / 放大查看）
js/k2v3_prompt_edit.js    预览编辑面板
js/k2v3_export.js         导出面板
presets/k2_v3_pools.json  主词库（由 HTML 脚本导出，**勿手改**）
presets/k2_v3_extra_pools.json  附加池
presets/character_pool.json     角色池（2165）
tools/inspect_png_metadata.py   查 PNG 元数据的诊断工具（纯标准库）
workflows/                示例工作流
docs/                     本文档与使用说明
```

---

## 三、测试基线（只增不减 · 全部要 0 失败）

改完**必须**跑：

```bash
cd ComfyUI/custom_nodes/Comfyui-Krea2-Portrait-V3

python tests/golden_snapshot.py --check   # 800/800 逐字一致 ← 生成逻辑未变的铁证
python tests/selftest.py                  # 358/0
python tests/check_old_workflows.py       # 56/0
cp js/k2v3_gen.js /tmp/x.mjs && node --check /tmp/x.mjs   # ⚠️ 见下
python tests/live_prompt_workflow.py      # 16/0（需 ComfyUI 在线）
```

> ⚠️ **JS 语法检查必须复制成 `.mjs`**：`node --check xxx.js` 对**含 `import` 的 `.js` 是空操作**
> （返回 0 却不解析）。必须 `cp js/k2v3_gen.js /tmp/x.mjs && node --check /tmp/x.mjs`。
> 曾因这个假阳性把一行 Python 风格 `#` 注释放进 JS，面板整块不加载却"检查通过"。

> ⚠️ `check_old_workflows` 的**条数** = 工作流目录里含 K2V3 的 json 份数 × 每条断言数。
> **只看失败数是否为 0**，不要纠结总数变化。

### 金标准快照是干什么的

`tests/golden_baseline.json` 存了 **800 条 (种子, 模式, 参数) → 完整提示词**。
`--check` 会逐字比对。它是「我没改坏生成逻辑」唯一的客观证据 ——
任何**有意的**输出变化都必须重新生成基线，并在提交信息里说明原因。

### 断言写法的两个坑

1. **别用裸词检查「某串已不存在」**。文件里的**变更说明/注释**往往正好引用那个旧名，
   裸词检查会把说明文字当成残留。正确做法是查**代码模式**：

   ```python
   # ✗ 会被变更说明误伤
   check("无残留", "k2_v3_meta_node" not in src)
   # ✓ 只看 import 语句（剥掉行内注释后判断）
   lines = [l.split("#")[0].strip() for l in src.splitlines()]
   check("无残留", not any(l.startswith(("from .k2_v3_meta_node",
                                        "import k2_v3_meta_node")) for l in lines))
   ```

2. **比对文案一致性时，两边都要归一化**。拿"带空格的原文"去比"去过空格的目标文本"
   永远不匹配；而且源码里长句子常被折成多段字符串字面量（Python 折行处留下 `""`、
   JS 折行处留下 `" + "`）—— 归一化要连这些**代码分隔符**一起去掉。

---

## 四、动过控件 / 端口 → 必须跑工作流迁移脚本

改过「**控件个数**」或「**输出端口顺序/个数**」以后，既有工作流会错位，需要迁移：

```bash
python tools/fix_workflows_v319.py --dry    # 先预览，确认要改什么
python tools/fix_workflows_v319.py          # 真正执行（自动备份、幂等）
```

它按位置修连线 + 规整 `widgets_values`。两条教训：

- **脚本必须幂等**：第一版没判断"这次到底删没删端口"就无条件减 1，越挪越偏；
- **"保留前 N 项"的 N 要随版本推进**，否则第二次跑会把用户新设的值冲成默认值。

---

## 五、词库的正确改法

**词库的唯一真源是原作者的 `K2_V3_融合版.html`**，`presets/k2_v3_pools.json` 是从它**导出**的。

- 改词库 → **同时**改 HTML 与 JSON；只改 JSON 会在下次导出时被冲回。
- 导出用 `tools/export_k2v3_pools.mjs`（零漂移）。
- 内容一律中文：能译的译成中文，译不出的英文条目**直接删**；
  唯一豁免是**角色英文原名**。

---

## 六、提交前自检清单

- [ ] 跑了第三节的六条测试命令，**失败数全为 0**
- [ ] 若改过生成逻辑：确认金标准快照的差异是**你有意的**，并已更新基线 + 说明原因
- [ ] 若动过控件 / 端口：跑过迁移脚本，并测过旧工作流
- [ ] 新增控件**追加在末尾**，且节点有 `VALIDATE_INPUTS`
- [ ] 改了用户可见文案：`grep` 过所有旧叫法，没有残留
- [ ] 新增内容**默认不干预**（默认参数下输出逐字不变）
- [ ] 版本号已在 `__init__.py` 里更新，`README.md` 与 `docs/OVERVIEW.md` 同步
- [ ] 换行符保持原样（改之前先探测：`'\r\n' in raw`，改完原样写回）

> 本仓库的源码在本机是**混合换行**（部分 CRLF、部分 LF）。
> `.gitattributes` 里统一成 LF 入库；本地编辑时**改之前先探测、改完保持原样**，
> 不要盲信也不要写混（MIXED 最糟）。
