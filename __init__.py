import asyncio
import base64
import binascii
import json
import os
import re
import secrets
import hashlib
import math
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

import aiohttp
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont, ImageOps

import folder_paths
from comfy_api.latest import ComfyExtension, io, ui
from comfy_execution.graph_utils import ExecutionBlocker
from server import PromptServer

from .jimeng import AstroGateJimengImageToVideo, register_jimeng_routes
from .h3_video import AstroGateMiniMaxH3Video
from .storyboard.graph import render_prompt, semantic_hash, snapshot
from .storyboard.native import analyse_native
from .storyboard.service import get_store, register_routes
from .storyboard.store import aspect_ratio as normalize_aspect_ratio, image_hash, invalidate
from .storyboard.video import video_prompt
from .storyboard.video_import import analyse_video


API_BASE = os.environ.get("ASTROGATE_IMG_API_BASE", "https://img.astrogate.sbs/internal/astrogate/v1").rstrip("/")
WEB_DIRECTORY = "./web"
KEY_FILE = Path(folder_paths.base_path) / ".secrets" / "astrogate-img-api-key"
ASPECT_SIZES = {
    "1:1": (1024, 1024), "2:3": (1024, 1536), "3:2": (1536, 1024),
    "4:3": (1280, 960), "3:4": (960, 1280), "16:9": (1536, 864),
    "9:16": (864, 1536), "4:5": (1024, 1280), "5:4": (1280, 1024),
    "2:1": (1536, 768), "1:2": (768, 1536), "21:9": (1792, 768),
    "9:21": (768, 1792),
}
RESOLUTION_EDGES = {"1K": 1024, "2K": 2048, "3K": 3072, "4K": 3840}
VIEW_PROMPTS = {
    "自由": "",
    "正面": "生成单独一张人物正面视图，镜头正对人物，全身从头到脚完整入画，避免三分之四侧面。",
    "背面": "生成单独一张人物背面视图，镜头位于人物身后，背部朝向镜头，不回头，全身完整入画。",
    "左侧": "生成单独一张人物自身左侧视图，镜头位于人物左侧，左耳和身体左侧可见，严格90度侧面，全身完整入画。",
    "右侧": "生成单独一张人物自身右侧视图，镜头位于人物右侧，右耳和身体右侧可见，严格90度侧面，全身完整入画。",
    "配饰细节": "生成单独一张服饰与配饰细节图，清晰展示本次要求的饰品、衣物或随身物件，保留材质、纹样、颜色、形状和佩戴位置。",
}


class FrameQueue:
    def __init__(self, limit=None):
        self.limit = int(os.environ.get("ASTROGATE_IMG_CONCURRENCY", "20")) if limit is None else limit
        if self.limit < 1:
            raise ValueError("ASTROGATE_IMG_CONCURRENCY 必须是正整数。")
        self.semaphore = None
        self.waiters = 0

    @asynccontextmanager
    async def slot(self):
        if self.semaphore is None:
            self.semaphore = asyncio.BoundedSemaphore(self.limit)
        self.waiters += 1
        try:
            async with self.semaphore:
                yield
        finally:
            self.waiters -= 1
            if not self.waiters:
                self.semaphore = None


frame_queue = FrameQueue()


def read_api_key():
    key = os.environ.get("ASTROGATE_IMG_API_KEY", "").strip()
    if not key and KEY_FILE.is_file():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("请配置 ASTROGATE_IMG_API_KEY，或将接口密钥保存到 .secrets/astrogate-img-api-key。")
    return key


def output_dimensions(aspect_ratio, resolution):
    ratio = normalize_aspect_ratio(aspect_ratio)
    if ratio in ASPECT_SIZES:
        width, height = ASPECT_SIZES[ratio]
    else:
        width, height = map(float, ratio.split(":"))
    if resolution == "original" and ratio in ASPECT_SIZES:
        return width, height
    edge = 1536 if resolution == "original" else RESOLUTION_EDGES[resolution]
    if width >= height:
        return edge, max(2, round(edge * height / width / 2) * 2)
    return max(2, round(edge * width / height / 2) * 2), edge


def encode_reference(image, image_format=None):
    image_format = image_format or os.environ.get("ASTROGATE_REFERENCE_FORMAT", "jpeg").strip().lower()
    if image_format not in ("jpeg", "jpg", "png"):
        raise ValueError("ASTROGATE_REFERENCE_FORMAT 请选择 jpeg 或 png。")
    image_format = "png" if image_format == "png" or image.mode not in ("RGB", "L") else "jpeg"
    with BytesIO() as buffer:
        if image_format == "png":
            image.save(buffer, format="PNG")
        else:
            image.save(buffer, format="JPEG", quality=85, subsampling=2)
        return buffer.getvalue(), image_format


