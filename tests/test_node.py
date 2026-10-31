import asyncio
import base64
import importlib.util
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from aiohttp import web
from PIL import Image


@pytest.fixture
def node(monkeypatch, tmp_path):
    path = Path(__file__).parents[1] / "__init__.py"
    assert path.exists(), "AstroGate custom node is not implemented"
    spec = importlib.util.spec_from_file_location("astrogate_node_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.AstroGateImage, "hidden", SimpleNamespace(unique_id=None), raising=False)
    monkeypatch.setenv("ASTROGATE_IMG_API_KEY", "test-only-key")
    monkeypatch.delenv("ASTROGATE_REFERENCE_FORMAT", raising=False)
    monkeypatch.setattr(module, "KEY_FILE", tmp_path / "api-key")
    return module


async def test_generation_status_tracks_success_and_failure(node, aiohttp_server, monkeypatch):
    messages = []
    monkeypatch.setattr(node, "PromptServer", SimpleNamespace(instance=SimpleNamespace(
        send_progress_text=lambda text, node_id: messages.append((text, node_id)))))
    monkeypatch.setattr(node.AstroGateImage, "hidden", SimpleNamespace(unique_id="42"))

    async def success(request):
        assert messages == [("正在生成图像，请稍候…", "42")]
        return web.json_response(image_payload())

    await api_server(aiohttp_server, node, success)
    await node.AstroGateImage.execute("test", "gpt-image-2", "1:1", "original", "medium")
    assert messages[-1] == ("图像生成完成", "42")

    messages.clear()

    async def failure(request):
        assert messages == [("正在生成图像，请稍候…", "42")]
        return web.Response(status=502)

    await api_server(aiohttp_server, node, failure)
    with pytest.raises(RuntimeError, match="502"):
        await node.AstroGateImage.execute("test", "gpt-image-2", "1:1", "original", "medium")
    assert messages[-1] == ("图像生成失败或已取消，请查看错误信息", "42")


async def test_generation_status_clears_on_cancellation(node, monkeypatch):
    messages = []
    monkeypatch.setattr(node, "PromptServer", SimpleNamespace(instance=SimpleNamespace(
        send_progress_text=lambda text, node_id: messages.append((text, node_id)))))
    monkeypatch.setattr(node.AstroGateImage, "hidden", SimpleNamespace(unique_id="42"))

    async def cancelled(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(node, "generate_image", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await node.AstroGateImage.execute("test", "gpt-image-2", "1:1", "original", "medium")
    assert messages == [("正在生成图像，请稍候…", "42"), ("图像生成失败或已取消，请查看错误信息", "42")]


def image_payload(size=(24, 16), color=(10, 20, 30, 255)):
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return {"created": 1, "data": [{"b64_json": base64.b64encode(buffer.getvalue()).decode(), "url": "https://untrusted.invalid/image", "revised_prompt": "test"}]}


async def api_server(aiohttp_server, node, handler):
    app = web.Application()
    app.router.add_post("/v1/images/{operation}", handler)
    server = await aiohttp_server(app)
    node.API_BASE = str(server.make_url("/v1"))


def test_schema_supports_native_connections_without_credentials(node):
    schema = node.AstroGateImage.define_schema()
    assert schema.category == "image"
    assert [output.io_type for output in schema.outputs] == ["IMAGE"]
    fields = {field.id: field for field in schema.inputs}
    assert fields["prompt"].get_io_type() == "STRING"
    assert not fields["prompt"].socketless
    assert fields["reference_images"].optional
    assert not any("key" in field for field in fields)


def test_text_import_connects_to_storyboard(node):
    source = node.AstroGateTXT.define_schema()
    storyboard = node.AstroGateStoryboard.define_schema()
    assert source.node_id == "AstroGateTXT"
    assert source.display_name == "导入文本"
    assert source.category == "text"
    assert [output.io_type for output in source.outputs] == ["ASTROGATE_SCRIPT", "STRING"]
    script = next(field for field in storyboard.inputs if field.id == "script")
    assert script.get_io_type() == source.outputs[0].io_type
    props = next(field for field in storyboard.inputs if field.id == "include_props")
    assert props.optional and props.default is False


async def test_completed_frame_reads_saved_image_after_live_inputs_change(node, monkeypatch, tmp_path):
    Image.new("RGB", (4, 3), (10, 20, 30)).save(tmp_path / "saved.png")
    job = {"id": "saved", "native": True, "status": "succeeded", "project_id": "p", "owner_id": "lamp",
           "snapshot": {"shot": {"summary": "油灯", "image_prompt": "旧提示词"}}, "prompt": "旧请求",
           "result_image": {"file": "saved.png"}, "saved_result": {"filename": "saved.png", "subfolder": "", "type": "output"}}
    monkeypatch.setattr(node, "get_store", lambda: SimpleNamespace(job=lambda _: job, image_path=lambda file: tmp_path / file))
    monkeypatch.setattr(node.AstroGateStoryboardFrame, "hidden", SimpleNamespace(prompt={}), raising=False)

    async def unexpected_request(*args):
        pytest.fail("Reading a completed reference must not request another image")

    monkeypatch.setattr(node, "generate_image", unexpected_request)
    result = await node.AstroGateStoryboardFrame.execute("新提示词", "saved", element_id="lamp", aspect_ratio="9:16",
                                                       resolution="1K", quality="low", board=["changed-board", 0], story_context=["changed-story", 1])
    assert result.result[0].shape == (1, 3, 4, 3)
    np.testing.assert_allclose(result.result[0][0, 0, 0], np.array([10, 20, 30]) / 255, atol=1e-6)
    assert result.result[1]["summary"] == "油灯"
    with pytest.raises(ValueError, match="任务与图片节点不匹配"):
        await node.AstroGateStoryboardFrame._execute_frame("新提示词", "saved", element_id="different")


async def test_completed_frame_missing_file_becomes_unknown_without_regeneration(node, monkeypatch, tmp_path):
    job = {"id": "missing", "native": True, "status": "succeeded", "project_id": "p", "owner_id": "lamp",
           "snapshot": {"shot": {"summary": "油灯"}}, "result_image": {"file": "missing.png"}}
    updates = []
    store = SimpleNamespace(job=lambda _: job, image_path=lambda file: tmp_path / file,
                            update_job=lambda *args, **kwargs: updates.append((args, kwargs)))
    monkeypatch.setattr(node, "get_store", lambda: store)

    async def unexpected_request(*args):
        pytest.fail("A missing completed result must not be regenerated automatically")

    monkeypatch.setattr(node, "generate_image", unexpected_request)
    result = await node.AstroGateStoryboardFrame.execute("新提示词", "missing", element_id="lamp")
    assert isinstance(result.result[0], node.ExecutionBlocker)
    assert updates[0][0] == ("missing",)
    assert updates[0][1]["expected_status"] == ("succeeded",)
    assert updates[0][1]["status"] == "unknown"


def test_continuity_fields_and_local_cache_revision(node):
    schema = node.AstroGateImage.define_schema()
    assert schema.not_idempotent
    fields = {field.id: field for field in schema.inputs}
    assert fields["consistency"].optional and not fields["consistency"].socketless
    assert fields["view"].optional and fields["view"].default == "自由"
    assert fields["generation_id"].optional and fields["generation_id"].socketless
    assert fields["generation_id"].default == 0
    assert node.AstroGateImage.fingerprint_inputs(generation_id=0) == 0
    assert node.AstroGateImage.fingerprint_inputs(generation_id=8) == 8
    assert np.isnan(node.AstroGateImage.fingerprint_inputs(generation_id=-1))
    assert np.isnan(node.AstroGateImage.fingerprint_inputs())
    assert np.isnan(node.AstroGateImage.fingerprint_inputs(generation_id=None))


@pytest.mark.parametrize("view,detail", [
    ("正面", "正面"), ("背面", "身后"), ("左侧", "左耳"),
    ("右侧", "右耳"), ("配饰细节", "材质"),
])
async def test_character_views_keep_shared_details_and_anchor(node, aiohttp_server, view, detail):
    async def handle(request):
        assert request.match_info["operation"] == "edits"
        body = await request.json()
        assert "短黑发，左耳月牙耳坠，黄色围巾" in body["prompt"]
        assert "保持同一位旅行者" in body["prompt"]
        assert detail in body["prompt"]
        assert "第1张参考图" in body["prompt"] and "不要镜像" in body["prompt"]
        assert len(body["images"]) == 2
        assert "generation_id" not in body and "seed" not in body
        return web.json_response(image_payload())

    await api_server(aiohttp_server, node, handle)
    references = {"reference_1": torch.zeros(1, 8, 8, 3), "reference_2": torch.ones(1, 8, 8, 3)}
    result = await node.AstroGateImage.execute("保持同一位旅行者", "gpt-image-2", "3:4", "original", "low", references,
                                             consistency="短黑发，左耳月牙耳坠，黄色围巾", view=view, generation_id=0)
    assert result.result[0].shape == (1, 16, 24, 3)


async def test_view_and_description_can_generate_without_freeform_prompt(node, aiohttp_server):
    async def handle(request):
        body = await request.json()
        assert "蓝衣旅行者" in body["prompt"] and "正面" in body["prompt"]
        assert "第1张参考图" not in body["prompt"]
        return web.json_response(image_payload())

    await api_server(aiohttp_server, node, handle)
    await node.AstroGateImage.execute("", "gpt-image-2", "3:4", "original", "low", consistency="蓝衣旅行者", view="正面")


async def test_final_combined_prompt_limit_is_checked_before_network(node, monkeypatch):
    def unexpected_session(*args, **kwargs):
        pytest.fail("Oversized prompt must fail before sending a request")

    monkeypatch.setattr(node.aiohttp, "ClientSession", unexpected_session)
    with pytest.raises(ValueError, match="16000"):
        await node.AstroGateImage.execute("x" * 15000, "gpt-image-2", "1:1", "original", "low", consistency="y" * 1500)


@pytest.mark.parametrize("aspect,resolution,expected", [
    ("16:9", "original", (1536, 864)),
    ("16:9", "2K", (2048, 1152)),
    ("9:16", "4K", (2160, 3840)),
    ("1:1", "1K", (1024, 1024)),
    ("21:9", "3K", (3072, 1316)),
])
def test_resolution_dimensions(node, aspect, resolution, expected):
    assert node.output_dimensions(aspect, resolution) == expected


async def test_text_generation_sends_options_and_returns_rgb_tensor(node, aiohttp_server):
    async def handle(request):
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert request.match_info["operation"] == "generations"
        body = await request.json()
        assert body["prompt"] == "a blue house"
        assert body["model"] == "gpt-image-2"
        assert body["size"] == "1536x864"
        assert body["quality"] == "high"
        assert body["n"] == 1 and body["stream"] is False
        assert len(body["gateway_request_id"]) == 64
        assert "images" not in body
        return web.json_response(image_payload())

    await api_server(aiohttp_server, node, handle)
    result = await node.AstroGateImage.execute("a blue house", "gpt-image-2", "16:9", "original", "high")
    image = result.result[0]
    assert image.shape == (1, 16, 24, 3)
    assert image.dtype == torch.float32
    np.testing.assert_allclose(image[0, 0, 0], np.array([10, 20, 30]) / 255, atol=1e-6)


async def test_reference_inputs_preserve_batch_order_and_switch_endpoint(node, aiohttp_server):
    async def handle(request):
        assert request.match_info["operation"] == "edits"
        body = await request.json()
        assert len(body["images"]) == 3
        for item, expected in zip(body["images"], [0, 128, 255]):
            with Image.open(BytesIO(base64.b64decode(item["b64_json"]))) as image:
                assert image.getpixel((0, 0))[0] == expected
            assert item["mime_type"] == "image/jpeg"
        return web.json_response(image_payload())

    await api_server(aiohttp_server, node, handle)
    references = {"reference_1": torch.zeros(1, 8, 8, 3), "reference_2": torch.stack([torch.full((8, 8, 3), 0.5), torch.ones(8, 8, 3)])}
    result = await node.AstroGateImage.execute("edit", "gpt-image-2", "1:1", "original", "low", references)
    assert result.result[0].shape == (1, 16, 24, 3)


def test_full_resolution_reference_transport_is_smaller_without_resizing(node):
    texture = np.random.default_rng(10).integers(0, 256, (270, 480, 3), dtype=np.uint8)
    image = Image.fromarray(texture).resize((3840, 2160), Image.Resampling.BICUBIC)
    compact, encoding = node.encode_reference(image)
    lossless, _ = node.encode_reference(image, "png")
    assert encoding == "jpeg"
    assert len(compact) < len(lossless) * .5
    with Image.open(BytesIO(compact)) as decoded:
        assert decoded.format == "JPEG" and decoded.size == image.size
        assert decoded.mode == "RGB"


def test_lossless_reference_config_preserves_pixels(node, monkeypatch):
    monkeypatch.setenv("ASTROGATE_REFERENCE_FORMAT", "png")
    tensor = torch.arange(8 * 8 * 3).reshape(1, 8, 8, 3) / 255
    encoded = node.encode_references({"reference_1": tensor})[0]
    assert encoded["mime_type"] == "image/png" and encoded["filename"].endswith(".png")
    with Image.open(BytesIO(base64.b64decode(encoded["b64_json"]))) as decoded:
        np.testing.assert_array_equal(np.array(decoded), tensor[0].numpy() * 255)


def test_reference_transparency_keeps_png(node):
    tensor = torch.ones(1, 8, 8, 4)
    tensor[..., 3] = .5
    encoded = node.encode_references({"reference_1": tensor})[0]
    assert encoded["mime_type"] == "image/png"
    with Image.open(BytesIO(base64.b64decode(encoded["b64_json"]))) as decoded:
        assert decoded.mode == "RGBA" and decoded.getpixel((0, 0))[3] == 128


def test_unknown_reference_format_fails_before_request(node, monkeypatch):
    monkeypatch.setenv("ASTROGATE_REFERENCE_FORMAT", "webp")
    with pytest.raises(ValueError, match="jpeg.*png"):
        node.encode_references({"reference_1": torch.ones(1, 8, 8, 3)})


async def test_requested_resolution_is_sent_and_applied_to_output(node, aiohttp_server):
    async def handle(request):
        assert (await request.json())["size"] == "576x1024"
        return web.json_response(image_payload())

    await api_server(aiohttp_server, node, handle)
    result = await node.AstroGateImage.execute("portrait", "gpt-image-2", "9:16", "1K", "medium")
    assert result.result[0].shape == (1, 1024, 576, 3)


async def test_missing_key_fails_before_network(node, monkeypatch):
    monkeypatch.delenv("ASTROGATE_IMG_API_KEY")
    with pytest.raises(RuntimeError, match="ASTROGATE_IMG_API_KEY"):
        await node.AstroGateImage.execute("test", "gpt-image-2", "1:1", "original", "medium")


def test_local_key_file_is_supported(node, monkeypatch):
    monkeypatch.delenv("ASTROGATE_IMG_API_KEY")
    node.KEY_FILE.write_text("local-test-key\n", encoding="utf-8")
    assert node.read_api_key() == "local-test-key"


@pytest.mark.parametrize("status", [401, 429, 502, 307])
async def test_http_errors_do_not_leak_body_or_retry(node, aiohttp_server, status):
    calls = []

    async def handle(request):
        calls.append(request.path)
        return web.Response(status=status, text="private test-only-key provider detail", headers={"Location": "/do-not-follow"})

    await api_server(aiohttp_server, node, handle)
    with pytest.raises(RuntimeError) as error:
        await node.AstroGateImage.execute("test", "gpt-image-2", "1:1", "original", "medium")
    assert str(status) in str(error.value)
    assert "private" not in str(error.value) and "test-only-key" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [{"data": []}, {"data": [{"b64_json": "broken"}]}, {"data": [{"url": "https://untrusted.invalid/"}]}])
async def test_bad_provider_images_are_rejected(node, aiohttp_server, payload):
    async def handle(request):
        return web.json_response(payload)

    await api_server(aiohttp_server, node, handle)
    with pytest.raises(RuntimeError, match="图片"):
        await node.AstroGateImage.execute("test", "gpt-image-2", "1:1", "original", "medium")


async def test_reference_limit_counts_all_batch_images(node):
    with pytest.raises(ValueError, match="8"):
        await node.AstroGateImage.execute("test", "gpt-image-2", "1:1", "original", "medium", {"reference_1": torch.zeros(9, 8, 8, 3)})


async def test_timeout_does_not_retry_or_leak_credentials(node, monkeypatch):
    class TimeoutSession:
        async def __aenter__(self):
            raise asyncio.TimeoutError("test-only-key")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(node.aiohttp, "ClientSession", lambda **kwargs: TimeoutSession())
    with pytest.raises(RuntimeError, match="超时") as error:
        await node.AstroGateImage.execute("test", "gpt-image-2", "1:1", "original", "medium")
    assert "test-only-key" not in str(error.value)


async def test_lost_upload_retries_only_after_gateway_confirms_missing(node, aiohttp_server):
    calls = []

    async def submit(request):
        calls.append("post")
        await request.json()
        if calls.count("post") == 1:
            request.transport.close()
            return web.Response()
        return web.json_response(image_payload())

    async def check(request):
        calls.append("get")
        return web.json_response({"status": "missing"})

    app = web.Application()
    app.router.add_post("/v1/images/generations", submit)
    app.router.add_get("/v1/images/jobs/{request_id}", check)
    server = await aiohttp_server(app)
    node.API_BASE = str(server.make_url("/v1"))
    result = await node.generate_image({"prompt": "test", "gateway_request_id": "a" * 64}, False)
    assert len(result["data"]) == 1
    assert calls == ["post", "get", "post"]
