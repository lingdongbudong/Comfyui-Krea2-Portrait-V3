/* ==========================================================================
 * K2V3Generator 前端面板（Comfyui-Krea2-Portrait 扩展）
 *
 * 为「K2 V3 融合版 · 随机出题」节点提供：
 *   ① 角色图鉴 —— 缩略图列表 + 作品分类快速筛选 + 搜索 + 点选预览（复用
 *      /krea2-v3/characters 的同一份 2000+ 角色数据，体验对齐 Krea2PortraitRoll）
 *   ② 角色预设 —— 保存 / 加载（切换）/ 删除（走 /k2v3/presets）
 *   ③ 选中预览 —— 节点内嵌当前角色的缩略图与属性条
 *   ④ 执行后把「提示词」与「自检报告」回显到面板（贴合原节点的报告区交互）
 * ========================================================================== */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// ⚠️ V2 改名：避免与 V1 的同名节点在 ComfyUI 里互相覆盖（见 __init__.py 说明）
const NODE_NAME = "K2V3GeneratorV2";

/* ---------- 最小 DOM 辅助 ---------- */
function h(tag, style, text) {
    const el = document.createElement(tag);
    if (style) el.style.cssText = style;
    if (text !== undefined) el.textContent = text;
    return el;
}

/* ---------- 缩略图 URL ----------
 * 【v3.16】角色的 img 现在存的是**本站相对路径**（/krea2-v3/thumb/...），
 * 图由 ComfyUI 自己发 —— 与出图服务同源，国内网络 / 断网都能显示。
 *
 * 三件事要处理：
 *   ① 老数据里可能还留着 http 开头的远程链接 → 原样返回，兼容；
 *   ② ComfyUI 若挂在子路径下（--base-path），绝对路径会 404
 *     → 优先用 api.apiURL() 补前缀（该函数在部分打包版里不存在，故做存在性判断）；
 *   ③ 空值返回空串，调用方据此决定**根本不创建 <img>** ——
 *      避免为空 src 发一个注定失败的请求（图鉴里有上千个角色）。
 */
function thumbURL(path) {
    if (!path) return "";
    if (/^https?:/i.test(path)) return path;
    try {
        if (typeof api !== "undefined" && api && typeof api.apiURL === "function") {
            return api.apiURL(path);
        }
    } catch (e) { /* 忽略：退回原路径 */ }
    return path;
}

/* ---------- 主题自适应（浅色/深色，读画布亮度） ---------- */
function brightness() {
    try {
        const c = document.querySelector("canvas") || document.body;
        const rgb = getComputedStyle(c).backgroundColor || "rgb(30,30,30)";
        const m = rgb.match(/\d+/g);
        if (m && m.length >= 3) return (Number(m[0]) + Number(m[1]) + Number(m[2])) / 3;
    } catch (e) { /* noop */ }
    return 30;
}

function palette() {
    const light = brightness() > 140;
    return light
        ? { bg: "#f3f4f6", panel: "#ffffff", card: "#ffffff", cardHover: "#f0f1f3",
            text: "#1f2430", sub: "#6b7280", accent: "#4a5cff", ok: "#1f7a48",
            err: "#d84b4b", warn: "#b05f12", border: "#d9dce6" }
        : { bg: "#121216", panel: "#1c1c24", card: "#23232e", cardHover: "#2c2c3a",
            text: "#ececf2", sub: "#a6a6b8", accent: "#7a8bff", ok: "#7ec98b",
            err: "#e07070", warn: "#e0a458", border: "#33333f" };
}

function cardStyle(on, p, radius) {
    const r = radius === undefined ? 8 : radius;
    return `background:linear-gradient(180deg,${on ? p.accent + "22" : p.cardHover},${p.card});
        border:1px solid ${on ? p.accent : p.border};border-radius:${r}px;`;
}

function liftable(el) {
    el.onmouseenter = () => { el.style.boxShadow = "0 4px 14px rgba(0,0,0,.25)"; };
    el.onmouseleave = () => { el.style.boxShadow = "none"; };
}

/* ---------- 控件读写 ---------- */
function getWidget(node, name) {
    return node.widgets ? node.widgets.find((w) => w.name === name) : null;
}

function setWidgetVal(node, name, value) {
    const w = getWidget(node, name);
    if (!w) return false;
    w.value = value;
    if (w.inputEl) w.inputEl.value = value;
    if (w.callback) { try { w.callback(value, app.canvas, node, null, null); } catch (e) { /* noop */ } }
    return true;
}

function getVal(node, name) {
    const w = getWidget(node, name);
    return w ? w.value : "";
}

/* ---------- 数据接口 ---------- */
let charsCache = null;
let charsPromise = null;
function fetchCharacters() {
    if (charsCache) return Promise.resolve(charsCache);
    if (!charsPromise) {
        charsPromise = api.fetchApi("/krea2-v3/characters")
            .then((r) => r.json()).then((d) => { charsCache = d; return d; })
            .catch((e) => { charsPromise = null; throw e; });
    }
    return charsPromise;
}

function fetchPresets() {
    return api.fetchApi("/krea2-v3/presets").then((r) => r.json());
}

function savePreset(name, character, overrides, note, weights, params) {
    return api.fetchApi("/krea2-v3/presets", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, character, overrides, note, weights, params }),
    }).then((r) => r.json());
}

/* ---------- 【v3.18】NSFW 内容项（展示生殖器 / 自慰 / 口交 / 性交 / 其它）----------
 * 与后端 k2_v3_node.ACT_WEIGHT_CTL 一一对应（控件名 = 内容项名 + "权重"）。
 * 这里只写"当前有哪几项"；**每项的档位门槛与当前档位下的可用集合，
 * 一律以 /krea2-v3/extra 返回的 act_mins / act_level_zh 为准**，不在前端猜。
 */
const ACT_CTL = {
    "展示生殖器": "展示生殖器权重",
    "自慰": "自慰权重",
    "潮吹·失禁": "潮吹·失禁权重",
    "手交": "手交权重",
    "乳交": "乳交权重",
    "足交": "足交权重",
    "口交": "口交权重",
    "深喉": "深喉权重",
    "性交": "性交权重",
    "肛交": "肛交权重",
    "束缚·捆绑": "束缚·捆绑权重",
    "多人": "多人权重",
    "其它": "其它权重",
};
// 【v3.19】上限 2.0 → 5.0（内容项多了，要能拉开"只想要某一项"这种强偏好）。
// 具体数值以 /krea2-v3/extra 返回的 act_min / act_max / act_step 为准，
// 这里只是取不到接口时的兜底。
const ACT_W_MIN = 0.0, ACT_W_MAX = 5.0, ACT_W_STEP = 0.05, ACT_W_DEFAULT = 1.0;

/** 【v3.19】「内容权重 · 跟随等级」开关的控件名（与后端 ACT_AUTO_CTL 一致） */
const ACT_AUTO_CTL = "内容权重跟随等级";

/** 【v3.13】方案快照：把会影响出题、但又不是"锁定字段"的那些控件一起存下来
 *  【v3.17】补上「第二角色」（双人第二位角色）。
 *  【v3.18】去掉「内容等级 / 内容边界 / NSFW开关 / NSFW强度」（已退休控件，
 *  存了也不会被读取），换成「内容强度」+ 五个 NSFW 内容项权重。
 *  ⚠️ 快照只认**当前有效**的控件 —— 存一堆退休控件的值只会让人误以为它们还起作用。
 */
const SCHEME_KEYS = ["内容强度", "人物数量",
    "百合模式", "纹身开关", "纹身权重", "纹身面积", "角色联动", "联动强度",
    "去重记忆", "多样性温度", "关闭可选项", "批次数", "第二角色",
    // 【v3.20】外观锁定也影响出题（同一角色外观会不一样），必须进快照
    "角色外观锁定",
    ACT_AUTO_CTL,
    "展示生殖器权重", "自慰权重", "潮吹·失禁权重", "手交权重", "乳交权重",
    "足交权重", "口交权重", "深喉权重", "性交权重", "肛交权重",
    "束缚·捆绑权重", "多人权重", "其它权重",
    // 【v3.22】鞋履种类权重 + 水面波光权重也影响出题，必须进快照
    "高跟鞋权重", "靴子权重", "运动鞋权重", "凉鞋权重", "皮鞋权重",
    "赤脚权重", "水面反光权重"];
function snapshotParams(node) {
    const o = {};
    SCHEME_KEYS.forEach((k) => { const w = getWidget(node, k); if (w) o[k] = w.value; });
    return o;
}
function applyParams(node, params) {
    if (!params || typeof params !== "object") return;
    Object.keys(params).forEach((k) => {
        if (SCHEME_KEYS.indexOf(k) >= 0 && getWidget(node, k)) setWidgetVal(node, k, params[k]);
    });
}

function removePreset(name) {
    return api.fetchApi("/krea2-v3/presets/remove", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
    }).then((r) => r.json());
}

/* ---------- 等级 / 权重数据（对齐选择器 v3.8.0 的命名与取值范围） ---------- */
let levelsCache = null;
function fetchLevels() {
    if (levelsCache) return Promise.resolve(levelsCache);
    return api.fetchApi("/krea2-v3/levels").then((r) => r.json())
        .then((d) => { levelsCache = d; return d; });
}

let axesCache = null;
function fetchAxes() {
    if (axesCache) return Promise.resolve(axesCache);
    return api.fetchApi("/krea2-v3/axes").then((r) => r.json())
        .then((d) => { axesCache = d; return d; });
}

/* ---------- v3.11：纹身面积 / 人物数量 / 百合分级说明 ---------- */
let extraCache = null;
/** 【v3.19】最近一次 /extra 的响应 —— ⑦ 区的自动基准表（act_auto_table）从这里取 */
let extraData = null;
function fetchExtra() {
    if (extraCache) return Promise.resolve(extraCache);
    return api.fetchApi("/krea2-v3/extra").then((r) => r.json())
        .then((d) => { extraCache = d; extraData = d; return d; })
        .catch(() => ({ ok: false, tattoo_areas: ["跟随随机"], tattoo_area_auto: "跟随随机",
            person_counts: ["单人", "双人"], person_default: "单人", grade_note: {} }));
}

/** 字段选项表（v3.13：逐字段重掷 / 逐项开关 用） */
let fieldsCache = null;
function fetchFields() {
    if (fieldsCache) return Promise.resolve(fieldsCache);
    return api.fetchApi("/krea2-v3/fields").then((r) => r.json())
        .then((d) => { fieldsCache = d; return d; })
        .catch(() => ({ ok: false, fields: {}, skippable: {}, skippable_zh: {} }));
}

/** 字段 id -> 中文名（面板显示用；不在表里的直接显示 id） */
const FIELD_ZH = {
    lens: "镜头焦段", viewpoint: "视角", shotSize: "景别", dof: "景深", device: "拍摄设备",
    mainLight: "主光", ambient: "环境光", colorTone: "色调",
    temperament: "气质", hairLen: "发长", hairColor: "发色", hairCurl: "卷度",
    hairTie: "扎法", hairBangs: "刘海", hairState: "发态", hairAcc: "发饰",
    makeup: "妆容", emotion: "情绪", eye: "眼神", mouth: "口部", body: "体型",
    firstImp: "第一印象", scene: "场景", prop: "道具", weather: "天气",
    comp: "构图", compPos: "构图位置", styleTag: "风格标签", film: "胶片感",
    cine: "电影感", tattoo: "纹身", socks: "袜类", shoes: "鞋履", accessory: "配饰",
    makeupDetail: "妆细", smudge: "晕妆", nails: "指甲",
    clothBottom: "下装", clothMat: "材质", clothPattern: "图案", clothDeco: "装饰",
    clothLayer: "层次", nsfwChain: "反应链", nsfwMod: "改造", nsfwFabric: "面料",
    nsfwBody: "身体表现", nsfwProp: "道具", nsfwShoes: "鞋袜",
    imperf1: "瑕疵一", imperf2: "瑕疵二",
};
function fieldZh(id) { return FIELD_ZH[id] || id; }

/** 读/写「锁定字段」JSON（容错：解析失败当空表） */
function readLocked(node) {
    try {
        const d = JSON.parse(getVal(node, "锁定字段") || "{}");
        return (d && typeof d === "object" && !Array.isArray(d)) ? d : {};
    } catch (e) { return {}; }
}
function writeLocked(node, obj) {
    const keys = Object.keys(obj || {});
    setWidgetVal(node, "锁定字段", keys.length ? JSON.stringify(obj) : "");
}


/** 「⑦ NSFW 内容权重」区的提示行（由 renderAct 重建、renderActHint 更新文字） */
let actHintBox = null;

/** 读内容项权重：取不到 / 非法值一律当 1.00（标准比例） */
function actWeightOf(node, ctl) {
    const raw = getVal(node, ctl);
    if (raw === "" || raw === null || raw === undefined) return ACT_W_DEFAULT;
    const v = Number(raw);
    return isNaN(v) ? ACT_W_DEFAULT : v;
}

/** 「内容强度」档位名 -> level 键。与后端 EX.INTENSITY_PRESETS 一致。 */
const INTENSITY_TO_LEVEL = {
    "全年龄": "sfw", "暗示": "suggestive", "露骨": "explicit",
    "强露骨": "hardcore", "极端": "extreme",
};

/** 当前生效档位。旧工作流的「内容强度」是「手动（分别设置）」→ 与后端一致，
 *  回落到旧的「内容等级」控件值（兼容桥，界面上看不到那个控件）。 */
function intLevelOf(node) {
    const name = String(getVal(node, "内容强度") || "");
    if (INTENSITY_TO_LEVEL[name]) return INTENSITY_TO_LEVEL[name];
    return String(getVal(node, "内容等级") || "sfw");
}

/** v3.11 三个控件的安全读取：取不到就退回"不干预/默认"值
 *
 * 【v3.17】这里同时镜像后端的「双人 + NSFW 档 → 自动百合」规则：
 *   前端要如实显示"现在到底走的是哪条池"，否则用户会以为开关坏了。
 *   档位映射与后端 LEVEL_TO_MODE 一致：sfw / suggestive → SFW，其余 → NSFW。
 */
function v311(node) {
    const pc = getVal(node, "人物数量");
    const area = getVal(node, "纹身面积");
    const person = pc || "单人";
    const double = person !== "单人";
    const yuriManual = !!getVal(node, "百合模式");
    // 【v3.18】档位只认「内容强度」（旧工作流回落「内容等级」，与后端同一口径）
    const lv = intLevelOf(node);
    const nsfw = !(lv === "sfw" || lv === "suggestive");
    const yuriAuto = double && nsfw;
    return {
        person: person,
        double: double,
        yuriManual: yuriManual,
        yuri: yuriManual || yuriAuto,     // 生效值（含自动百合）
        yuriAuto: yuriAuto,               // 是否由「双人 + NSFW」自动触发
        area: area || "跟随随机",
        charA: getVal(node, "角色") || "自动",
        charB: getVal(node, "第二角色") || "自动",
        // 【v3.18】当前档位的 level 键（前端用它判断"哪些内容项还没到档位"）
        level: lv,
    };
}

/** 【v3.18】把五个内容项权重收成一个对象（报告/摘要/回显共用） */
function actWeightsOf(node) {
    const out = {};
    Object.keys(ACT_CTL).forEach((a) => { out[a] = actWeightOf(node, ACT_CTL[a]); });
    return out;
}

/** 解析「权重覆盖」控件里的写法 -> {轴名或选项名: 倍数} */
function parseSpec(spec) {
    const out = {};
    String(spec || "").split(/[,，;；]/).forEach((p) => {
        p = p.trim();
        if (!p) return;
        let n, v;
        if (p.includes(":") || p.includes("：")) {
            const arr = p.split(/[:：]/); n = arr[0].trim(); v = arr[1];
        } else { n = p; v = "1"; }
        const num = parseFloat(v);
        if (n && !isNaN(num)) out[n] = num;
    });
    return out;
}

/* ---------- 弹窗基座 ---------- */
let modal = null;
let activeNode = null;

/* ---------- 【v3.20】放大查看：在弹窗之上再开一层 ----------
 * 为什么不复用 openModal：那一层是"设置/图鉴"的容器，高度固定、内容自己 scroll，
 * 而这里要的是一块**尽可能大的只读文本区**。开第二层（z-index 更高）最省事，
 * 也顺手做到了"放大查看时底层列表还在，关掉就回到原位"。
 */
let zoomLayer = null;

function closeZoom() {
    if (zoomLayer) { zoomLayer.remove(); zoomLayer = null; }
}

