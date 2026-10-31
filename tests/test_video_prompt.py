import copy

import pytest

from test_storyboard import modules as modules, project as project


def test_distinct_character_sheets_and_voice_follow_identity(modules, project):
    from astrogate_story_test.storyboard.video import video_prompt

    first, second = project["shots"][0], copy.deepcopy(project["shots"][0])
    second["id"] = "shot2"
    second["summary"] = "林夏反应"
    second["participants"] = [second["participants"][0]]
    second["video_action"] = "切到林夏面部近景，转身看向门口。"
    project["shots"].append(second)
    for name in ("林夏", "周野"):
        asset = modules.domain.asset_by_id(project, name)
        asset["versions"][0]["view_images"] = {"sheet": {"file": f"astrogate/{project['id']}/{name}.png"}}
    speaker = modules.domain.asset_by_id(project, "周野")
    speaker["voice_versions"] = [{"id": "voice1", "file": f"astrogate/{project['id']}/voices/voice.wav"}]
    speaker["current_voice_id"] = "voice1"
    project["dialogues"] = [{"id": "d1", "chapter_id": first["chapter_id"], "character_id": "周野",
                             "type": "dialogue", "source": "original", "status": "confirmed", "text": "你先走，我来挡住他们。"}]
    first["dialogue_links"] = [{"dialogue_id": "d1", "start": 0, "end": 3, "mode": "start"}]
    second["dialogue_links"] = [{"dialogue_id": "d1", "start": 4, "end": 11, "mode": "finish"}]
    result = video_prompt(project, first["chapter_id"])

    assert [item["name"] for item in result["images"]] == ["林夏", "周野"]
    assert [item["name"] for item in result["audios"]] == ["周野"]
    assert result["prompt"].count("图1 林夏 [image]") == 1
    assert "周野对白（声线音频1）：{你先走}" in result["prompt"]
    assert "周野画外对白（声线音频1）：{我来挡住他们。}" in result["prompt"]
    assert "BGM" in result["prompt"] and "字幕" in result["prompt"]
    assert "04:01" not in result["prompt"]


def test_offscreen_speaker_does_not_add_character_picture_or_unconfirmed_adaptation(modules, project):
    from astrogate_story_test.storyboard.video import video_prompt

    shot = project["shots"][0]
    shot["participants"] = []
    project["dialogues"] = [{"id": "d", "chapter_id": shot["chapter_id"], "character_id": "周野",
                             "type": "voiceover", "source": "adapted", "status": "pending", "text": "快走"}]
    shot["dialogue_links"] = [{"dialogue_id": "d", "start": 0, "end": 2, "mode": "start"}]
    result = video_prompt(project, shot["chapter_id"])

    assert not any(item["name"] == "周野" for item in result["images"])
    assert "{快走}" not in result["prompt"]
    assert result["warnings"]


def test_voice_upload_keeps_old_versions_and_rejects_other_files(modules, project):
    store = modules.store
    wav = b"RIFF" + (40).to_bytes(4, "little") + b"WAVEfmt " + b"\0" * 40
    first = store.save_voice(project["id"], wav)
    second = store.save_voice(project["id"], wav)
    character = modules.domain.asset_by_id(project, "周野")
    character["voice_versions"] = [first, second]
    character["current_voice_id"] = second["id"]

    assert first["id"] != second["id"]
    assert store.image_path(first["file"]).read_bytes() == wav
    assert len(character["voice_versions"]) == 2
    with pytest.raises(ValueError, match="声线音频请选择"):
        store.save_voice(project["id"], b"not audio")


def test_audio_only_continuation_needs_no_new_text_range(modules, project):
    from astrogate_story_test.storyboard.video import video_prompt

    shot = project["shots"][0]
    shot["participants"] = []
    project["dialogues"] = [{"id": "d", "chapter_id": shot["chapter_id"], "character_id": "周野",
                             "type": "dialogue", "source": "original", "status": "confirmed", "text": "你先走，我来挡住他们。"}]
    modules.domain.save_dialogue_links(project, shot["id"], [{"dialogue_id": "d", "start": None, "end": None,
                                                              "mode": "audio_only", "show_subtitle": False}])
    result = video_prompt(project, shot["chapter_id"])

    assert not shot["dialogue_links"][0]["needs_review"]
    assert "周野的声音从上一镜自然延续，不重新朗读整句" in result["prompt"]
    assert "{你先走" not in result["prompt"]
    assert result["warnings"] == []


