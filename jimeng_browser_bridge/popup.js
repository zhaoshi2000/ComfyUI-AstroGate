const code = document.querySelector("#code");
const connect = document.querySelector("#connect");
const status = document.querySelector("#status");

function show(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

async function activeJimengTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url?.startsWith("https://jimeng.jianying.com/")) {
    throw new Error("请先切换到已登录的即梦图生视频页面。");
  }
  return tab;
}

connect.addEventListener("click", async () => {
  const pairingCode = code.value.trim();
  if (!/^[A-Za-z0-9_-]{20,}$/.test(pairingCode)) {
    show("请粘贴节点显示的有效连接码。", true);
    return;
  }
  connect.disabled = true;
  show("正在准备当前页面…");
  try {
    const tab = await activeJimengTab();
    const prepared = await chrome.runtime.sendMessage({ type: "astrogate-prepare", tabId: tab.id });
    if (!prepared?.ok) {
      throw new Error(prepared?.message || "无法准备当前页面。");
    }
    const result = await chrome.tabs.sendMessage(tab.id, { type: "astrogate-connect", code: pairingCode });
    if (!result?.ok) {
      throw new Error(result?.message || "连接失败。");
    }
    show("已发送连接请求；ComfyUI 会显示浏览器已连接。", false);
  } catch (error) {
    show(error instanceof Error ? error.message : "连接失败。", true);
  } finally {
    connect.disabled = false;
  }
});
