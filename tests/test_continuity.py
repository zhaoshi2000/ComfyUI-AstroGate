import copy
import json
import sys

import pytest

from test_storyboard import modules as modules, project as project


def candidate(project, local_id, **changes):
    paragraph = project["chapters"][0]["paragraphs"][1]
    return {"local_id": local_id, "summary": local_id, "image_prompt": "客栈中的独立分镜",
            "source_refs": [{"paragraph_id": paragraph["id"], "quote": None}], "participants": [],
            "scene_id": "客栈", "story_time": "当晚", "prop_ids": [], **changes}


def merge(modules, project, shots, analysis_id="analysis", chapter_id=None):
    chapter_id = chapter_id or project["chapters"][0]["id"]
    modules.analysis.merge_shots(project, chapter_id, {"shots": shots},
                                 {p["id"] for c in project["chapters"] for p in c["paragraphs"]}, analysis_id)


def test_consecutive_same_scene_shots_keep_story_links_without_locking_image_composition(modules, project):
    project["shots"] = []
    closeup = "特写，俯拍；画面仅保留钥匙与桌面，不出现人物，钥匙被轻推的瞬间。"
    merge(modules, project, [candidate(project, "s1", shot_size="全景", camera_angle="平视"),
                             candidate(project, "s2", shot_size="特写", camera_angle="俯拍", image_prompt=closeup,
                                       story_parent_local_id="s1", story_relation="动作延续")])
    first, second = project["shots"]
    assert first["previous_shot_id"] is None
    assert second["previous_shot_id"] is None
    assert second["story_parent_id"] == first["id"]
    assert second["shot_size"] == "特写" and second["camera_angle"] == "俯拍"
    assert second["image_prompt"] == closeup and second["participants"] == []
    native = sys.modules[modules.node.__name__ + ".storyboard.native"]
    recipe = native.native_canvas(modules.store, project, first["chapter_id"], "analysis")
    assert not any(link["from"] == first["id"] and link["to"] == second["id"] and link["type"] == "IMAGE"
                   for link in recipe["links"])
    assert any(link["from"] == first["id"] and link["to"] == second["id"] and link["type"] == "ASTROGATE_STORY"
               for link in recipe["links"])


@pytest.mark.parametrize("changes", [
    {"continue_previous": False}, {"continue_previous": True}, {"story_time": "次日"}, {"scene_id": None},
    {"story_relation": "转场"}, {"story_relation": "时间推进"}, {"story_relation": "回忆"},
])
def test_cut_or_time_change_does_not_chain_images(modules, project, changes):
    project["shots"] = []
    merge(modules, project, [candidate(project, "s1"), candidate(project, "s2", **changes)])
    assert project["shots"][1]["previous_shot_id"] is None


def test_different_scene_and_empty_scenes_remain_independent(modules, project):
    street = copy.deepcopy(project["assets"][-1])
    street.update(id="街道", name="街道", current_version_id="街道1")
    street["versions"][0]["id"] = "街道1"
    project["assets"].append(street)
    project["shots"] = []
    merge(modules, project, [candidate(project, "s1"), candidate(project, "s2", scene_id="街道"),
                             candidate(project, "s3", scene_id=None), candidate(project, "s4", scene_id=None)])
    assert all(shot["previous_shot_id"] is None for shot in project["shots"])


def test_explicit_previous_is_preserved_and_explicit_cut_disconnects(modules, project):
    project["shots"] = []
    merge(modules, project, [candidate(project, "s1"), candidate(project, "s2", continue_previous=False),
                             candidate(project, "s3", previous_local_id="s1", previous_reason="延续空间"),
                             candidate(project, "s4", previous_local_id="s3", continue_previous=False)])
    assert project["shots"][2]["previous_shot_id"] == project["shots"][0]["id"]
    assert project["shots"][2]["previous_reason"] == "延续空间"
    assert project["shots"][3]["previous_shot_id"] is None


def test_cross_chunk_image_reference_requires_an_explicit_known_previous_shot(modules, project):
    project["shots"] = []
    merge(modules, project, [candidate(project, "s1")])
    prior = copy.deepcopy(project["shots"][0])
    merge(modules, project, [candidate(project, "s1", previous_local_id="previous_chunk")])
    assert project["shots"][-1]["previous_shot_id"] == prior["id"]
    assert project["shots"][0] == prior
    merge(modules, project, [candidate(project, "s1")])
    assert project["shots"][-1]["previous_shot_id"] is None
    merge(modules, project, [candidate(project, "s1")], analysis_id="other-analysis")
    assert project["shots"][-1]["previous_shot_id"] is None
    merge(modules, project, [candidate(project, "s1")], chapter_id=project["chapters"][1]["id"])
    assert project["shots"][-1]["previous_shot_id"] is None


