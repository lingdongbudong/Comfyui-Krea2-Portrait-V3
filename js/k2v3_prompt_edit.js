import { app } from "../../scripts/app.js";

// K2V3PromptEdit 前端：把「提示词」输入框放大成可读可写的大文本域，
// 并回显编辑后的输出。纯展示层，不参与任何出题逻辑。
const NODE_NAME = "K2V3PromptEdit";

app.registerExtension({
    name: "K2V3.PromptEdit.Panel",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onCreated?.apply(this, arguments);
            const w = (this.widgets || []).find((x) => x.name === "提示词");
            if (w) {
                w.computeSize = function () { return [560, 200]; };
                if (w.inputEl && w.inputEl.style) {
                    w.inputEl.style.minHeight = "180px";
                    w.inputEl.style.fontFamily = "ui-monospace,Consolas,monospace";
                    w.inputEl.style.fontSize = "12px";
                }
            }
            // 「手动文本」也给点高度，方便粘贴改写
            const m = (this.widgets || []).find((x) => x.name === "手动文本");
            if (m && m.inputEl && m.inputEl.style) {
                m.inputEl.style.minHeight = "100px";
                m.inputEl.style.fontFamily = "ui-monospace,Consolas,monospace";
            }
            this.size[0] = Math.max(this.size[0], 580);
            this.size[1] = Math.max(this.size[1], 360);
        };
    },
});
