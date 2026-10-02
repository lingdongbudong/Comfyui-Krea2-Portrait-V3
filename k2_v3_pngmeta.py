# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-Portrait-V3 :: 把正向提示词写进 PNG 元数据（v3.21 新增模块）

⚠️ v3.24 起插件**不再自带保存图片节点**（定位收敛为「只出提示词」）。本模块保留，
   由出题 / 提示词预览与编辑节点在运行时调用：把正文注入 `extra_pnginfo`，
   让**下游的原生 SaveImage** 照样能把它写进 PNG。

作者：灵冻不冻 ｜ 插件底层框架源于脚本：K2_V3_融合版.html（HTML 版人像提示词生成器）已授权开源 —— 作者：飞蓬。


【为什么要这个模块】
用户报障：用装了本插件的工作流出图后，看图工具里这张图的「提示词」不完整，
只显示成 `bl00m,`。解剖图片后确认 —— **不是被截断，是完整提示词一个字都没进元数据**：

    PNG 里只有 prompt(API JSON) + workflow(UI JSON) 两个原生键；
    扫描全文：「纵向构图」「中焦镜头」等提示词片段 workflow=False prompt=False；
    最长字符串是 LoRA 文本（406/850 字）。

根因：这条链路里所有承载文本的 widget 都处于「widget 已被转成输入连线」的状态，
`widgets_values` 里全是空串：

    CLIPTextEncode#1133           [""]              ← text 是连线(← StringConcatenate)
    StringConcatenate#1135        ["", "", ", "]    ← string_a / string_b 都是连线
    PrimitiveStringMultiline#1004 [""]              ← value 是连线(← K2V3PromptEdit)
    K2V3PromptEdit#1281           ["", "直通", …]   ← 提示词是连线(← 出题节点)
    K2V3GeneratorV2#1277          43 个值，无一是正文
                                  （正文预览框是 serialize:false 的 DOM widget，
                                    因此**从不进 workflow 元数据**）

于是文本只在运行时内存里流动，**从未落到任何可序列化的位置**。看图工具拿不到，
就退而取了链上唯一"有字"的节点 —— LoRA 触发词 `bl00m` 加拼接分隔符 `", "`，
这正是它显示成 `bl00m,` 的来由。


【怎么修：注入 extra_pnginfo】
ComfyUI 里 `extra_data['extra_pnginfo']` 是**同一次执行中所有节点共享的同一个 dict**
（看 execution.py 的 `extra_data.get('extra_pnginfo', None)`），而原生 SaveImage 是在
**自己执行的那一刻**才 `json.dumps(extra_pnginfo["workflow"])`。所以插件节点只要在运行时
把文本写进这个 dict，SaveImage 就会照常把它写进 PNG：

    ✅ 当次出图就生效
    ✅ 不用改用户的工作流（一根线都不用动）
    ✅ 不碰任何第三方节点
    ✅ 完全不影响出图结果

另外，新版前端（ComfyUI 0.37）除了 `widgets_values` 还写了 `widgets_values_named`，
**两份都要改** —— 只改前者会出现"两套值不一致"，而前端优先读后者。


【安全边界（很重要，改这个文件前务必读完）】

· `workflow`（UI JSON）**只用于序列化展示**，ComfyUI 从不用它跑图 → 随便写，零风险。

· `prompt`（API JSON）**就是执行用的那张图**：`DynamicPrompt.get_node()` 会惰性读取它，
  所以改 `inputs` 里**已声明的输入**（比如 CLIPTextEncode 的 `text`）会真的改变执行结果。
  因此我们**只写"惰性键"** —— 在节点 `inputs` 下加一个 `k2v3_prompt`。依据：
  execution.py 的 `get_input_data` 是 `for x in inputs:` 逐个查 `get_input_info()`，
  未声明的输入名拿到 `input_category = None`，**不会进 `input_data_all`**，执行时静默忽略。

  ⚠️ 这是刻意取舍：CLIPTextEncode 的 `inputs.text` 保持连线、**不改成字面量** ——
  它的真实取值由第三方节点（LoRA 触发词拼接）决定，插件无从得知；改成字面量会把
  触发词丢掉，直接改变出图。宁可少写一个键，也不能改坏图。


【对接方式】
节点侧只需声明 hidden 输入 `extra_pnginfo`，拿到后调用：

    from .k2_v3_pngmeta import inject_metadata
    inject_metadata(extra_pnginfo, prompt_dict, text)