function openTextView(title, text, metaText) {
    closeZoom();
    const p = palette();
    zoomLayer = h("div", `position:fixed;inset:0;z-index:10000;display:flex;
        align-items:center;justify-content:center;background:rgba(0,0,0,.55);`);
    const box = h("div", `width:min(880px,94vw);height:min(88vh,880px);display:flex;
        flex-direction:column;background:${p.panel};border:1px solid ${p.border};
        border-radius:14px;overflow:hidden;box-shadow:0 24px 70px rgba(0,0,0,.5);`);
    const head = h("div", `display:flex;align-items:center;gap:9px;
        padding:12px 16px;border-bottom:1px solid ${p.border};`);
    head.appendChild(h("div", `flex:1;min-width:0;font-size:14px;font-weight:700;
        color:${p.text};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`, title));
    const bCopy = mkBtn("📋 复制全文", p, true);
    bCopy.onclick = () => copyText(ta.value, bCopy, "📋 复制全文");
    head.appendChild(bCopy);
    const close = h("button", `border:1px solid ${p.border};background:transparent;
        color:${p.sub};border-radius:8px;padding:4px 12px;cursor:pointer;font-size:13px;`, "关闭");
    close.onclick = closeZoom;
    head.appendChild(close);
    box.appendChild(head);

    const body = h("div", `flex:1;display:flex;flex-direction:column;padding:12px 16px;
        min-height:0;`);
    if (metaText) {
        const mt = h("div", `font-size:11px;color:${p.sub};line-height:1.7;
            margin-bottom:9px;user-select:text;`, metaText);
        body.appendChild(mt);
    }
    // ⚠️ 用可编辑的 textarea（不是只读 div）：用户往往要"看一眼 → 顺手改两个字 → 复制"，
    //    只读的话这一步就得再绕一回。改这里不会回写节点，纯属临时编辑。
    const ta = document.createElement("textarea");
    ta.value = text || "";
    ta.style.cssText = `flex:1;min-height:0;width:100%;box-sizing:border-box;
        resize:none;background:${p.bg};border:1px solid ${p.border};border-radius:10px;
        padding:11px 13px;font-size:12.5px;line-height:1.75;color:${p.text};
        font-family:ui-monospace,Consolas,monospace;outline:none;`;
    body.appendChild(ta);
    body.appendChild(h("div", `font-size:10.5px;color:${p.sub};margin-top:7px;line-height:1.6;`,
        "这里可以直接改文字再复制（不会写回节点）。字数：" 
        + (text || "").length + " 字符。"));
    box.appendChild(body);
    zoomLayer.appendChild(box);
    zoomLayer.onclick = (e) => { if (e.target === zoomLayer) closeZoom(); };
    document.body.appendChild(zoomLayer);
    try { ta.focus(); } catch (e) { }
}

function closeModal() {
    closeZoom();   // 【v3.20】放大层是叠在弹窗之上的第二层，关弹窗时一并收掉
    if (modal) { modal.remove(); modal = null; activeNode = null; }
}

function openModal(node, title, build) {
    closeModal();
    activeNode = node;
    const p = palette();
    modal = h("div", `position:fixed;inset:0;z-index:9999;display:flex;
        align-items:center;justify-content:center;background:rgba(0,0,0,.45);`);
    const box = h("div", `width:min(980px,94vw);height:min(86vh,820px);display:flex;
        flex-direction:column;background:${p.panel};border:1px solid ${p.border};
        border-radius:14px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.4);`);
    const head = h("div", `display:flex;align-items:center;justify-content:space-between;
        padding:12px 16px;border-bottom:1px solid ${p.border};`);
    head.appendChild(h("div", `font-size:15px;font-weight:700;color:${p.text};`, title));
    const close = h("button", `border:1px solid ${p.border};background:transparent;
        color:${p.sub};border-radius:8px;padding:4px 12px;cursor:pointer;font-size:13px;`, "关闭");
    close.onclick = closeModal;
    head.appendChild(close);
    const body = h("div", `flex:1;overflow:auto;padding:14px 16px;`);
    build(body, p, node);
    box.appendChild(head);
    box.appendChild(body);
    modal.appendChild(box);
    document.body.appendChild(modal);
}

/* ---------- 角色图鉴 ----------
 * 【v3.17】target 参数决定"点选写回哪个控件"：
 *   "角色"（默认）= 角色 A，正文主体的特征来源；
 *   "第二角色"   = 角色 B，只用于双人 / 百合段的点名与外观锚点。
 * 同一个图鉴复用两遍，不为双人再做一个面板。
 */
function openCharacters(node, target) {
    const key = target || "角色";
    const isB = key === "第二角色";
    openModal(node, "角色图鉴 · " + (isB ? "选给「角色 B（双人段）」" : "选给「角色 A（正文主体）」")
        + " · 缩略图 + 作品筛选 + 点选", (body, p, node) => {
        body.appendChild(h("div", `font-size:12px;color:${p.sub};margin-bottom:10px;`, "加载角色图鉴中…"));
        fetchCharacters().then((data) => {
            body.innerHTML = "";
            const list = body;

            // —— 「点选写入哪个控件」切换条 ——
            //   只在双人模式下出现：单人模式选角色 B 没有任何意义，
            //   平白多一行控件只会让人以为"双人模式生效了"。
            const s = v311(node);
            if (s.double || isB) {
                const bar = h("div", `display:flex;gap:8px;align-items:center;flex-wrap:wrap;
                    margin-bottom:10px;`);
                bar.appendChild(h("span", `font-size:11.5px;color:${p.sub};`, "点选写入："));
                [["角色", "角色 A（正文主体）"], ["第二角色", "角色 B（双人段）"]].forEach((pair) => {
                    const k2 = pair[0];
                    const cur = getVal(node, k2) || "自动";
                    const b = mkBtn(pair[1] + "：" + cur, p, k2 === key);
                    b.onclick = () => { closeModal(); openCharacters(node, k2); };
                    bar.appendChild(b);
                });
                list.appendChild(bar);
            }

            const search = h("input", `width:100%;box-sizing:border-box;padding:8px 12px;
                border:1px solid ${p.border};border-radius:8px;background:${p.card};
                color:${p.text};font-size:13px;margin-bottom:10px;outline:none;`);
            search.placeholder = "搜索 中文名 / 英文名 / 作品名…";
            list.appendChild(search);

            const works = h("div", "display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;");
            works.appendChild(h("span", `font-size:11.5px;color:${p.sub};padding:4px 0;`, "作品分类："));
            const chipAll = h("span", `font-size:11.5px;padding:3px 10px;border-radius:999px;
                cursor:pointer;border:1px solid ${p.accent}66;color:${p.accent};
                background:${p.accent}14;`, "全部");
            chipAll.onclick = () => renderGrid("");
            works.appendChild(chipAll);
            data.works.forEach((w) => {
                const b = h("span", `font-size:11.5px;padding:3px 10px;border-radius:999px;
                    cursor:pointer;border:1px solid ${w.color}66;color:${w.color};
                    background:${w.color}14;`, `${w.zh} ${w.count}`);
                b.onclick = () => { search.value = w.zh; renderGrid(w.zh); };
                works.appendChild(b);
            });
            list.appendChild(works);

            const gridWrap = h("div", "");
            list.appendChild(gridWrap);

            const io = (typeof IntersectionObserver === "function")
                ? new IntersectionObserver((ents, obs) => {
                    ents.forEach((en) => {
                        if (!en.isIntersecting) return;
                        const img = en.target; obs.unobserve(img);
                        if (img.dataset.src) img.src = img.dataset.src;
                    });
                }, { root: list, rootMargin: "220px" })
                : null;

            // 【v3.16.1】「只看有图」+ 分块渲染
            //
            // 为什么必须分块：角色从 2000 涨到 2165，而且**现在几乎人人有图**，
            // 一次性建 2165 个卡片（每个 5–8 个节点）会让弹窗卡住好几百毫秒，
            // 打开图鉴像"死了一下"。改成每批 150 张，滚到底再补。
            //
            // renderToken：每次重渲染自增，异步的回调拿着旧 token 就直接返回 ——
            // 否则「搜索改词 → 旧批次又追加上来」会串出重复卡片。
            let onlyImg = false;
            let renderToken = 0;
            const PAGE = 150;

            function renderGrid(q) {
                gridWrap.innerHTML = "";
                const cur = getVal(node, key);          // 【v3.17】跟随当前目标控件
                const query = (q || "").trim().toLowerCase();
                const filtered = data.characters.filter((c) => {
                    if (onlyImg && !c.img) return false;
                    return !query
                        || c.zh.toLowerCase().includes(query)
                        || c.en.toLowerCase().includes(query)
                        || c.work_zh.toLowerCase().includes(query)
                        || c.work.toLowerCase().includes(query)
                        // 【v3.11】也能按中文标签搜（发色 / 发型 / 瞳色 / 特征）
                        || (c.tags_zh || []).some((t) => String(t).toLowerCase().includes(query));
                });
                if (!filtered.length) {
                    gridWrap.appendChild(h("div", `font-size:12.5px;color:${p.sub};`, "没有匹配的角色"));
                    return;
                }

                // —— 计数行 + 「只看有图」开关 ——
                const withImg = data.characters.filter((c) => c.img).length;
                const head = h("div", `display:flex;align-items:center;gap:14px;
                    flex-wrap:wrap;font-size:11.5px;color:${p.sub};margin-bottom:8px;`);
                head.appendChild(h("span", "", `共 ${filtered.length} 名角色`));
                head.appendChild(h("span", `color:${p.accent};`,
                    `全库 ${withImg} / ${data.characters.length} 有图`));
                const cbWrap = h("label", "display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;");
                const cb = document.createElement("input");
                cb.type = "checkbox";
                cb.checked = onlyImg;
                cb.style.cssText = "accent-color:" + p.accent + ";cursor:pointer;";
                cb.onchange = () => { onlyImg = cb.checked; renderGrid(query); };
                cbWrap.appendChild(cb);
                cbWrap.appendChild(h("span", "", "只看有图"));
                head.appendChild(cbWrap);
                gridWrap.appendChild(head);

                const grid = h("div", `display:grid;grid-template-columns:repeat(auto-fill,minmax(126px,1fr));gap:9px;`);
                const token = ++renderToken;
                let cursor = 0;

                function appendChunk() {
                    if (token !== renderToken) return;
                    const end = Math.min(cursor + PAGE, filtered.length);
                    for (; cursor < end; cursor++) grid.appendChild(buildCard(filtered[cursor]));
                    if (cursor >= filtered.length) {
                        sentinel.remove();
                        if (more.parentNode) more.remove();
                        return;
                    }
                    more.textContent = `已显示 ${cursor} / ${filtered.length}　·　继续向下滚动加载`;
                }

                function buildCard(c) {
                    const on = cur === c.value;
                    const card = h("div", cardStyle(on, p) + "overflow:hidden;cursor:pointer;position:relative;");
                    const clip = h("div", `position:relative;width:100%;aspect-ratio:3/4;
                        background:linear-gradient(135deg,${c.work_color}33,${c.work_color}11);overflow:hidden;`);
                    const initial = h("div", `position:absolute;inset:0;display:flex;align-items:center;
                        justify-content:center;font-size:28px;font-weight:800;color:${p.sub};`,
                        (c.zh || c.en || "?").charAt(0));
                    clip.appendChild(initial);
                    const src = thumbURL(c.img);
                    if (src) {
                        const img = document.createElement("img");
                        img.loading = "lazy";
                        img.dataset.src = src;
                        img.style.cssText = `position:absolute;inset:0;width:100%;height:100%;
                            object-fit:cover;opacity:0;transition:opacity .25s;`;
                        img.onload = () => { img.style.opacity = "1"; initial.style.display = "none"; };
                        img.onerror = () => { img.remove(); };
                        clip.appendChild(img);
                        if (io) { io.observe(img); } else { img.src = img.dataset.src; }
                    }
                    card.appendChild(clip);
                    const info = h("div", `position:absolute;left:0;right:0;bottom:0;
                        background:linear-gradient(to top,rgba(0,0,0,.88),rgba(0,0,0,.15));
                        padding:13px 6px 5px;`);
                    info.appendChild(h("div", `font-size:11.5px;font-weight:700;color:#fff;
                        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`, c.zh || c.en));
                    info.appendChild(h("div", `font-size:9.5px;color:#ffffffcc;margin-top:1px;
                        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`, c.work_zh));
                    if (c.build_zh || c.fig_zh) {
                        info.appendChild(h("div", `font-size:9px;color:#ffffffaa;margin-top:1px;
                            overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`,
                            [c.build_zh, c.fig_zh].filter(Boolean).join(" · ")));
                    }
                    // 【v3.11】中文标签行（发色 / 发型 / 瞳色 / 特征）
                    const tzh = (c.tags_zh || []).slice(0, 3);
                    if (tzh.length) {
                        info.appendChild(h("div", `font-size:9px;color:#ffffffcc;margin-top:1px;
                            overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`,
                            tzh.join(" · ")));
                    }
                    card.appendChild(info);
                    if (on) {
                        card.appendChild(h("div", `position:absolute;right:6px;top:6px;width:18px;
                            height:18px;border-radius:50%;background:${p.accent};color:#fff;
                            font-size:12px;display:flex;align-items:center;justify-content:center;`, "✓"));
                    }
                    card.title = `${c.zh}（${c.en}）\n作品：${c.work_zh}\n角色词：${c.word || c.en}`;
                    liftable(card);
                    card.onclick = () => {
                        // 【v3.17】写回 key（角色 A 或 角色 B）——
                        //   同时刷新主界面回显，选完关掉弹窗就能看到两边都是新的。
                        setWidgetVal(node, key, c.value);
                        refreshSelectedPreview(node);
                        refreshContentRow(node);
                        refreshSummary(node);
                        renderGrid(q);
                    };
                    return card;   // 由 appendChunk 统一 append，这里不自己挂
                }

                // —— 分块渲染的收尾 ——
                // `more` / `sentinel` 必须在 buildCard 之后声明，但 appendChunk
                // 只在两者初始化**之后**才被调用，所以闭包引用它们是安全的。
                gridWrap.appendChild(grid);
                const more = h("div",
                    `font-size:11.5px;color:${p.sub};text-align:center;padding:10px 0;`);
                const sentinel = h("div", "height:1px;");
                gridWrap.appendChild(more);
                gridWrap.appendChild(sentinel);
                appendChunk();
                if (typeof IntersectionObserver === "function") {
                    // 滚到底前 400px 就补下一批，用户感觉不到在"加载"
                    const moreIo = new IntersectionObserver((ents) => {
                        if (ents.some((e) => e.isIntersecting)) appendChunk();
                    }, { root: list, rootMargin: "400px" });
                    moreIo.observe(sentinel);
                } else {
                    // 没有 IntersectionObserver（老环境 / jsdom 桩）→ 直接全渲染，
                    // 功能不能因为少个 API 就残废
                    while (cursor < filtered.length) appendChunk();
                }
            }

            search.oninput = () => renderGrid(search.value);
            renderGrid("");
        }).catch((e) => {
            body.innerHTML = "";
            body.appendChild(h("div", `font-size:12.5px;color:${p.err};`, "读取角色图鉴失败：" + e.message));
        });
    });
}

