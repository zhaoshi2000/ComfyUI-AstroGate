import asyncio
import importlib.util
import sys
from io import BytesIO
from pathlib import Path

import pytest
import torch
from PIL import Image


@pytest.fixture
def jimeng(monkeypatch):
    path = Path(__file__).parents[1] / "jimeng.py"
    spec = importlib.util.spec_from_file_location("astrogate_jimeng_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("url", [
    "https://media.jianying.com/output.mp4",
    "https://tos.byteimg.com/video/output.mp4",
    "https://jimeng.jianying.com/video/output.mp4",
])
def test_video_download_address_accepts_only_expected_https_hosts(jimeng, url):
    assert jimeng.allowed_video_url(url)


@pytest.mark.parametrize("url", [
    "http://media.jianying.com/output.mp4",
    "https://jianying.com/output.mp4",
    "https://not-jianying.example/output.mp4",
    "https://media.jianying.com:8443/output.mp4",
    "https://user@media.jianying.com/output.mp4",
    "https://media.jianying.com.evil.example/output.mp4",
])
def test_video_download_address_rejects_untrusted_origins(jimeng, url):
    assert not jimeng.allowed_video_url(url)


def test_bridge_image_creates_bounded_rgb_jpeg(jimeng):
    image = torch.zeros((1, 8, 4, 4), dtype=torch.float32)
    image[:, :, :, 0] = 1
    image[:, :, :, 3] = 0.5

    data = jimeng.bridge_image(image)

    assert data[:2] == b"\xff\xd8"
    assert len(data) <= jimeng.MAX_IMAGE_BYTES
    with Image.open(BytesIO(data)) as saved:
        assert saved.mode == "RGB"
        assert min(saved.size) >= 320


def test_pair_codes_are_one_time_and_expire_from_the_bridge_state(jimeng):
    bridge = jimeng.JimengBrowserBridge()
    code = bridge.new_pair()

    assert bridge.claim_pair(code)
    assert not bridge.claim_pair(code)


async def test_job_requires_a_paired_browser_before_any_request(jimeng):
    bridge = jimeng.JimengBrowserBridge()
    with pytest.raises(RuntimeError, match="浏览器未连接"):
        await bridge.run(torch.zeros((1, 8, 8, 3)), "walk forward", "node-1")


async def test_replaced_browser_only_fails_its_own_pending_job(jimeng):
    class Socket:
        closed = False

    bridge = jimeng.JimengBrowserBridge()
    old_socket = Socket()
    replacement = Socket()
    future = asyncio.get_running_loop().create_future()
    bridge.jobs["job"] = jimeng.BridgeJob("job", "", b"image", None, future, old_socket)
    bridge.socket = replacement

    bridge.disconnected(old_socket)

    assert bridge.socket is replacement
    assert future.done()
    assert "会话已断开" in str(future.exception())


def test_node_schema_uses_native_video_without_credential_inputs(jimeng):
    schema = jimeng.AstroGateJimengImageToVideo.define_schema()
    fields = {field.id: field for field in schema.inputs}

    assert schema.node_id == "AstroGateJimengImageToVideo"
    assert schema.category == "video/AstroGate"
    assert [output.io_type for output in schema.outputs] == ["VIDEO"]
    assert fields["image"].get_io_type() == "IMAGE"
    assert fields["prompt"].get_io_type() == "STRING"
    assert not any("cookie" in field.lower() or "key" in field.lower() for field in fields)
    assert jimeng.AstroGateJimengImageToVideo.fingerprint_inputs(generation_id=7) == 7
    assert jimeng.AstroGateJimengImageToVideo.fingerprint_inputs(generation_id=-1) != jimeng.AstroGateJimengImageToVideo.fingerprint_inputs(generation_id=-1)
