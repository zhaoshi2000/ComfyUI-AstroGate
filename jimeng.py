import asyncio
import ipaddress
import json
import math
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
import numpy as np
import torch
from aiohttp import WSMsgType, web
from PIL import Image

import folder_paths
from comfy_api.latest import InputImpl, io, ui
from server import PromptServer


PAIR_TTL = 300
JOB_TIMEOUT = 900
MAX_IMAGE_BYTES = 4_500_000
MAX_VIDEO_BYTES = 256 * 1024 * 1024
VIDEO_HOST_SUFFIXES = (".jianying.com", ".byteimg.com")


def loopback(request):
    peer = request.transport.get_extra_info("peername") if request.transport else None
    if not peer:
        return False
    try:
        return ipaddress.ip_address(peer[0]).is_loopback
    except ValueError:
        return False


def bridge_timeout():
    try:
        value = float(os.environ.get("ASTROGATE_JIMENG_TIMEOUT", JOB_TIMEOUT))
    except ValueError:
        raise ValueError("ASTROGATE_JIMENG_TIMEOUT 必须是正数秒。") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("ASTROGATE_JIMENG_TIMEOUT 必须是正数秒。")
    return value


def bridge_image(image):
    if not isinstance(image, torch.Tensor) or image.ndim != 4 or image.shape[0] < 1:
        raise ValueError("请连接一张 IMAGE 图像。")
    array = np.clip(image[0].detach().cpu().numpy() * 255, 0, 255).round().astype(np.uint8)
    source = Image.fromarray(array)
    if source.mode == "RGBA":
        background = Image.new("RGBA", source.size, "white")
        source = Image.alpha_composite(background, source).convert("RGB")
    else:
        source = source.convert("RGB")
    if min(source.size) < 320:
        scale = 320 / min(source.size)
        source = source.resize((round(source.width * scale), round(source.height * scale)), Image.Resampling.LANCZOS)
    if max(source.size) > 4096:
        source.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
    for _ in range(5):
        for quality in (95, 90, 85, 80, 75, 70, 65):
            with BytesIO() as buffer:
                source.save(buffer, format="JPEG", quality=quality, subsampling=0)
                data = buffer.getvalue()
            if len(data) <= MAX_IMAGE_BYTES:
                return data
        source = source.resize((max(320, round(source.width * .85)), max(320, round(source.height * .85))), Image.Resampling.LANCZOS)
    raise ValueError("参考图压缩后仍超过即梦网页支持的大小，请先缩小图像。")


def allowed_video_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "https" and not parsed.username and not parsed.password and port in (None, 443)
            and any(host.endswith(suffix) for suffix in VIDEO_HOST_SUFFIXES))


@dataclass
class BridgeJob:
    id: str
    prompt: str
    image: bytes
    node_id: str | None
    future: asyncio.Future
    socket: web.WebSocketResponse
    state: str = "waiting"