def test_video_prompt_node_keeps_existing_video_node_name(modules):
    schema = modules.node.AstroGateVideoPrompt.define_schema()
    assert schema.node_id == "AstroGateVideoPrompt"
    assert schema.category == "video/AstroGate"
    assert [output.io_type for output in schema.outputs] == ["STRING", "STRING"]
    assert modules.node.AstroGateJimengImageToVideo.define_schema().node_id == "AstroGateJimengImageToVideo"


def test_video_prompt_omits_ai_music_and_subtitle_directions(modules, project):
    from astrogate_story_test.storyboard.video import video_prompt

    shot = project["shots"][0]
    shot["video_action"] = "镜头推近，并添加字幕和BGM。"
    shot["sound_cues"] = ["脚步声", "背景音乐渐强"]
    result = video_prompt(project, shot["chapter_id"])

    assert "现场声：脚步声" in result["prompt"]
    assert "背景音乐渐强" not in result["prompt"]
    assert "镜头推近，并添加字幕" not in result["prompt"]
    assert len(result["warnings"]) == 2


def test_shot_video_prompt_uses_only_this_shot_and_confirmed_dialogue(modules, project):
    from astrogate_story_test.storyboard.video import shot_video_prompt

    shot = project["shots"][0]
    shot.update(video_action="周野抬头看向林夏，摄影机轻微推近", shot_size="近景", camera_angle="平视",
                sound_cues=["脚步声", "背景音乐渐强"])
    project["dialogues"] = [{"id": "d", "character_id": "周野", "type": "dialogue", "source": "original",
                             "status": "confirmed", "text": "你先走，我来挡住他们。"}]
    shot["dialogue_links"] = [{"dialogue_id": "d", "start": 0, "end": 3, "mode": "start"}]
    prompt = shot_video_prompt(project, shot)

    assert "近景，平视" in prompt
    assert "周野对白：{你先走}" in prompt
    assert "我来挡住他们" not in prompt
    assert "脚步声" in prompt
    assert "背景音乐渐强" not in prompt
    assert "严禁 BGM" in prompt


def test_minimax_h3_expansion_uses_existing_native_nodes_and_connected_first_frame(modules):
    schema = modules.node.AstroGateMiniMaxH3Video.define_schema()
    assert schema.node_id == "AstroGateMiniMaxH3Video"
    assert schema.category == "video/AstroGate"
    assert schema.outputs[0].io_type == "VIDEO"
    result = modules.node.AstroGateMiniMaxH3Video.execute(
        first_frame=["shot", 0], prompt="人物走向门口。严禁 BGM 和字幕。",
        model_name="minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        text_encoder="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        video_vae="minimax_h3_video_vae_fp16.safetensors", audio_vae="minimax_h3_audio_vae_fp32.safetensors",
        turbo_lora="none", duration=3, width=1024, height=576, steps=8, seed=7)
    graph = result.expand
    by_type = {item["class_type"]: item["inputs"] for item in graph.values()}
    assert by_type["MiniMaxH3ImageToVideo"]["first_frame"] == ["shot", 0]
    assert by_type["MiniMaxH3ImageToVideo"]["length"] == 73
    assert by_type["BasicScheduler"]["steps"] == 8
    assert by_type["CreateVideo"]["fps"] == 24
    assert by_type["CreateVideo"]["audio"][0] in graph
    assert result.result[0][0] in graph


def test_native_storyboard_frame_stores_video_prompt_without_changing_image_prompt(modules, project):
    from astrogate_story_test.storyboard.native import native_canvas

    shot = project["shots"][0]
    shot["video_action"] = "林夏转身看向门口"
    recipe = native_canvas(modules.store, project, shot["chapter_id"])
    frame = next(item for item in recipe["nodes"] if item["properties"]["astrogate_kind"] == "shot")

    assert "林夏转身看向门口" in frame["properties"]["astrogate_video_prompt"]
    assert frame["widgets"]["prompt"] == shot["image_prompt"]
