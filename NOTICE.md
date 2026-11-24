# 使用条款与来源声明 / Terms of Use & Attribution

> 本文件是**条款声明**，与 [LICENSE](LICENSE) 配合使用。许可证正文是 **PolyForm Strict License 1.0.0**；
> 本文件补充说明中文口径、历史版本授权、以及第三方素材来源。
> This file supplements the [LICENSE](LICENSE) (PolyForm Strict License 1.0.0) with a Chinese
> statement of terms, the legacy-license carve-out, and third-party attributions.

## 一、授权生效时间 / Effective date

自 **2026-11-24** 起，本仓库的许可为 **PolyForm Strict License 1.0.0**。

The license of this repository is the PolyForm Strict License 1.0.0 with effect from **2026-11-24**.

## 二、历史版本 / Legacy versions

本次变更**之前**发布的版本（tag / release / 历史 commit），仍按其发布时的许可证 **MIT** 授权。
该授权**不因本次变更而撤回** —— 任何人在变更前合法取得的 MIT 权利继续有效。

Versions published **before** 2026-11-24 (tags, releases, historical commits) remain available under
**MIT**, the license they were published with. That grant is **not withdrawn**; rights already
obtained under MIT before the change remain valid.

## 三、允许与禁止 / Permitted & prohibited

| | 中文 | English |
|---|---|---|
| ❌ | **禁止商业使用** —— 付费分发、捆绑销售、代跑收费、商业课程、作为商业服务的技术底座，均需事先取得作者书面许可。 | **No commercial use** — paid distribution, bundling, paid generation services, commercial courses, or use as the base of a commercial service all require prior written permission. |
| ❌ | **禁止二次包装与再分发** —— 不得重新打包、改名、套壳、二次封装发布，不得上传至第三方整合包、网盘合集、资源站或任何分发渠道，无论是否修改。 | **No repackaging or redistribution** — no repackaging, renaming, wrapping, re-uploading, or inclusion in third-party bundles, mirror archives, net-disk collections or resource sites, modified or not. |
| ❌ | **禁止移除或篡改**本声明、作者署名、来源链接。 | **Do not remove or alter** this notice, the author attribution, or the source link. |
| ✅ | **允许**个人非商业使用（研究、学习、爱好、自己出图）、修改源码自用。 | **Permitted**: personal non-commercial use (research, study, hobby, generating your own images) and modifying the source for your own use. |
| ✅ | **允许**在保留完整署名与本声明的前提下，引用截图／录屏做非商业分享（如 B 站教程）。 | **Permitted**: quoting screenshots / screen recordings for non-commercial sharing (e.g. a tutorial) with full attribution and this notice kept intact. |

**商业授权 / 二次开发授权 / 转载申请**：请通过 [B 站私信](https://space.bilibili.com/516891894) 联系作者取得书面许可。
**Commercial / derivative-work / repost licensing**: contact the author via Bilibili DM (https://space.bilibili.com/516891894) for written permission.

## 四、作者与来源 / Author & upstream

| 角色 / Role | 说明 / Detail |
|---|---|
| 作者 / Author | 灵冻不冻（lingdongbudong）· [B 站主页](https://space.bilibili.com/516891894) / [GitHub](https://github.com/lingdongbudong) |
| 仓库 / Repository | <https://github.com/lingdongbudong/Comfyui-Krea2-Portrait-V3> |

本仓库原创部分（节点封装、档位闸门、权重体系、界面与数据结构）著作权归作者所有。
第三方素材（上游词库、角色名与作品名、缩略图等）的权利归属详见各仓库 README 的「署名与来源」一节，
本文件不改变其原有归属。

Original work in this repository (node wrappers, level gating, weight system, UI and data structures)
is owned by the author. Third-party material (upstream prompt pools, character and franchise names,
thumbnails) retains its own ownership as described in the README's attribution section.

---

## 五、来源声明（原有内容，未改动） / Original attribution (unchanged)

本文件是**声明**，不是许可证条款。许可证见 [LICENSE](LICENSE)（PolyForm Strict 1.0.0，历史版本为 MIT）。

### 一、移植来源

本插件的**词库与生成逻辑**移植自脚本 **`K2_V3_融合版.html`**
（HTML 版人像提示词生成器；页面标题：`K2 人像提示词生成器 V2 · 优化版`）。

> 该脚本**已授权开源 —— 作者：飞蓬**。
> 来源文件路径（作者本机）：`<ComfyUI>/提示词/K2_V3_融合版.html`。

移植对应关系：

| 原脚本里的东西 | 在本仓库里 |
|---|---|
| 全部词库（63 池 / 949 条） | `presets/k2_v3_pools.json` |
| `buildPrompt()` 的 10 段装配 | `krea2_v3_rules.build_prompt()` |
| 两级级联随机 + 冲突预过滤 + 兜底纠错 | `randomize_*()` / `_prefilter()` / `enforce_conflicts()` |
| `runSelfCheck()` 的 F1–F10 / S1–S13 | `run_self_check()` |
| 超字数按顺序精简 | `generate()` 里的 `TRIM_ORDER` |

> ⚠️ **词库的唯一真源仍是那份 HTML**：改词库要**同时**改 HTML 与 JSON，
> 只改 JSON 会在下次导出时被冲回。

### 二、角色名与作品名

仓库内出现的角色名、作品名、角色标签等，**知识产权均归各自权利人所有**。
本项目使用它们**仅作提示词的检索与描述**，不主张任何相关权利，
也不代表与原权利人存在任何关联或获得其认可。

### 三、角色图标

本仓库**不包含**任何角色图片。
插件运行时支持从本地 `thumbs/` 目录读取角色缩略图（该目录被 `.gitignore` 排除），
用于角色图鉴的识别对照；图源由使用者自行准备，需遵守相应图源站点的使用条款。

### 四、生成内容的免责

- 本插件**仅生成文本提示词**，不生成、也不保存图片与影像。
- 所有角色一律视为 **18 岁以上虚构人物**；
  禁止用于真实人物、未成年人、非自愿及暴力伤害相关内容。
- 生成内容及其用途**由使用者自行负责**，请自行确认是否符合所在地法律法规与平台规则。

---

## 六、关于原文件中的许可证表述 / License reference in the original notice

> 原文件写作「许可证见 LICENSE（MIT）」。自 2026-11-24 起已变更为 **PolyForm Strict 1.0.0**，
> 历史版本仍为 MIT（见第二节）。
>
> The original notice referred to the MIT license. Since 2026-11-24 the license is
> **PolyForm Strict 1.0.0**; legacy versions remain MIT (see section 2).
