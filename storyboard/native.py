import copy
import secrets

from .analysis import analyse
from .graph import CHARACTER_SHEET_PROMPT, SCENE_MASTER_PROMPT, SCENE_STATE_PROMPT, render_prompt, snapshot
from .store import aspect_ratio, asset_by_id, binding, digest, invalidate, shot_bindings, version
from .video import shot_video_prompt


PREVIOUS_SCENE_MASTER_PROMPT = "无人环境素材：生成一张16:9连续的场景全景母图，24mm等效广角、平视机位，中远距离全景，在同一画面尽可能完整交代房间或场地的空间。室内清楚展示墙面、地面、顶面、门窗、通道及主要陈设的相对位置；室外对应展示地形、建筑、道路、植被及主要空间边界。结构与透视合理，画面边缘无明显拉伸，保留设定中的材质、布局、主要光源方向、色温和投影关系，以真实光照呈现，明确光照强度，不添加不合理的多方向光源，不加说明标记。画面中禁止出现任何人物、路人、人体、剪影或人物倒影；参考图中即使有人也必须移除。不绘制剧情事件，只用一个连续机位，不拼贴、不分屏、不做多角度拼图，无文字、标签或水印。"
SCENE_ONLY_PROMPT = "无人环境素材：只绘制建筑、空间、陈设与光照，不出现任何人物、路人、剪影或人物倒影。"


async def analyse_native(store, project_id, chapter_number, model, style, density, revision):
    project = store.get(project_id)
    if not 1 <= chapter_number <= len(project["chapters"]):
        raise ValueError(f"本剧本共 {len(project['chapters'])} 章，请修改章节序号。")
    chapter = project["chapters"][chapter_number - 1]
    fields = [project["source"]["hash"], chapter["id"], model, style, density, revision]
    include_props = project["settings"].get("include_props", False)
    key = digest(fields + ([True] if include_props else []))
    job = next((j for j in store.jobs(project_id) if j.get("analysis_key") == key), None)
    if job and job["status"] != "succeeded":
        reason = job.get("error") or "本次分析尚未完成。"
        raise ValueError(f"{reason}\n请在分镜节点查看状态，失败后可点击重试继续分析。")
    if job is None:
        def settings(p):
            p["settings"].update(text_model=model, style=style, density=density)
        store.change(project_id, settings)
        job = store.add_job(project_id, {"kind": "text", "chapter_id": chapter["id"], "model": model,
                                        "style": style, "density": density, "analysis_key": key, "include_props": include_props, "chunk_limit": 700, "sentence_chunks": True})
        await analyse(store, job["id"])
        job = store.job(job["id"])
    if job["status"] != "succeeded":
        raise RuntimeError(job.get("error", "分镜分析未完成。"))
    return {"project_id": project_id, "chapter_id": chapter["id"], "analysis_id": job["id"]}


def asset_shot(project, asset):
    parent_version = version(asset).get("parent_version_id")
    return {"id": asset["id"], "summary": asset["name"], "image_prompt": version(asset)["description"],
            "asset_description": version(asset)["description"], "asset_kind": asset["kind"], "participants": [],
            "props": [binding(project, asset["parent_id"], "locked" if parent_version else "follow_latest", parent_version)] if asset.get("parent_id") else [], "source_refs": asset.get("source_refs", [])}


def local_asset_job(store, project, asset, image, view=None, sheet_look=None):
    if view is None:
        view = "front" if asset["kind"] == "character" else ""
    existing = next((j for j in store.jobs(project["id"]) if j.get("owner_id") == asset["id"] and j["status"] == "succeeded"
                     and j.get("asset_version_id") == asset["current_version_id"]
                     and j.get("asset_view", "front" if asset["kind"] == "character" else "") == view
                     and j.get("snapshot", {}).get("shot", {}).get("sheet_look") == sheet_look
                     and j.get("result_image", {}).get("hash") == image["hash"]), None)
    if existing:
        return existing
    shot = asset_shot(project, asset)
    if view:
        shot["asset_view"] = view
    if sheet_look:
        shot["sheet_look"] = copy.deepcopy(sheet_look)
    snap = snapshot(project, shot)
    folder, filename = image["file"].rsplit("/", 1)
    return store.add_job(project["id"], {"kind": "image", "native": True, "status": "succeeded", "owner_type": "asset",
                        "asset_version_id": asset["current_version_id"], "asset_view": view,
                        "owner_id": asset["id"], "prompt": shot["image_prompt"], "snapshot": snap, "settings": snap["settings"],
                        "references": [], "result_image": image, "saved_result": {"filename": filename, "subfolder": folder, "type": "input"}})