/* ---------- 预设管理 ---------- */
function openPresets(node) {
    openModal(node, "方案 · 保存 / 切换 / 删除（角色 + 锁定字段 + 权重 + 参数）", (body, p, node) => {
        const curChar = getVal(node, "角色");
        const nameInput = h("input", `width:100%;box-sizing:border-box;padding:8px 12px;
            border:1px solid ${p.border};border-radius:8px;background:${p.card};
            color:${p.text};font-size:13px;margin-bottom:8px;outline:none;`);
        nameInput.placeholder = "方案名（例如：甘雨·温泉·高露骨）";
        const saveBtn = h("button", `border:1px solid ${p.accent};background:${p.accent};
            color:#fff;border-radius:8px;padding:7px 16px;cursor:pointer;font-size:13px;
            font-weight:700;margin-bottom:14px;`, "💾 保存当前为方案");
        saveBtn.onclick = () => {
            const nm = (nameInput.value || "").trim();
            if (!nm) { nameInput.style.borderColor = p.err; return; }
            // 【v3.13 整合】一条方案 = 角色 + 锁定字段 + 权重覆盖 + 其余控件快照
            savePreset(nm, curChar, readLocked(node), "",
                getVal(node, "权重覆盖") || "", snapshotParams(node)).then((r) => {
                if (r.ok) {
                    renderPresets();
                    refreshPresetWidget(node);
                    nameInput.value = "";
                } else {
                    alert("保存失败：" + (r.error || ""));
                }
            });
        };
        body.appendChild(nameInput);
        body.appendChild(saveBtn);

        const listWrap = h("div", "");
        body.appendChild(listWrap);

        function renderPresets() {
            listWrap.innerHTML = "";
            fetchPresets().then((r) => {
                const presets = r.presets || [];
                if (!presets.length) {
                    listWrap.appendChild(h("div", `font-size:12.5px;color:${p.sub};`, "还没有预设，先在上方输入名字保存一个。"));
                    return;
                }
                presets.forEach((pr) => {
                    const row = h("div", cardStyle(false, p) + `display:flex;align-items:center;
                        gap:10px;padding:9px 12px;margin-bottom:8px;`);
                    const info = h("div", "flex:1;min-width:0;");
                    info.appendChild(h("div", `font-size:13px;font-weight:700;color:${p.text};
                        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`, pr.name));
                    info.appendChild(h("div", `font-size:11px;color:${p.sub};
                        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`,
                        (pr.character || "（不绑角色）")
                        + (pr.weights ? " · 权重" : "")
                        + (pr.params ? " · 参数快照" : "")
                        + (pr.note ? " · " + pr.note : "")));
                    row.appendChild(info);
                    const load = h("button", `border:1px solid ${p.accent};color:${p.accent};
                        background:transparent;border-radius:7px;padding:4px 12px;cursor:pointer;
                        font-size:12px;`, "加载");
                    load.onclick = () => {
                        setWidgetVal(node, "角色预设", pr.name);
                        if (pr.character) setWidgetVal(node, "角色", pr.character);
                        // 【v3.13 整合】一条方案把 锁定字段 + 权重 + 控件快照 一起还原
                        if (pr.overrides && typeof pr.overrides === "object") {
                            writeLocked(node, pr.overrides);
                        }
                        if (typeof pr.weights === "string" && pr.weights) {
                            setWidgetVal(node, "权重覆盖", pr.weights);
                        }
                        applyParams(node, pr.params);
                        refreshSelectedPreview(node);
                        refreshContentRow(node);
                        refreshSummary(node);
                        renderPresets();
                        refreshPresetWidget(node);
                    };
                    row.appendChild(load);
                    const del = h("button", `border:1px solid ${p.err};color:${p.err};
                        background:transparent;border-radius:7px;padding:4px 12px;cursor:pointer;
                        font-size:12px;`, "删除");
                    del.onclick = () => {
                        removePreset(pr.name).then(() => {
                            if (getVal(node, "角色预设") === pr.name) setWidgetVal(node, "角色预设", "不使用");
                            renderPresets();
                            refreshPresetWidget(node);
                        });
                    };
                    row.appendChild(del);
                    listWrap.appendChild(row);
                });
            });
        }
        renderPresets();
    });
}

/* 保存/删除后刷新「角色预设」下拉（ComfyUI combo 的选项需要重建） */
function refreshPresetWidget(node) {
    const w = getWidget(node, "角色预设");
    if (!w) return;
    fetchPresets().then((r) => {
        const names = r.presets.map((p) => p.name);
        const opts = ["不使用"].concat(names);
        const cur = w.value;
        w.options.values = opts;
        if (!opts.includes(cur)) w.value = "不使用";
        if (w.inputEl) {
            w.inputEl.innerHTML = "";
            opts.forEach((o) => {
                const opt = document.createElement("option");
                opt.value = o; opt.textContent = o;
                w.inputEl.appendChild(opt);
            });
            w.inputEl.value = w.value;
        }
    });
}

/* ---------- 设置：内容等级 + 权重（对齐选择器 v3.8.0） ---------- */
function setWeightSpec(node, name, val) {
    const cur = parseSpec(getVal(node, "权重覆盖"));
    cur[name] = val;
    const spec = Object.keys(cur)
        .filter((k) => Number(cur[k]) !== 1)     // 1 = 默认，不写进控件，保持整洁
        .map((k) => k + ":" + cur[k]).join(", ");
    setWidgetVal(node, "权重覆盖", spec);
}