def encode_references(reference_images, max_images=8, image_format=None):
    batches = [batch for batch in (reference_images or {}).values() if batch is not None]
    if max_images is not None and sum(batch.shape[0] for batch in batches) > max_images:
        raise ValueError("AstroGate 最多支持 8 张参考图，包含输入批次中的所有图片。")
    images = []
    total_bytes = 0
    for batch in batches:
        for tensor in batch:
            if tensor.shape[0] * tensor.shape[1] > 24_000_000:
                raise ValueError("单张参考图不能超过 2400 万像素，请先连接图像缩放节点。")
            array = np.clip(tensor.detach().cpu().numpy() * 255, 0, 255).round().astype(np.uint8)
            data, encoding = encode_reference(Image.fromarray(array), image_format)
            total_bytes += len(data)
            if len(data) > 32 * 1024 * 1024 or total_bytes > 50 * 1024 * 1024:
                raise ValueError("参考图单张不能超过 32 MB，合计不能超过 50 MB。")
            images.append({"b64_json": base64.b64encode(data).decode("ascii"), "filename": f"reference-{len(images) + 1}.{encoding}", "mime_type": f"image/{encoding}"})
    return images


def pack_character_views(images, refs):
    packed, purposes, consumed = [], [], set()
    for index, (image, ref) in enumerate(zip(images, refs)):
        if index in consumed:
            continue
        pair = next((i for i, other in enumerate(refs) if i != index and i not in consumed
                     and ref.get("asset_view") in ("front", "left") and other.get("asset_view") == ("left" if ref["asset_view"] == "front" else "front")
                     and (ref.get("asset_id"), ref.get("version_id")) == (other.get("asset_id"), other.get("version_id"))), None)
        if pair is None:
            packed.append(image)
            purposes.append(ref)
            continue
        front_index, left_index = (index, pair) if ref["asset_view"] == "front" else (pair, index)
        with Image.open(BytesIO(base64.b64decode(images[front_index]["b64_json"]))) as front, Image.open(BytesIO(base64.b64decode(images[left_index]["b64_json"]))) as left:
            sheet = Image.new("RGB", (front.width + left.width, max(front.height, left.height)), "white")
            sheet.paste(front, (0, 0))
            sheet.paste(left, (front.width, 0))
            if sheet.width * sheet.height > 24_000_000:
                scale = (24_000_000 / (sheet.width * sheet.height)) ** 0.5
                sheet = sheet.resize((int(sheet.width * scale), int(sheet.height * scale)), Image.Resampling.LANCZOS)
            data, encoding = encode_reference(sheet)
        packed.append({"b64_json": base64.b64encode(data).decode(), "filename": f"character-{index}.{encoding}", "mime_type": f"image/{encoding}"})
        purposes.append({**refs[front_index], "purpose": refs[front_index]["purpose"] + "；同一人物双视图图册：左半为正视，右半为左视，不能画成两个人。"})
        consumed.add(pair)
    groups = {}
    for index, ref in enumerate(purposes):
        if ref.get("character_id"):
            groups.setdefault(ref["character_id"], []).append(index)
    if len(packed) > 8:
        props = [i for i, ref in enumerate(purposes) if ref.get("asset_kind") == "prop"]
        if len(props) > 1:
            groups["props"] = props
    combined, bindings, consumed = [], [], set()
    for index, (image, ref) in enumerate(zip(packed, purposes)):
        if index in consumed:
            continue
        group = next((indices for indices in groups.values() if index in indices and len(indices) > 1), None)
        if not group:
            combined.append(image)
            bindings.append(ref)
            continue
        sheet = reference_sheet([packed[i] for i in group])
        label = "同一人物设定图册" if ref.get("character_id") else "道具参考图册"
        purpose = label + "（按图上编号逐项参考，不能将图册排版画进分镜）：" + "；".join(f"{n+1}号：{purposes[i]['purpose']}" for n, i in enumerate(group))
        combined.append(sheet)
        bindings.append({**ref, "purpose": purpose})
        consumed.update(group)
    return combined, bindings


