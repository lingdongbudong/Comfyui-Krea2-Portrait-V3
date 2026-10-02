# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-PortraitV2 :: 提示词预览与编辑（v3.12 新增节点）

用途：接在 K2V3GeneratorV2 的「提示词」输出之后，
      ① 把上游生成的完整提示词**原样展示**在一个可读的大文本框里；
      ② 支持手动改写，并接给 CLIP Text Encode / 原生 SaveImage。

设计要点（严格满足「纯展示层」约束）：
  · 默认「直通（不修改）」—— 输出与输入**逐字一致**（不做 trim、不做任何加工），
    因此老工作流把本节点串进来，行为也完全不变；
  · 所有编辑动作（节点内覆盖 / 查找替换 / 追加前后缀）都只在用户显式选择后才生效；
  · 本节点**不接触**任何出题 / 权重 / 随机逻辑，只对"已经生成的字符串"做文本处理；
  · 依赖只用标准库（json），无 numpy / PIL，纯逻辑环境下也能 import。

节点：
  K2V3PromptEdit
    输入：提示词（STRING）
          【可选】编辑模式 / 手动文本 / 查找替换(JSON) / 前缀 / 后缀 / 去除首尾空白
    输出：提示词（STRING） + 编辑报告（STRING）
"""

import json

from . import k2_v3_pngmeta as PM

CATEGORY = "Krea2-Portrait-V3/主要功能"

MODE_PASS = "直通（不修改）"
MODE_REPLACE = "节点内文本覆盖"
MODE_FIND = "查找替换"
MODE_AFFIX = "追加前后缀"
MODES = [MODE_PASS, MODE_REPLACE, MODE_FIND, MODE_AFFIX]


class K2V3PromptEdit:
    """提示词预览与编辑：默认直通，可选覆盖 / 查找替换 / 追加前后缀。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "提示词": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "接 K2V3GeneratorV2 的「提示词」输出；"
                               "也可直接在本框里键入/改写。"
                               "默认直通时原样输出，不做任何改动。",
                }),
                "编辑模式": (MODES, {
                    "default": MODE_PASS,
                    "tooltip": "直通（不修改）= 输出与输入逐字一致；"
                               "节点内文本覆盖 = 用「手动文本」整体替换；"
                               "查找替换 = 按 JSON 键值对替换；"
                               "追加前后缀 = 在原文前后拼接。",
                }),
            },
            "optional": {
                "手动文本": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "仅「节点内文本覆盖」时使用，整体作为最终提示词。",
                }),
                "查找替换": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "仅「查找替换」时使用。JSON 写法："
                               '{"旧词":"新词","另一个旧词":"另一个新词"}。',
                }),
                "前缀": ("STRING", {"default": "",
                    "tooltip": "仅「追加前后缀」时使用，拼在原文最前面。"}),
                "后缀": ("STRING", {"default": "",
                    "tooltip": "仅「追加前后缀」时使用，拼在原文最后面。"}),
                "去除首尾空白": ("BOOLEAN", {"default": False,
                    "tooltip": "勾选后对**非直通**的输出做一次首尾空白清理"
                               "（直通模式永远不清理，保证逐字一致）。"}),
            },
            # 【v3.21】hidden 输入：把本节点**输出的最终文本**写进 PNG 元数据。
            #   为什么放在这个节点而不只是出题节点：本节点是链路上"最终文本"的
            #   出口 —— 用户若选了「节点内文本覆盖 / 查找替换 / 追加前后缀」，
            #   真正送进编码器的是这里的输出，元数据必须记这一份才叫"与运行一致"。
            #   两个节点都会注入，后跑的那个覆盖前者，结果始终等于最终文本。
            "hidden": {
                "extra_pnginfo": "EXTRA_PNGINFO",
                "prompt": "PROMPT",
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("提示词", "编辑报告")
    FUNCTION = "edit"
    CATEGORY = CATEGORY
    OUTPUT_TOOLTIPS = (
        "编辑后的提示词。默认直通时与输入逐字一致。",
        "本次编辑做了什么的说明（直通时说明未修改）。",
    )
    DESCRIPTION = (
        "提示词预览与编辑节点（接在出题节点之后）。\n"
        "· 默认直通：输出 = 输入，逐字一致，不改任何内容；\n"
        "· 可选：节点内文本覆盖 / 查找替换 / 追加前后缀；\n"
        "· 只处理已生成的字符串，不参与出题 / 权重 / 随机逻辑。"
    )

    def edit(self, 提示词="", 编辑模式="", 手动文本="", 查找替换="",
             前缀="", 后缀="", 去除首尾空白=False,
             extra_pnginfo=None, prompt=None, **kw):
        src = 提示词 or ""
        mode = 编辑模式 or MODE_PASS
        notes = []

        if mode == MODE_PASS:
            out = src
            notes.append("直通：未做任何修改，输出与输入逐字一致。")
        elif mode == MODE_REPLACE:
            out = 手动文本 or ""
            notes.append("节点内文本覆盖：已用「手动文本」整体替换。")
        elif mode == MODE_FIND:
            out = src
            pairs = {}
            try:
                d = json.loads(查找替换) if (查找替换 or "").strip() else {}
                if isinstance(d, dict):
                    pairs = {str(k): str(v) for k, v in d.items()}
            except Exception:
                notes.append("查找替换 JSON 解析失败，已按未修改处理。")
            applied = 0
            for old, new in pairs.items():
                if old and old in out:
                    out = out.replace(old, new)
                    applied += 1
            notes.append("查找替换：应用 %d 处。" % applied)
        elif mode == MODE_AFFIX:
            out = (前缀 or "") + src + (后缀 or "")
            notes.append("追加前后缀：已拼接前缀/后缀。")
        else:  # 未知模式兜底：直通
            out = src
            notes.append("未知模式，按直通处理。")

        if 去除首尾空白 and mode != MODE_PASS:
            before = len(out)
            out = out.strip()
            if len(out) != before:
                notes.append("已去除首尾空白。")

        # 【v3.21】把**最终输出的这一份文本**写进 PNG 元数据。
        #   ⚠️ 放在所有编辑动作之后、return 之前 —— 记的必须是真正送出去的那一份。
        #   注入逻辑本身永不抛异常（元数据写不进去不该影响出图）。
        PM.inject_metadata(extra_pnginfo, prompt, out)

        return {"ui": {"text": [out]},
                "result": (out, "；".join(notes))}


NODE_CLASS_MAPPINGS = {"K2V3PromptEdit": K2V3PromptEdit}
NODE_DISPLAY_NAME_MAPPINGS = {"K2V3PromptEdit": "★ K2V3 提示词预览与编辑"}