function openSettings(node) {
    openModal(node, "设置 · 内容强度 / 随机性 / 权重", (body, p, node) => {
        const wWrap = h("div", "");

        // ==================================================================
        // 【v3.18】分区块 + **子级交互（折叠）按钮**
        //
        // 需求（用户）："设置界面各功能增加子级交互按钮，便于快速浏览"。
        // 做法：每个功能区都是一条可点的折叠标题（▶/▼），点标题展开 / 收起，
        //      展开状态记在 node._k2v3_sec 上 —— 同一次会话里切来切去不用重复点。
        //      默认只展开「① 内容强度」（唯一的等级主控、最常动），
        //      其余默认收起，一屏就能看完全部功能区标题，不用一路滚到底。
        //
        // ⚠️ 折叠只改 `display`，DOM 与事件照常存在 ——
        //    收起不等于卸载，重开面板时状态还在。
        // ==================================================================
        node._k2v3_sec = node._k2v3_sec || {};
        function section(key, title, subtitle, defaultOpen) {
            const open = node._k2v3_sec[key] === undefined
                ? !!defaultOpen : !!node._k2v3_sec[key];
            const head = h("div", `display:flex;align-items:center;gap:8px;cursor:pointer;
                padding:9px 12px;border:1px solid ${p.border};border-radius:9px;
                background:${p.card};font-size:12.5px;font-weight:700;color:${p.text};
                user-select:none;`);
            const arrow = h("span", "font-size:11px;", open ? "▼" : "▶");
            head.appendChild(arrow);
            head.appendChild(h("span", "flex:1;min-width:120px;", title));
            if (subtitle) {
                head.appendChild(h("span",
                    `font-size:10.5px;font-weight:400;color:${p.sub};`, subtitle));
            }
            const inner = h("div", `display:${open ? "block" : "none"};
                border-left:2px solid ${p.border};margin:6px 0 0 7px;padding:4px 0 0 13px;`);
            const wrap = h("div", "margin-top:10px;");
            wrap.appendChild(head);
            wrap.appendChild(inner);
            liftable(head);
            head.onclick = () => {
                const now = inner.style.display === "none";
                inner.style.display = now ? "block" : "none";
                arrow.textContent = now ? "▼" : "▶";
                node._k2v3_sec[key] = now;
            };
            body.appendChild(wrap);
            return {head: head, body: inner, wrap: wrap, key: key};
        }

        // ==================================================================
        // ⓪ 【v3.16】免责声明入口 —— 放在设置面板**最顶部**，
        //    与快捷操作行的「📜 免责声明」互为兜底：无论横幅被收起还是
        //    被误关，这里都能重新打开全文。
        //    用 aside 而不是按钮：它现在是"条款"入口，不是可调项。
        // ==================================================================
        const discBar = h("div", `display:flex;align-items:center;gap:10px;flex-wrap:wrap;
            padding:9px 12px;margin-bottom:16px;background:${p.warn}12;
            border:1px solid ${p.warn}55;border-radius:9px;`);
        discBar.appendChild(h("span", `flex:1;min-width:200px;font-size:11.5px;
            color:${p.text};line-height:1.6;`,
            "⚠ 本插件为开源插件，仅供成年人用于虚构创作。使用前请阅读免责声明与风险提示。"));
        const bDiscTop = mkBtn("📜 查看免责声明全文", p);
        bDiscTop.onclick = () => { openDisclaimer(node); };
        discBar.appendChild(bDiscTop);
        body.appendChild(discBar);

        // ==================================================================
        // ① 内容强度 —— **v3.18 起唯一的 NSFW 等级控制项**
        //
        // v3.13~v3.17 期间，「内容等级 / 内容边界 / NSFW开关+强度 / 内容强度」
        // 四个控件都能改"多露骨"，互相打架（用户明确点名）。现在只剩这一个：
        //   全年龄 / 暗示 → SFW；露骨 → +展示生殖器/自慰；强露骨 → +口交；
        //   极端 → +性交（并自动追加越界描写）。
        // ==================================================================
        const secIntensity = section("intensity",
            "① 内容强度（NSFW 等级 · 唯一控制项）", "最常动", true);
        const intWrap = h("div", "display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin-top:8px;");
        secIntensity.body.appendChild(h("div", `font-size:11px;color:${p.sub};line-height:1.6;`,
            "**这是唯一的等级控制项** —— 原来还能改露骨程度的「内容等级 / 内容边界 / "
            + "NSFW 开关 / NSFW 强度」已全部撤掉（它们会互相打架）。"
            + "抽卡严格按所选档位出对应等级的内容；各项内容的概率在最下面「⑦ NSFW 内容权重」里调。"));
        secIntensity.body.appendChild(intWrap);

        // —— ② 随机性（v3.13）——
        const secRandom = section("random", "② 随机性质量", "去重 / 温度 / 批次");
        const rndWrap = h("div", "margin-top:8px;");
        secRandom.body.appendChild(rndWrap);

        // —— ③ 逐项开关（v3.13）——
        const secOff = section("off", "③ 逐项开关", "关掉正文里的补充项");
        const offWrap = h("div", "margin-top:8px;");
        secOff.body.appendChild(h("div", `font-size:11px;color:${p.sub};margin-bottom:8px;line-height:1.6;`,
            "只有这 28 项能被关掉；镜头/人物/发型/服装主件/姿态/场景/构图是核心项，关不掉。"));
        secOff.body.appendChild(offWrap);

        // —— ④ 字段微调（v3.13 逐字段重掷；v3.20 扩展为"点选 + 手输 + 搜索 + 批量"）——
        //   v3.13 只有「下拉选字段 → 摇一个」：看不到有哪些候选，也不能指定具体值。
        //   v3.20 补上：候选值点选（点一下即锁定）、手输自定义值、按段落分组折叠、
        //   搜索筛选、三个批量动作。写进去的仍是同一个「锁定字段」控件 ——
        //   **完全没碰生成逻辑**，只是把"往锁定字段里写什么"变得好用。
        const secTune = section("tune", "④ 字段微调", "点选 / 手输 / 批量锁定");
        const tuneWrap = h("div", "margin-top:8px;");
        secTune.body.appendChild(h("div", `font-size:11px;color:${p.sub};margin-bottom:8px;line-height:1.6;`,
            "把某个字段钉成指定值 → 写入「锁定字段」，随机时跳过它，其余字段照常随机。"
            + "锁定值可来自候选列表，也可手输任意文本；点「✕」或清空即撤销。"));
        secTune.body.appendChild(tuneWrap);

        // —— ⑤ 权重 ——
        const secWeight = section("weight", "⑤ 权重", "轴级权重滑块");
        secWeight.body.appendChild(h("div", `font-size:11px;color:${p.sub};margin:8px 0 10px;line-height:1.6;`,
            "轴级权重：命中概率 = 该选项权重 ÷ 同轴全部选项权重之和。0 = 永不出现；"
            + "整轴同乘一个倍数不改变分布。"));
        const wSearch = document.createElement("input");
        wSearch.placeholder = "🔍 筛选轴 / 选项…";
        wSearch.style.cssText = `width:100%;box-sizing:border-box;border:1px solid ${p.border};
            background:${p.card};color:${p.text};border-radius:8px;padding:6px 10px;
            font-size:12px;margin-bottom:10px;outline:none;`;
        secWeight.body.appendChild(wSearch);
        secWeight.body.appendChild(wWrap);

        // ==================================================================
        // ⑤b 【v3.22】外观细节权重：鞋履详细种类 + 水面波光
        //
        //  与「⑤ 权重」的轴级权重、以及「⑦ NSFW 内容权重」同构，都是
        //  "权重 0 = 永不出现 / 1.00 = 标准比例"，交互（滑块 + ×值）保持一致。
        //  数据从 /extra 的 shoe_categories / water_option 来 —— 种类表改了
        //  前端自动跟着变，不在 JS 里手写鞋款清单。
        // ==================================================================
        const secShoe = section("shoe", "⑤b 外观细节权重", "鞋履种类 / 水面波光");
        const shoeWrap = h("div", "margin-top:8px;");
        node._k2v3_shoe = shoeWrap;
        secShoe.body.appendChild(shoeWrap);

        /** 【v3.22】渲染鞋履种类权重 + 水面波光权重（数据驱动） */
        function renderShoeWeights(d) {
            const cats = (d && d.shoe_categories) || [];
            const water = (d && d.water_option) || null;
            if (!cats.length && !water) { shoeWrap.innerHTML = ""; return; }
            const min = Number(d.shoe_min != null ? d.shoe_min : 0);
            const max = Number(d.shoe_max != null ? d.shoe_max : 3);
            const step = Number(d.shoe_step != null ? d.shoe_step : 0.05);
            shoeWrap.innerHTML = "";

            shoeWrap.appendChild(h("div",
                `font-size:11px;color:${p.sub};margin:8px 0 10px;line-height:1.6;`,
                "控制各鞋款 / 该效果在随机出题里被抽中的概率：命中概率 = 该权重 ÷ "
                + "该字段全部候选权重之和。0 = 永不出现，1.00（默认）= 标准比例。"
                + "「不使用（不写鞋履）」恒为 1.00，不在此列。"));

            function rowOf(label, ctl, itemsNote) {
                let cur = Number(getVal(node, ctl));
                if (isNaN(cur)) cur = 1.0;
                const row = h("div", "display:flex;align-items:center;gap:9px;margin-bottom:6px;");
                row.appendChild(h("div", `flex:0 0 96px;font-size:11.5px;color:${p.text};
                    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`, label));
                const sl = document.createElement("input");
                sl.type = "range"; sl.min = String(min); sl.max = String(max); sl.step = String(step);
                sl.value = String(cur);
                sl.style.cssText = "flex:1;min-width:90px;accent-color:" + p.accent + ";";
                const paint = (v) => {
                    lab.textContent = v > 0 ? "×" + v.toFixed(2) : "不出现";
                    lab.style.color = v > 0 ? p.accent : p.sub;
                };
                const lab = h("div", `flex:0 0 78px;font-size:11px;text-align:right;`, "");
                paint(cur);
                sl.oninput = () => {
                    const v = parseFloat(sl.value);
                    setWidgetVal(node, ctl, v);
                    paint(v);
                    refreshSummary(node);
                };
                row.appendChild(sl);
                row.appendChild(lab);
                shoeWrap.appendChild(row);
                if (itemsNote) {
                    shoeWrap.appendChild(h("div", `font-size:10px;color:${p.sub};
                        margin:-2px 0 9px 105px;line-height:1.5;`, itemsNote));
                }
            }

            cats.forEach((c) => {
                const items = (c.items || []);
                const shown = items.slice(0, 6).join("、") + (items.length > 6 ? "…" : "");
                rowOf(c.name, c.ctl, shown);
            });
            if (water) {
                rowOf(water.name, water.ctl,
                    "「水面反光」：水面波光倒映在皮肤上，光影流转（环境光效果）");
            }

            const bReset = mkBtn("↺ 全部恢复 1.00", p);
            bReset.onclick = () => {
                cats.forEach((c) => setWidgetVal(node, c.ctl, 1.0));
                if (water) setWidgetVal(node, water.ctl, 1.0);
                renderShoeWeights(d);
                refreshSummary(node);
            };
            shoeWrap.appendChild(bReset);
        }

        // ==================================================================
        // ⑥ 【v3.17】人物与角色
        //
        //  「单人／双人」原来在主界面内容设定行，与设置面板里的其它开关并存
        //  ⇒ 同一个设置两个入口（违反单入口约定）。现已整体收进设置面板，
        //  主界面只保留状态回显。双人时可分别指定角色 A / B。
        // ==================================================================
        const secPerson = section("person", "⑥ 人物与角色", "单人 / 双人 · 角色 B");
        const subWrap = h("div", "margin-top:8px;");
        // 挂到节点上：换等级 / 换人数后要能就地重渲染，前端冒烟测试也靠它定位
        node._k2v3_sub = subWrap;
        secPerson.body.appendChild(subWrap);

        /** 「⑥ 人物与角色」区渲染（人物数量 / 百合 / 角色 A·B / 分级说明） */
        function renderSub() {
            subWrap.innerHTML = "";
            const s = v311(node);

            // ---- ① 人物数量 ----
            const box1 = h("div", cardStyle(false, p) + "padding:10px 12px;margin-bottom:10px;");
            box1.appendChild(h("div", `font-size:12px;font-weight:700;color:${p.text};margin-bottom:7px;`,
                "人物数量"));
            const prow = h("div", "display:flex;gap:8px;flex-wrap:wrap;align-items:center;");
            const bSolo = mkBtn("👤 单人", p, !s.double);
            bSolo.title = "保证画面里只有一个人：NSFW 词库里带「双人/多人」的姿势会被换成纯单人条目";
            bSolo.onclick = () => {
                setWidgetVal(node, "人物数量", "单人");
                renderSub(); refreshContentRow(node); refreshSummary(node); refreshSelectedPreview(node);
            };
            const bDuo = mkBtn("👥 双人", p, s.double);
            bDuo.title = "在提示词**最前面**交代两人正在做什么，再写镜头/环境："
                + "SFW 档走通用互动，NSFW 档走百合并按内容项出对应内容";
            bDuo.onclick = () => {
                setWidgetVal(node, "人物数量", "双人");
                renderSub(); refreshContentRow(node); refreshSummary(node); refreshSelectedPreview(node);
            };
            prow.appendChild(bSolo);
            prow.appendChild(bDuo);
            box1.appendChild(prow);
            box1.appendChild(h("div", `font-size:10.5px;color:${p.sub};margin-top:6px;line-height:1.6;`,
                "单人（默认）= 保证画面里只有一个人；双人 = 提示词最前面先交代两人互动的详细内容，"
                + "再写镜头 / 人物 / 场景（整条上限 1000 字，单人仍是 600 字）。"));

            if (s.double) {
                // ---- ①b 百合模式（双人才有意义）----
                const yrow = h("div", "display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px;");
                const bYuri = mkBtn("🌸 百合", p, s.yuri);
                bYuri.title = "双人 + NSFW 档本来就会自动走百合；这个开关用来在 SFW 档也强制百合";
                bYuri.onclick = () => {
                    setWidgetVal(node, "百合模式", !s.yuriManual);
                    renderSub(); refreshContentRow(node); refreshSummary(node);
                };
                yrow.appendChild(bYuri);
                yrow.appendChild(h("span", `font-size:10.5px;color:${s.yuriAuto ? p.accent : p.sub};
                    max-width:100%;line-height:1.6;`,
                    s.yuriAuto
                        ? "当前 NSFW 档 → 已自动走百合（SFW 档想百合再用左边开关强制）"
                        : "当前 SFW 档 → 走通用多人互动"));
                box1.appendChild(yrow);

                // ---- ①c 两位角色（A = 正文主体 / B = 双人段）----
                const crow = h("div", "display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px;");
                crow.appendChild(h("span", `font-size:11.5px;color:${p.sub};`, "两位角色："));
                [["角色", "A（正文主体）"], ["第二角色", "B（双人段）"]].forEach((pair) => {
                    const k2 = pair[0];
                    const cur = getVal(node, k2) || "自动";
                    const b = mkBtn(pair[1] + "：" + cur, p, k2 === "第二角色" && cur !== "自动");
                    b.title = "打开角色图鉴，点选写入「" + k2 + "」";
                    b.onclick = () => { openCharacters(node, k2); };
                    crow.appendChild(b);
                    if (cur !== "自动") {
                        const x = mkBtn("清除", p);
                        x.title = "把这一位退回「自动」";
                        x.onclick = () => {
                            setWidgetVal(node, k2, "自动");
                            renderSub(); refreshContentRow(node);
                            refreshSummary(node); refreshSelectedPreview(node);
                        };
                        crow.appendChild(x);
                    }
                });
                box1.appendChild(crow);
                box1.appendChild(h("div", `font-size:10.5px;color:${p.sub};margin-top:6px;line-height:1.6;`,
                    "双人段会把两位角色**点名**写进提示词：互动句的主语换成真实角色名，"
                    + "并用发色 / 发长做外观锚点 —— 指向明确，不会出现「她…她」的指代歧义。"
                    + "角色 A 同时是正文主体特征的来源；角色 B 留「自动」则不点名。"));

                // ---- ①d 当前分级说明（档位一变就要跟着刷，见 refreshSubLevelTips）----
                const gtip = h("div", `font-size:10.5px;color:${p.accent};margin-top:6px;`);
                node._k2v3_gradetip = gtip;
                box1.appendChild(gtip);
                refreshSubLevelTips();
            }
            subWrap.appendChild(box1);

            // ---- ①e【v3.20】角色外观锁定 ----
            //   为什么单独一个卡片而不是塞进「人物数量」那一块：
            //   它是**外观一致性**的总开关，与人数/百合无关（单人双人都适用），
            //   而且用户实测踩过的坑就是"角色设定后外观每次变"，需要显眼、可自查。
            const appOn = (function () {
                const raw = getVal(node, "角色外观锁定");
                // 与后端同一套归一：空串 / 取不到 = 默认「开」
                if (raw === "" || raw === null || raw === undefined) return true;
                return !!raw;
            })();
            const boxA = h("div", cardStyle(false, p) + "padding:10px 12px;margin-bottom:10px;");
            const arow = h("div", "display:flex;gap:8px;flex-wrap:wrap;align-items:center;");
            arow.appendChild(h("span", `font-size:12px;font-weight:700;color:${p.text};`,
                "🔒 角色外观锁定"));
            const bApp = mkBtn(appOn ? "开（推荐）" : "关", p, appOn);
            bApp.title = "开启后：同一角色的发色 / 发长 / 卷度 / 发态 / 扎法 / 刘海 / 瞳色"
                + " 每次重随完全一致，并且不依赖「角色联动」";
            bApp.onclick = () => {
                setWidgetVal(node, "角色外观锁定", !appOn);
                renderSub(); refreshSummary(node);
            };
            arow.appendChild(bApp);
            arow.appendChild(h("span", `font-size:10.5px;color:${appOn ? p.accent : p.warn};`,
                appOn ? "已锁定：换种子 / 重随，外观描述逐字不变"
                      : "⚠ 已关闭：发色 / 发型 / 瞳色 每次重随都会变"));
            boxA.appendChild(arow);
            boxA.appendChild(h("div",
                `font-size:10.5px;color:${p.sub};margin-top:6px;line-height:1.6;`,
                "锁的是「这个人长什么样」：发色 · 发长 · 卷度 · 发态 · 扎法 · 刘海 · 瞳孔颜色。"
                + "它**独立于「角色联动」**——联动关掉（不想要被锁服装 / 场景 / 气质）时，"
                + "外观依然保持一致。角色没有对应标签时按「角色名派生」固定取一个值"
                + "（同一角色永远一样），扎法与刘海则取中性值（= 正文里不写这两句，不杜撰）。"));
            if (getVal(node, "角色") === "自动") {
                boxA.appendChild(h("div",
                    `font-size:10.5px;color:${p.sub};margin-top:5px;line-height:1.6;`,
                    "ⓘ 当前「角色」是「自动」→ 本项不生效（随机角色无所谓一致）。"));
            }
            subWrap.appendChild(boxA);
        }

        /** 【v3.18】刷新「人物与角色」区的分级说明。
         *  档位来自「内容强度」（唯一等级控制项），分级文案来自 /extra 的 grade_note。 */
        function refreshSubLevelTips() {
            const gtip = node._k2v3_gradetip;
            if (!gtip) return;
            const lv = intLevelOf(node);
            fetchLevels().then((ld) => fetchExtra().then((d) => {
                const zh = ((ld.zh) || {})[lv] || lv;
                // ★ 双人段的档位口径（v3.17 起：SFW→通用互动，NSFW→百合）
                const note = ((d && d.grade_note) || {})[zh] || "";
                gtip.textContent = "分级：" + (zh || "—") + (note ? "（" + note + "）" : "");
            })).catch(() => {});
        }

        // ==================================================================
        // ⑦ 【v3.18】NSFW 内容权重（**设置页面最底部**）
        //
        // 需求（用户）："在设置内最底部增加自慰、口交、展示生殖器、性交等
        // 功能权重调节项，用于保证在 NSFW 等级下，提示词能根据权重设置输出
        // 对应内容概率"。
        //
        // 与等级的关系：**等级先筛可用范围，权重再定概率**（用户选定）。
        //   档位不够的项不参与竞争（例：强露骨档下「性交」根本不在候选里），
        //   候选之间的概率 = 权重 ÷ 候选权重之和；权重 0 = 该项永不出现。
        // 滑块只写控件值，抽卡在生成时发生 —— 所以同一套权重换个种子就是另一条。
        // ==================================================================
        const secAct = section("act", "⑦ NSFW 内容权重", "五项概率 · 设置页最底部");
        const actWrap = h("div", "margin-top:8px;");
        // 挂到节点上：换档位后 renderActHint 要就地刷新，前端冒烟测试也靠它定位
        node._k2v3_act = actWrap;
        secAct.body.appendChild(actWrap);

        /** 【v3.19】自动档：按当前档位取出这一套权重基准（来自 /extra 的 act_auto_table） */
        function autoBase(level) {
            const t = (extraData && extraData.act_auto_table) || {};
            return t[level] || {};
        }
        function actAutoOn() { return !!getVal(node, ACT_AUTO_CTL); }

        function actSliderRow(act, label, minLvZh, note, ctx) {
            const ctl = ACT_CTL[act] || (act + "权重");
            const auto = ctx.auto;
            const base = auto ? Number(ctx.base[act] || 0) : 0;
            const cur = auto ? base : actWeightOf(node, ctl);
            const row = h("div", "display:flex;align-items:center;gap:9px;margin-bottom:6px;");
            row.appendChild(h("div", `flex:0 0 112px;font-size:11.5px;
                color:${auto ? p.sub : p.text};`, label));
            row.appendChild(h("span", `flex:0 0 58px;font-size:9.5px;color:${p.sub};
                border:1px solid ${p.border};border-radius:999px;padding:1px 0;text-align:center;`,
                minLvZh + "起"));
            const sl = document.createElement("input");
            sl.type = "range";
            sl.min = String(ctx.min); sl.max = String(ctx.max); sl.step = String(ctx.step);
            sl.value = String(cur);
            sl.disabled = auto;
            sl.style.cssText = "flex:1;min-width:90px;accent-color:" + p.accent
                + (auto ? ";opacity:.45;cursor:not-allowed;" : ";");
            const paint = (v) => {
                const txt = v > 0 ? "×" + v.toFixed(2) : "不出现";
                lab.textContent = (auto ? "自动 " : "") + txt;
                lab.style.color = (auto || v > 0) ? (auto ? p.sub : p.accent) : p.sub;
            };
            const lab = h("div", `flex:0 0 84px;font-size:11px;text-align:right;`, "");
            paint(cur);
            sl.oninput = () => {
                const v = parseFloat(sl.value);
                setWidgetVal(node, ctl, v);
                paint(v);
                renderActHint();
                refreshSummary(node);
            };
            row.appendChild(sl);
            row.appendChild(lab);
            actWrap.appendChild(row);
            if (note) {
                actWrap.appendChild(h("div",
                    `font-size:10px;color:${p.sub};margin:-2px 0 9px 179px;line-height:1.5;`, note));
            }
        }

        /** 当前档位的自动基准（供提示行与摘要共用） */
        function autoBaseNow() {
            return autoBase(intLevelOf(node));
        }

        function renderActHint() {
            if (!actHintBox) return;
            const lv = intLevelOf(node);
            const auto = actAutoOn();
            const table = auto ? autoBaseNow() : null;
            const tips = [];
            Object.keys(ACT_CTL).forEach((a) => {
                const w = auto ? Number(table[a] || 0) : actWeightOf(node, ACT_CTL[a]);
                if (w > 0) tips.push(a + "×" + w.toFixed(2));
            });
            fetchLevels().then((ld) => {
                const lvZh = ((ld.zh) || {})[lv] || lv;
                if (lv === "sfw" || lv === "suggestive") {
                    actHintBox.textContent = "⚠ 当前内容强度 = " + lvZh
                        + "（SFW 档）→ 根本不会写 ⑧ 姿态段，这一节的权重**不生效**。";
                    actHintBox.style.color = p.warn;
                    return;
                }
                actHintBox.textContent = (auto ? "🔗 自动档（跟随「" + lvZh + "」）：本档参与抽卡的项 "
                    : "当前档位下参与抽卡的项：")
                    + (tips.length ? tips.join("、")
                       : "（全部为 0 → 会退回保守姿态，不会出错）");
                actHintBox.style.color = auto ? p.accent : p.sub;
            }).catch(() => {});
        }

        function renderAct(d) {
            if (d) { node._k2v3_actmeta = d; }
            const meta = d || node._k2v3_actmeta || {};
            const names = (meta.act_names && meta.act_names.length)
                ? meta.act_names : Object.keys(ACT_CTL);
            const labels = meta.act_labels || null;
            const mins = meta.act_mins || null;
            const zh = meta.act_level_zh || {};
            const lv = intLevelOf(node);
            const auto = actAutoOn();
            const ctx = {
                auto: auto,
                base: autoBase(lv),
                min: Number(meta.act_min != null ? meta.act_min : ACT_W_MIN),
                max: Number(meta.act_max != null ? meta.act_max : ACT_W_MAX),
                step: Number(meta.act_step != null ? meta.act_step : ACT_W_STEP),
            };

            actWrap.innerHTML = "";

            // ---- ① 自动档开关（常驻控件，默认关 = 纯手动）----
            const autoBox = h("div", `padding:9px 11px;margin-bottom:11px;border-radius:8px;
                background:${auto ? p.accent + "14" : p.card};
                border:1px solid ${auto ? p.accent + "66" : p.border};`);
            const autoRow = h("div", "display:flex;align-items:center;gap:9px;flex-wrap:wrap;");
            const chk = document.createElement("input");
            chk.type = "checkbox";
            chk.checked = auto;
            chk.style.cssText = "width:15px;height:15px;accent-color:" + p.accent + ";cursor:pointer;";
            chk.onchange = () => {
                setWidgetVal(node, ACT_AUTO_CTL, chk.checked);
                renderAct(meta);
                refreshSummary(node);
            };
            autoRow.appendChild(chk);
            autoRow.appendChild(h("span", `font-size:12.5px;font-weight:700;color:${p.text};`,
                "🔗 内容权重 · 跟随等级"));
            autoRow.appendChild(h("span",
                `font-size:10.5px;color:${auto ? p.accent : p.sub};`,
                auto ? "已开启：滑块只读，按档位自动分配" : "关闭：由下面的滑块说了算"));
            autoBox.appendChild(autoRow);
            autoBox.appendChild(h("div",
                `font-size:10.5px;color:${p.sub};margin-top:6px;line-height:1.6;`,
                "开启后这一节的权重不再由滑块决定，而是**按当前「内容强度」档位自动算出一套基准** ——"
                + "档位越高越偏向该档的招牌内容（露骨偏自慰、强露骨偏口交、极端偏性交），"
                + "保证出题与所选分级匹配。关掉立刻回到滑块里的手动值（手动值不会被覆盖）。"));
            actWrap.appendChild(autoBox);

            actWrap.appendChild(h("div",
                `font-size:11px;color:${p.sub};margin-bottom:9px;line-height:1.6;`,
                "每一项控制该类内容在 NSFW 正文里的**出现概率**。"
                + "1.00 = 标准比例；0 = 该项永不出现；上限 " + ctx.max.toFixed(2) + " = 大幅提高占比。"
                + "概率 = 该项权重 ÷ 当前档位下全部可用项的权重之和。"
                + "标「单人」的项在单人模式下会自动排除（那些条目必须有第二个人）。"));

            const noteMap = {
                "展示生殖器": "把私处暴露在镜头前。单人模式用单人可见的条目。",
                "自慰": "自我刺激。单人模式只抽单人条目（含道具版），不会带出第二个人。",
                "潮吹·失禁": "高潮射出体液 / 失控排泄。单人可达。",
                "手交": "用手刺激对方。需要第二人 → 单人模式自动排除。",
                "乳交": "用胸部夹合。需要第二人 → 单人模式自动排除。",
                "足交": "用脚刺激对方。需要第二人 → 单人模式自动排除。",
                "口交": "口部对性器的接触。单人模式自动改用单人可见的条目。",
                "深喉": "吞到喉咙深处。需要第二人 → 单人模式自动排除。",
                "性交": "含各类插入体位的交合描写，另含单人道具版。",
                "肛交": "后庭插入，含单人道具版与双人体位。",
                "束缚·捆绑": "绳索 / 缎带固定肢体，单人自缚或双人牵引都有条目。",
                "多人": "三人及以上同时在场。需要第二人以上 → 单人模式自动排除。",
                "其它": "保守姿态（诱惑/展示/暴露）与非插入式挑逗，抽不到上面那些时的兜底。",
            };
            names.forEach((a) => {
                actSliderRow(a, (labels && labels[a]) || a,
                    zh[(mins && mins[a]) || "explicit"] || "露骨",
                    noteMap[a] || "", ctx);
            });

            actHintBox = h("div", `font-size:10.5px;color:${p.sub};margin:2px 0 9px;line-height:1.6;`, "…");
            actWrap.appendChild(actHintBox);

            const bReset = mkBtn("↺ 全部恢复 1.00", p);
            bReset.disabled = auto;
            if (auto) bReset.style.opacity = "0.45";
            bReset.onclick = () => {
                if (auto) return;
                Object.keys(ACT_CTL).forEach((a) => setWidgetVal(node, ACT_CTL[a], ACT_W_DEFAULT));
                renderAct(meta);
                refreshSummary(node);
            };
            actWrap.appendChild(bReset);
            renderActHint();
        }

        function renderWeights(d) {
            if (!d) return;
            wWrap.innerHTML = "";
            const spec = parseSpec(getVal(node, "权重覆盖"));
            const kw = (wSearch.value || "").trim().toLowerCase();
            (d.axes || []).forEach((ax) => {
                const matchKw = !kw || ax.axis.toLowerCase().includes(kw)
                    || (ax.options || []).some((o) => (o.value || "").toLowerCase().includes(kw));
                if (!matchKw) return;

                const sec = h("div", cardStyle(false, p) + "padding:10px 12px;margin-bottom:10px;");
                // 头：轴名 + 轴级倍率 + 折叠 + 重置
                const head = h("div", "display:flex;align-items:center;gap:8px;cursor:pointer;");
                head.appendChild(h("div", `font-size:12.5px;font-weight:700;color:${p.text};flex:0 0 auto;`,
                    ax.axis));
                const axMult = document.createElement("input");
                axMult.type = "range"; axMult.min = String(d.min); axMult.max = String(d.max);
                axMult.step = String(d.step);
                axMult.value = String(spec[ax.axis] !== undefined ? spec[ax.axis] : 1.0);
                axMult.title = "轴级倍率：整轴所有选项同乘，不改变相对分布";
                axMult.style.cssText = "flex:1;min-width:60px;accent-color:" + p.accent + ";";
                axMult.oninput = (ev) => {
                    ev.stopPropagation();
                    setWeightSpec(node, ax.axis, parseFloat(axMult.value));
                    renderWeights(d);
                };
                const amLab = h("span", `flex:0 0 46px;font-size:10.5px;color:${p.sub};text-align:right;`,
                    "轴×" + Number(axMult.value).toFixed(2));
                const bReset = mkBtn("重置", p);
                bReset.onclick = (ev) => {
                    ev.stopPropagation();
                    setWeightSpec(node, ax.axis, 1.0);
                    // 同时清掉该轴下所有选项级覆盖，回到出厂默认
                    (ax.options || []).forEach((o) => { if (spec[o.value] !== undefined) setWeightSpec(node, o.value, 1.0); });
                    renderWeights(d);
                };
                head.appendChild(axMult);
                head.appendChild(amLab);
                head.appendChild(bReset);
                sec.appendChild(head);

                const body = h("div", "margin-top:8px;");
                // 当前各选项权重（轴级倍数 × 选项级倍数，与后端 build_axis_weights 一致）
                const curW = {};
                ax.options.forEach((o) => {
                    let w = Number(o.default);
                    const am = spec[ax.axis];
                    if (am !== undefined) w = w * am;
                    if (spec[o.value] !== undefined) w = spec[o.value] * (am !== undefined ? am : 1);
                    curW[o.value] = w;
                });
                const total = Object.keys(curW).reduce((a, k) => a + curW[k], 0) || 1;
                ax.options.forEach((o) => {
                    const row = h("div", "display:flex;align-items:center;gap:8px;margin-bottom:4px;");
                    row.appendChild(h("div", `flex:0 0 128px;font-size:11.5px;color:${p.text};
                        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`, o.value));
                    const sl = document.createElement("input");
                    sl.type = "range";
                    sl.min = String(d.min); sl.max = String(d.max); sl.step = String(d.step);
                    sl.value = String(curW[o.value]);
                    sl.style.cssText = "flex:1;min-width:80px;accent-color:" + p.accent + ";";
                    const pct = h("div", `flex:0 0 82px;font-size:11px;color:${p.accent};text-align:right;`,
                        "×" + Number(curW[o.value]).toFixed(2) + " · "
                        + Math.round(curW[o.value] / total * 100) + "%");
                    sl.oninput = () => {
                        setWeightSpec(node, o.value, parseFloat(sl.value));
                        renderWeights(d);
                    };
                    row.appendChild(sl);
                    row.appendChild(pct);
                    body.appendChild(row);
                });
                sec.appendChild(body);

                // 折叠：点标题（非控件区）切换
                head.addEventListener("click", () => {
                    body.style.display = body.style.display === "none" ? "" : "none";
                });
                wWrap.appendChild(sec);
            });
        }

        wSearch.addEventListener("input", () => { fetchAxes().then(renderWeights); });

        // ---------- 内容强度总旋钮 ----------
        function renderIntensity() {
            intWrap.innerHTML = "";
            const w = getWidget(node, "内容强度");
            const choices = (w && w.options && w.options.values)
                || ["手动（分别设置）"];
            const cur = getVal(node, "内容强度") || choices[0];
            choices.forEach((k) => {
                const on = cur === k;
                const card = h("div", cardStyle(on, p) + "padding:8px 11px;cursor:pointer;");
                card.appendChild(h("div", `font-size:12.5px;font-weight:700;color:${on ? p.accent : p.text};`, k));
                liftable(card);
                card.onclick = () => {
                    setWidgetVal(node, "内容强度", k);
                    renderIntensity();
                    // 【v3.18/3.19】档位一变，「哪些项够得着」和「自动基准是多少」都变了
                    //   —— ⑦ 区整段重画（renderAct 内部会顺带刷新提示行）。
                    renderAct();
                    refreshSubLevelTips();
                    refreshContentRow(node);
                    refreshSummary(node);
                };
                intWrap.appendChild(card);
            });
        }

        // ---------- 随机性质量 ----------
        function sliderRow(label, key, min, max, step, fmt, hint) {
            const on = Number(getVal(node, key));
            const row = h("div", "display:flex;align-items:center;gap:8px;margin-bottom:6px;");
            row.appendChild(h("div", `flex:0 0 104px;font-size:11.5px;color:${p.text};`, label));
            const sl = document.createElement("input");
            sl.type = "range"; sl.min = String(min); sl.max = String(max); sl.step = String(step);
            sl.value = String(on);
            sl.style.cssText = "flex:1;min-width:90px;accent-color:" + p.accent + ";";
            const lab = h("div", `flex:0 0 78px;font-size:11px;color:${p.accent};text-align:right;`, fmt(on));
            sl.oninput = () => {
                const v = step === 1 ? parseInt(sl.value, 10) : parseFloat(sl.value);
                setWidgetVal(node, key, v);
                lab.textContent = fmt(v);
            };
            row.appendChild(sl); row.appendChild(lab);
            rndWrap.appendChild(row);
            if (hint) rndWrap.appendChild(h("div",
                `font-size:10px;color:${p.sub};margin:-2px 0 8px 112px;line-height:1.5;`, hint));
        }
        function renderRandom() {
            rndWrap.innerHTML = "";
            sliderRow("去重记忆", "去重记忆", 0, 20, 1, (v) => (v ? "最近 " + v + " 次" : "关闭"),
                "记录最近用过的取值并降权，避免连续抽到同一场景/道具。推荐 3–6。");
            sliderRow("多样性温度", "多样性温度", 0.5, 2, 0.05, (v) => "×" + Number(v).toFixed(2),
                "<1 更保守、>1 更发散。只在你调过权重或开了去重记忆时才看得出效果。");
            sliderRow("联动强度", "联动强度", 0, 1, 0.05, (v) => Math.round(v * 100) + "%",
                "角色特征的采用比例。100% = 全部采用；50% = 只用一半，其余交回随机。");
            sliderRow("批次数", "批次数", 1, 8, 1, (v) => v + " 条", "多出几条候选放在第 7 个输出端口。");
        }

        // ---------- 逐项开关 ----------
        function currentOff() {
            const s = getVal(node, "关闭可选项") || "";
            return s.split(/[,，、;；\s]+/).map((x) => x.trim()).filter(Boolean);
        }
        function renderOff(data) {
            offWrap.innerHTML = "";
            if (!data || !data.skippable) {
                offWrap.appendChild(h("div", `font-size:11px;color:${p.sub};`, "（取不到可关闭项清单）"));
                return;
            }
            const zh = data.skippable_zh || {};
            const cur = new Set(currentOff());
            Object.keys(data.skippable).forEach((sec) => {
                const box = h("div", cardStyle(false, p) + "padding:8px 11px;margin-bottom:8px;");
                box.appendChild(h("div", `font-size:11.5px;font-weight:700;color:${p.text};margin-bottom:6px;`,
                    zh[sec] || sec));
                const line = h("div", "display:flex;flex-wrap:wrap;gap:10px;");
                (data.skippable[sec] || []).forEach((fid) => {
                    const wrap = h("label", "display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer;");
                    const cb = document.createElement("input");
                    cb.type = "checkbox"; cb.checked = cur.has(fid);
                    cb.style.cssText = "accent-color:" + p.accent + ";";
                    cb.onchange = () => {
                        const s = new Set(currentOff());
                        if (cb.checked) s.add(fid); else s.delete(fid);
                        setWidgetVal(node, "关闭可选项", Array.from(s).join(", "));
                    };
                    wrap.appendChild(cb);
                    wrap.appendChild(h("span", `color:${p.text};`, fieldZh(fid)));
                    line.appendChild(wrap);
                });
                box.appendChild(line);
                offWrap.appendChild(box);
            });
        }

        // ==================================================================
        // ④ 字段微调（v3.13 起：逐字段重掷；v3.20 扩展为完整编辑器）
        //
        // 三层结构，从上到下：
        //   ① 工具条 —— 搜索 + 批量（锁定当前整套 / 重摇全部已锁 / 清空）
        //   ② 候选项 —— 当前选中字段的全部可选值，点一下即锁定；另有手输框
        //   ③ 字段表 —— 按段落（① 镜头 … ⑪ 附加）折叠，每行显示 当前值 / 锁定值
        //
        // ★ 为什么「当前值」取自 node._k2v3_slots 而不是读控件：
        //   控件里存的是"锁定值"，而这一版的**实际取值**只有生成时才知道。
        //   后端把槽位表随 ui 消息一起发过来了（v3.20 新增），直接用它 ——
        //   于是「锁定当前整套值」才能一键钉死整条结果，而不只是看得见的那几个。
        // ==================================================================
        let tuneSel = "";                    // 当前选中的字段 id
        let tuneQuery = "";                  // 搜索关键词

        function curValueOf(fid) {
            const sl = node._k2v3_slots || {};
            return sl[fid] || "";
        }

        /** 往「锁定字段」里写 / 删一项，并就地重画。 */
        function tuneLock(fid, val) {
            const lk = readLocked(node);
            if (val === null || val === undefined || val === "") {
                delete lk[fid];
            } else {
                lk[fid] = String(val);
            }
            writeLocked(node, lk);
            renderTune(node._k2v3_tunemeta || {});
            refreshSummary(node);
        }

        function renderTune(data) {
            if (data) { node._k2v3_tunemeta = data; }
            const meta = data || node._k2v3_tunemeta || {};
            const fmap = meta.fields || {};
            const ids = Object.keys(fmap).filter((k) => (fmap[k] || []).length);
            tuneWrap.innerHTML = "";
            if (!ids.length) {
                tuneWrap.appendChild(h("div", `font-size:11px;color:${p.sub};`, "（取不到字段选项表）"));
                return;
            }
            const sections = meta.field_sections || {};
            const sectionZh = meta.section_zh || {};
            const lk = readLocked(node);

            // ---------- ① 工具条 ----------
            const bar = h("div", "display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:9px;");
            const search = document.createElement("input");
            search.placeholder = "🔍 筛选字段（中文名 / 英文 id / 已锁定值）…";
            search.value = tuneQuery;
            search.style.cssText = `flex:1;min-width:170px;box-sizing:border-box;
                border:1px solid ${p.border};background:${p.card};color:${p.text};
                border-radius:8px;padding:6px 10px;font-size:12px;outline:none;`;
            search.oninput = () => { tuneQuery = search.value.trim().toLowerCase(); renderTune(meta); };
            bar.appendChild(search);

            const lockedKeys = Object.keys(lk);

            const bLockAll = mkBtn("🔒 锁定当前整套值", p, true);
            bLockAll.title = "把最近一次生成结果里**所有**字段的取值一次性锁死"
                + "（需要先跑过一次，槽位表才有内容）";
            bLockAll.onclick = () => {
                const sl = node._k2v3_slots || {};
                const keys = Object.keys(sl).filter(
                    (k) => fmap[k] && String(sl[k] || "").trim());
                if (!keys.length) {
                    alert("还没有可锁的取值。先跑一次出题，再来点这个按钮。");
                    return;
                }
                const out = readLocked(node);
                keys.forEach((k) => { out[k] = String(sl[k]); });
                writeLocked(node, out);
                renderTune(meta);
                refreshSummary(node);
            };
            bar.appendChild(bLockAll);

            const bReroll = mkBtn("🎲 重摇全部已锁", p);
            bReroll.title = "保持锁定项的个数不变，每项各换一个新的候选取值";
            bReroll.disabled = !lockedKeys.length;
            bReroll.onclick = () => {
                const out = readLocked(node);
                let n = 0;
                Object.keys(out).forEach((fid) => {
                    const arr = fmap[fid] || [];
                    if (!arr.length) return;
                    const pool = arr.filter((o) => o.v !== out[fid]);
                    const use = pool.length ? pool : arr;
                    out[fid] = use[Math.floor(Math.random() * use.length)].v;
                    n += 1;
                });
                if (n) { writeLocked(node, out); renderTune(meta); refreshSummary(node); }
            };
            bar.appendChild(bReroll);

            const bClear = mkBtn("🗑 清空全部锁定", p);
            bClear.disabled = !lockedKeys.length;
            bClear.onclick = () => {
                writeLocked(node, {});
                renderTune(meta);
                refreshSummary(node);
            };
            bar.appendChild(bClear);
            tuneWrap.appendChild(bar);

            const slotsN = Object.keys(node._k2v3_slots || {}).length;
            tuneWrap.appendChild(h("div",
                `font-size:10.5px;color:${p.sub};margin-bottom:10px;line-height:1.6;`,
                "已锁定 " + lockedKeys.length + " 项"
                + (lockedKeys.length ? "：" + lockedKeys.map(
                    (k) => fieldZh(k) + "=" + lk[k]).join("、") : "")
                + "　｜　最近一次生成"
                + (slotsN ? "有槽位数据（可点「锁定当前整套值」）"
                    : "还没有槽位数据（先跑一次出题）")));

            // ---------- ② 候选项区（当前选中字段）----------
            if (tuneSel && fmap[tuneSel]) {
                const box = h("div", cardStyle(true, p) + "padding:10px 12px;margin-bottom:11px;");
                const tHead = h("div", "display:flex;align-items:center;gap:8px;flex-wrap:wrap;");
                tHead.appendChild(h("span", `font-size:12.5px;font-weight:700;color:${p.text};`,
                    fieldZh(tuneSel)));
                tHead.appendChild(h("span", `font-size:10.5px;color:${p.sub};`, tuneSel));
                const cur = curValueOf(tuneSel);
                if (cur) {
                    tHead.appendChild(h("span", `font-size:10.5px;color:${p.sub};`,
                        "本次实际值：" + cur));
                }
                if (lk[tuneSel] !== undefined) {
                    tHead.appendChild(h("span", `font-size:10.5px;color:${p.accent};`,
                        "已锁定：" + lk[tuneSel]));
                    const bx = mkBtn("✕ 取消锁定", p);
                    bx.onclick = () => tuneLock(tuneSel, null);
                    tHead.appendChild(bx);
                }
                const bClose = mkBtn("收起", p);
                bClose.onclick = () => { tuneSel = ""; renderTune(meta); };
                tHead.appendChild(bClose);
                box.appendChild(tHead);

                // 候选值：铺成可点的格子（点一下 = 锁定为该值）
                const chips = h("div", "display:flex;flex-wrap:wrap;gap:6px;margin-top:9px;");
                (fmap[tuneSel] || []).forEach((o) => {
                    const on = lk[tuneSel] === o.v;
                    const c = h("div", `cursor:pointer;font-size:11.5px;padding:4px 9px;
                        border-radius:8px;line-height:1.4;
                        border:1px solid ${on ? p.accent : p.border};
                        background:${on ? p.accent + "22" : p.card};
                        color:${on ? p.accent : p.text};`, o.t || o.v);
                    c.title = "锁定为「" + o.v + "」";
                    c.onclick = () => tuneLock(tuneSel, o.v);
                    chips.appendChild(c);
                });
                box.appendChild(chips);

                // 手输：想写词库外的值（例如"银白挑染"）时用
                const irow = h("div", "display:flex;gap:8px;margin-top:9px;align-items:center;");
                const input = document.createElement("input");
                input.placeholder = "或手输任意值（可写词库外的描述）…";
                input.value = (lk[tuneSel] !== undefined
                    && !(fmap[tuneSel] || []).some((o) => o.v === lk[tuneSel]))
                    ? lk[tuneSel] : "";
                input.style.cssText = `flex:1;min-width:120px;box-sizing:border-box;
                    border:1px solid ${p.border};background:${p.card};color:${p.text};
                    border-radius:8px;padding:6px 10px;font-size:12px;outline:none;`;
                const bSet = mkBtn("锁定为这个值", p);
                const apply = () => {
                    const v = input.value.trim();
                    if (!v) return;
                    tuneLock(tuneSel, v);
                };
                bSet.onclick = apply;
                input.onkeydown = (e) => { if (e.key === "Enter") apply(); };
                irow.appendChild(input);
                irow.appendChild(bSet);
                box.appendChild(irow);
                tuneWrap.appendChild(box);
            }

            // ---------- ③ 字段表（按段落折叠）----------
            const match = (fid) => {
                if (!tuneQuery) return true;
                const hay = (fid + " " + fieldZh(fid) + " " + (lk[fid] || "")).toLowerCase();
                return hay.indexOf(tuneQuery) >= 0;
            };
            const order = ["camera", "light", "person", "hair", "makeup", "expression",
                "cloth", "pose", "bg", "comp", "extra", "other"];
            // 搜索时默认全展开（否则"搜到了却看不见"），平时只展开人物/发型两组
            const HOME_OPEN = ["person", "hair"];
            const seen = new Set();
            let shown = 0;
            node._k2v3_tune_sec = node._k2v3_tune_sec || {};
            order.forEach((secId) => {
                const list = (sections[secId] || []).filter(
                    (fid) => fmap[fid] && fmap[fid].length && match(fid));
                if (!list.length) return;
                list.forEach((f) => seen.add(f));
                shown += list.length;
                const open = tuneQuery ? true
                    : (node._k2v3_tune_sec[secId] !== undefined
                        ? !!node._k2v3_tune_sec[secId]
                        : HOME_OPEN.indexOf(secId) >= 0);
                const grp = h("div", "margin-bottom:8px;");
                const gh = h("div", `display:flex;align-items:center;gap:7px;cursor:pointer;
                    padding:6px 9px;border:1px solid ${p.border};border-radius:8px;
                    background:${p.card};font-size:12px;font-weight:700;color:${p.text};
                    user-select:none;`);
                gh.appendChild(h("span", "font-size:10.5px;", open ? "▼" : "▶"));
                gh.appendChild(h("span", "flex:1;", sectionZh[secId] || secId));
                gh.appendChild(h("span", `font-size:10px;font-weight:400;color:${p.sub};`,
                    list.length + " 项"));
                liftable(gh);
                const gb = h("div", `display:${open ? "block" : "none"};
                    padding:7px 0 2px 12px;margin-left:7px;border-left:2px solid ${p.border};`);
                gh.onclick = () => {
                    const now = gb.style.display === "none";
                    gb.style.display = now ? "block" : "none";
                    gh.firstChild.textContent = now ? "▼" : "▶";
                    node._k2v3_tune_sec[secId] = now;
                };
                const rows = h("div", "display:flex;flex-wrap:wrap;gap:6px;");
                list.forEach((fid) => {
                    const isLocked = lk[fid] !== undefined;
                    const cur2 = curValueOf(fid);
                    const row = h("div", `cursor:pointer;font-size:11px;padding:4px 9px;
                        border-radius:8px;line-height:1.45;display:flex;gap:6px;align-items:center;
                        border:1px solid ${isLocked ? p.accent : p.border};
                        background:${isLocked ? p.accent + "1a" : p.card};
                        color:${isLocked ? p.accent : p.text};`);
                    row.appendChild(h("span", `font-weight:${isLocked ? 700 : 400};`,
                        fieldZh(fid)));
                    if (isLocked) {
                        row.appendChild(h("span", "font-size:10px;opacity:.85;",
                            "🔒 " + lk[fid]));
                    } else if (cur2) {
                        row.appendChild(h("span", `font-size:10px;color:${p.sub};`, cur2));
                    }
                    row.title = "点一下选中该字段，再从上面的候选里挑一个值"
                        + (cur2 ? "；本次实际值：" + cur2 : "")
                        + (lk[fid] !== undefined ? "；已锁定：" + lk[fid] : "");
                    row.onclick = () => {
                        tuneSel = (tuneSel === fid) ? "" : fid;
                        renderTune(meta);
                    };
                    rows.appendChild(row);
                });
                gb.appendChild(rows);
                grp.appendChild(gh);
                grp.appendChild(gb);
                tuneWrap.appendChild(grp);
            });
            // 兜底：不在分组表里的字段（前后端分组表不同步时也不至于"消失"）
            const rest = ids.filter((f) => !seen.has(f) && match(f));
            if (rest.length) {
                const grp = h("div", `font-size:11px;color:${p.sub};margin-top:4px;`, "");
                grp.textContent = "未分组字段：" + rest.map(fieldZh).join("、");
                tuneWrap.appendChild(grp);
                shown += rest.length;
            }
            if (!shown) {
                tuneWrap.appendChild(h("div", `font-size:11px;color:${p.sub};`,
                    "没有匹配「" + tuneQuery + "」的字段。"));
            }
        }
        // 【v3.18】初始化：① 内容强度（唯一等级控制项）；② 随机性；
        //   ③ 逐项开关；④ 字段微调；⑤ 权重；⑥ 人物与角色；
        //   ⑦ NSFW 内容权重（页面最底部）。
        //   「内容等级 / 内容边界 / NSFW 强度」三个已退休控件不再渲染。
        renderIntensity();
        renderRandom();
        renderSub();
        fetchExtra().then((d) => { renderAct(d); renderShoeWeights(d); });
        fetchFields().then((d) => { renderOff(d); renderTune(d); });
        fetchAxes().then(renderWeights);
    });
}

