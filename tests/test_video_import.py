import json
import sys
import copy

import numpy as np
import pytest
from PIL import Image, ImageFilter
from io import BytesIO

from test_storyboard import modules, project


def test_video_subtitle_correction_keeps_ids_and_raw_text(modules):
    video = sys.modules[modules.node.__name__ + ".storyboard.video_import"]
    lines = [{"id": "a", "time": 1.0, "raw": "你先去", "text": "你先去", "uncertain": False}]
    names = video.validate_correction(lines, {"lines": [{"id": "a", "text": "你先走", "uncertain": False}], "protagonists": ["周野", "周野"]})
    assert lines[0]["raw"] == "你先去"
    assert lines[0]["text"] == "你先走"
    assert names == ["周野"]
    with pytest.raises(ValueError, match="原始识别结果已保留"):
        video.validate_correction(lines, {"lines": [{"id": "other", "text": "伪造"}], "protagonists": []})


def test_portrait_metrics_prefers_sharp_frame(modules):
    video = sys.modules[modules.node.__name__ + ".storyboard.video_import"]
    sharp = Image.fromarray(np.random.default_rng(3).integers(0, 256, (180, 320, 3), dtype=np.uint8), "RGB")
    blurred = sharp.filter(ImageFilter.GaussianBlur(4))
    assert video.portrait_metrics(sharp)["portrait_score"] > video.portrait_metrics(blurred)["portrait_score"]


@pytest.mark.asyncio
async def test_video_analysis_reuses_ocr_and_links_existing_board(modules, monkeypatch):
    video = sys.modules[modules.node.__name__ + ".storyboard.video_import"]
    project = video.video_project("短剧")
    modules.store.create("owner", project)
    calls = []

    def extract(*args):
        calls.append("ocr")
        return ([{"id": "line1", "time": 1.5, "raw": "你先去", "text": "你先去", "uncertain": False}],
                [{"time": 1.5, "image": {"file": "astrogate/x/frame.png"}, "line_id": "line1"}])

    async def correct(*args):
        calls.append("correct")
        return json.dumps({"lines": [{"id": "line1", "text": "你先走", "uncertain": False}], "protagonists": ["周野"]}), {}

    async def build(store, project_id, chapter, model, style, density, revision, chunk_limit):
        calls.append("board")
        assert chunk_limit == 3000
        return {"project_id": project_id, "chapter_id": store.get(project_id)["chapters"][0]["id"], "analysis_id": "analysis1"}

    monkeypatch.setattr(video, "extract_video", extract)
    monkeypatch.setattr(video, "text_request", correct)
    monkeypatch.setattr(video, "analyse_native", build)
    first = await video.analyse_video(modules.store, project["id"], object(), "gpt-6-luna", 1.0, "画面下方", "写实", "精细", 0, source_mode="画面字幕（OCR）")
    second = await video.analyse_video(modules.store, project["id"], object(), "gpt-6-luna", 1.0, "画面下方", "写实", "精细", 0, source_mode="画面字幕（OCR）")
    saved = modules.store.get(project["id"])
    assert first == second
    assert calls == ["ocr", "correct", "board"]
    assert saved["source"]["text"] == "[1.500s] 你先走"
    assert saved["video_import"]["ocr_lines"][0]["raw"] == "你先去"
    assert saved["video_import"]["protagonists"] == ["周野"]