def reference_sheet(images):
    opened = []
    for image in images:
        with Image.open(BytesIO(base64.b64decode(image["b64_json"]))) as source:
            opened.append(source.convert("RGB"))
    columns = math.ceil(math.sqrt(len(opened)))
    rows = math.ceil(len(opened) / columns)
    width, height = max(im.width for im in opened), max(im.height for im in opened) + 64
    scale = min(1, math.sqrt(24_000_000 / (columns * width * rows * height)))
    width, height = int(width * scale), int(height * scale)
    header = max(16, int(64 * scale))
    sheet = Image.new("RGB", (columns * width, rows * height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(12, int(header * .7)))
    for index, image in enumerate(opened):
        image.thumbnail((width, height - header), Image.Resampling.LANCZOS)
        x, y = (index % columns) * width, (index // columns) * height
        sheet.paste(image, (x, y + header))
        draw.text((x + 4, y), str(index + 1), fill="black", font=font)
    data, encoding = encode_reference(sheet)
    return {"b64_json": base64.b64encode(data).decode(), "filename": f"reference-atlas.{encoding}", "mime_type": f"image/{encoding}"}


def image_timeout():
    try:
        seconds = float(os.environ.get("ASTROGATE_IMG_TIMEOUT", "900"))
    except ValueError:
        raise ValueError("ASTROGATE_IMG_TIMEOUT 必须是正数秒。") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("ASTROGATE_IMG_TIMEOUT 必须是正数秒。")
    return aiohttp.ClientTimeout(total=seconds, connect=15)


async def generate_image(payload, edit):
    key = read_api_key()
    operation = "edits" if edit else "generations"
    timeout = image_timeout()
    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            for attempt in range(2):
                try:
                    async with session.post(f"{API_BASE}/images/{operation}", json=payload,
                                            headers=headers, allow_redirects=False) as response:
                        if response.status != 200:
                            hints = {401: "接口密钥无效", 403: "接口访问被拒绝", 409: "请求已提交，请稍后确认结果", 429: "接口繁忙或额度不足",
                                     502: "图片上游异常，未返回完整结果", 504: "图片上游超时，结果未确认"}
                            raise RuntimeError(f"AstroGate HTTP {response.status}：{hints.get(response.status, '图片服务未完成请求')}。")
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            raw.extend(chunk)
                            if len(raw) > 48 * 1024 * 1024:
                                raise RuntimeError("AstroGate 图片响应超过大小限制。")
                    return json.loads(raw)
                except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                    request_id = payload.get("gateway_request_id")
                    if request_id:
                        try:
                            async with session.get(f"{API_BASE}/images/jobs/{request_id}", headers=headers,
                                                   timeout=aiohttp.ClientTimeout(total=60, connect=15)) as response:
                                state = await response.json() if response.status == 200 else {}
                        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                            state = {}
                        if state.get("status") == "succeeded":
                            return state["result"]
                        if state.get("status") == "missing" and attempt == 0:
                            continue
                    kind = "超时" if isinstance(error, asyncio.TimeoutError) else "连接中断"
                    raise RuntimeError(f"AstroGate 图片请求{kind}（{type(error).__name__}）；已查询网关，结果未确认。") from None
    except asyncio.TimeoutError:
        raise RuntimeError("AstroGate 图片请求超时，服务端可能仍在生成；本次未自动重试。") from None
    except aiohttp.ClientError:
        raise RuntimeError("无法连接 AstroGate 图片接口；本次未自动重试。") from None
    except (ValueError, UnicodeError):
        raise RuntimeError("AstroGate 图片接口返回了无效 JSON。") from None


def decode_image(response, target=None):
    items = response.get("data") if isinstance(response, dict) else None
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise RuntimeError("AstroGate 未返回有效图片。")
    try:
        encoded = items[0].get("b64_json")
        if not isinstance(encoded, str) or len(encoded) > 45 * 1024 * 1024:
            raise ValueError()
        data = base64.b64decode(encoded, validate=True)
        if len(data) > 32 * 1024 * 1024:
            raise ValueError()
        with Image.open(BytesIO(data)) as opened:
            if opened.width * opened.height > 24_000_000:
                raise ValueError()
            image = ImageOps.exif_transpose(opened).convert("RGB")
            if target is not None and image.size != target:
                image = ImageOps.fit(image, target, method=Image.Resampling.LANCZOS)
            array = np.array(image, dtype=np.float32) / 255.0
        return torch.from_numpy(array).unsqueeze(0)
    except (ValueError, binascii.Error, OSError, Image.DecompressionBombError):
        raise RuntimeError("AstroGate 返回的图片无法读取或超过大小限制。") from None


def save_storyboard_image(store, job, saved, expected_status=None):
    output_file = Path(folder_paths.get_output_directory()) / saved.subfolder / saved.filename
    stored_image = store.save_image(job["project_id"], output_file.read_bytes())
    store.update_job(job["id"], expected_status=expected_status, status="succeeded", result_image=stored_image,
                     saved_result=dict(saved), error=None)

    def completed(project):
        if job["owner_type"] != "shot":
            return
        shot = next((s for s in project["shots"] if s["id"] == job["owner_id"]), None)
        if shot:
            invalidate(project, [s["id"] for s in project["shots"] if s.get("previous_shot_id") == shot["id"]])
            shot["latest_job_id"] = job["id"]
            shot["stale"] = (shot["image_prompt"] != job["snapshot"]["shot"]["image_prompt"] or
                             shot.get("atmosphere", "") != job["snapshot"]["shot"].get("atmosphere", "")) if job.get("native") else semantic_hash(snapshot(project, shot)) != job["fingerprint"]
            for ref in job.get("references", []):
                if ref.get("previous_shot_id"):
                    previous = next(s for s in project["shots"] if s["id"] == ref["previous_shot_id"])
                    if previous.get("latest_job_id") != ref["job_id"] or previous.get("stale"):
                        shot["stale"] = True
    store.change(job["project_id"], completed)


async def generate_prepared_frame(job_id):
    store = get_store()
    job = store.job(job_id)
    if job["status"] != "queued" or not job.get("native"):
        raise ValueError("图片任务未排队。")
    try:
        while True:
            sources = [store.job(ref["job_id"]) for ref in job["references"]]
            if any(source["status"] in ("failed", "unknown", "cancelled", "blocked") for source in sources):
                store.update_job(job_id, expected_status=("queued",), status="blocked", error="参考图未成功，已跳过本镜。")
                return
            if all(source["status"] == "succeeded" for source in sources):
                break
            await asyncio.sleep(1)
        async with frame_queue.slot():
            ordered = sorted(job["references"], key=lambda ref: int(ref["slot"].rsplit("_", 1)[1]))
            references = []
            for index, ref in enumerate(ordered):
                source = store.job(ref["job_id"])
                with Image.open(store.image_path(source["result_image"]["file"])) as opened:
                    data, encoding = encode_reference(opened.convert("RGB"))
                references.append({"b64_json": base64.b64encode(data).decode(), "filename": f"reference-{index + 1}.{encoding}", "mime_type": f"image/{encoding}"})
                ref["image"] = source["result_image"]
            if references:
                references, ordered = pack_character_views(references, ordered)
            if len(references) > 8:
                raise ValueError("本镜超过接口的8张参考图限制，请只连接必要的出镜人物。")
            settings = job["settings"]
            width, height = output_dimensions(settings["aspect_ratio"], settings["resolution"])
            payload = {"model": "gpt-image-2", "prompt": render_prompt(job["snapshot"], ordered),
                       "size": f"{width}x{height}", "quality": settings["quality"], "n": 1,
                       "stream": False, "response_format": "b64_json", "gateway_request_id": job["gateway_request_id"]}
            if references:
                payload["images"] = references
            read_api_key()
            image_timeout()
            request_path = store.root / "requests" / f"{job_id}.json"
            request_path.parent.mkdir(exist_ok=True)
            serialized = json.dumps(payload, ensure_ascii=False).encode()
            request_path.write_bytes(serialized)
            store.update_job(job_id, expected_status=("queued",), status="running", references=ordered,
                             prompt=payload["prompt"], request_hash=hashlib.sha256(serialized).hexdigest())
            try:
                response = await generate_image(payload, bool(references))
                image = decode_image(response, (width, height) if settings["resolution"] != "original" else None)
                saved = ui.ImageSaveHelper.save_images(image, f"AstroGate/{job['project_id']}/{job_id}", io.FolderType.output, None)[0]
                save_storyboard_image(store, {**job, "references": ordered}, saved)
            except (RuntimeError, ValueError, OSError, asyncio.CancelledError) as error:
                if store.job(job_id)["status"] != "succeeded":
                    store.update_job(job_id, status="unknown", error=f"请求已发出，结果未确认：{str(error) or '本地任务已停止'}；未自动重试。")
                raise
    except asyncio.CancelledError:
        if store.job(job_id)["status"] == "queued":
            store.update_job(job_id, status="cancelled")
    except (RuntimeError, ValueError, OSError) as error:
        if store.job(job_id)["status"] == "queued":
            store.update_job(job_id, status="failed", error=str(error))


async def recover_storyboard_image(store, job):
    request_id = job.get("gateway_request_id", "")
    if not re.fullmatch(r"[0-9a-f]{64}", request_id):
        raise ValueError("此任务没有可查询的图片请求编号。")
    try:
        key = read_api_key()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60, connect=15), trust_env=False) as session:
            async with session.get(f"{API_BASE}/images/jobs/{request_id}", headers={"Authorization": f"Bearer {key}"}, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError(f"图片服务暂不可查询结果（HTTP {response.status}），未重新生成。")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    raw.extend(chunk)
                    if len(raw) > 48 * 1024 * 1024:
                        raise ValueError("图片查询结果过大，未重新生成。")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("图片服务返回了无效查询结果，未重新生成。")
        status = result.get("status")
        if status in ("pending", "unknown", "missing", "expired", "unavailable"):
            return {"status": status}
        if status != "succeeded":
            return {"status": "unavailable"}
        latest = store.job(job["id"])
        if latest["status"] == "succeeded":
            return {"status": "succeeded", "saved_result": latest["saved_result"]}
        if latest["status"] != "unknown":
            raise ValueError("任务状态已变化，请刷新后查看。")
        settings = job["settings"]
        target = output_dimensions(settings["aspect_ratio"], settings["resolution"]) if settings["resolution"] != "original" else None
        image = decode_image(result.get("result"), target)
        saved = ui.ImageSaveHelper.save_images(image, f"AstroGate/{job['project_id']}/{job['id']}", io.FolderType.output, None)[0]
        save_storyboard_image(store, job, saved, expected_status=("unknown",))
        return {"status": "succeeded", "saved_result": dict(saved)}
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, RuntimeError) as error:
        raise ValueError("查询图片结果失败，请稍后再试；未重新生成。") from error