/* ---------- 选中预览 ---------- */
function refreshSelectedPreview(node) {
    const box = node._k2v3_preview;
    if (!box) return;
    const p = palette();
    box.innerHTML = "";
    const s = v311(node);
    const cur = s.charA;
    if (!cur || cur === "自动") {
        // 双人且只选了角色 B 时，A 仍然是「自动」，但 B 要显示出来
        if (!(s.double && s.charB && s.charB !== "自动")) {
            box.appendChild(h("div", `font-size:12px;color:${p.sub};`, "未绑定角色（自动）。点「👤 角色图鉴」选一个。"));
            return;
        }
    }
    fetchCharacters().then((data) => {
        const pick = (name) => data.characters.find((x) => x.value === name);
        const rowOf = (c, label) => {
            const row = h("div", "display:flex;gap:10px;align-items:stretch;margin-bottom:6px;");
            const clip = h("div", `position:relative;width:64px;flex:0 0 64px;aspect-ratio:3/4;
                border-radius:8px;overflow:hidden;background:linear-gradient(135deg,${c.work_color}33,${c.work_color}11);`);
            if (c.img) {
                const img = document.createElement("img");
                img.src = thumbURL(c.img);
                img.style.cssText = "position:absolute;inset:0;width:100%;height:100%;object-fit:cover;";
                img.onerror = () => img.remove();
                clip.appendChild(img);
            }
            row.appendChild(clip);
            const info = h("div", "flex:1;min-width:0;");
            if (label) info.appendChild(h("div", `font-size:10px;color:${p.accent};`,
                label));
            info.appendChild(h("div", `font-size:13px;font-weight:700;color:${p.text};`, c.zh || c.en));
            info.appendChild(h("div", `font-size:11px;color:${p.sub};margin-top:2px;`, c.work_zh + " · " + (c.en || "")));
            const attrs = [c.build_zh, c.fig_zh].filter(Boolean).join(" · ");
            if (attrs) info.appendChild(h("div", `font-size:11px;color:${p.accent};margin-top:3px;`, attrs));
            if (c.word) info.appendChild(h("div", `font-size:10.5px;color:${p.sub};margin-top:3px;
                overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`, "角色词：" + c.word));
            row.appendChild(info);
            return row;
        };
        const fallback = (name) => h("div", `font-size:12px;color:${p.sub};margin-bottom:6px;`, "角色：" + name);
        if (cur && cur !== "自动") {
            const c = pick(cur);
            box.appendChild(c ? rowOf(c, s.double ? "角色 A（正文主体）" : "") : fallback(cur));
        }
        // 【v3.17】双人模式下把角色 B 一起显示 —— 两个人都要对得上才敢出图
        if (s.double && s.charB && s.charB !== "自动") {
            const c = pick(s.charB);
            box.appendChild(c ? rowOf(c, "角色 B（双人段）") : fallback(s.charB));
        }
    });
}