@pytest.mark.asyncio
async def test_video_analysis_defaults_to_speech_and_reuses_transcript(modules, monkeypatch):
    video = sys.modules[modules.node.__name__ + ".storyboard.video_import"]
    project = video.video_project("短剧")
    modules.store.create("owner", project)
    calls = []

    def extract(*args):
        calls.append("speech")
        return ([{"id": "line1", "time": 1.5, "end": 2.5, "raw": "你先走", "text": "你先走", "source": "asr", "uncertain": False}],
                [{"time": 1.5, "image": {"file": "astrogate/x/frame.png"}, "line_id": "line1"}])

    async def correct(*args):
        calls.append("correct")
        return json.dumps({"lines": [{"id": "line1", "text": "你先走", "uncertain": False}], "protagonists": ["周野"]}), {}

    async def build(store, project_id, chapter, model, style, density, revision, chunk_limit):
        calls.append("board")
        return {"project_id": project_id, "chapter_id": store.get(project_id)["chapters"][0]["id"], "analysis_id": "analysis1"}

    monkeypatch.setattr(video, "extract_speech", extract)
    monkeypatch.setattr(video, "text_request", correct)
    monkeypatch.setattr(video, "analyse_native", build)
    await video.analyse_video(modules.store, project["id"], object(), "gpt-6-luna", 1.0, "画面下方", "写实", "精细", 0)
    await video.analyse_video(modules.store, project["id"], object(), "gpt-6-luna", 1.0, "画面下方", "写实", "精细", 0)
    assert calls == ["speech", "correct", "board"]
    assert modules.store.get(project["id"])["video_import"]["transcript_lines"][0]["end"] == 2.5


def test_video_reference_nodes_join_existing_story_graph(modules, project):
    native = sys.modules[modules.node.__name__ + ".storyboard.native"]
    raw = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(raw, "PNG")
    frame = modules.store.save_image(project["id"], raw.getvalue())
    project["video_import"] = {"status": "completed", "protagonists": ["林夏"], "frames": [{"time": 1, "image": frame}]}
    project["assets"][0]["video_reference"] = frame
    project["shots"][0]["video_reference"] = frame
    recipe = native.native_canvas(modules.store, project, project["chapters"][0]["id"])
    sources = [node for node in recipe["nodes"] if node["type"] == "LoadImage"]
    assert len(sources) == 2
    assert all(node["widgets"]["image"] == frame["file"] for node in sources)
    assert any(link["from"] == project["shots"][0]["id"] + "::video_source" and link["type"] == "IMAGE" for link in recipe["links"])
    assert next(node for node in recipe["nodes"] if node["id"] == project["assets"][0]["id"])["properties"]["astrogate_is_protagonist"]


def test_character_reference_uses_clearest_solo_shot_frame(modules, project):
    video = sys.modules[modules.node.__name__ + ".storyboard.video_import"]
    original = project["shots"][0]
    original["analysis_id"] = "analysis1"
    original["participants"] = [original["participants"][0]]
    original["source_refs"] = [{"paragraph_id": "line1", "quote": None}]
    second = copy.deepcopy(original)
    second["id"] = "shot2"
    second["source_refs"] = [{"paragraph_id": "line2", "quote": None}]
    third = copy.deepcopy(original)
    third["id"] = "shot3"
    third["participants"] = [{"visible": True, "identity": modules.domain.binding(project, "周野")},
                             {"visible": True, "identity": modules.domain.binding(project, "林夏")}]
    third["source_refs"] = [{"paragraph_id": "line3", "quote": None}]
    fourth = copy.deepcopy(third)
    fourth["id"] = "shot4"
    fourth["participants"] = [third["participants"][0]]
    fourth["source_refs"] = [{"paragraph_id": "line4", "quote": None}]
    project["shots"] = [original, second, third, fourth]
    project["video_import"] = {"frames": [
        {"time": 0, "image": {"file": "astrogate/test/soft.png"}, "line_id": "line1", "portrait_score": 10},
        {"time": 1, "image": {"file": "astrogate/test/clear.png", "hash": "same-frame"}, "line_id": "line2", "portrait_score": 80},
        {"time": 2, "image": {"file": "astrogate/test/group.png"}, "line_id": "line3", "portrait_score": 90},
        {"time": 3, "image": {"file": "astrogate/test/copy.png", "hash": "same-frame"}, "line_id": "line4", "portrait_score": 90}]}
    video.attach_frames(project, "analysis1")
    assert project["assets"][0]["video_reference"]["file"].endswith("clear.png")
    assert project["assets"][0]["video_reference_time"] == 1
    assert project["assets"][0]["video_reference_status"] == "suggested"
    assert "video_reference" not in project["assets"][2]