class AstroGateImage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AstroGateImage",
            display_name="AstroGate AI 生成图像",
            category="image",
            not_idempotent=True,
            has_intermediate_output=True,
            description="通过 AstroGate 生成图像。把前一个节点的图像接到参考图即可续图；多视角可共用基准图和统一设定。输出可接预览、保存或后续生成节点。",
            inputs=[
                io.String.Input("prompt", display_name="提示词", default="", multiline=True,
                                tooltip="描述要生成的画面，也可连接 STRING 文本节点。"),
                io.Combo.Input("model", display_name="模型", options=["gpt-image-2"], default="gpt-image-2"),
                io.Combo.Input("aspect_ratio", display_name="画幅比例", options=list(ASPECT_SIZES), default="16:9"),
                io.Combo.Input("resolution", display_name="分辨率", options=["original", *RESOLUTION_EDGES], default="original",
                               tooltip="original 保留接口原图；1K/2K/3K/4K 长边为 1024/2048/3072/3840。接口返回尺寸不符时会按所选比例裁切缩放。"),
                io.Combo.Input("quality", display_name="质量", options=["low", "medium", "high"], default="medium"),
                io.Autogrow.Input("reference_images", optional=True,
                                  template=io.Autogrow.TemplateNames(io.Image.Input("reference", display_name="参考图"),
                                                                    names=[f"reference_{i}" for i in range(1, 9)], min=0),
                                  tooltip="可不连接；连接后使用图生图。支持多个输入或图像批次，合计最多 8 张。"),
                io.String.Input("consistency", display_name="统一设定", optional=True, default="", multiline=True,
                                tooltip="可连接同一个文本节点，描述人物脸型、发型、服装、配饰和画风。第1张参考图作为基准，后续参考图作补充。"),
                io.Combo.Input("view", display_name="视角", optional=True, options=list(VIEW_PROMPTS), default="自由",
                               tooltip="每个节点输出一张图；左/右侧指人物自身的左/右侧。场景续图或自定义角度选自由。"),
                io.Int.Input("generation_id", display_name="重绘编号", optional=True, default=0, min=-1, max=2147483647,
                             socketless=True,
                             tooltip="编号不变时可复用 ComfyUI 缓存；修改编号重新生成，-1 每次重画。这不是模型随机种子。重启或清缓存后可能重新生成，定稿请保存后加载。"),
            ],
            outputs=[io.Image.Output(display_name="图像")],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, generation_id=-1, **kwargs):
        return float("nan") if generation_id is None or generation_id < 0 else generation_id

    @classmethod
    async def execute(cls, prompt, model, aspect_ratio, resolution, quality, reference_images=None,
                      consistency="", view="自由", generation_id=-1):
        width, height = output_dimensions(aspect_ratio, resolution)
        references = encode_references(reference_images)
        parts = []
        if references and (consistency.strip() or view != "自由"):
            parts.append("参考约束：第1张参考图是主体身份、服装配饰和画风的基准，后续参考图仅补充动作、构图或细节。"
                         "除本次明确要求修改的内容外，保持人物脸型、年龄、发型、服装层次、颜色和配饰一致。"
                         "配饰左右以人物自身为准，不要镜像或互换左右。不可见部分合理补全；本次明确的修改要求优先。")
        if consistency.strip():
            parts.append(f"统一设定：\n{consistency}")
        if view != "自由":
            parts.append(f"视角要求：{VIEW_PROMPTS[view]}")
        parts.append(prompt)
        combined_prompt = "\n\n".join(parts)
        if not combined_prompt.strip() or len(combined_prompt) > 16000:
            raise ValueError("请填写提示词或统一设定；合并视角要求后的提示词最多 16000 个字符。")
        payload = {"model": model, "prompt": combined_prompt, "size": f"{width}x{height}", "quality": quality,
                   "n": 1, "stream": False, "response_format": "b64_json", "gateway_request_id": secrets.token_hex(32)}
        if references:
            payload["images"] = references
        node_id = cls.hidden.unique_id
        if node_id:
            PromptServer.instance.send_progress_text("正在生成图像，请稍候…", node_id)
        status = "图像生成失败或已取消，请查看错误信息"
        try:
            response = await generate_image(payload, bool(references))
            image = decode_image(response, (width, height) if resolution != "original" else None)
            status = "图像生成完成"
        finally:
            if node_id:
                PromptServer.instance.send_progress_text(status, node_id)
        return io.NodeOutput(image, ui=ui.PreviewImage(image))


