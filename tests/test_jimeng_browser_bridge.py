import json
from pathlib import Path


BRIDGE_ROOT = Path(__file__).parents[1] / "jimeng_browser_bridge"


def test_browser_bridge_uses_only_the_page_and_local_comfyui_permissions():
    manifest = json.loads((BRIDGE_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert set(manifest["permissions"]) == {"activeTab", "scripting", "tabs"}
    assert manifest["host_permissions"] == [
        "https://jimeng.jianying.com/*",
        "http://127.0.0.1:8190/*",
    ]


def test_browser_bridge_does_not_read_browser_credentials_or_generate_page_signatures():
    source = "\n".join(
        (BRIDGE_ROOT / name).read_text(encoding="utf-8")
        for name in ("background.js", "content.js", "observer.js", "popup.js")
    ).lower()

    assert "document.cookie" not in source
    assert "chrome.cookies" not in source
    assert "webrequest" not in source
    assert "debugger" not in source
    assert "a_bogus" not in source
    assert "mstoken" not in source


def test_observer_only_tracks_native_generation_results():
    source = (BRIDGE_ROOT / "observer.js").read_text(encoding="utf-8")

    assert 'path.endsWith("/node_generate")' in source
    assert 'path.endsWith("/resource/batch_get")' in source
    assert 'type: "resource"' in source
    assert 'type: "video"' in source