本模块只依赖标准库，纯逻辑环境下也能 import 与单测。
"""

import json

# 写进 API prompt 的惰性键名（执行的节点不会读到它，见上面「安全边界」）
INJECT_KEY = "k2v3_prompt"


# ---------------------------------------------------------------------------
# 节点类型表
# ---------------------------------------------------------------------------

# 插件自己的节点里，「第一个 widget 就是提示词文本」的那些 —— 写进去是安全的。
#
# ⚠️ K2V3GeneratorV2 **故意不在表里**：它的 widgets_values[0] 是「内容等级」，
#    把提示词写进去等于改坏出题参数（下次打开工作流会看到内容等级变成一大段中文）。
OWN_TEXT_WIDGET = {
    "K2V3PromptEdit": "提示词",
    "K2V3PromptExport": "提示词",
}

# 插件节点上「提示词」这个输出端口的索引（其余端口不是文本，别乱跟）
OWN_TEXT_OUTPUT_SLOT = {
    "K2V3GeneratorV2": 0,      # (提示词, 自检报告, 槽位JSON, 随机种子, 元数据JSON, 候选列表)
    "K2V3PromptEdit": 0,       # (提示词, 编辑报告)
    "K2V3PromptExport": 1,     # (文件路径, 提示词)
}

# 每个节点类型：widget 名 → 在 `widgets_values` 里的位置索引。
# 同时充当"白名单"：**表里没有的输入名一律不写**（不猜、不碰别人的控件）。
WIDGET_INDEX = {
    "CLIPTextEncode": {"text": 0},
    "K2V3PromptEdit": {"提示词": 0},
    "K2V3PromptExport": {"提示词": 0},
    "PrimitiveStringMultiline": {"value": 0},
    "PrimitiveString": {"value": 0},
    "String": {"value": 0},
    "StringConcatenate": {"string_a": 0, "string_b": 1, "delimiter": 2},
}

# 哪些输入名是可以"继续往下传"的（delimiter 不在内 —— 我们的文本不会流进分隔符）
PROPAGATE_INPUTS = {"value", "string_a", "string_b", "提示词"}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _node_index(workflow):
    out = {}
    for n in (workflow.get("nodes") or []):
        if isinstance(n, dict) and n.get("id") is not None:
            out[n["id"]] = n
    return out


def _link_maps(workflow):
    """返回 (下游表, 上游表)。

    下游表：(起点节点 id, 起点槽位) -> [(终点节点 id, 终点槽位), …]
    上游表：link_id -> (起点节点 id, 起点槽位)
    """
    downstream, src = {}, {}
    for L in (workflow.get("links") or []):
        if not isinstance(L, (list, tuple)) or len(L) < 5:
            continue
        downstream.setdefault((L[1], L[2]), []).append((L[3], L[4]))
        src[L[0]] = (L[1], L[2])
    return downstream, src


def _input_name_at(node, slot):
    ins = node.get("inputs") or []
    if isinstance(slot, int) and 0 <= slot < len(ins) and isinstance(ins[slot], dict):
        return ins[slot].get("name")
    return None


def _set_widget(node, widget_name, value):
    """按 widget 名把值写进 `widgets_values` 与 `widgets_values_named`。

    返回 True 表示确实改了东西（用来统计"写了几处"，方便自检与排障）。
    """
    changed = False
    idx = (WIDGET_INDEX.get(node.get("type")) or {}).get(widget_name)
    wv = node.get("widgets_values")
    if isinstance(wv, list) and idx is not None:
        # 短了就补齐：ComfyUI 加载时按索引取，缺位会当成 undefined
        while len(wv) <= idx:
            wv.append(None)
        if wv[idx] != value:
            wv[idx] = value
            changed = True
    wvn = node.get("widgets_values_named")
    if isinstance(wvn, dict) and widget_name in wvn and wvn[widget_name] != value:
        wvn[widget_name] = value
        changed = True
    return changed


# ---------------------------------------------------------------------------
# 对外：注入
# ---------------------------------------------------------------------------

def inject_workflow_text(workflow, text):
    """把 `text` 写进 UI 工作流（`workflow` 键）里所有该有它的位置。

    写三处：
      ① 插件自己的节点（K2V3PromptEdit / K2V3PromptExport）的「提示词」框；
      ② 顺连线往下，中间那些"只是把文本搬过去"的 widget
         （PrimitiveString* 的 value、StringConcatenate 的 string_a/string_b）
         —— 补上它们，只读 widget 值、自己逐段拼链的看图工具也才能拼出完整串；
      ③ 喂给采样器 `positive` 的 CLIPTextEncode 的 text。

    ⚠️ 这里写的是**插件产出的正向提示词正文**。若工作流在它后面又拼了别的东西
       （例如 LoRA 触发词 `bl00m`），那段属于第三方节点、插件无从得知；
       但如果看图工具会自己顺链拼（它连 `bl00m` 都认得出），补上 ② 之后
       它拼出来的就是**与实际运行文本逐字一致**的整串。

    返回统计 dict（ok / written / encoders），供自检报告与排障用。
    """
    st = {"ok": False, "written": [], "encoders": 0, "reason": ""}
    if not isinstance(workflow, dict):
        st["reason"] = "没有 workflow 元数据"
        return st
    if not text:
        st["reason"] = "提示词为空"
        return st
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        st["reason"] = "workflow 里没有 nodes"
        return st

    by_id = _node_index(workflow)
    downstream, src = _link_maps(workflow)
    written = []

    def write(node, name):
        if not isinstance(node, dict):
            return False
        if (WIDGET_INDEX.get(node.get("type")) or {}).get(name) is None:
            return False
        if _set_widget(node, name, text):
            written.append("%s#%s.%s" % (node.get("type"), node.get("id"), name))
            return True
        return False

    # ---- ① 插件自己的节点 ----
    seeds = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        if t in OWN_TEXT_WIDGET:
            write(n, OWN_TEXT_WIDGET[t])
        if t in OWN_TEXT_OUTPUT_SLOT:
            seeds.append((n.get("id"), OWN_TEXT_OUTPUT_SLOT[t]))

    # ---- ② 顺链往下补中间 widget ----
    seen = set()
    stack = list(seeds)
    while stack:
        oid, oslot = stack.pop()
        for (tid, tslot) in downstream.get((oid, oslot), []):
            tn = by_id.get(tid)
            if not isinstance(tn, dict):
                continue
            iname = _input_name_at(tn, tslot)
            if iname not in PROPAGATE_INPUTS:
                continue                      # 白名单外一律不写
            key = (tid, iname)
            if key in seen:
                continue
            seen.add(key)
            write(tn, iname)
            # 值经过拼接后已不是纯 text，但继续往下走是为了让下游也被覆盖到
            stack.append((tid, 0))

    # ---- ③ 喂 positive 的 CLIPTextEncode ----
    encoders = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        for inp in (n.get("inputs") or []):
            if not isinstance(inp, dict) or inp.get("name") != "positive":
                continue
            if inp.get("link") is None:
                continue
            s = src.get(inp["link"])
            if not s:
                continue
            pn = by_id.get(s[0])
            if isinstance(pn, dict) and pn.get("type") == "CLIPTextEncode":
                encoders.add(pn.get("id"))
    for nid in encoders:
        write(by_id.get(nid), "text")
    st["encoders"] = len(encoders)

    st["written"] = written
    st["ok"] = bool(written)
    if not written:
        st["reason"] = "没找到可写的位置"
    return st


def inject_api_prompt(prompt, text):
    """把 `text` 写进 API 格式的 prompt（`prompt` 键）—— **只写惰性键**。

    ⚠️ 绝不改动 `inputs` 里已声明的输入（那会改变执行结果，见文件头的「安全边界」）。

    返回统计 dict（ok / written）。
    """
    st = {"ok": False, "written": []}
    if not isinstance(prompt, dict) or not text:
        return st

    targets = set()
    for nid, nd in prompt.items():
        if not isinstance(nd, dict):
            continue
        # ① 插件自己的节点
        if (nd.get("class_type") or "") in OWN_TEXT_OUTPUT_SLOT:
            targets.add(nid)
        # ② 喂 positive 的 CLIPTextEncode（挂在它身上，方便只翻这个节点的工具看到）
        ins = nd.get("inputs")
        if not isinstance(ins, dict):
            continue
        pos = ins.get("positive")
        if isinstance(pos, (list, tuple)) and len(pos) >= 1:
            up = prompt.get(str(pos[0]))
            if isinstance(up, dict) and up.get("class_type") == "CLIPTextEncode":
                targets.add(str(pos[0]))

    for nid in targets:
        ins = prompt[nid].setdefault("inputs", {})
        if ins.get(INJECT_KEY) != text:
            ins[INJECT_KEY] = text
            st["written"].append("%s#%s" % (prompt[nid].get("class_type"), nid))
    st["ok"] = bool(st["written"])
    return st


def inject_metadata(extra_pnginfo, prompt, text):
    """节点侧的**唯一入口**：把提示词写进 `workflow` 与 `prompt` 两处元数据。

    · `extra_pnginfo` 是 hidden 输入 EXTRA_PNGINFO（同一次执行内共享的 dict）；
    · `prompt` 是 hidden 输入 PROMPT（同一份 API 图）；
    · 两者都可能是 None（例如用 API 方式提交、没带 extra_data），此时静默跳过。

    ⚠️ 本函数**永不抛异常** —— 元数据写不进去是小事，把出图搞挂是大事。
    """
    st = {"workflow": None, "prompt": None}
    try:
        if isinstance(extra_pnginfo, dict):
            st["workflow"] = inject_workflow_text(extra_pnginfo.get("workflow"), text)
    except Exception as e:                     # pragma: no cover
        st["workflow"] = {"ok": False, "reason": "异常：%s" % e}
    try:
        st["prompt"] = inject_api_prompt(prompt, text)
    except Exception as e:                     # pragma: no cover
        st["prompt"] = {"ok": False, "reason": "异常：%s" % e}
    return st


def merge_metadata_stats(extra_pnginfo, prompt, text, report_head=None):
    """注入 + 把结果写进自检报告头（人能在报告里直接看到"元数据写没写进去"）。

    返回注入统计；`report_head` 给了就顺手 append 一行说明。
    """
    st = inject_metadata(extra_pnginfo, prompt, text)
    if report_head is None:
        return st
    wf = st.get("workflow") or {}
    pr = st.get("prompt") or {}
    if wf.get("ok") or pr.get("ok"):
        report_head.append(
            "元数据：已写入提示词（工作流 %d 处%s；API 惰性键 %d 处）"
            % (len(wf.get("written") or []),
               "· 正向编码 %d 个" % wf["encoders"] if wf.get("encoders") else "",
               len(pr.get("written") or [])))
    else:
        report_head.append(
            "⚠ 元数据：本次未能写入提示词（%s）—— 用 API 提交、或工作流里"
            "没有可写的编码节点时会这样，不影响出图。"
            % (wf.get("reason") or pr.get("reason") or "无 extra_pnginfo"))
    return st



def dump_workflow_prompt(workflow):
    """从 workflow 里读回"当前写在元数据里的正向提示词"（校验脚本用）。

    按 ③②① 的优先级取第一个非空的：正向编码节点 → 插件节点 → 插件节点输入框。
    注：这是**读**，只用于校验，不参与出图。
    """
    if not isinstance(workflow, dict):
        return ""
    by_id = _node_index(workflow)
    _, src = _link_maps(workflow)

    def val(node, name):
        idx = (WIDGET_INDEX.get(node.get("type")) or {}).get(name)
        wvn = node.get("widgets_values_named")
        if isinstance(wvn, dict) and isinstance(wvn.get(name), str) and wvn[name]:
            return wvn[name]
        wv = node.get("widgets_values")
        if isinstance(wv, list) and idx is not None and idx < len(wv):
            return wv[idx] if isinstance(wv[idx], str) else ""
        return ""

    # ③ 正向编码
    for n in (workflow.get("nodes") or []):
        if not isinstance(n, dict):
            continue
        for inp in (n.get("inputs") or []):
            if not isinstance(inp, dict) or inp.get("name") != "positive":
                continue
            if inp.get("link") is None:
                continue
            s = src.get(inp["link"])
            pn = by_id.get(s[0]) if s else None
            if isinstance(pn, dict) and pn.get("type") == "CLIPTextEncode":
                v = val(pn, "text")
                if v:
                    return v
    # ② / ① 插件自己的节点
    for n in (workflow.get("nodes") or []):
        if not isinstance(n, dict):
            continue
        name = OWN_TEXT_WIDGET.get(n.get("type"))
        if name:
            v = val(n, name)
            if v:
                return v
    return ""


__all__ = ["INJECT_KEY", "inject_workflow_text", "inject_api_prompt",
           "inject_metadata", "merge_metadata_stats",
           "dump_workflow_prompt", "json"]