class AstroGateStoryboardFrame(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AstroGateStoryboardFrame", display_name="AstroGate 分镜帧", category="image/AstroGate",
            description="图片和提示词同在一个原生节点。IMAGE 是实际参考图；剧情线只读取前情文本，不触发前镜重画。",
            is_output_node=True, not_idempotent=True,
            inputs=[io.String.Input("prompt", display_name="提示词", multiline=True, default=""),
                    io.String.Input("job_id", display_name="任务 ID", default="", socketless=True),
                    io.Autogrow.Input("reference_images", optional=True,
                                      template=io.Autogrow.TemplateNames(io.Image.Input("reference", lazy=True),
                                                                        names=[f"reference_{i}" for i in range(1, 101)], min=0)),
                    io.String.Input("element_id", optional=True, default="", socketless=True),
                    io.String.Input("aspect_ratio", display_name="画幅", optional=True, default="16:9"),
                    io.Combo.Input("resolution", display_name="分辨率", optional=True, options=["original", *RESOLUTION_EDGES], default="4K"),
                    io.Combo.Input("quality", display_name="质量", optional=True, options=["low", "medium", "high"], default="high"),
                    io.Custom("ASTROGATE_BOARD").Input("board", display_name="分镜脚本", optional=True, lazy=True, raw_link=True),
                    io.Custom("ASTROGATE_STORY").Input("story_context", display_name="剧情承接", optional=True, lazy=True, raw_link=True)],
            outputs=[io.Image.Output(display_name="图像"), io.Custom("ASTROGATE_STORY").Output(display_name="剧情")],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.extra_pnginfo])

    @classmethod
    def check_lazy_status(cls, job_id="", reference_images=None, **kwargs):
        if not job_id or get_store().job(job_id)["status"] == "succeeded":
            return []
        # V3 wraps an empty Autogrow group as ({}, input_key) during lazy checks.
        if isinstance(reference_images, tuple):
            reference_images = reference_images[0]
        return [key for value, key in (reference_images or {}).values() if value is None]

    @classmethod
    async def execute(cls, prompt, job_id, reference_images=None, element_id="", aspect_ratio=None, resolution=None, quality=None, board=None, story_context=None):
        if not job_id:
            return io.NodeOutput(ExecutionBlocker("图片尚未生成，请点击卡片上的“生成图像”。"), None)
        async with frame_queue.slot():
            try:
                return await cls._execute_frame(prompt, job_id, reference_images, element_id, aspect_ratio, resolution, quality, board, story_context)
            except (RuntimeError, ValueError, OSError) as error:
                store = get_store()
                job = store.job(job_id)
                if not job.get("native"):
                    raise
                if job["status"] in ("prepared", "queued"):
                    store.update_job(job_id, expected_status=("prepared", "queued"), status="failed", error=str(error))
                elif job["status"] == "succeeded" and isinstance(error, OSError):
                    store.update_job(job_id, expected_status=("succeeded",), status="unknown",
                                     error="本地保存的图片无法读取，可尝试查询并取回；未重新生成。")
                story = {"project_id": job["project_id"], "element_id": job["owner_id"], "summary": job["snapshot"]["shot"].get("summary", "")}
                return io.NodeOutput(ExecutionBlocker("上游参考图未成功，请检查对应卡片；不会自动重试。"), story)

    @classmethod
    async def _execute_frame(cls, prompt, job_id, reference_images=None, element_id="", aspect_ratio=None, resolution=None, quality=None, board=None, story_context=None):
        store = get_store()
        job = store.job(job_id)
        if element_id and element_id != job["owner_id"]:
            raise ValueError("任务与图片节点不匹配，请重新生成。")
        story_output = {"project_id": job["project_id"], "element_id": job["owner_id"], "summary": job["snapshot"]["shot"].get("summary", "")}
        if job["status"] == "succeeded":
            with Image.open(store.image_path(job["result_image"]["file"])) as opened:
                image = torch.from_numpy(np.array(opened.convert("RGB"), dtype=np.float32) / 255).unsqueeze(0)
            saved = job["saved_result"]
            return io.NodeOutput(image, story_output, ui=ui.SavedImages([ui.SavedResult(saved["filename"], saved["subfolder"], io.FolderType(saved["type"]))]))
        if job.get("native"):
            graph = cls.hidden.prompt or {}
            if board:
                analysis_inputs = graph[board[0]]["inputs"]
                source = graph[analysis_inputs["script"][0]]["inputs"]
                if source["project_id"] != job["project_id"]:
                    raise ValueError("图片与所连分镜脚本不属于同一项目。")
            if story_context:
                previous_id = graph[story_context[0]]["inputs"].get("element_id")
                if previous_id != job["snapshot"].get("story_parent_id"):
                    raise ValueError("剧情连线已改变，请点击生成建立新的快照。")
            for key, value in (("aspect_ratio", aspect_ratio), ("resolution", resolution), ("quality", quality)):
                if key == "aspect_ratio" and value is not None:
                    value = normalize_aspect_ratio(value)
                if value is not None and value != job["settings"][key]:
                    raise ValueError("图片参数已改变，请点击生成建立新的任务。")
        if prompt not in (job["prompt"], job["snapshot"]["shot"]["image_prompt"]):
            raise ValueError("提示词已修改，请点击卡片上的生成按钮建立新任务。")
        if job["status"] not in ("prepared", "queued"):
            raise RuntimeError("此任务不能重复发送。请查看任务状态；结果未知时不会自动重试。")
        ordered = dict(sorted((reference_images or {}).items(), key=lambda item: int(item[0].rsplit("_", 1)[1])))
        references = encode_references(ordered, max_images=None if job.get("native") else 8, image_format=None if job.get("native") else "png")
        expected = job["references"]
        if job.get("native"):
            resolved = []
            for entry, batch in ordered.items():
                if batch is None:
                    continue
                spec = next((r for r in expected if r["slot"] == f"reference_images.{entry}"), None)
                if spec is None:
                    raise ValueError("参考连线已改变，请重新点击生成。")
                for index in range(batch.shape[0]):
                    ref = {**spec}
                    if batch.shape[0] > 1:
                        ref["purpose"] += f"（批次第{index + 1}张）"
                    resolved.append(ref)
            if {r["slot"] for r in resolved} != {r["slot"] for r in expected}:
                raise ValueError("参考连线已改变，请重新点击生成。")
            expected = resolved
        if len(references) != len(expected):
            raise ValueError("参考图数量与生成计划不符，请重新建立连线。")
        for ref, binding in zip(references, expected):
            if binding.get("job_id") and not job.get("native"):
                upstream = store.job(binding["job_id"])
                if upstream["status"] != "succeeded":
                    raise ValueError("前帧尚未成功保存。")
                binding["image"] = upstream["result_image"]
            with Image.open(BytesIO(base64.b64decode(ref["b64_json"]))) as opened:
                if job.get("native"):
                    binding["image"] = {"hash": image_hash(opened), "width": opened.width, "height": opened.height}
                elif image_hash(opened) != binding["image"]["hash"]:
                    raise ValueError("参考图顺序或内容与计划不符，请更新素材引用后重新生成。")
        settings = job["settings"]
        request_refs = expected
        if job.get("native"):
            references, request_refs = pack_character_views(references, expected)
        if len(references) > 8:
            raise ValueError("本镜超过接口的8张参考图限制，请只连接必要的出镜人物或整理手动参考图。")
        width, height = output_dimensions(settings["aspect_ratio"], settings["resolution"])
        payload = {"model": "gpt-image-2", "prompt": job["prompt"], "size": f"{width}x{height}", "quality": settings["quality"],
                   "n": 1, "stream": False, "response_format": "b64_json", "gateway_request_id": job["gateway_request_id"]}
        if references:
            payload["images"] = references
        if job.get("native"):
            payload["prompt"] = render_prompt(job["snapshot"], request_refs)
        read_api_key()
        image_timeout()
        request_path = store.root / "requests" / f"{job['id']}.json"
        request_path.parent.mkdir(exist_ok=True)
        serialized = json.dumps(payload, ensure_ascii=False).encode()
        request_path.write_bytes(serialized)
        store.update_job(job_id, expected_status=("prepared", "queued"), status="running", references=expected,
                         prompt=payload["prompt"], request_hash=hashlib.sha256(serialized).hexdigest())
        node_id = cls.hidden.unique_id
        if node_id:
            PromptServer.instance.send_progress_text("正在生成分镜帧…", node_id)
        try:
            response = await generate_image(payload, bool(references))
            image = decode_image(response, (width, height) if settings["resolution"] != "original" else None)
            saved = ui.ImageSaveHelper.save_images(image, f"AstroGate/{job['project_id']}/{job['id']}", io.FolderType.output, cls)[0]
            save_storyboard_image(store, {**job, "references": expected}, saved)
            if node_id:
                PromptServer.instance.send_progress_text("已保存分镜帧", node_id)
            return io.NodeOutput(image, story_output, ui=ui.SavedImages([saved]))
        except (RuntimeError, ValueError, OSError, asyncio.CancelledError) as error:
            # A failed response cannot prove that the provider did not generate/bill the image.
            if store.job(job_id)["status"] != "succeeded":
                reason = str(error) or "本地任务已停止"
                store.update_job(job_id, status="unknown", error=f"请求已发出，结果未确认：{reason}；未自动重试。")
            if node_id:
                PromptServer.instance.send_progress_text("结果未确认，请查看任务记录", node_id)
            raise