def native_canvas(store, project, chapter_id, analysis_id=None):
    shots = [s for s in project["shots"] if s["chapter_id"] == chapter_id and (not analysis_id or s.get("analysis_id") == analysis_id)]
    needed = {s["id"] for s in shots}
    assets = {}
    parents = {}
    characters, appearances, character_aliases = {}, {}, {}
    include_props = project["settings"].get("include_props", False)

    for shot in shots:
        for person in shot.get("participants", []):
            if person.get("visible") is not True:
                continue
            identity = person["identity"]
            character = asset_by_id(project, identity["asset_id"])
            if character["id"] not in characters:
                selected = version(character, identity["version_id"] if identity["policy"] == "locked" else None)
                key = character["id"] if selected["id"] == character["current_version_id"] else f"{character['id']}@{selected['id']}"
                characters[character["id"]] = key
                assets[key] = {**character, "current_version_id": selected["id"]}
            key = characters[character["id"]]
            if person.get("look"):
                item = person["look"]
                look = asset_by_id(project, item["asset_id"])
                selected = version(look, item["version_id"] if item["policy"] == "locked" else None)
                appearances.setdefault(key, {"asset_id": look["id"], "version_id": selected["id"]})
                alias = look["id"] if selected["id"] == look["current_version_id"] else f"{look['id']}@{selected['id']}"
                character_aliases[alias] = key
                character_aliases[look["id"]] = key

    def include_asset(asset_id, version_id=None):
        original = asset_by_id(project, asset_id)
        if original["kind"] == "character":
            return characters.get(original["id"])
        if original["kind"] == "look":
            return characters.get(original["parent_id"])
        if original["kind"] == "prop" and not include_props:
            return None
        selected = version(original, version_id)
        key = asset_id if selected["id"] == original["current_version_id"] else f"{asset_id}@{selected['id']}"
        if key not in assets:
            if original.get("parent_id"):
                parent = include_asset(original["parent_id"], selected.get("parent_version_id"))
                if parent:
                    parents[key] = parent
            assets[key] = {**original, "current_version_id": selected["id"]}
        return key
    for shot in shots:
        for item in shot_bindings(shot):
            include_asset(item["asset_id"], item["version_id"] if item["policy"] == "locked" else None)
    jobs = store.jobs(project["id"])
    nodes, links = [], []
    colors = {"character": "#55436d", "look": "#55436d", "scene": "#385448", "scene_state": "#385448", "prop": "#635035", "shot": "#294d63"}

    def add(owner, kind, prompt, position, job=None, key=None, view=""):
        key = key or owner["id"]
        default_prompt = prompt
        if job and job["status"] == "succeeded":
            prompt = job["snapshot"]["shot"]["image_prompt"]
        props = {"astrogate_project": project["id"], "astrogate_owner": owner["id"], "astrogate_owner_type": "shot" if kind == "shot" else "asset",
                 "astrogate_kind": kind, "astrogate_key": key, "astrogate_base_prompt": prompt, "astrogate_prompt_folded": True,
                 "astrogate_view": view, "astrogate_layout_version": 5,
                 "astrogate_images": [job["saved_result"]] if job and job.get("saved_result") else []}
        if kind == "shot":
            props["astrogate_atmosphere"] = owner.get("atmosphere", "")
            props["astrogate_video_prompt"] = shot_video_prompt(project, owner)
            props["astrogate_unresolved_scene"] = bool(owner.get("unresolved_scene_id"))
            props["astrogate_unresolved_source"] = bool(owner.get("source_refs_needs_review"))
            props["astrogate_unresolved_people"] = bool(owner.get("unresolved_participants"))
        parent = asset_by_id(project, owner.get("parent_id"))
        purpose = {"character": "仅锁定这个人物的身份、脸型与体貌；服装发型配饰以本镜文字设定为准，不自动套用设定卡的基准造型", "look": "此人物的身份、服装、发型和配饰",
                   "scene": "场景空间布局，不自动带入参考图中的人物", "scene_state": "场景时段、天气与光线",
                   "prop": "道具外形与材质", "shot": "前镜空间与光线，人物名单以本镜为准"}[kind]
        props["astrogate_reference_purpose"] = f"{parent['name'] + ' / ' if parent else ''}{owner.get('name', owner.get('summary', ''))}：{purpose}"
        if kind != "shot":
            props["astrogate_version"] = owner["current_version_id"]
            props["astrogate_locked"] = "@" in key
        if kind == "character" and key in appearances:
            props["astrogate_look_id"] = appearances[key]["asset_id"]
            props["astrogate_look_version"] = appearances[key]["version_id"]
        if view:
            props["astrogate_reference_purpose"] += " · 同一人物的半身与正侧背全身设定图册，各视图不是不同人物"
        nodes.append({"id": key, "type": "AstroGateStoryboardFrame", "title": owner.get("summary", owner.get("name", "图片")),
                      "pos": position, "color": colors[kind], "properties": props,
                      "widgets": {"prompt": prompt, "job_id": job["id"] if job else "", "element_id": owner["id"],
                                  "aspect_ratio": project["settings"]["aspect_ratio"] if kind == "shot" else "16:9", "resolution": "4K", "quality": "high"}})
        if kind == "scene_state" and job:
            previous = version(owner)["description"] + "\n" + PREVIOUS_SCENE_MASTER_PROMPT
            if prompt in (previous, previous + "\n" + SCENE_ONLY_PROMPT):
                nodes[-1]["default_prompt"] = default_prompt
        if parent:
            nodes[-1]["title"] = f"{parent['name']} / {owner['name']}"
        links.append({"from": "$board", "slot": 0, "to": key, "input": "board", "type": "ASTROGATE_BOARD", "label": "所属分镜脚本"})

    positions = {}
    def place_asset(key, depth, row):
        positions[key] = [520 + depth * 480, 80 + row * 620]
        children = [child for child in assets if parents.get(child) == key]
        if not children:
            return row + 1
        for child in children:
            row = place_asset(child, depth + 1, row)
        return row

    row = 0
    for root in [key for key in assets if key not in parents]:
        row = place_asset(root, 0, row)
    shot_column = max(1560, max((pos[0] + 560 for pos in positions.values()), default=1560))
    for key, asset in assets.items():
        parent = parents.get(key)
        selected = version(asset)
        view = "sheet" if asset["kind"] == "character" else ""
        sheet_look = appearances.get(key)
        if view:
            image = selected.get("view_images", {}).get("sheet") if selected.get("sheet_look") == sheet_look else None
        else:
            image = selected.get("image")
        job = local_asset_job(store, project, asset, image, view, sheet_look) if image else next(
            (j for j in jobs if j.get("owner_id") == asset["id"] and j["status"] == "succeeded" and
             j.get("asset_view", "") == view and
             j.get("snapshot", {}).get("shot", {}).get("sheet_look") == sheet_look and
             j.get("asset_version_id") == selected["id"]), None)
        prompt = selected["description"]
        if view:
            prompt = f"{asset['name']}的人物设定图册。身份与体貌：{prompt}"
            if sheet_look:
                look = asset_by_id(project, sheet_look["asset_id"])
                prompt += f"\n本卡基准造型：{look['name']}，{version(look, sheet_look['version_id'])['description']}"
            prompt += "\n" + CHARACTER_SHEET_PROMPT
        elif asset["kind"] in ("scene", "scene_state"):
            prompt += "\n" + (SCENE_STATE_PROMPT if asset["kind"] == "scene_state" else SCENE_MASTER_PROMPT) + "\n" + SCENE_ONLY_PROMPT
        add(asset, asset["kind"], prompt, positions[key], job, key, view)
        if key != asset["id"]:
            nodes[-1]["title"] += " · 固定版本"
        if parent:
            links.append({"from": parent, "slot": 0, "to": key, "input": "reference_images.reference_1", "type": "IMAGE", "label": "身份/场景基准"})

    for index, shot in enumerate(shots):
        scene_id = (shot.get("scene") or {}).get("asset_id", "none")
        job = next((j for j in jobs if j["id"] == shot.get("latest_job_id")), None)
        add(shot, "shot", shot["image_prompt"], [shot_column, 80 + index * 720], job)
        refs = []
        seen = set()
        for person in shot.get("participants", []):
            if person.get("visible") is not True:
                continue
            key = characters[asset_by_id(project, person["identity"]["asset_id"])["id"]]
            if key and key not in seen:
                refs.append(key)
                seen.add(key)
        for ref_index, source in enumerate(refs):
            links.append({"from": source, "slot": 0, "to": shot["id"], "input": f"reference_images.reference_{ref_index + 1}", "type": "IMAGE", "label": "图像参考"})
        story_parent = shot.get("story_parent_id")
        if story_parent in needed:
            links.append({"from": story_parent, "slot": 1, "to": shot["id"], "input": "story_context", "type": "ASTROGATE_STORY", "label": shot.get("story_relation", "剧情承接")})
        elif index:
            links.append({"from": shots[index - 1]["id"], "slot": 1, "to": shot["id"], "input": "story_context", "type": "ASTROGATE_STORY", "label": "叙事顺序"})
        nodes[-1]["title"] = " · ".join(str(part) for part in (f"{index + 1:03d}", shot.get("shot_size"), shot["summary"]) if part)
        nodes[-1]["properties"]["astrogate_scene"] = asset_by_id(project, scene_id)["name"] if scene_id != "none" else "空镜 / 未指定场景"
    return {"project_id": project["id"], "chapter_id": chapter_id, "layout_version": 5, "reference_wiring_version": 4,
            "character_aliases": character_aliases, "nodes": nodes, "links": links}


