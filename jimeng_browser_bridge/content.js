(() => {
  if (globalThis.__astrogateJimengBridgeInstalled) {
    return;
  }
  globalThis.__astrogateJimengBridgeInstalled = true;

  const bridge = {
    socket: null,
    pendingJob: null,
    activeJob: null,
  };

  function isSocketOpen() {
    return bridge.socket?.readyState === WebSocket.OPEN;
  }

  function send(message) {
    if (isSocketOpen()) {
      bridge.socket.send(JSON.stringify(message));
    }
  }

  function report(jobId, message) {
    send({ type: "status", job_id: jobId, message: String(message).slice(0, 180) });
  }

  function finishActive() {
    if (bridge.activeJob?.progressTimer) {
      clearInterval(bridge.activeJob.progressTimer);
    }
    bridge.activeJob = null;
  }

  function sendCompletedVideo() {
    const active = bridge.activeJob;
    if (!active?.resourceId) {
      return;
    }
    const result = active.videos.find((item) => item.resourceId === active.resourceId && typeof item.videoUrl === "string");
    if (!result) {
      return;
    }
    const jobId = active.id;
    finishActive();
    send({ type: "result", job_id: jobId, video_url: result.videoUrl });
  }

  function visible(element) {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }

  function sleep(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  function imageInput() {
    const inputs = [...document.querySelectorAll('input[type="file"]')]
      .filter((input) => !input.disabled);
    if (!inputs.length) {
      return null;
    }
    return inputs.sort((left, right) => {
      const leftScore = (left.accept || "").includes("image") ? 1 : 0;
      const rightScore = (right.accept || "").includes("image") ? 1 : 0;
      return rightScore - leftScore;
    })[0];
  }

  function promptField() {
    const fields = [
      ...document.querySelectorAll("textarea, input[type='text'], [contenteditable='true']"),
    ].filter(visible);
    if (!fields.length) {
      return null;
    }
    return fields.sort((left, right) => promptScore(right) - promptScore(left))[0];
  }

  function promptScore(element) {
    const hint = [element.placeholder, element.getAttribute("aria-label"), element.getAttribute("title")]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    let score = element.tagName === "TEXTAREA" ? 4 : 0;
    if (/提示|描述|prompt|动作|内容/.test(hint)) {
      score += 8;
    }
    score += Math.min(element.getBoundingClientRect().width / 100, 4);
    return score;
  }

  function setFieldValue(element, value) {
    if (element.isContentEditable) {
      element.focus();
      element.textContent = value;
    } else {
      const prototype = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (setter) {
        setter.call(element, value);
      } else {
        element.value = value;
      }
    }
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function generateButton() {
    const buttons = [...document.querySelectorAll("button, [role='button']")].filter((button) => {
      const text = (button.innerText || button.textContent || "").trim();
      return visible(button) && !button.matches("[disabled], [aria-disabled='true']")
        && /^(生成视频|开始生成|立即生成|生成)$/.test(text);
    });
    return buttons.sort((left, right) => {
      const leftText = (left.innerText || left.textContent || "").trim();
      const rightText = (right.innerText || right.textContent || "").trim();
      return rightText.length - leftText.length;
    })[0] || null;
  }

  async function waitForGenerateButton() {
    for (let attempts = 0; attempts < 90; attempts += 1) {
      const button = generateButton();
      if (button) {
        return button;
      }
      await sleep(500);
    }
    return null;
  }

  async function submitJob(job, bytes) {
    const input = imageInput();
    if (!input) {
      send({ type: "error", job_id: job.id, message: "未找到即梦图生视频的图片上传控件。请先打开图生视频页面。" });
      return;
    }
    const prompt = promptField();
    if (!prompt) {
      send({ type: "error", job_id: job.id, message: "未找到即梦图生视频的提示词输入框。请先打开图生视频页面。" });
      return;
    }

    bridge.activeJob = { id: job.id, resourceId: null, videos: [], progressTimer: null };
    try {
      report(job.id, "正在填写图像和提示词…");
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], "astrogate-first-frame.jpg", { type: "image/jpeg" }));
      input.files = transfer.files;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      setFieldValue(prompt, job.prompt);
      report(job.id, "正在等待即梦网页完成图片上传…");
      const button = await waitForGenerateButton();
      if (!button) {
        throw new Error("即梦网页未显示可用的生成按钮，请确认已打开图生视频页面且图片上传成功。");
      }
      button.click();
      report(job.id, "已在即梦网页提交，等待生成结果…");
      bridge.activeJob.progressTimer = setInterval(() => {
        if (bridge.activeJob?.id === job.id) {
          report(job.id, "即梦正在生成视频…");
        }
      }, 20000);
    } catch (error) {
      finishActive();
      send({ type: "error", job_id: job.id, message: error instanceof Error ? error.message.slice(0, 180) : "即梦网页提交失败。" });
    }
  }

  function receiveText(data) {
    let message;
    try {
      message = JSON.parse(data);
    } catch {
      return;
    }
    if (message.type === "paired") {
      return;
    }
    if (message.type === "job") {
      if (!message.job_id || bridge.pendingJob || bridge.activeJob) {
        send({ type: "error", job_id: message.job_id, message: "浏览器桥接当前正忙。" });
        return;
      }
      bridge.pendingJob = { id: message.job_id, prompt: String(message.prompt || "") };
      return;
    }
    if (message.type === "cancel") {
      if (bridge.pendingJob?.id === message.job_id) {
        bridge.pendingJob = null;
      } else if (bridge.activeJob?.id === message.job_id) {
        report(message.job_id, "已停止等待；即梦网页中的远端任务不会自动取消。");
        finishActive();
      }
    }
  }

  function receiveImage(data) {
    const job = bridge.pendingJob;
    bridge.pendingJob = null;
    if (!job) {
      return;
    }
    submitJob(job, new Uint8Array(data));
  }

  function connect(code) {
    if (!/^[A-Za-z0-9_-]{20,}$/.test(code)) {
      return { ok: false, message: "连接码格式不正确。" };
    }
    bridge.socket?.close();
    const socket = new WebSocket("ws://127.0.0.1:8190/astrogate/jimeng/bridge");
    socket.binaryType = "arraybuffer";
    bridge.socket = socket;
    socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "pair", code })));
    socket.addEventListener("message", (event) => {
      if (typeof event.data === "string") {
        receiveText(event.data);
      } else {
        receiveImage(event.data);
      }
    });
    socket.addEventListener("close", () => {
      if (bridge.socket === socket) {
        finishActive();
        bridge.socket = null;
      }
    });
    socket.addEventListener("error", () => {});
    return { ok: true, message: "正在连接 ComfyUI…" };
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== location.origin) {
      return;
    }
    const data = event.data;
    if (data?.source !== "astrogate-jimeng-observer" || !bridge.activeJob) {
      return;
    }
    if (data.type === "resource" && typeof data.resourceId === "string") {
      bridge.activeJob.resourceId = data.resourceId;
      report(bridge.activeJob.id, "即梦已受理任务，正在生成视频…");
      sendCompletedVideo();
      return;
    }
    if (data.type === "video" && Array.isArray(data.results)) {
      bridge.activeJob.videos = data.results;
      sendCompletedVideo();
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "astrogate-connect") {
      return;
    }
    sendResponse(connect(String(message.code || "")));
  });

  chrome.runtime.sendMessage({ type: "astrogate-inject-observer" }).catch(() => {});
})();
