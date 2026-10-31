import sys
from types import SimpleNamespace

import pytest
from aiohttp import web

from test_storyboard import modules as modules, project as project


@pytest.mark.parametrize("scene_id", ["missing-id", "林夏"])
async def test_unknown_scene_is_preserved_and_requires_confirmation(modules, project, monkeypatch, aiohttp_client, scene_id):
    chapter = project["chapters"][0]
    refs = project["shots"][0]["source_refs"]
    draft = {"shots": [{"local_id": "s1", "summary": "镜头", "source_refs": refs, "scene_id": scene_id,
                         "participants": [], "image_prompt": "客栈中景"}]}
    modules.store.change(project["id"], lambda p: modules.analysis.merge_shots(p, chapter["id"], draft,
                                                                              {refs[0]["paragraph_id"]}, "analysis"))
    current = modules.store.get(project["id"])
    shot = current["shots"][-1]
    assert shot["unresolved_scene_id"] == scene_id
    assert shot["scene"] is None
    native = sys.modules[modules.node.__name__ + ".storyboard.native"]
    recipe = native.native_canvas(modules.store, current, chapter["id"], "analysis")
    assert next(item for item in recipe["nodes"] if item["id"] == shot["id"])["properties"]["astrogate_unresolved_scene"]
    body = {"owner_id": shot["id"], "owner_type": "shot", "prompt": "客栈中景", "settings": {}}
    with pytest.raises(ValueError, match="场景引用待确认"):
        native.prepare_native(modules.store, current, body)
    with pytest.raises(ValueError, match="场景引用待确认"):
        modules.graph.plan(modules.store, current, [shot["id"]])

    service = sys.modules[modules.node.__name__ + ".storyboard.service"]
    routes = web.RouteTableDef()
    monkeypatch.setattr(service, "get_store", lambda: modules.store)
    monkeypatch.setattr(service, "PromptServer", SimpleNamespace(instance=SimpleNamespace(routes=routes,
        user_manager=SimpleNamespace(get_request_user_id=lambda request: "default"))))
    service.register_routes()
    app = web.Application()
    app.add_routes(routes)
    client = await aiohttp_client(app)
    path = f"/astrogate/storyboard/projects/{project['id']}/edit"
    invalid = await client.post(path, json={"action": "resolve_scene", "shot_id": shot["id"], "scene_id": "林夏"})
    assert invalid.status == 400
    response = await client.post(path, json={"action": "resolve_scene", "shot_id": shot["id"], "scene_id": "客栈"})
    assert response.status == 200
    saved = await response.json()
    updated = next(item for item in saved["shots"] if item["id"] == shot["id"])
    assert "unresolved_scene_id" not in updated
    assert updated["scene"]["asset_id"] == "客栈"
    assert saved["shots"][0] == current["shots"][0]
    job = native.prepare_native(modules.store, saved, body)
    assert job["owner_id"] == shot["id"]


async def test_unknown_character_and_speaker_keep_draft_for_review(modules, project, monkeypatch, aiohttp_client):
    chapter = project["chapters"][0]
    refs = project["shots"][0]["source_refs"]
    person = {"character_id": "wrong-id", "look_id": None, "visible": True, "action": "抬头"}
    dialogue = {"local_id": "d1", "character_id": "wrong-id", "type": "dialogue", "text": "你好",
                "source": "adapted", "source_refs": refs}
    draft = {"dialogues": [dialogue], "shots": [{"local_id": "s1", "summary": "镜头", "source_refs": refs,
             "participants": [person], "image_prompt": "林夏抬头", "dialogue_refs": [{"dialogue_id": "d1", "start": 0,
             "end": 2, "mode": "start", "show_subtitle": True}]}]}
    modules.store.change(project["id"], lambda p: modules.analysis.merge_shots(p, chapter["id"], draft,
                                                                              {refs[0]["paragraph_id"]}, "analysis"))
    saved = modules.store.get(project["id"])
    shot = saved["shots"][-1]
    assert shot["participants"] == []
    assert shot["unresolved_participants"] == [person]
    assert saved["dialogues"][-1]["character_id"] is None
    assert saved["dialogues"][-1]["unresolved_character_id"] == "wrong-id"
    native = sys.modules[modules.node.__name__ + ".storyboard.native"]
    recipe = native.native_canvas(modules.store, saved, chapter["id"], "analysis")
    card = next(item for item in recipe["nodes"] if item["id"] == shot["id"])
    assert card["properties"]["astrogate_unresolved_people"]
    planned = modules.graph.plan(modules.store, saved, [shot["id"]], allow_missing=True)
    assert planned["selected"] == [shot["id"]]
    text_only = native.prepare_native(modules.store, saved, {"owner_id": shot["id"], "owner_type": "shot", "prompt": "林夏抬头", "settings": {}})
    assert text_only["references"] == []
    assert text_only["snapshot"]["shot"]["unresolved_participants"] == [person]
    modules.store.update_job(text_only["id"], status="cancelled")

    service = sys.modules[modules.node.__name__ + ".storyboard.service"]
    routes = web.RouteTableDef()
    monkeypatch.setattr(service, "get_store", lambda: modules.store)
    monkeypatch.setattr(service, "PromptServer", SimpleNamespace(instance=SimpleNamespace(routes=routes,
        user_manager=SimpleNamespace(get_request_user_id=lambda request: "default"))))
    service.register_routes()
    app = web.Application()
    app.add_routes(routes)
    client = await aiohttp_client(app)
    path = f"/astrogate/storyboard/projects/{project['id']}/edit"
    invalid = await client.post(path, json={"action": "resolve_participant", "shot_id": shot["id"], "character_id": "wrong-id"})
    assert invalid.status == 400
    response = await client.post(path, json={"action": "resolve_participant", "shot_id": shot["id"], "character_id": "林夏"})
    assert response.status == 200
    updated = next(item for item in (await response.json())["shots"] if item["id"] == shot["id"])
    assert "unresolved_participants" not in updated
    assert updated["participants"][0]["identity"]["asset_id"] == "林夏"
    assert native.prepare_native(modules.store, modules.store.get(project["id"]),
                                 {"owner_id": shot["id"], "owner_type": "shot", "prompt": "林夏抬头", "settings": {}})["owner_id"] == shot["id"]


