import copy

import pytest

from test_storyboard import modules as modules, project as project


def setup_dialogue(project):
    paragraph = project["chapters"][0]["paragraphs"][1]
    refs = [{"paragraph_id": paragraph["id"], "quote": None}]
    dialogue = {"local_id": "d1", "character_id": "周野", "type": "dialogue", "text": "两人在客栈相遇",
                "source": "original", "source_refs": refs}
    shot = {"local_id": "s1", "summary": "反应", "source_refs": refs, "participants": [],
            "image_prompt": "林夏的反应", "dialogue_refs": []}
    return paragraph, dialogue, shot


def link(ref, start, end, mode):
    return {"dialogue_id": ref, "start": start, "end": end, "mode": mode, "show_subtitle": True}


def test_one_line_across_shots_and_chunks_without_image_dependency(modules, project):
    paragraph, dialogue, shot = setup_dialogue(project)
    first = {**shot, "dialogue_refs": [link("d1", 0, 3, "start")]}
    second = {**shot, "local_id": "s2", "dialogue_refs": [link("d1", 3, 7, "finish")]}
    chapter = project["chapters"][0]["id"]
    modules.analysis.merge_shots(project, chapter, {"dialogues": [dialogue], "shots": [first, second]}, {paragraph["id"]}, "a")
    saved = project["dialogues"][0]
    assert len(project["dialogues"]) == 1
    assert [s["dialogue_links"][0]["dialogue_id"] for s in project["shots"][-2:]] == [saved["id"]] * 2
    third = {**shot, "local_id": "s3", "dialogue_refs": [link(saved["id"], 3, 7, "finish")]}
    modules.analysis.merge_shots(project, chapter, {"dialogues": [], "shots": [third]}, {paragraph["id"]}, "a")
    assert len(project["dialogues"]) == 1
    assert project["shots"][-1]["dialogue_links"][0]["dialogue_id"] == saved["id"]
    assert project["shots"][-1]["participants"] == []
    assert project["shots"][-1]["previous_shot_id"] is None


@pytest.mark.parametrize("change,error", [
    ({"source": "original", "text": "不存在的台词"}, "原文台词"),
    ({"type": "unknown"}, "台词类型"),
])
def test_invalid_ai_dialogue_is_rejected(modules, project, change, error):
    paragraph, dialogue, shot = setup_dialogue(project)
    dialogue.update(change)
    with pytest.raises(ValueError, match=error):
        modules.analysis.merge_shots(project, project["chapters"][0]["id"], {"dialogues": [dialogue], "shots": [shot]}, {paragraph["id"]}, "a")


def test_dialogue_repairs_wrong_paragraph_id_only_with_unique_exact_text(modules, project):
    paragraph, dialogue, shot = setup_dialogue(project)
    dialogue["source_refs"] = [{"paragraph_id": "invented", "quote": "两人在客栈相遇"}]
    modules.analysis.merge_shots(project, project["chapters"][0]["id"],
                                 {"dialogues": [dialogue], "shots": [shot]}, {paragraph["id"]}, "a", [paragraph])
    assert project["dialogues"][-1]["source_refs"] == [{"paragraph_id": paragraph["id"], "quote": "两人在客栈相遇"}]

    dialogue["local_id"] = "d2"
    dialogue["text"] = "原文也没有这句"
    dialogue["source_refs"] = [{"paragraph_id": "invented", "quote": "原文没有这句"}]
    with pytest.raises(ValueError, match="不在本次正文"):
        modules.analysis.merge_shots(project, project["chapters"][0]["id"],
                                     {"dialogues": [dialogue], "shots": [shot]}, {paragraph["id"]}, "a", [paragraph])


def test_codepoint_offsets_and_old_drafts(modules, project):
    paragraph, dialogue, shot = setup_dialogue(project)
    dialogue.update(text="你好😀！", source="adapted")
    shot["dialogue_refs"] = [link("d1", 2, 3, "start")]
    chapter = project["chapters"][0]["id"]
    modules.analysis.merge_shots(project, chapter, {"dialogues": [dialogue], "shots": [shot]}, {paragraph["id"]}, "a")
    assert project["dialogues"][-1]["text"][2:3] == "😀"
    assert project["shots"][-1]["dialogue_links"][0]["end"] == 3
    old = copy.deepcopy(shot)
    old.pop("dialogue_refs")
    old["dialogue"] = ["旧字段"]
    modules.analysis.merge_shots(project, chapter, {"shots": [old]}, {paragraph["id"]}, "b")
    assert project["shots"][-1]["dialogue"] == ["旧字段"]
    assert project["shots"][-1]["dialogue_links"] == []


def test_duplicate_in_later_analysis_is_preserved_and_marked_for_review(modules, project):
    paragraph, dialogue, shot = setup_dialogue(project)
    shot["dialogue_refs"] = [link("d1", 0, 3, "start")]
    chapter = project["chapters"][0]["id"]
    draft = {"dialogues": [dialogue], "shots": [shot]}
    modules.analysis.merge_shots(project, chapter, draft, {paragraph["id"]}, "a")
    existing = project["dialogues"][-1]["id"]
    modules.analysis.merge_shots(project, chapter, draft, {paragraph["id"]}, "b")
    assert project["dialogues"][-1]["possible_matches"] == [existing]
    assert project["shots"][-1]["dialogue_links"][0]["needs_review"]


def test_original_line_cannot_be_taken_from_another_chunk_of_same_paragraph(modules, project):
    paragraph, dialogue, shot = setup_dialogue(project)
    with pytest.raises(ValueError, match="原文台词"):
        modules.analysis.merge_shots(project, project["chapters"][0]["id"], {"dialogues": [dialogue], "shots": [shot]},
                                     {paragraph["id"]}, "a", [{"id": paragraph["id"], "text": "这一块没有那句台词"}])