class AstroGateTXT(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="AstroGateTXT", display_name="导入文本", category="text",
                         description="点击节点里的选择 TXT 文件，将剧本输出连接到 AI 分镜脚本节点。",
                         inputs=[io.String.Input("project_id", default="", socketless=True)],
                         outputs=[io.Custom("ASTROGATE_SCRIPT").Output(display_name="剧本"), io.String.Output(display_name="原文")])

    @classmethod
    def execute(cls, project_id):
        project = get_store().get(project_id)
        return io.NodeOutput({"project_id": project_id, "source_id": project["source"]["id"]}, project["source"]["text"])


class AstroGateStoryboard(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="AstroGateStoryboard", display_name="AI 分镜脚本 · 自动建图", category="text/AstroGate",
                         is_output_node=True, not_idempotent=True,
                         inputs=[io.Custom("ASTROGATE_SCRIPT").Input("script", display_name="TXT 剧本"),
                                 io.Combo.Input("text_model", display_name="文本模型", options=["gpt-6-luna", "gpt-5.6-terra"], default="gpt-6-luna"),
                                 io.Int.Input("chapter", display_name="章节序号", default=1, min=1),
                                 io.String.Input("style", display_name="画风设定", default="写实电影，自然摄影质感，所有人物和场景使用同一画风", multiline=True),
                                 io.String.Input("density", display_name="拆镜要求", default="精细拆镜，保留关键动作、对话反应、空间关系和必要空镜，每镜一张独立关键帧", multiline=True),
                                 io.Int.Input("analysis_revision", display_name="重写编号", default=0, min=0),
                                 io.String.Input("frame_aspect_ratio", display_name="统一画幅（宽:高）", optional=True, default="16:9"),
                                 io.Boolean.Input("include_props", display_name="独立物品素材（特殊需要时开启）", optional=True, default=False)],
                         outputs=[io.Custom("ASTROGATE_BOARD").Output(display_name="分镜脚本"), io.String.Output(display_name="脚本 JSON")],
                         hidden=[io.Hidden.unique_id])

    @classmethod
    async def execute(cls, script, text_model, chapter, style, density, analysis_revision=0, frame_aspect_ratio=None, include_props=False):
        store = get_store()
        settings = {"include_props": include_props}
        if frame_aspect_ratio is not None:
            settings["aspect_ratio"] = normalize_aspect_ratio(frame_aspect_ratio)
        if any(store.get(script["project_id"])["settings"].get(key) != value for key, value in settings.items()):
            store.change(script["project_id"], lambda p: p["settings"].update(settings))
        if cls.hidden.unique_id:
            PromptServer.instance.send_progress_text("AI 正在分析剧本、编写分镜与提示词…", cls.hidden.unique_id)
        board = await analyse_native(get_store(), script["project_id"], chapter, text_model, style, density, analysis_revision)
        project = get_store().get(board["project_id"])
        shots = [s for s in project["shots"] if s.get("analysis_id") == board["analysis_id"]]
        if cls.hidden.unique_id:
            PromptServer.instance.send_progress_text(f"已完成 {len(shots)} 镜，正在自动建立节点与连线", cls.hidden.unique_id)
        return io.NodeOutput(board, json.dumps(shots, ensure_ascii=False, indent=2), ui={"astrogate_board": [board], "astrogate_summary": [f"已完成 {len(shots)} 个镜头"]})