class JimengBrowserBridge:
    def __init__(self):
        self.pairs = {}
        self.socket = None
        self.jobs = {}
        self.lock = asyncio.Lock()

    def status(self):
        return {"connected": self.socket is not None and not self.socket.closed,
                "active": next((job.id for job in self.jobs.values() if not job.future.done()), None)}

    def new_pair(self):
        self.prune_pairs()
        code = secrets.token_urlsafe(24)
        self.pairs[code] = time.monotonic() + PAIR_TTL
        return code

    def claim_pair(self, code):
        self.prune_pairs()
        expires = self.pairs.pop(code, None)
        return expires is not None and expires > time.monotonic()

    def prune_pairs(self):
        now = time.monotonic()
        self.pairs = {code: expires for code, expires in self.pairs.items() if expires > now}

    def progress(self, job, text):
        if job.node_id:
            PromptServer.instance.send_progress_text(text, job.node_id)

    async def run(self, image, prompt, node_id):
        data = bridge_image(image)
        job_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        async with self.lock:
            socket = self.socket
            if socket is None or socket.closed:
                raise RuntimeError("即梦浏览器未连接。请先在节点中生成连接码，并在已登录的即梦图生视频页加载浏览器桥接扩展。")
            job = BridgeJob(job_id, prompt, data, node_id, future, socket)
            self.jobs[job_id] = job
            try:
                self.progress(job, "正在把图像交给已登录的即梦网页…")
                await socket.send_json({"type": "job", "job_id": job.id, "prompt": job.prompt,
                                        "image_size": len(job.image), "image_type": "image/jpeg"})
                await socket.send_bytes(job.image)
                result = await asyncio.wait_for(asyncio.shield(job.future), timeout=bridge_timeout())
                self.progress(job, "即梦视频已保存")
                return result
            except asyncio.TimeoutError:
                self.progress(job, "即梦网页等待超时，结果未确认")
                if not future.done():
                    future.set_exception(RuntimeError("即梦网页等待超时，远端可能仍在生成；未自动重试。"))
                raise RuntimeError("即梦网页等待超时，远端可能仍在生成；未自动重试。") from None
            except asyncio.CancelledError:
                self.progress(job, "已停止等待即梦网页，远端结果未确认")
                if not socket.closed:
                    await socket.send_json({"type": "cancel", "job_id": job.id})
                raise
            finally:
                self.jobs.pop(job.id, None)

    async def report(self, data):
        job = self.jobs.get(data.get("job_id"))
        if job is None or job.future.done():
            return
        message_type = data.get("type")
        if message_type == "status":
            label = str(data.get("message", ""))[:240]
            if label:
                self.progress(job, label)
            return
        if message_type == "error":
            job.future.set_exception(RuntimeError("即梦网页未完成任务：" + str(data.get("message", "未知错误"))[:300]))
            return
        if message_type == "result":
            if job.state == "downloading":
                return
            url = data.get("video_url")
            if not allowed_video_url(url):
                job.future.set_exception(RuntimeError("即梦网页返回了无效的视频地址。"))
                return
            job.state = "downloading"
            self.progress(job, "即梦已完成，正在保存视频…")
            asyncio.create_task(self.download(job, url))

    async def download(self, job, url):
        try:
            path = await download_video(url, job.id)
            if not job.future.done():
                job.future.set_result(path)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, RuntimeError):
            if not job.future.done():
                job.future.set_exception(RuntimeError("即梦视频下载失败，结果未确认；未自动重试。"))

    def disconnected(self, socket):
        if self.socket is socket:
            self.socket = None
        for job in list(self.jobs.values()):
            if job.socket is socket and job.state != "downloading" and not job.future.done():
                job.future.set_exception(RuntimeError("即梦浏览器会话已断开，远端结果未确认；未自动重试。"))


