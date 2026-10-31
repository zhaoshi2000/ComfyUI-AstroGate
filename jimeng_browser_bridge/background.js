const JIMENG_ORIGIN = "https://jimeng.jianying.com/";

async function getJimengTab(tabId) {
  const tab = await chrome.tabs.get(tabId);
  if (!tab.url || !tab.url.startsWith(JIMENG_ORIGIN)) {
    throw new Error("not_jimeng");
  }
  return tab;
}

async function prepareTab(tabId) {
  await getJimengTab(tabId);
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content.js"],
  });
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["observer.js"],
    world: "MAIN",
  });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "astrogate-prepare" && Number.isInteger(message.tabId)) {
    prepareTab(message.tabId)
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false, message: "请在已登录的即梦图生视频页面中配对。" }));
    return true;
  }

  if (message?.type === "astrogate-inject-observer" && sender.tab?.id) {
    chrome.scripting.executeScript({
      target: { tabId: sender.tab.id },
      files: ["observer.js"],
      world: "MAIN",
    }).catch(() => {});
  }
});