class AstroGateVideoPrompt(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="AstroGateVideoPrompt", display_name="AI 分镜视频提示词与素材清单", category="video/AstroGate",
                         description="将本章分镜、人物绘本、场景、角色声线和台词片段整理为视频提示词。图片和音频标记是需在即梦网页逐项附加的素材清单。",
                         is_output_node=True, not_idempotent=True,
                         inputs=[io.Custom("ASTROGATE_BOARD").Input("board", display_name="分镜脚本")],
                         outputs=[io.String.Output(display_name="视频提示词"), io.String.Output(display_name="素材清单 JSON")])

    @classmethod
    def execute(cls, board):
        store = get_store()
        project = store.get(board["project_id"])
        result = video_prompt(project, board["chapter_id"], board.get("analysis_id"), store.jobs(project["id"]))
        manifest = {key: result[key] for key in ("images", "audios", "warnings", "shot_ids")}
        return io.NodeOutput(result["prompt"], json.dumps(manifest, ensure_ascii=False, indent=2), ui={"text": [result["prompt"]]})


class AstroGateVideoAnalysis(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="AstroGateVideoAnalysis", display_name="分析剧情 · 视频字幕与分镜", category="video/AstroGate",
                         description="连接原生加载视频节点。CUDA 识别画面字幕，AI 校对一次，并创建人物、场景和分镜画布。",
                         is_output_node=True, not_idempotent=True,
                         inputs=[io.Video.Input("video", display_name="视频"),
                                 io.String.Input("project_id", default="", socketless=True),
                                 io.Combo.Input("text_model", display_name="文本模型", options=["gpt-6-luna", "gpt-5.6-terra"], default="gpt-6-luna"),
                                 io.Float.Input("sample_interval", display_name="采样间隔（秒）", default=1.0, min=0.2, max=10.0, step=0.1),
                                 io.Combo.Input("subtitle_region", display_name="字幕区域", options=["画面下方", "全画面"], default="画面下方"),
                                 io.String.Input("style", display_name="画风设定", default="写实电影，自然摄影质感，人物与场景和原片一致", multiline=True),
                                 io.String.Input("density", display_name="拆镜要求", default="依据原片字幕和抽帧还原剧情，保留关键镜头、反应、特写与转场", multiline=True),
                                 io.Int.Input("analysis_revision", display_name="重分析编号", default=0, min=0),
                                 io.String.Input("frame_aspect_ratio", display_name="分镜画幅", default="16:9")],
                         outputs=[io.Custom("ASTROGATE_BOARD").Output(display_name="剧情图"), io.String.Output(display_name="字幕 JSON")],
                         hidden=[io.Hidden.unique_id])

    @classmethod
    async def execute(cls, video, project_id, text_model, sample_interval, subtitle_region, style, density, analysis_revision, frame_aspect_ratio):
        if not project_id:
            raise ValueError("请先点击分析节点里的‘分析视频’，创建项目并提交任务。")
        store = get_store()
        store.change(project_id, lambda p: p["settings"].update(aspect_ratio=normalize_aspect_ratio(frame_aspect_ratio), style=style, density=density, text_model=text_model))
        if cls.hidden.unique_id:
            PromptServer.instance.send_progress_text("正在抽帧、CUDA 识别字幕并建立剧情图…", cls.hidden.unique_id)
        progress = (lambda message: PromptServer.instance.send_progress_text(message, cls.hidden.unique_id)) if cls.hidden.unique_id else None
        board = await analyse_video(store, project_id, video, text_model, sample_interval, subtitle_region, style, density, analysis_revision, on_progress=progress)
        lines = store.get(project_id)["video_import"]["ocr_lines"]
        return io.NodeOutput(board, json.dumps(lines, ensure_ascii=False, indent=2), ui={"astrogate_board": [board], "astrogate_summary": [f"已提取 {len(lines)} 条字幕"]})


class AstroGateExtension(ComfyExtension):
    async def get_node_list(self):
        return [AstroGateImage, AstroGateStoryboardFrame, AstroGateTXT, AstroGateStoryboard, AstroGateVideoAnalysis, AstroGateVideoPrompt,
                AstroGateJimengImageToVideo, AstroGateMiniMaxH3Video]


async def comfy_entrypoint():
    register_routes(recover_storyboard_image, generate_prepared_frame)
    register_jimeng_routes()
    return AstroGateExtension()
