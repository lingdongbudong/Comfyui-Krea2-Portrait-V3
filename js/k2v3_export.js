import { app } from "../../scripts/app.js";

// K2V3PromptExport 前端面板
//
// 职责只有两件事，全部属于展示层：
//   ① 把「提示词」输入框放大 —— 这个节点的用法之一就是「直接键入文本当笔记用」；
//   ② 回显后端返回的落盘结果（文件路径 + 内容），并给一个复制按钮。
//
// 不参与、不读取、不写入任何出题状态。删掉本文件节点照常工作，
// 只是少了个好看的预览框。
const NODE_NAME = "K2V3PromptExport";

app.registerExtension({
    name: "K2V3.PromptExport.Panel",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onCreated?.apply(this, arguments);

            // ---- ① 放大提示词输入框 ----
            const w = (this.widgets || []).find((x) => x.name === "提示词");
            if (w) {
                w.computeSize = function () { return [460, 170]; };
                if (w.inputEl && w.inputEl.style) {
                    w.inputEl.style.minHeight = "150px";
                    w.inputEl.style.fontFamily = "ui-monospace,Consolas,monospace";
                    w.inputEl.style.fontSize = "12px";
                }
            }

            // ---- ② 结果回显框 + 复制按钮 ----
            const wrap = document.createElement("div");
            wrap.style.cssText = "display:flex;flex-direction:column;gap:5px;width:100%;";

            const bar = document.createElement("div");
            bar.style.cssText = ("display:flex;align-items:center;gap:6px;"
                + "font-size:11px;opacity:.85;");

            const label = document.createElement("span");
            label.textContent = "导出结果";
            bar.appendChild(label);

            const btn = document.createElement("button");
            btn.textContent = "📋 复制正文";
            btn.style.cssText = ("margin-left:auto;cursor:pointer;font-size:11px;"
                + "padding:2px 10px;border-radius:6px;border:1px solid #8b8b8b;"
                + "background:transparent;color:inherit;");
            bar.appendChild(btn);

            const box = document.createElement("textarea");
            box.readOnly = true;
            box.style.cssText = ("width:100%;min-height:150px;resize:vertical;"
                + "font-family:ui-monospace,Consolas,monospace;font-size:11px;"
                + "line-height:1.55;padding:7px 9px;border-radius:8px;"
                + "border:1px solid #6a6a6a;background:transparent;color:inherit;"
                + "white-space:pre;overflow:auto;");
            box.value = "（执行一次后显示保存路径与内容）";

            // 复制的是"正文"，不是整个回显（回显头几行是文件路径，粘出去是噪音）
            btn.onclick = () => {
                const txt = this._k2v3_export_text || "";
                if (!txt) {
                    btn.textContent = "无内容";
                    setTimeout(() => { btn.textContent = "📋 复制正文"; }, 1200);
                    return;
                }
                navigator.clipboard.writeText(txt).then(() => {
                    btn.textContent = "已复制 ✓";
                    setTimeout(() => { btn.textContent = "📋 复制正文"; }, 1200);
                }).catch(() => {
                    // 剪贴板被浏览器策略挡住时退化成选中，用户可手按 Ctrl+C
                    box.focus();
                    box.select();
                    btn.textContent = "已选中，Ctrl+C";
                    setTimeout(() => { btn.textContent = "📋 复制正文"; }, 1600);
                });
            };

            wrap.appendChild(bar);
            wrap.appendChild(box);
            this._k2v3_export_out = box;
            this._k2v3_export_text = "";

            this.addDOMWidget("k2v3_export_out", "out", wrap, {
                serialize: false,
                hideOnZoom: false,
                getValue() { return ""; },
                setValue() { },
            });

            this.size[0] = Math.max(this.size[0], 500);
            this.size[1] = Math.max(this.size[1], 460);
        };

        // ---- 执行完回显 ----
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            const box = this._k2v3_export_out;
            if (!box) return;
            const arr = message && message["k2v3_export"];
            if (!arr || !arr.length) return;
            box.value = arr[0];

            // 复制用的"正文"由后端单独回一路（k2v3_export_text），
            // 前端不去猜回显文本里哪一段是提示词 —— 提示词本身可能含空行，
            // 按空行切分一定会在那种情况下切错。
            const pure = message && message["k2v3_export_text"];
            this._k2v3_export_text = (pure && pure.length) ? pure[0] : "";
        };
    },
});