async def download_video(url, job_id):
    current = url
    timeout = aiohttp.ClientTimeout(total=120, connect=15)
    output_root = Path(folder_paths.get_output_directory()) / "AstroGate" / "Jimeng"
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"{job_id}.mp4"
    partial = output_path.with_suffix(".part")
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            for _ in range(4):
                if not allowed_video_url(current):
                    raise RuntimeError("不允许的视频下载地址。")
                async with session.get(current, headers={"Accept": "video/*"}, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise RuntimeError("视频下载重定向无效。")
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        raise RuntimeError("即梦视频下载未完成。")
                    content_type = response.headers.get("Content-Type", "").lower()
                    if content_type and not (content_type.startswith("video/") or content_type.startswith("application/octet-stream")):
                        raise RuntimeError("即梦下载结果不是视频。")
                    total = 0
                    with partial.open("wb") as file:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            total += len(chunk)
                            if total > MAX_VIDEO_BYTES:
                                raise RuntimeError("即梦视频超过大小限制。")
                            file.write(chunk)
                    if total < 16:
                        raise RuntimeError("即梦视频文件无效。")
                    with partial.open("rb") as file:
                        header = file.read(12)
                    if header[4:8] != b"ftyp":
                        raise RuntimeError("即梦下载结果不是 MP4 视频。")
                    partial.replace(output_path)
                    return output_path
        raise RuntimeError("即梦视频下载重定向过多。")
    finally:
        partial.unlink(missing_ok=True)


bridge = JimengBrowserBridge()
_routes_registered = False


def register_jimeng_routes():
    global _routes_registered
    if _routes_registered:
        return
    _routes_registered = True
    server = PromptServer.instance

    @server.routes.get("/astrogate/jimeng/bridge/status")
    async def status(request):
        if not loopback(request):
            return web.json_response({"error": "浏览器桥接只能在本机查看。"}, status=403)
        return web.json_response(bridge.status())

    @server.routes.post("/astrogate/jimeng/bridge/pair")
    async def pair(request):
        if not loopback(request):
            return web.json_response({"error": "浏览器桥接只能在本机配置。"}, status=403)
        return web.json_response({"code": bridge.new_pair(), "expires_in": PAIR_TTL})

    @server.routes.get("/astrogate/jimeng/bridge")
    async def socket(request):
        if not loopback(request):
            return web.Response(status=403)
        ws = web.WebSocketResponse(max_msg_size=1024 * 1024, heartbeat=20)
        await ws.prepare(request)
        try:
            first = await ws.receive(timeout=30)
            if first.type != WSMsgType.TEXT:
                await ws.close(message=b"pairing required")
                return ws
            try:
                data = json.loads(first.data)
            except json.JSONDecodeError:
                data = {}
            if data.get("type") != "pair" or not bridge.claim_pair(str(data.get("code", ""))):
                await ws.send_json({"type": "error", "message": "连接码无效或已过期。"})
                await ws.close()
                return ws
            if bridge.socket and not bridge.socket.closed:
                await bridge.socket.close(code=4000, message=b"replaced")
            bridge.socket = ws
            await ws.send_json({"type": "paired"})
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        await bridge.report(json.loads(message.data))
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "message": "桥接消息无效。"})
                elif message.type == WSMsgType.ERROR:
                    break
        except asyncio.TimeoutError:
            await ws.close(message=b"pairing timeout")
        finally:
            bridge.disconnected(ws)
        return ws


class AstroGateJimengImageToVideo(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AstroGateJimengImageToVideo",
            display_name="即梦图生视频 · 浏览器桥接",
            category="video/AstroGate",
            description="使用已登录的即梦网页生成图生视频。先在节点中生成连接码，并在即梦图生视频页的 AstroGate 浏览器桥接扩展中配对。登录 Cookie 和网页临时签名不会进入工作流。",
            not_idempotent=True,
            has_intermediate_output=True,
            inputs=[
                io.Image.Input("image", display_name="首帧图像", tooltip="即梦网页只使用这一张首帧图。图像会临时发送给本机浏览器桥接。"),
                io.String.Input("prompt", display_name="提示词", default="", multiline=True,
                                tooltip="描述动作、运镜和镜头变化。即梦网页当前选择的视频规格会继续生效。"),
                io.Int.Input("generation_id", display_name="重绘编号", optional=True, default=0, min=-1, max=2147483647,
                             socketless=True, tooltip="编号改变时重新提交；-1 每次提交。这不是即梦种子。"),
            ],
            outputs=[io.Video.Output(display_name="视频")],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, generation_id=-1, **kwargs):
        return float("nan") if generation_id is None or generation_id < 0 else generation_id

    @classmethod
    async def execute(cls, image, prompt, generation_id=-1):
        result = await bridge.run(image, prompt, cls.hidden.unique_id)
        relative = result.relative_to(Path(folder_paths.get_output_directory()))
        saved = ui.SavedResult(result.name, str(relative.parent).replace("\\", "/"), io.FolderType.output)
        return io.NodeOutput(InputImpl.VideoFromFile(str(result)), ui=ui.PreviewVideo([saved]))