def test_invalid_previous_chunk_is_rejected_without_prior_analysis(modules, project):
    with pytest.raises(ValueError, match="上一帧依赖"):
        merge(modules, project, [candidate(project, "s1", previous_local_id="previous_chunk")])


@pytest.mark.parametrize(("include_props", "job_include_props"), [(None, None), (False, None), (True, None), (True, False), (False, True)])
async def test_analysis_passes_prop_generation_setting_to_both_stages(modules, project, monkeypatch, include_props, job_include_props):
    prop = {"id": "油灯", "name": "油灯", "kind": "prop", "parent_id": None, "aliases": [],
            "current_version_id": "油灯1", "versions": [{"id": "油灯1", "description": "铜制油灯", "image": None}]}
    def configure(p):
        p["assets"].append(prop)
        if include_props is not None:
            p["settings"]["include_props"] = include_props
    modules.store.change(project["id"], configure)
    calls = []
    async def request(model, system, content, on_progress=None):
        calls.append((system, copy.deepcopy(content)))
        return ('{"assets":[]}' if system == modules.analysis.EXTRACT_PROMPT else '{"shots":[]}'), {}
    monkeypatch.setattr(modules.analysis, "text_request", request)
    fields = {"kind": "text", "chapter_id": project["chapters"][0]["id"], "model": "gpt-6-luna", "style": "写实", "density": "精细"}
    if job_include_props is not None:
        fields["include_props"] = job_include_props
    job = modules.store.add_job(project["id"], fields)
    await modules.analysis.analyse(modules.store, job["id"])
    assert modules.store.job(job["id"])["status"] == "succeeded"
    assert [system for system, _ in calls] == [modules.analysis.EXTRACT_PROMPT, modules.analysis.SHOT_PROMPT]
    for _, content in calls:
        assert content["generate_prop_assets"] is (job_include_props if job_include_props is not None else include_props is True)
        assert any(asset["id"] == "油灯" and asset["kind"] == "prop" for asset in content["known_assets"])
    assert modules.domain.asset_by_id(modules.store.get(project["id"]), "油灯")["versions"] == prop["versions"]


@pytest.mark.parametrize("lock_previous", [False, True])
async def test_analysis_keeps_cinematic_crop_and_parses_explicit_legacy_previous_metadata(modules, project, monkeypatch, lock_previous):
    density = "精细拆镜，保持人物和空间关系一致" if not lock_previous else "明确使用上一帧图像作为连续构图参考"
    closeup = "物品特写，俯拍；裁切仅保留铜钥匙与桌面，钥匙停在桌缘的瞬间，无人物。"

    async def request(model, system, content, on_progress=None):
        if system == modules.analysis.EXTRACT_PROMPT:
            return '{"assets":[]}', {}
        assert content["shot_density"] == density
        assert "关键微动作" in system and "近景或特写" in system
        assert "场景母图的24mm全景只用于空间参考" in system
        assert "默认只把本镜实际出镜人物的设定卡作为图片参考" in system
        assert "由用户在画布手动连线" in system
        shots = [candidate(project, "s1", shot_size="全景", camera_angle="平视"),
                 candidate(project, "s2", shot_size="特写", camera_angle="俯拍", image_prompt=closeup,
                           continue_previous=lock_previous, previous_local_id="s1" if lock_previous else None,
                           story_parent_local_id="s1", story_relation="动作延续")]
        return json.dumps({"shots": shots}), {}

    monkeypatch.setattr(modules.analysis, "text_request", request)
    job = modules.store.add_job(project["id"], {"kind": "text", "chapter_id": project["chapters"][0]["id"],
        "model": "gpt-6-luna", "style": "写实", "density": density})
    await modules.analysis.analyse(modules.store, job["id"])
    assert modules.store.job(job["id"])["status"] == "succeeded"
    saved = modules.store.get(project["id"])
    first, second = [shot for shot in saved["shots"] if shot.get("analysis_id") == job["id"]]
    assert [first["shot_size"], second["shot_size"]] == ["全景", "特写"]
    assert second["camera_angle"] == "俯拍" and second["image_prompt"] == closeup
    assert second["participants"] == []
    assert second["story_parent_id"] == first["id"]
    assert second["previous_shot_id"] == (first["id"] if lock_previous else None)
