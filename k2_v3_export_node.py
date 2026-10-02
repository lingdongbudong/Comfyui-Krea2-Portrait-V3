# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-Portrait-V3 :: 提示词导出节点（v3.14 新增）

用途：让「提示词生成」这件事**彻底脱离出图链路独立跑起来** ——
      出题节点算完提示词，直接由本节点落盘成可读文本（txt / md / json）
      并在节点面板上回显。整条链路不需要 CLIP Text Encode / KSampler /
      VAE Decode / SaveImage 中的任何一环。

为什么单独做一个节点，而不是复用 ComfyUI 自带的 SaveText / PreviewAny：
  ① 自带 SaveText 只接一路 STRING，本节点一次要落「提示词 +
     自检报告 + 元数据」四路，还要按格式排版成一份能直接读的文档；
  ② 自带文本节点在不同 ComfyUI 版本里命名与签名会变（SaveText 属于新的
     io.ComfyNode 风格，PreviewAny 依赖 comfy.comfy_types），本节点零外部
     依赖，装到哪个版本都能用；
  ③ 本节点是 OUTPUT_NODE，可以**独自当工作流的终点** —— 这正是「独立运行」
     成立的前提（ComfyUI 只执行通向输出节点的链路）。

设计要点（与插件既有约定完全一致）：
  · **完全不参与出题 / 权重 / 随机逻辑**：只读字符串，不读也不写任何生成状态，
    所以它的存在不可能改变任何一条提示词的内容；
  · 输出端口「提示词」原样回吐输入（直通），接不接下游都不影响出题；
  · 所有依赖（folder_paths / os / time）延迟或极轻量导入，纯逻辑环境下也能
    import 本文件来查 UI 定义（自测脚本依赖这一点）；
  · **写盘失败不抛异常**：降级成把错误放进「文件路径」返回值与面板回显，
    避免因为磁盘/权限问题把整条队列带崩。
