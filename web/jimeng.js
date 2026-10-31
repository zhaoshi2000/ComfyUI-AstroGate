import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const bridgePath = "/astrogate/jimeng/bridge";
const nodes = new Set();

function element(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
}

async function request(path, options = {}) {
    const response = await api.fetchApi(bridgePath + path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
    return data;
}

function showStatus(node, text, error = false) {
    if (node.agJimengStatus.textContent === text && node.agJimengStatus.classList.contains("error") === error) return;
    node.agJimengStatus.textContent = text;
    node.agJimengStatus.classList.toggle("error", error);
    node.setDirtyCanvas(true, true);
}

function updateCode(node) {
    const remaining = Math.max(0, Math.ceil((node.agJimengExpiresAt - Date.now()) / 1000));
    const valid = node.agJimengPairCode && remaining > 0;
    node.agJimengCodeBox.hidden = !valid;
    if (valid) {
        node.agJimengCodeElement.textContent = node.agJimengPairCode;
        node.agJimengCodeHint.textContent = `连接码将在 ${remaining} 秒后失效`;
    }
}

async function createPairingCode(node) {
    const pair = await request("/pair", {method: "POST"});
    node.agJimengPairCode = pair.code;
    node.agJimengExpiresAt = Date.now() + pair.expires_in * 1000;
    updateCode(node);
    showStatus(node, "在已登录的即梦图生视频页打开 AstroGate 扩展，粘贴连接码并点击配对。");
}

async function copyCode(node) {
    if (!node.agJimengPairCode || node.agJimengExpiresAt <= Date.now()) {
        throw new Error("请先生成新的连接码。");
    }
    await navigator.clipboard.writeText(node.agJimengPairCode);
    showStatus(node, "连接码已复制；请在即梦网页的 AstroGate 扩展中粘贴。");
}

function button(node, label, action, primary = false) {
    const result = element("button", label, primary ? "primary" : "");
    result.type = "button";
    result.addEventListener("click", async () => {
        result.disabled = true;
        try {
            await action();
        } catch (error) {
            showStatus(node, error instanceof Error ? error.message : "操作失败。", true);
        } finally {
            result.disabled = false;
        }
    });
    return result;
}

function install(node) {
    const root = element("section", undefined, "ag-jimeng");
    const title = element("strong", "即梦浏览器桥接");
    const note = element("p", "使用当前已登录的即梦页面；不会读取或保存 Cookie。", "ag-jimeng-note");
    const controls = element("div", undefined, "ag-jimeng-actions");
    const codeBox = element("div", undefined, "ag-jimeng-code");
    const code = element("code");
    const codeHint = element("span", "");
    const status = element("div", "未连接。先生成连接码，再在即梦网页扩展中配对。", "ag-jimeng-status");
    codeBox.append(code, codeHint);
    codeBox.hidden = true;
    node.agJimengStatus = status;
    node.agJimengCodeBox = codeBox;
    node.agJimengCodeElement = code;
    node.agJimengCodeHint = codeHint;
    controls.append(button(node, "生成连接码", () => createPairingCode(node), true), button(node, "复制", () => copyCode(node)));
    root.append(title, note, controls, codeBox, status);
    const widget = node.addDOMWidget("astrogate_jimeng_bridge", "astrogate_jimeng_bridge", root, {
        serialize: false, hideOnZoom: false, getMinHeight: 148, getHeight: 148,
    });
    widget.options.serialize = false;
    root.addEventListener("pointerdown", event => {
        if (event.target.closest("button")) event.stopPropagation();
    });
    root.addEventListener("wheel", event => event.stopPropagation());
    node.size = [390, Math.max(node.size?.[1] || 0, 295)];
    nodes.add(node);
}

async function refresh() {
    if (!nodes.size) return;
    let state;
    try {
        state = await request("/status");
    } catch (error) {
        for (const node of nodes) showStatus(node, `浏览器桥接状态不可用：${error.message}`, true);
        return;
    }
    for (const node of nodes) {
        updateCode(node);
        if (state.connected) {
            showStatus(node, state.active ? "浏览器已连接，正在处理一个视频任务。" : "浏览器已连接，可以运行当前节点。");
        } else if (!node.agJimengPairCode || node.agJimengExpiresAt <= Date.now()) {
            showStatus(node, "未连接。先生成连接码，再在即梦网页扩展中配对。");
        }
    }
}

app.registerExtension({
    name: "AstroGate.JimengBridge",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "AstroGateJimengImageToVideo") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            install(this);
            return result;
        };
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            nodes.delete(this);
            return removed?.apply(this, arguments);
        };
    },
    setup() {
        const style = element("link");
        style.rel = "stylesheet";
        style.href = new URL("./jimeng.css", import.meta.url).href;
        document.head.append(style);
        async function tick() {
            await refresh();
            setTimeout(tick, 3000);
        }
        setTimeout(tick, 1000);
    },
});