def prepare_native(store, project, body):
    owner_id = body["owner_id"]
    kind = body["owner_type"]
    if kind == "shot":
        shot = next((s for s in project["shots"] if s["id"] == owner_id), None)
        if shot is None:
            raise ValueError("分镜已不存在。")
        if shot.get("unresolved_scene_id"):
            raise ValueError("本镜场景引用待确认，请先在分镜卡选择场景或确认按文字生成。")
        if shot.get("source_refs_needs_review"):
            raise ValueError("本镜原文定位待确认，请先在分镜卡选择对应段落。")
        shot = copy.deepcopy(shot)
    elif kind == "asset":
        asset = asset_by_id(project, owner_id)
        if asset is None:
            raise ValueError("素材已不存在。")
        asset = {**asset, "current_version_id": body.get("version_id") or asset["current_version_id"]}
        shot = asset_shot(project, asset)
    else:
        raise ValueError("节点归属类型无效。")
    view = body.get("view", "front" if kind == "asset" and asset["kind"] == "character" else "")
    views = ("sheet", "front", "left") if kind == "asset" and asset["kind"] == "character" else ("",)
    if kind == "asset" and asset["kind"] == "look":
        views = ("", "sheet")
    if kind == "asset" and asset["kind"] in ("scene", "scene_state"):
        views = ("", "scene_left", "scene_right", "scene_reverse", "scene_high")
    if view not in views:
        raise ValueError("素材视图无效。")
    if view:
        shot["asset_view"] = view
    sheet_look = None
    if kind == "asset" and asset["kind"] == "character" and view == "sheet" and body.get("look_id"):
        look = asset_by_id(project, body["look_id"])
        if look is None or look["kind"] != "look" or look.get("parent_id") != asset["id"]:
            raise ValueError("设定卡的基准造型必须属于这个人物。")
        selected_look = version(look, body.get("look_version_id"))
        sheet_look = {"asset_id": look["id"], "version_id": selected_look["id"]}
        shot["sheet_look"] = sheet_look
    shot["image_prompt"] = body["prompt"]
    if kind == "shot":
        atmosphere = body.get("atmosphere", shot.get("atmosphere", ""))
        if not isinstance(atmosphere, str):
            raise ValueError("特效与氛围须为文字。")
        shot["atmosphere"] = atmosphere.strip()
    if kind == "asset" and not view:
        shot["asset_description"] = body["prompt"]
    settings = {**project["settings"], **body["settings"]}
    if kind == "asset" and (view == "sheet" or asset["kind"] in ("scene", "scene_state")) and "aspect_ratio" not in body["settings"]:
        settings["aspect_ratio"] = "16:9"
    settings["aspect_ratio"] = aspect_ratio(settings["aspect_ratio"])
    if settings["resolution"] not in ("original", "1K", "2K", "3K", "4K") or settings["quality"] not in ("low", "medium", "high"):
        raise ValueError("图片参数无效。")
    refs = []
    for item in body.get("references", []):
        ref = {"purpose": str(item.get("purpose") or "参考构图与细节"), "slot": item["slot"]}
        role = item.get("role") or "auto"
        if role not in ("auto", "character", "look", "scene", "prop", "style", "effect", "previous"):
            raise ValueError("参考图用途无效。")
        if role != "auto":
            ref["role"] = role
        if item.get("source_signature"):
            ref["source_signature"] = str(item["source_signature"])
        if item.get("job_id"):
            source = store.job(item["job_id"])
            if source["project_id"] != project["id"]:
                raise ValueError("参考任务不属于本项目。")
            ref["job_id"] = source["id"]
            ref["asset_id"] = source["owner_id"]
            source_asset = asset_by_id(project, source["owner_id"]) if source["owner_type"] == "asset" else None
            same_role = source_asset and (role in ("auto", source_asset["kind"]) or role == "scene" and source_asset["kind"] == "scene_state")
            if same_role:
                ref["asset_kind"] = source_asset["kind"]
                if source_asset["kind"] in ("character", "look"):
                    ref["character_id"] = source_asset["parent_id"] if source_asset["kind"] == "look" else source_asset["id"]
            if same_role and source.get("asset_view"):
                ref.update(asset_view=source["asset_view"], version_id=source["asset_version_id"])
        refs.append(ref)
    story_id = body.get("story_parent_id")
    story = next((s for s in project["shots"] if s["id"] == story_id), None)
    if story_id and (story is None or story_id == owner_id):
        raise ValueError("剧情关联必须指向本项目的另一镜头。")
    snap = snapshot(project, shot)
    if sheet_look:
        snap["descriptions"].append(f"本卡基准造型：{look['name']}，{selected_look['description']}。各视图使用这同一造型。")
    snap.update(settings=settings, story_parent_id=story_id, story_summary=story["summary"] if story else "")
    if story:
        snap["descriptions"].append(f"前情（只延续叙事，不自动画入前镜人物）：{story['summary']}")
    fingerprint_values = [shot["image_prompt"], settings, refs, story_id, snap["story_summary"], snap["descriptions"], snap["people"], view, shot.get("asset_kind"), shot.get("atmosphere", "")]
    if sheet_look:
        fingerprint_values.append(sheet_look)
    if kind == "shot":
        fingerprint_values.append([shot.get(key, "") for key in ("shot_size", "camera_angle", "frozen_moment")])
    fingerprint = digest(fingerprint_values)
    asset_version = asset["current_version_id"] if kind == "asset" else None
    jobs = [j for j in store.jobs(project["id"]) if j.get("owner_id") == owner_id and j.get("native") and j.get("asset_version_id") == asset_version
            and j.get("asset_view", "front" if view else "") == view]
    active = next((j for j in jobs if j["status"] in ("running", "prepared", "queued", "cancelling")), None)
    if active:
        raise ValueError("该图片节点已经有任务，请等待或停止后再生成。")
    reused = next((j for j in jobs if j.get("fingerprint") == fingerprint and j["status"] == "succeeded"), None)
    if reused and not body.get("force"):
        return reused
    if not body.get("force") and jobs and jobs[0]["status"] == "unknown":
        raise ValueError("上次结果未知。点击“重绘”会明确发起新请求。")
    if kind == "shot":
        def edited(p):
            current = next(s for s in p["shots"] if s["id"] == owner_id)
            if current["image_prompt"] != body["prompt"] or current.get("atmosphere", "") != shot.get("atmosphere", ""):
                current["image_prompt"] = body["prompt"]
                current["atmosphere"] = shot["atmosphere"]
                current["revision"] += 1
                invalidate(p, [owner_id])
        store.change(project["id"], edited)
    return store.add_job(project["id"], {"kind": "image", "native": True, "owner_type": kind, "owner_id": owner_id,
                         "asset_version_id": asset_version, "asset_view": view,
                         "snapshot": snap, "settings": settings, "prompt": render_prompt(snap, refs), "fingerprint": fingerprint,
                         "references": refs, "gateway_request_id": secrets.token_hex(32)})