"""

import json
import os
import time

CATEGORY = "Krea2-Portrait-V3/输出"

# ---------------------------------------------------------------------------
# 输出格式
#
# 为什么不直接用「扩展名」当选项：三种格式的**内容范围**不一样，用户真正要选的是
# 「这份文件里装多少东西」，而不是「后缀叫啥」。所以选项名把内容说清楚。
# ---------------------------------------------------------------------------
FMT_MD = "md（提示词 + 报告）"
FMT_TXT = "txt（只有提示词正文）"
FMT_JSON = "json（全字段结构化）"
FMT_ALL = "全部三种"
FORMATS = [FMT_MD, FMT_TXT, FMT_JSON, FMT_ALL]

# 文件名里不能出现的字符（Windows 尤其严格）
_BAD_NAME_CHARS = '<>:"/\\|?*'


def _safe_name(s, fallback="K2V3"):
    """把用户填的前缀洗成合法文件名。空 → fallback。"""
    s = (s or "").strip() or fallback
    for ch in _BAD_NAME_CHARS:
        s = s.replace(ch, "_")
    s = s.replace("\n", " ").replace("\r", " ").strip()
    # 结尾的点和空格在 Windows 上会被吞掉，导致实际文件名与预期不符
    return s.rstrip(". ") or fallback


def _unique_path(folder, base, ext):
    """避免覆盖同名文件：存在就加 _2 / _3 …

    刻意不用 folder_paths.get_save_image_path —— 那个函数的返回值个数与语义
    在 ComfyUI 各版本间变过（有的返回 5 个、有的 4 个），自己算一遍最稳。
    """
    p = os.path.join(folder, base + ext)
    i = 2
    while os.path.exists(p):
        p = os.path.join(folder, "%s_%d%s" % (base, i, ext))
        i += 1
    return p


class K2V3PromptExport:
    """把出题结果落盘成可读文本，独立于出图链路运行。"""

    def __init__(self):
        try:
            import folder_paths
            self.output_dir = folder_paths.get_output_directory()
        except Exception:
            self.output_dir = ""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "提示词": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "正面提示词。接出题节点的「提示词」或编辑节点的"
                               "「提示词」输出；也可以直接在本框里键入文本。",
                }),
            },
            "optional": {
                # ---- 其余几路内容：不接就留空，格式里对应小节自动省略 ----
                # 【v3.19】原来的「负面提示词」输入已删除（插件不再产生负面词）。
                "自检报告": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "可选，接出题节点的「自检报告」输出。"
                               "md 格式会把它附在末尾，方便回溯这次用了什么设置。",
                }),
                "元数据JSON": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "可选，接出题节点的「元数据JSON」输出。"
                               "json 格式会把它原样嵌进 meta 字段。",
                }),
                "槽位JSON": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "可选，接出题节点的「槽位JSON」输出。"
                               "json 格式会把它嵌进 slots 字段。",
                }),
                "候选列表": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "可选，接出题节点的「候选列表」输出（JSON 数组）。"
                               "配合下面的「导出全部候选」使用。",
                }),
                "随机种子": ("INT", {
                    "default": 0, "min": 0, "max": 0xFFFFFFFF,
                    "tooltip": "可选，接出题节点的第 4 路「随机种子」输出。"
                               "会写进文件名与文件头，方便日后按种子复现。"
                               "留 0 时会尝试从「元数据JSON」里取。",
                }),
                # ---- 落盘控制 ----
                "保存目录": ("STRING", {
                    "default": "K2V3_prompts",
                    "tooltip": "相对于 ComfyUI output 目录的子目录，默认 "
                               "K2V3_prompts。留空则直接存到 output 根目录。",
                }),
                "文件名前缀": ("STRING", {
                    "default": "K2V3",
                    "tooltip": "文件名前缀。实际文件名 = 前缀_日期-时间[_seed]"
                               "（重名自动加 _2 / _3）。",
                }),
                "输出格式": (FORMATS, {
                    "default": FMT_MD,
                    "tooltip": "md（提示词 + 报告）= 一份可直接读的文档；"
                               "txt（只有提示词正文）= 纯提示词，方便整段复制；"
                               "json（全字段结构化）= 含 seed / 等级 / 槽位，便于程序处理；"
                               "全部三种 = 一次写三份。",
                }),
                "导出全部候选": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "勾选后，若「候选列表」里有 N 条（出题节点的"
                               "「批次数」设为 N），会一次性把 N 条都写进同一个文件"
                               "（md 里按序号分段，txt 里每行一条）。",
                }),
                "只预览不保存": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "勾选后只在节点面板回显内容、不写任何文件。"
                               "适合只想看一眼结果的场景。",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("文件路径", "提示词")
    RETURN_TOOLTIPS = (
        "本次实际写出的文件绝对路径（多个文件用 \" | \" 连接）；"
        "只预览或写盘失败时是说明文字。",
        "输入的正面提示词原样直通（不受保存与否影响）。",
    )
    FUNCTION = "export"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True
    DESCRIPTION = (
        "提示词导出节点 —— 让提示词生成独立于出图链路。\n"
        "· 接出题节点的提示词 / 报告 / 元数据，落盘成 txt / md / json；\n"
        "· 本节点即工作流终点，不需要 CLIP Text Encode、KSampler、SaveImage；\n"
        "· 只读字符串，不参与出题 / 权重 / 随机逻辑，不影响任何一条提示词内容。"
    )

    # ------------------------------------------------------------------
    # 内容排版
    # ------------------------------------------------------------------
    @staticmethod
    def _stamp():
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _candidates(self, raw):
        """把「候选列表」解析成字符串列表。

        宽容处理：JSON 数组 / 每行一条 / 空 —— 解析不出来就当整段是一条。
        """
        s = (raw or "").strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                arr = json.loads(s)
                if isinstance(arr, list):
                    return [str(x) for x in arr if str(x).strip()]
            except Exception:
                pass
        return [ln.strip() for ln in s.splitlines() if ln.strip()]

    def _build_md(self, text, report, meta_raw, slots_raw,
                  seed, level, character, items):
        L = []
        L.append("# Krea2-Portrait-V3 提示词")
        L.append("")
        L.append("- 生成插件：Comfyui-Krea2-Portrait-V3")
        L.append("- 导出时间：%s" % self._stamp())
        if seed:
            L.append("- 随机种子：%s" % seed)
        if level:
            L.append("- 内容等级：%s" % level)
        if character:
            L.append("- 角色：%s" % character)
        L.append("")
        if items:
            L.append("## 候选提示词（共 %d 条）" % len(items))
            L.append("")
            for i, it in enumerate(items, 1):
                L.append("### %d." % i)
                L.append("")
                L.append("```text")
                L.append(it)
                L.append("```")
                L.append("")
        else:
            L.append("## 正面提示词")
            L.append("")
            L.append("```text")
            L.append(text or "")
            L.append("```")
            L.append("")
        if report:
            L.append("## 自检报告")
            L.append("")
            L.append("```text")
            L.append(report)
            L.append("```")
            L.append("")
        if meta_raw or slots_raw:
            L.append("## 结构化数据")
            L.append("")
            for title, raw in (("元数据", meta_raw), ("槽位", slots_raw)):
                if not raw:
                    continue
                L.append("### %s" % title)
                L.append("")
                L.append("```json")
                L.append(raw.strip())
                L.append("```")
                L.append("")
        return "\n".join(L)

    def _build_json(self, text, report, meta_raw, slots_raw,
                    seed, level, character, items):
        """结构化导出。

        元数据 / 槽位本身是 JSON 字符串 —— 能解析就嵌成对象（可读性更好），
        解析不了就原样存成字符串（不丢数据）。这是刻意的宽容策略：
        上游换了格式也不至于让导出直接报错。
        """
        def _try(raw):
            s = (raw or "").strip()
            if not s:
                return None
            try:
                return json.loads(s)
            except Exception:
                return s

        d = {
            "prompt": text or "",
            "seed": int(seed or 0),
            "level": level or "",
            "character": character or "",
            "exported_at": self._stamp(),
            "plugin": "Comfyui-Krea2-Portrait-V3",
        }
        if items:
            d["candidates"] = items
        if report:
            d["report"] = report
        m = _try(meta_raw)
        if m is not None:
            d["meta"] = m
        s = _try(slots_raw)
        if s is not None:
            d["slots"] = s
        return json.dumps(d, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def export(self, 提示词="", 自检报告="", 元数据JSON="",
               槽位JSON="", 候选列表="", 随机种子=0, 保存目录="K2V3_prompts",
               文件名前缀="K2V3", 输出格式=FMT_MD, 导出全部候选=False,
               只预览不保存=False):
        text = 提示词 or ""
        report = 自检报告 or ""

        # seed / level / character 优先取显式连线，取不到再从元数据兜底 ——
        # 这样即使用户只连了一根「元数据JSON」，文件头也能带上关键信息。
        try:
            seed = int(随机种子 or 0)
        except (TypeError, ValueError):
            seed = 0
        level, character = "", ""
        try:
            m = json.loads(元数据JSON) if (元数据JSON or "").strip() else {}
            if isinstance(m, dict):
                if not seed:
                    seed = m.get("seed") or 0
                level = (m.get("level_zh") or m.get("level") or "")
                character = m.get("character") or ""
                # 元数据里带了 is 提示词 但用户没连提示词线时，也别导出空文件
                if not text and m.get("prompt"):
                    text = str(m["prompt"])
        except Exception:
            pass

        items = self._candidates(候选列表) if 导出全部候选 else []

        # ---------- 只预览 ----------
        if 只预览不保存:
            preview = ("【只预览 · 未保存】\n"
                       + (("候选 %d 条\n\n" % len(items))
                          + "\n\n".join(items) if items else text))
            return {"ui": {"k2v3_export": [preview],
                        "k2v3_export_text": [text]},
                    "result": ("（只预览，未写文件）", text)}

        if not text and not items:
            return {"ui": {"k2v3_export": ["【未导出】提示词为空。"],
                        "k2v3_export_text": [""]},
                    "result": ("（提示词为空，未写文件）", text)}

        # ---------- 目标目录 ----------
        base_dir = self.output_dir or ""
        sub = (保存目录 or "").strip().strip("/\\")
        if base_dir and sub:
            folder = os.path.join(base_dir, sub)
        else:
            folder = base_dir or "."
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            return {"ui": {"k2v3_export": ["【保存失败】无法创建目录：%s" % e],
                        "k2v3_export_text": [text]},
                    "result": ("保存失败：无法创建目录 %s（%s）" % (folder, e), text)}

        # ---------- 文件名 ----------
        prefix = _safe_name(文件名前缀, "K2V3")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = "%s_%s" % (prefix, stamp)
        if seed:
            base += "_%s" % seed

        # ---------- 按格式写盘 ----------
        fmt = 输出格式 or FMT_MD
        wanted = []
        if fmt == FMT_ALL:
            wanted = [(".md", "md"), (".txt", "txt"), (".json", "json")]
        elif fmt == FMT_TXT:
            wanted = [(".txt", "txt")]
        elif fmt == FMT_JSON:
            wanted = [(".json", "json")]
        else:
            wanted = [(".md", "md")]

        written, errors = [], []
        for ext, kind in wanted:
            try:
                path = _unique_path(folder, base, ext)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    if kind == "txt":
                        # txt 给「整段复制」用：有候选就一行一条，否则就是正文
                        f.write("\n".join(items) if items else text)
                    elif kind == "json":
                        f.write(self._build_json(text, report, 元数据JSON,
                                                 槽位JSON, seed, level, character, items))
                    else:
                        f.write(self._build_md(text, report, 元数据JSON,
                                               槽位JSON, seed, level, character, items))
                written.append(path)
            except Exception as e:
                errors.append("%s：%s" % (kind, e))

        # ---------- 回显 ----------
        ui_lines = []
        if written:
            ui_lines.append("【已保存 %d 个文件】" % len(written))
            ui_lines.extend(written)
        if errors:
            ui_lines.append("【部分失败】" + "；".join(errors))
        ui_lines.append("")
        ui_lines.append(items[0] if items else text)

        result_path = " | ".join(written) if written else ("保存失败：" + "；".join(errors))
        # 额外回一路「纯净正文」给前端：让"复制正文"按钮不用去猜
        # 回显文本里哪一段是提示词（提示词本身可能含空行，切分不可靠）。
        return {"ui": {"k2v3_export": ["\n".join(ui_lines)],
                       "k2v3_export_text": [items[0] if items else text]},
                "result": (result_path, text)}


NODE_CLASS_MAPPINGS = {"K2V3PromptExport": K2V3PromptExport}
NODE_DISPLAY_NAME_MAPPINGS = {
    "K2V3PromptExport": "★ Krea2-Portrait-V3 · 提示词导出（txt / md / json）",
}