const DISCLAIMER_TEXT =
    "本插件为<b>开源插件</b>（Comfyui-Krea2-Portrait-V3），按原样提供、用于 ComfyUI 工作流内的"
    + "提示词生成。生成过程<b>全部在你本机完成</b>，不联网、不上传任何内容。"
    // 【v3.20】署名与来源：放在免责声明里，是因为这是**插件唯一常驻的"关于"入口** ——
    //   三处入口（顶部横幅 / 快捷操作行 / 设置面板顶部）都能看到，不需要额外加按钮。
    + "<br>· 插件作者：<b>灵冻不冻</b>"
    + "<br>· 插件底层框架源于脚本：K2_V3_融合版.html（HTML 版人像提示词生成器）"
    + "已授权开源 —— 作者：飞蓬。"
    + "<br>① 所有角色一律视为<b>18 岁以上虚构人物</b>，禁止用于真实人物、未成年人、"
    + "非自愿及暴力伤害相关内容。"
    + "<br>② 角色名与作品名等知识产权归各自权利人所有，此处仅作提示词检索与描述；"
    + "角色头像取自公开图库，仅用于识别对照，不作他用。"
    + "<br>③ 提示词是<b>词库模板的随机组合</b>，不保证每条都符合预期 —— "
    + "正式出片前请自行筛选；同一设置换个种子结果就不同。"
    + "<br>④ 调高「内容强度」会让措辞明显更露骨，"
    + "请自行确认是否符合你所在平台的发布政策。"
    + "<br>⑤ <b>生成内容及其用途由你自行负责</b>，本插件不对由此产生的任何后果承担责任。";

const DISCLAIMER_KEY = "k2v3_disclaimer_closed_v1";

function disclaimerClosed() {
    try { return localStorage.getItem(DISCLAIMER_KEY) === "1"; } catch (e) { return false; }
}

function closeDisclaimer() {
    try { localStorage.setItem(DISCLAIMER_KEY, "1"); } catch (e) { /* noop */ }
}

/* --------------------------------------------------------------------------
 * 【v3.16 修复】免责声明此前是「一次性自毁横幅」——
 *
 *   buildDisclaimer() 在 localStorage 记到已关闭时**直接 return null**，
 *   于是 node.addDOMWidget("k2v3_notice", …) 整块不注册，而界面上
 *   **没有任何其它入口**能再看到它。结果：用户点过一次「关闭」，
 *   免责声明就永远消失了 —— 这正是"插件界面里看不到免责声明"的原因。
 *
 * 改法（两条，互为兜底）：
 *   ① 横幅**永远注册**。点「关闭」只把内容收成一行窄条，
 *      右侧保留「展开全文」，随时能展开回来 —— 不再删除任何东西。
 *   ② 另加常驻入口：快捷操作行与设置面板里各有一个「📜 免责声明」，
 *      点开是弹窗全文（弹窗内可滚动，保证**完整**显示，不受节点宽度挤压）。
 * -------------------------------------------------------------------------- */

/** 免责声明弹窗：全文、可滚动、不依赖 localStorage 状态 */
function openDisclaimer(node) {
    openModal(node, "📜 免责声明 · 使用条款与风险提示", (body, p) => {
        const art = h("div", `font-size:13px;line-height:1.95;color:${p.text};
            padding:15px 18px;background:${p.warn}12;border:1px solid ${p.warn}55;
            border-radius:10px;`);
        // DISCLAIMER_TEXT 用 <br> 分隔条目：首段是总述，其余是 ①–⑤。
        // 弹窗里拆成独立段落并加大行距 —— 节点里受宽度挤压看不全的正文，这里能完整读。
        DISCLAIMER_TEXT.split("<br>").forEach((seg, i) => {
            if (!seg.trim()) return;
            const line = h("div", i ? "margin-top:11px;" : "");
            line.innerHTML = seg;
            art.appendChild(line);
        });
        body.appendChild(art);

        const extra = h("div", `font-size:12px;line-height:1.9;color:${p.sub};margin-top:14px;`);
        extra.innerHTML = "· 本插件为开源插件，不含任何模型权重，也不采集、不上传你的数据。<br>"
            + "· 角色图仅用于「识别对照」——帮你确认选中的是哪一个角色。<br>"
            + "· 「内容强度」是唯一的等级控制项，默认「全年龄」；需要更露骨的内容请自行调高。<br>"
            + "· 关闭本弹窗后，随时可再次点开：快捷操作行的「📜 免责声明」，"
            + "或「⚙️ 设置」面板顶部。";
        body.appendChild(extra);

        const foot = h("div", "display:flex;gap:8px;margin-top:16px;");
        const bClose = mkBtn("我知道了", p, true);
        bClose.onclick = closeModal;
        foot.appendChild(bClose);
        body.appendChild(foot);
    });
}

/**
 * 面板内的免责声明横幅。**永远返回元素**（不再返回 null）。
 *
 * 收起态只留一行，展开态显示全文 —— 两种状态都在同一个 DOM 里切换，
 * 不会出现"关掉就再也找不到"的情况。
 */
function buildDisclaimer(p, node) {
    const box = h("div", "");
    const render = () => {
        box.innerHTML = "";
        if (disclaimerClosed()) {
            // —— 收起态：一行窄条 + 两个入口 ——
            box.style.cssText = `display:flex;align-items:center;flex-wrap:wrap;gap:8px;
                padding:7px 11px;background:${p.card};border:1px solid ${p.border};
                border-radius:8px;`;
            box.appendChild(h("span", `flex:1;min-width:170px;font-size:11.5px;color:${p.sub};`,
                "⚠ 免责声明已收起（仅界面提示，不影响出图流程）"));
            const bModal = h("button", `border:1px solid ${p.warn};color:${p.warn};
                background:transparent;border-radius:7px;padding:3px 10px;cursor:pointer;
                font-size:11.5px;`, "📜 查看全文");
            bModal.onclick = () => openDisclaimer(node);
            box.appendChild(bModal);
            const bOpen = h("button", `border:1px solid ${p.border};color:${p.text};
                background:transparent;border-radius:7px;padding:3px 10px;cursor:pointer;
                font-size:11.5px;`, "展开");
            bOpen.onclick = () => {
                try { localStorage.removeItem(DISCLAIMER_KEY); } catch (e) { /* noop */ }
                render();
            };
            box.appendChild(bOpen);
            return;
        }
        // —— 展开态：全文（节点内按宽度自动折行，不截断）——
        box.style.cssText = `font-size:11.5px;line-height:1.75;padding:9px 11px;
            background:${p.warn}14;border:1px solid ${p.warn}55;border-radius:8px;
            color:${p.text};position:relative;`;
        const txt = h("div", "padding-right:60px;");
        txt.innerHTML = "⚠ 免责声明　" + DISCLAIMER_TEXT;
        box.appendChild(txt);
        const bModal = h("button", `position:absolute;right:8px;bottom:8px;
            border:1px solid ${p.warn};color:${p.warn};background:transparent;
            border-radius:6px;padding:2px 9px;cursor:pointer;font-size:11px;`, "放大查看");
        bModal.onclick = () => openDisclaimer(node);
        box.appendChild(bModal);
        const bHide = h("button", `position:absolute;right:8px;top:8px;
            border:1px solid ${p.warn};color:${p.warn};background:transparent;
            border-radius:6px;padding:2px 9px;cursor:pointer;font-size:11px;`, "收起");
        // ⚠️ 「收起」只切状态，不再销毁元素 —— 这是本次修复的核心
        bHide.onclick = () => { closeDisclaimer(); render(); };
        box.appendChild(bHide);
    };
    render();
    return box;
}

/* ---------- 历史 / 收藏 ---------- */
function fetchHistory(limit) {
    return api.fetchApi("/krea2-v3/history?limit=" + (limit || 30))
        .then((r) => r.json()).catch(() => ({ ok: false, items: [], total: 0 }));
}
function fetchFavorites() {
    return api.fetchApi("/krea2-v3/favorites").then((r) => r.json())
        .catch(() => ({ ok: false, items: [], total: 0 }));
}
function addFavorite(entry) {
    return api.fetchApi("/krea2-v3/favorites", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entry || {}),
    }).then((r) => r.json()).catch(() => ({ ok: false }));
}
function removeFavorite(id) {
    return api.fetchApi("/krea2-v3/favorites/remove", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
    }).then((r) => r.json()).catch(() => ({ ok: false }));
}
function fetchEntry(id) {
    return api.fetchApi("/krea2-v3/entry?id=" + encodeURIComponent(id || ""))
        .then((r) => r.json()).catch(() => ({ ok: false }));
}