async def test_user_can_confirm_scene_text_only(modules, project, monkeypatch, aiohttp_client):
    modules.store.change(project["id"], lambda p: p["shots"][0].update(scene=None, unresolved_scene_id="missing"))
    service = sys.modules[modules.node.__name__ + ".storyboard.service"]
    routes = web.RouteTableDef()
    monkeypatch.setattr(service, "get_store", lambda: modules.store)
    monkeypatch.setattr(service, "PromptServer", SimpleNamespace(instance=SimpleNamespace(routes=routes,
        user_manager=SimpleNamespace(get_request_user_id=lambda request: "default"))))
    service.register_routes()
    app = web.Application()
    app.add_routes(routes)
    client = await aiohttp_client(app)
    response = await client.post(f"/astrogate/storyboard/projects/{project['id']}/edit",
                                 json={"action": "resolve_scene", "shot_id": "shot1", "scene_id": None})
    assert response.status == 200
    shot = (await response.json())["shots"][0]
    assert shot["scene"] is None and "unresolved_scene_id" not in shot


async def test_unknown_source_and_invalid_dialogue_ranges_require_review(modules, project, monkeypatch, aiohttp_client):
    chapter = project["chapters"][0]
    paragraph = chapter["paragraphs"][1]
    section = [{"id": paragraph["id"], "text": paragraph["text"]}]
    dialogue = {"local_id": "d", "character_id": "周野", "type": "dialogue", "text": "你好",
                "source": "adapted", "source_refs": [{"paragraph_id": paragraph["id"], "quote": None}]}
    shots = [
        {"local_id": "s1", "summary": "无定位", "source_refs": [{"paragraph_id": "invented", "quote": "无法匹配"}],
         "participants": [], "image_prompt": "客栈", "dialogue_refs": [{"dialogue_id": "d", "start": 0, "end": 99, "mode": "start", "show_subtitle": True}]},
        {"local_id": "s2", "summary": "唯一引文", "source_refs": [{"paragraph_id": "invented", "quote": "两人在客栈相遇"}],
         "participants": [], "image_prompt": "两人相遇"},
    ]
    modules.store.change(project["id"], lambda p: modules.analysis.merge_shots(p, chapter["id"], {"dialogues": [dialogue], "shots": shots},
                                                                              {paragraph["id"]}, "analysis", section))
    saved = modules.store.get(project["id"])
    first, second = saved["shots"][-2:]
    assert first["source_refs"] == [] and first["source_refs_needs_review"]
    assert first["unresolved_source_refs"][0]["paragraph_id"] == "invented"
    assert first["dialogue_links"][0]["end"] == 99 and first["dialogue_links"][0]["needs_review"]
    assert second["source_refs"][0]["paragraph_id"] == paragraph["id"]
    assert not second.get("source_refs_needs_review")
    native = sys.modules[modules.node.__name__ + ".storyboard.native"]
    with pytest.raises(ValueError, match="原文定位待确认"):
        native.prepare_native(modules.store, saved, {"owner_id": first["id"], "owner_type": "shot", "prompt": "客栈", "settings": {}})

    service = sys.modules[modules.node.__name__ + ".storyboard.service"]
    routes = web.RouteTableDef()
    monkeypatch.setattr(service, "get_store", lambda: modules.store)
    monkeypatch.setattr(service, "PromptServer", SimpleNamespace(instance=SimpleNamespace(routes=routes,
        user_manager=SimpleNamespace(get_request_user_id=lambda request: "default"))))
    service.register_routes()
    app = web.Application()
    app.add_routes(routes)
    client = await aiohttp_client(app)
    response = await client.post(f"/astrogate/storyboard/projects/{project['id']}/edit", json={"action": "resolve_sources",
                                 "shot_id": first["id"], "paragraph_id": paragraph["id"]})
    assert response.status == 200
    result = next(item for item in (await response.json())["shots"] if item["id"] == first["id"])
    assert result["source_refs"] == [{"paragraph_id": paragraph["id"], "quote": None}]
    assert not result["source_refs_needs_review"] and result["unresolved_source_refs"]


def test_asset_source_is_recovered_only_from_unique_exact_quote(modules, project):
    paragraph = project["chapters"][0]["paragraphs"][1]
    section = [{"id": paragraph["id"], "text": paragraph["text"]}]
    candidate = {"local_id": "new", "kind": "scene", "name": "新场景", "description": "客栈",
                 "source_refs": [{"paragraph_id": "invented", "quote": "两人在客栈相遇"}]}
    modules.analysis.merge_assets(project, {"assets": [candidate]}, {paragraph["id"]}, section)
    assert project["assets"][-1]["source_refs"][0]["paragraph_id"] == paragraph["id"]

    candidate = {"local_id": "another", "kind": "scene", "name": "其他场景", "description": "客栈",
                 "source_refs": [{"paragraph_id": "invented", "quote": "两人在客栈相遇"}]}
    ambiguous = section + [{"id": "other", "text": paragraph["text"]}]
    with pytest.raises(ValueError, match="不在本次正文"):
        modules.analysis.merge_assets(project, {"assets": [candidate]}, {paragraph["id"], "other"}, ambiguous)