/** 一键复用：把历史/收藏条目里的参数写回节点控件 */
function reuseEntry(node, e) {
    if (!e) return;
    setWidgetVal(node, "随机种子", Number(e.seed || 0));
    setWidgetVal(node, "每次运行重随机", false);   // 复用 = 固定种子，必须关掉重随机
    if (e.character) setWidgetVal(node, "角色", e.character);
    if (e.weight) setWidgetVal(node, "权重覆盖", e.weight);
    if (e.locked && typeof e.locked === "object") {
        setWidgetVal(node, "锁定字段", JSON.stringify(e.locked, null, 0));
    }
    // 【v3.18】档位只认「内容强度」：历史里的 intensity 就是它；旧记录只有 level 时
    //   反查档位名再写回去，否则复用回来的还是「手动（分别设置）」→ 掉到兼容兜底。
    if (e.intensity && INTENSITY_TO_LEVEL[e.intensity]) {
        setWidgetVal(node, "内容强度", e.intensity);
    } else if (e.level) {
        const nm = Object.keys(INTENSITY_TO_LEVEL)
            .find((k) => INTENSITY_TO_LEVEL[k] === e.level);
        if (nm) setWidgetVal(node, "内容强度", nm);
    }
    // 【v3.11】人物数量 / 纹身面积 / 百合 也要一并还原，否则复用回来的人数是旧的
    if (e.person_count) setWidgetVal(node, "人物数量", e.person_count);
    if (e.tattoo_area) setWidgetVal(node, "纹身面积", e.tattoo_area);
    // 【v3.17】百合模式还原成**手动的原值**：历史里的 e.yuri 是"生效值"
    //   （双人+NSFW 档时后端会自动置真），直接写回去会把"自动"变成"手动锁定"，
    //   于是切回 SFW 档时还留着百合 —— 那就不是复现，是改行为。
    setWidgetVal(node, "百合模式", !!e.yuri && !e.yuri_auto);
    // 【v3.17】双人第二位角色
    if (e.character2) setWidgetVal(node, "第二角色", e.character2);

    // 【v3.18/v3.19】NSFW 内容项权重（缺项就保持当前值，不动）+ 是否跟随等级
    Object.keys(ACT_CTL).forEach((a) => {
        const w = (e.act_weights || {})[a];
        if (w != null && getWidget(node, ACT_CTL[a])) setWidgetVal(node, ACT_CTL[a], Number(w));
    });
    if (e.act_auto != null) setWidgetVal(node, ACT_AUTO_CTL, !!e.act_auto);
    // 【v3.20】外观锁定开关：历史里有就还原，没有（旧记录）就保持默认「开」
    if (e.app_lock != null) setWidgetVal(node, "角色外观锁定", !!e.app_lock);
    // 【v3.22】鞋履种类权重 + 水面波光权重（缺项保持当前值，不动）
    Object.keys(e.shoe_weights || {}).forEach((c) => {
        const w = Number(e.shoe_weights[c]);
        if (!isNaN(w) && getWidget(node, c + "权重")) setWidgetVal(node, c + "权重", w);
    });
    if (e.water_weight != null) setWidgetVal(node, "水面反光权重", Number(e.water_weight));
    refreshSelectedPreview(node);
    refreshContentRow(node);
    refreshSummary(node);
}

/** 历史/收藏条目 -> 一行元数据文本（列表与放大查看共用，避免两处各写一遍） */
function entryMeta(e) {
    const bits = [];
    if (e.seed != null) bits.push("种子 " + e.seed);
    if (e.level) bits.push("等级 " + e.level);
    if (e.intensity) bits.push("强度 " + e.intensity);
    if (e.character) bits.push("角色 " + e.character);
    if (e.character2) bits.push("角色B " + e.character2);
    if (e.person_count) bits.push(e.person_count);
    if (e.act) bits.push("内容项 " + e.act);
    if (e.n) bits.push("字数 " + e.n);
    if (e.app_lock === false) bits.push("外观锁定 关");
    if (e.ts) {
        try { bits.push(new Date(e.ts * 1000).toLocaleString()); } catch (err) { }
    }
    return bits.join("　");
}

function openHistory(node) {
    openModal(node, "历史 / 收藏 · 复制 · 放大 · 一键复用", (body, p) => {
        let tab = "his";              // his / fav
        let q = "";                   // 搜索关键词
        const bar = h("div", "display:flex;gap:8px;margin-bottom:10px;align-items:center;flex-wrap:wrap;");
        const bHis = mkBtn("🕘 历史", p, true);
        const bFav = mkBtn("⭐ 收藏", p);
        bar.appendChild(bHis); bar.appendChild(bFav);
        const search = document.createElement("input");
        search.placeholder = "🔍 搜索：角色 / 正文内容 / 种子…";
        search.style.cssText = `flex:1;min-width:180px;box-sizing:border-box;
            border:1px solid ${p.border};background:${p.card};color:${p.text};
            border-radius:8px;padding:6px 11px;font-size:12px;outline:none;`;
        search.oninput = () => { q = search.value.trim().toLowerCase(); render(); };
        bar.appendChild(search);
        liftable(search);
        body.appendChild(bar);
        const countLine = h("div", `font-size:10.5px;color:${p.sub};margin-bottom:9px;`, "");
        body.appendChild(countLine);
        const list = h("div", "");
        body.appendChild(list);

        /** 关键词匹配：角色 / 角色B / 正文 / 种子 / 内容项 都算 */
        function hit(e) {
            if (!q) return true;
            const hay = [e.character, e.character2, e.text, e.seed, e.act,
                e.level, e.intensity].map((x) => String(x == null ? "" : x))
                .join(" ").toLowerCase();
            return hay.indexOf(q) >= 0;
        }

        function row(e, isFav) {
            const r = h("div", cardStyle(false, p) + "padding:9px 11px;margin-bottom:8px;");
            const head = h("div", `font-size:11px;color:${p.sub};`);
            head.textContent = entryMeta(e);
            r.appendChild(head);

            const txt = h("div", `font-size:12px;color:${p.text};margin-top:4px;
                max-height:56px;overflow:hidden;line-height:1.6;cursor:zoom-in;`);
            txt.textContent = (e.text || "").slice(0, 160) + ((e.text || "").length > 160 ? "…" : "");
            txt.title = "点一下放大看全文";
            txt.onclick = () => openTextView("提示词 · 全文", e.text || "", entryMeta(e));
            r.appendChild(txt);

            const acts = h("div", "display:flex;gap:8px;margin-top:7px;flex-wrap:wrap;");
            // 【v3.20】快捷复制（不进放大页就能拿到整条提示词）
            const bCopy = mkBtn("📋 复制提示词", p, true);
            bCopy.title = "复制这条的完整提示词（不是列表里那 160 字的预览）";
            bCopy.onclick = () => copyText(e.text || "", bCopy, "📋 复制提示词");
            acts.appendChild(bCopy);
            const bZoom = mkBtn("🔍 放大查看", p);
            bZoom.title = "弹窗显示全文，可就地编辑后复制";
            bZoom.onclick = () => openTextView("提示词 · 全文", e.text || "", entryMeta(e));
            acts.appendChild(bZoom);

            const bRe = mkBtn("↩ 复用此参数", p);
            bRe.onclick = () => { reuseEntry(node, e); closeModal(); };
            acts.appendChild(bRe);
            if (isFav) {
                const bDel = mkBtn("🗑 删除收藏", p);
                bDel.onclick = () => { removeFavorite(e.id).then(() => render()); };
                acts.appendChild(bDel);
            } else {
                const bF = mkBtn("⭐ 收藏", p);
                bF.onclick = () => { addFavorite(e).then(() => render()); };
                acts.appendChild(bF);
            }
            r.appendChild(acts);
            return r;
        }

        function render() {
            bHis.style.borderColor = tab === "his" ? p.accent : p.border;
            bFav.style.borderColor = tab === "fav" ? p.accent : p.border;
            list.innerHTML = "";
            list.appendChild(h("div", `font-size:12px;color:${p.sub};`, "加载中…"));
            const fetcher = tab === "his" ? fetchHistory(30) : fetchFavorites();
            fetcher.then((d) => {
                list.innerHTML = "";
                const items = (d.items || []).filter(hit);
                const all = (d.items || []).length;
                countLine.textContent = (tab === "his" ? "历史 " : "收藏 ")
                    + all + " 条" + (q ? "，匹配 " + items.length + " 条" : "")
                    + "　（点正文可放大；📋 直接复制全文）";
                if (!items.length) {
                    list.appendChild(h("div", `font-size:12px;color:${p.sub};`,
                        (tab === "his" ? "还没有历史记录，执行一次后这里会出现。"
                            : "还没有收藏。")
                        + (q ? "（当前搜索无匹配）" : "")));
                    return;
                }
                items.forEach((e) => list.appendChild(row(e, tab === "fav")));
            });
        }

        bHis.onclick = () => { tab = "his"; render(); };
        bFav.onclick = () => { tab = "fav"; render(); };
        render();
    });
}

/* ---------- 收起原始控件（对齐 Krea2-Portrait 的 compact 布局） ----------
 * ⚠️ 必须是「黑名单式」：只保留 k2v3_ 面板控件可见，其余逐个收起。
 *    收起 ≠ 删除 —— 值与连线照常参与运算、照常存进工作流。
 */
const NEVER_COLLAPSE = ["k2v3_intro", "k2v3_notice", "k2v3_summary", "k2v3_row_action",
    "k2v3_row_theme", "k2v3_row_person", "k2v3_preview", "k2v3_out", "k2v3_outbar",
    "k2v3_report"];

/** 复制到剪贴板：优先 navigator.clipboard，失败退回 execCommand（有些浏览器非 https 下禁用前者） */
function copyText(text, btn, okLabel) {
    const s = String(text || "");
    if (!s.trim()) { if (btn) btn.textContent = "没有内容"; setTimeout(() => { if (btn) btn.textContent = okLabel; }, 1200); return; }
    const done = () => {
        if (!btn) return;
        const old = btn.textContent;
        btn.textContent = "✓ 已复制";
        setTimeout(() => { btn.textContent = old; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(s).then(done).catch(() => fallbackCopy(s, done));
    } else {
        fallbackCopy(s, done);
    }
}

function fallbackCopy(s, done) {
    try {
        const ta = document.createElement("textarea");
        ta.value = s;
        ta.style.cssText = "position:fixed;left:-9999px;top:0;";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        done();
    } catch (e) { /* noop */ }
}

/** 把文本存成本地 .txt 文件（纯前端下载，不经过后端） */
function downloadText(text, filename) {
    try {
        const blob = new Blob([String(text || "")], { type: "text/plain;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename || "krea2-v3-prompt.txt";
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
    } catch (e) { /* noop */ }
}

function collapseWidget(node, w) {
    if (!w || NEVER_COLLAPSE.indexOf(w.name) >= 0) return;
    w.hidden = true;
    w.computeSize = () => [0, -4];
    // 有些控件（BOOLEAN / 纯展示型）没有 inputEl，或 inputEl 上没有 style，直接跳过即可
    if (w.inputEl && w.inputEl.style) w.inputEl.style.display = "none";
}

function applyCompactLayout(node) {
    (node.widgets || []).forEach((w) => collapseWidget(node, w));
}

/* ---------- 小工具：节标题 / 按钮 ---------- */
function secTitle(text, p) {
    return h("div", `font-size:12px;font-weight:700;color:${p.accent};
        margin:12px 0 6px;letter-spacing:.5px;`, text);
}

function mkBtn(label, p, primary) {
    const b = h("button", `border:1px solid ${primary ? p.accent : p.border};
        color:${primary ? p.accent : p.text};background:${primary ? "transparent" : "transparent"};
        border-radius:8px;padding:6px 13px;cursor:pointer;font-size:12.5px;`, label);
    return b;
}

/* ---------- 面板安装（分区结构对齐 Krea2-Portrait） ---------- */
function installPanel(node) {
    const p = palette();

    // ① 说明
    const intro = h("div", `font-size:11px;color:${p.sub};line-height:1.7;
        padding:8px 10px;background:${p.card};border:1px solid ${p.border};border-radius:8px;`,
        "Krea2-Portrait-V3 · 随机出题：每次 Queue 自动出一条新提示词。"
        + "出题 = 从 63 个词库池（949 条）里按权重随机组合，同一套设置换个种子就是另一条，"
        + "所以不必刻意囤词，记下「随机种子」就能复现同一条。"
        + "出完直接在下方文本框里选中/编辑，点「📋 复制提示词」拿走，不需要接任何其它节点。"
        + "设置面板里每个功能区都有**折叠按钮**（▶/▼）可以单独展开，一屏看完全部："
        + "① 内容强度（唯一的 NSFW 等级控制项）· ② 随机性 · ③ 逐项开关 · ④ 字段微调 · ⑤ 权重 · "
        + "⑥ 人物与角色（单人／双人 · 百合 · 角色 B）· ⑦ NSFW 内容权重（设置页最底部）；"
        + "主界面下方「内容设定」一行只回显当前人数状态，另留一个进设置的入口。");
    node.addDOMWidget("k2v3_intro", "intro", intro,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    // ①b 免责声明
    //   【v3.16 修复】原来这里拿到 buildDisclaimer 的结果后，用 `if (notice)`
    //   守卫再注册 —— 而 buildDisclaimer 在「已关闭」时返回 null，
    //   于是整个控件不注册，别处又没有入口，导致免责声明一旦收起就永远看不到。
    //   现在 buildDisclaimer **永远返回元素**（收起态是一行窄条 + 展开按钮），
    //   这里也就**无条件注册**。
    const notice = buildDisclaimer(p, node);
    node._k2v3_notice = notice;
    node.addDOMWidget("k2v3_notice", "notice", notice,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    // ② 已选摘要 + 设置回显
    const summary = h("div", `font-size:11.5px;color:${p.text};line-height:1.65;
        padding:8px 10px;background:${p.card};border:1px solid ${p.border};border-radius:8px;`);
    node._k2v3_summary = summary;
    node.addDOMWidget("k2v3_summary", "summary", summary,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    // ③ 快捷操作
    const rowAction = h("div", "display:flex;flex-wrap:wrap;gap:8px;");
    const bRoll = mkBtn("🎲 一键随机", p, true);
    bRoll.onclick = () => {
        setWidgetVal(node, "每次运行重随机", true);
        setWidgetVal(node, "随机种子", Math.floor(Math.random() * 0xFFFFFFFF));
        refreshSummary(node);
    };
    const bSeed = mkBtn("🔁 换个种子", p);
    bSeed.onclick = () => {
        setWidgetVal(node, "随机种子", Math.floor(Math.random() * 0xFFFFFFFF));
        refreshSummary(node);
    };
    const bChar = mkBtn("👤 角色", p);
    bChar.onclick = () => { openCharacters(node); refreshSummary(node); };
    const bClear = mkBtn("🧹 清空", p);
    bClear.onclick = () => {
        setWidgetVal(node, "角色", "自动");
        setWidgetVal(node, "第二角色", "自动");     // 【v3.17】双人第二位角色
        setWidgetVal(node, "角色预设", "不使用");
        setWidgetVal(node, "权重覆盖", "");
        setWidgetVal(node, "锁定字段", "");
        // 【v3.11】新控件一并回到"不干预 / 默认"值
        setWidgetVal(node, "纹身面积", "跟随随机");
        setWidgetVal(node, "人物数量", "单人");
        setWidgetVal(node, "百合模式", false);
        // 【v3.13】新控件
        setWidgetVal(node, "内容强度", "全年龄");
        setWidgetVal(node, "联动强度", 1.0);
        setWidgetVal(node, "去重记忆", 0);
        setWidgetVal(node, "多样性温度", 1.0);
        setWidgetVal(node, "关闭可选项", "");
        setWidgetVal(node, "批次数", 1);
        // 【v3.18/v3.19】内容项权重回到「标准比例」，自动档回到「关闭（手动）」
        Object.keys(ACT_CTL).forEach((a) => setWidgetVal(node, ACT_CTL[a], ACT_W_DEFAULT));
        setWidgetVal(node, ACT_AUTO_CTL, false);
        // 【v3.20】外观锁定回到默认「开」（这是用户要求的一致性默认值）
        setWidgetVal(node, "角色外观锁定", true);
        // 【v3.22】鞋履种类权重 + 水面波光权重回到「标准比例」
        ["高跟鞋", "靴子", "运动鞋", "凉鞋", "皮鞋", "赤脚", "水面反光"].forEach(
            (n) => setWidgetVal(node, n + "权重", 1.0));
        refreshSelectedPreview(node);
        refreshContentRow(node);
        refreshSummary(node);
    };
    rowAction.appendChild(bRoll);
    rowAction.appendChild(bSeed);
    rowAction.appendChild(bChar);
    rowAction.appendChild(bClear);
    node.addDOMWidget("k2v3_row_action", "action", rowAction,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    // ④ 重要入口
    const rowTheme = h("div", "display:flex;flex-wrap:wrap;gap:8px;");
    const bSet = mkBtn("⚙️ 设置（强度/随机性/权重）", p);
    bSet.onclick = () => { openSettings(node); refreshSummary(node); };
    const bPreset = mkBtn("🗂 方案（角色/权重/参数）", p);
    bPreset.onclick = () => { openPresets(node); refreshSummary(node); };
    const bHis = mkBtn("🕘 历史/收藏", p);
    bHis.onclick = () => { openHistory(node); };
    // 【v3.16】免责声明的**常驻入口**：不管横幅是展开还是收起，
    // 这一颗按钮都在，点开就是全文弹窗 —— 彻底杜绝"关掉就再也找不到"。
    const bDisc = mkBtn("📜 免责声明", p);
    bDisc.onclick = () => { openDisclaimer(node); };
    rowTheme.appendChild(bSet);
    rowTheme.appendChild(bPreset);
    rowTheme.appendChild(bHis);
    rowTheme.appendChild(bDisc);
    node.addDOMWidget("k2v3_row_theme", "theme", rowTheme,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    // ④b 内容设定（v3.11：人物数量 / 百合 / 纹身面积）
    //     原生同名控件由 applyCompactLayout 收起 —— 同一个设置只留一个入口。
    const rowPerson = h("div", "display:flex;flex-wrap:wrap;gap:8px;align-items:center;");
    node._k2v3_row_person = rowPerson;
    node.addDOMWidget("k2v3_row_person", "person", rowPerson,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });
    refreshContentRow(node);

    // ⑤ 角色预览
    const preview = h("div", cardStyle(false, p) + "padding:10px 12px;min-height:64px;");
    node._k2v3_preview = preview;
    node.addDOMWidget("k2v3_preview", "preview", preview,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    // ⑥ 提示词预览（v3.13：改成可编辑文本域 + 复制工具条）
    //    ⚠️ 这里编辑**只影响复制出去的内容**，不影响出图 —— 出图结果由上面的
    //       控件与"锁定字段"决定。要在链路上真正改写，请用「提示词预览与编辑」节点。
    const outBar = h("div", "display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px;");
    node._k2v3_outbar = outBar;
    node.addDOMWidget("k2v3_outbar", "outbar", outBar,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    const outBox = document.createElement("textarea");
    outBox.value = "（执行一次后显示提示词）";
    outBox.style.cssText = `margin:0;padding:8px 10px;height:170px;width:100%;box-sizing:border-box;
        resize:vertical;background:${p.bg};border:1px solid ${p.border};border-radius:8px;
        line-height:1.55;font-size:11px;color:${p.text};
        font-family:ui-monospace,Consolas,monospace;outline:none;`;
    outBox.title = "可直接选中/编辑；编辑只影响复制出去的内容，不影响出图";
    // 用户一编辑就置位，之后的执行不再覆盖他改过的文本（避免"改完一跑就没了"）
    outBox.addEventListener("input", () => { node._k2v3_edited = true; });
    node._k2v3_out = outBox;
    node.addDOMWidget("k2v3_out", "out", outBox,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    // 【v3.19】这里原本是「负面提示词」只读框 —— 插件不再产生负面词，整块删除。

    // 自检报告 / 元数据回显（只读，便于核对）
    const repBox = h("pre", `margin:6px 0 0;padding:7px 10px;max-height:150px;overflow:auto;
        white-space:pre-wrap;word-break:break-word;background:${p.card};
        border:1px solid ${p.border};border-radius:8px;line-height:1.5;font-size:10.5px;
        color:${p.sub};font-family:ui-monospace,Consolas,monospace;`, "（执行一次后显示报告）");
    repBox.title = "自检报告 + 元数据 + 候选数量提示（不可编辑）";
    node._k2v3_reportbox = repBox;
    node.addDOMWidget("k2v3_report", "report", repBox,
        { serialize: false, hideOnZoom: false, getValue() { return ""; }, setValue() { } });

    // 工具条：让这个节点**自己就能把提示词拿走**，不依赖别的节点
    const bCopyP = mkBtn("📋 复制提示词", p, true);
    bCopyP.onclick = () => copyText(node._k2v3_out ? node._k2v3_out.value : "", bCopyP, "📋 复制提示词");
    const bCopyR = mkBtn("📄 复制报告", p);
    bCopyR.onclick = () => copyText(node._k2v3_lastReport || "", bCopyR, "📄 复制报告");
    const bDl = mkBtn("💾 存为 txt", p);
    bDl.onclick = () => {
        const t = (node._k2v3_out ? node._k2v3_out.value : "") +
            "\n\n--- 自检报告 ---\n" + (node._k2v3_lastReport || "");
        downloadText(t, "krea2-v3-prompt.txt");
    };
    const bReset = mkBtn("↺ 还原", p);
    bReset.title = "把编辑过的文本还原成最近一次生成的结果";
    bReset.onclick = () => {
        node._k2v3_edited = false;
        if (node._k2v3_out) node._k2v3_out.value = node._k2v3_lastText || "";
    };
    outBar.appendChild(bCopyP);
    outBar.appendChild(bCopyR);
    outBar.appendChild(bDl);
    outBar.appendChild(bReset);

    // 收起所有原生控件（黑名单之外的），保持面板整洁
    applyCompactLayout(node);

    refreshSelectedPreview(node);
    refreshContentRow(node);
    refreshSummary(node);
    node.size[0] = Math.max(node.size[0], 460);
    node.size[1] = Math.max(node.size[1], 520);
}

/* ==========================================================================
 * 内容设定行（v3.11 起）—— 【v3.17 改为「状态回显」】
 *
 * 原来这一行放的是「👤 单人 / 👥 双人 / 🌸 百合」按钮。现在它们整体迁进
 * 设置面板最底部的「子级交互」区（那里才是设置项该待的地方），这一行只保留：
 *   ① 当前人数状态（只读回显）
 *   ② 一个「⚙️ 设置里改」入口
 *   ③ 纹身面积下拉（本来就是这一行独有的设置，保持不变）
 *   ④ 双人百合时的分级提示 + 纹身冲突警告
 * ⚠️ 状态回显用的是 v311() 的**生效值**（含「双人 + NSFW 自动百合」），
 *    所以看到「双人 · 百合」时后端确实是这么出的，不会骗人。
 * ========================================================================== */
function buildContentRow(node, d) {
    const row = node._k2v3_row_person;
    if (!row) return;
    const p = palette();
    const s = v311(node);
    row.innerHTML = "";

    row.appendChild(h("span", `font-size:11.5px;color:${p.sub};`, "内容设定"));

    // 【v3.17】单人／双人 + 百合 已经迁进「⚙️ 设置 → 子级交互」——
    //   这里只留**状态回显 + 一个设置入口**，主界面不再重复放按钮
    //   （同一个设置只留一个入口，是插件的既定界面口径）。
    const chip = h("span", `font-size:11.5px;padding:3px 9px;border-radius:999px;
        border:1px solid ${s.double ? p.accent + "66" : p.border};
        color:${s.double ? p.accent : p.text};background:${p.card};`,
        s.person + (s.double ? (s.yuri ? " · 百合" : " · 互动") : ""));
    chip.title = "当前人物数量。改它请点右边的「⚙️ 设置里改」";
    row.appendChild(chip);

    const bCfg = mkBtn("⚙️ 设置里改", p);
    bCfg.title = "单人／双人 · 百合 · 第二位角色 · NSFW 场景权重 都在设置面板最底部的「子级交互」里";
    bCfg.onclick = () => { openSettings(node); refreshContentRow(node); refreshSummary(node); };
    row.appendChild(bCfg);

    // 分级提示：双人百合段按等级分档，把当前档位说清楚
    if (s.double && s.yuri) {
        const tip = h("span", `font-size:10.5px;color:${p.accent};max-width:100%;`);
        tip.textContent = "分级：—";
        row.appendChild(tip);
        fetchLevels().then((ld) => {
            // 【v3.18】档位只认「内容强度」
            const zh = (ld.zh || {})[intLevelOf(node)] || "";
            const note = ((d && d.grade_note) || {})[zh] || "";
            tip.textContent = "分级：" + (zh || "—") + (note ? "（" + note + "）" : "");
        }).catch(() => { });
    }

    const sel = document.createElement("select");
    sel.style.cssText = `border:1px solid ${p.border};background:${p.card};color:${p.text};
        border-radius:7px;padding:4px 8px;font-size:11.5px;cursor:pointer;outline:none;`;
    sel.title = "纹身覆盖身体面积。「跟随随机」= 本控件不介入；「不使用」= 强制无纹身。";
    const areas = (d && d.tattoo_areas) || ["跟随随机"];
    areas.forEach((a) => {
        const o = document.createElement("option");
        o.value = a; o.textContent = "纹身·" + a;
        sel.appendChild(o);
    });
    sel.value = areas.indexOf(s.area) >= 0 ? s.area : areas[0];
    sel.onchange = () => {
        setWidgetVal(node, "纹身面积", sel.value);
        refreshContentRow(node);     // 让下面的冲突提示跟着刷新
        refreshSummary(node);
    };
    row.appendChild(sel);

    // 【v3.13 修复】纹身冲突可视化：
    //   「面积=不使用」会静默压过「纹身权重」（实测 200 次出纹身 0 次），
    //   这里当场把它说出来，不让人以为权重坏了。
    if (s.area === "不使用" && getVal(node, "纹身开关")) {
        const warn = h("span", `font-size:10.5px;color:#c8811a;max-width:100%;line-height:1.5;`,
            "⚠ 面积=不使用 会强制关闭纹身，「纹身权重」本次不生效");
        row.appendChild(warn);
    }
}

function refreshContentRow(node) {
    // fetchExtra 有缓存，二次调用直接走 Promise.resolve，不会递归
    fetchExtra().then((d) => buildContentRow(node, d)).catch(() => { });
}

/** 摘要行：已选内容 + 设置回显（对齐 Krea2-Portrait 的 k2p_summary 两行回显） */
function refreshSummary(node) {
    const box = node._k2v3_summary;
    if (!box) return;
    const lv = intLevelOf(node);          // 【v3.18】档位 = 内容强度
    const char = getVal(node, "角色");
    const w = (getVal(node, "权重覆盖") || "").trim();
    const lock = (getVal(node, "锁定字段") || "").trim();
    const s = v311(node);
    fetchLevels().then((d) => {
        if (!box.isConnected) return;
        const zh = (d.zh || {})[lv] || lv;
        const band = (d.band || {})[lv] || "";
        box.textContent = "已选：" + zh + "　角色：" + (char || "自动")
            + "　重随机：" + (getVal(node, "每次运行重随机") ? "开" : "关")
            + "　种子：" + getVal(node, "随机种子")
            + "　人数：" + s.person
            + (s.double ? "（" + (s.yuri ? "百合" : "互动") + (s.yuriAuto ? "·自动" : "") + "）" : "")
            // 【v3.17】双人时把角色 B 一并回显 —— 两个人都是谁，一眼看到
            + (s.double && s.charB !== "自动" ? "　角色B：" + s.charB : "")
            + "\n设置：等级带 " + band + "　权重：" + (w || "默认（等概率）")
            + "　纹身面积：" + s.area
            // 【v3.18】只回显**非默认**的内容项权重，默认全 1.00 时不啰嗦
            // 【v3.19】开了自动档就只报"跟随等级"，不逐项罗列（那是自动算的）
            + (getVal(node, ACT_AUTO_CTL)
                ? "　内容权重：跟随等级"
                : (function () {
                    const aw = actWeightsOf(node);
                    const chg = Object.keys(aw).filter(
                        (a) => Math.abs(aw[a] - ACT_W_DEFAULT) > 1e-9);
                    return chg.length
                        ? "　内容权重：" + chg.map((a) => a + "×" + aw[a].toFixed(2)).join("、")
                        : "";
                })())
            + (lv === "extreme" ? "　越界：极端档自动含" : "")
            + (String(getVal(node, "内容强度") || "").startsWith("手动") ? ""
                : "　内容强度：" + getVal(node, "内容强度"))
            + (Number(getVal(node, "联动强度") ?? 1) < 1
                ? "　联动：" + Math.round(Number(getVal(node, "联动强度")) * 100) + "%" : "")
            + (Number(getVal(node, "去重记忆") || 0) > 0
                ? "　去重记忆：" + getVal(node, "去重记忆") : "")
            + (Number(getVal(node, "多样性温度") ?? 1) !== 1
                ? "　温度：×" + Number(getVal(node, "多样性温度")).toFixed(2) : "")
            + (Number(getVal(node, "批次数") || 1) > 1
                ? "　批次：" + getVal(node, "批次数") : "")
            + ((getVal(node, "关闭可选项") || "").trim() ? "　已关闭：" + getVal(node, "关闭可选项") : "")
            + (lock ? "　锁定：" + lock : "");
    }).catch(() => { });
}

/* ---------- 扩展注册 ---------- */
app.registerExtension({
    // ⚠️ 扩展名也带 V2：与 V1 的 "K2V3Generator.Panel" 区分开，
    //    避免两套 JS 用同名扩展互相顶掉（这正是刷新后界面随机的直接原因之一）。
    name: "K2V3GeneratorV2.Panel",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onCreated?.apply(this, arguments);
            if (this._k2v3_done) return;
            this._k2v3_done = true;
            installPanel(this);
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            const box = this._k2v3_out;
            if (!box) return;
            const prompt = message && message["k2v3_text"];
            const report = message && message["k2v3_report"];
            // 【v3.11】元数据回显：让人一眼看到这次的种子（写进 PNG 用的就是它）
            const metaArr = message && message["k2v3_meta"];
            const meta = metaArr && metaArr.length ? metaArr[0] : null;
            // 【v3.13】候选列表
            //   【v3.19】负面提示词已取消 —— 不再读取 k2v3_negative。
            // 【v3.20】最近一次的槽位表（字段 → 实际取值）。
            //   「④ 字段微调 → 🔒 锁定当前整套值」靠它把这一整套结果钉死；
            //   没有它就只能锁 UI 上看得见的那几个，锁不全。
            //   ⚠️ 消息里**没有** k2v3_slots 时不要清空缓存：老版本节点（或
            //      别的执行路径）不发这一路，清空会让"锁定当前整套值"突然失灵。
            const slotsArr = message && message["k2v3_slots"];
            if (slotsArr && slotsArr.length) this._k2v3_slots = slotsArr[0] || {};
            const candArr = message && message["k2v3_candidates"];
            const cands = (candArr && candArr[0]) || [];
            const metaLine = meta
                ? "\n\n【元数据】种子 " + meta.seed + "　等级 " + meta.level
                    + "　人数 " + meta.person_count
                    + (meta.person_count !== "单人"
                        ? "（" + (meta.yuri ? "百合" : "互动") + (meta.yuri_auto ? "·自动" : "") + "）" : "")
                    + (meta.character2 ? "　角色B " + meta.character2 : "")
                    + (meta.act ? "　内容项 " + meta.act : "")
                    + (meta.intensity ? "　内容强度 " + meta.intensity : "")
                    + "　纹身面积 " + meta.tattoo_area
                    + (meta.grade ? "　分级 " + meta.grade : "")
                    + (meta.boundary ? "　越界" + (meta.beyond_grade ? "（" + meta.beyond_grade + "）" : "") : "")
                    + (meta.act_auto ? "　内容权重 跟随等级"
                        : (function () {
                            const aw = meta.act_weights || {};
                            const chg = Object.keys(aw).filter(
                                (a) => Math.abs(Number(aw[a]) - ACT_W_DEFAULT) > 1e-9);
                            return chg.length
                                ? "　内容权重 " + chg.map((a) => a + "×" + Number(aw[a]).toFixed(2)).join("、")
                                : "";
                        })())
                    + (meta.app_lock === false ? "　外观锁定 关" : "")
                    + (meta.dedupe ? "　去重记忆 " + meta.dedupe : "")
                    + (Number(meta.temperature ?? 1) !== 1 ? "　温度 ×" + Number(meta.temperature).toFixed(2) : "")
                : "";
            const cLine = (cands && cands.length > 1)
                ? "\n\n【候选 x" + cands.length + "】已放在第 6 个输出端口；"
                    + "点「📋 复制提示词」拿到的是第 1 条。"
                : "";
            const base = (prompt && Array.isArray(prompt) && prompt.length)
                ? prompt[0]
                : (report && report.length ? "" : "");
            if (!this._k2v3_edited) box.value = base;
            this._k2v3_lastText = base;
            this._k2v3_lastReport = (report && report.length ? report[0] : "");
            if (this._k2v3_reportbox) {
                this._k2v3_reportbox.textContent =
                    (report && report.length ? report[0] : "（无报告）") + metaLine + cLine;
            }
        };
    },
});
