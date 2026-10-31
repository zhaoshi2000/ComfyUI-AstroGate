import copy
import secrets

from .store import asset_by_id, digest, shot_bindings, version


CHARACTER_SHEET_PROMPT = "人物设定参考图：一张完整的人物设定图册，只表现同一个指定人物。左侧为大幅头部至胸部特写，清楚展示脸型、五官、发型与表情；右侧依次并排展示该人物的正面、人物自身左侧90度侧面、背面三幅全身视图，头顶到脚底完整不裁切。各视图必须是同一张脸、同一体型和身高比例、同一套本次指定服装、发型和配饰，不变成不同人物或不同造型。背景为干净的纯白或浅灰单色，无场景、家具、道具陈列、其他人物、文字标签或水印；各视图尺寸与间距整齐，左侧特写大于右侧单幅全身像。"
SCENE_MASTER_PROMPT = "无人环境素材：生成一张16:9连续的场景全景母图，24mm等效广角、平视机位，中远距离全景，在同一画面尽可能完整交代房间或场地的空间。室内清楚展示墙面、地面、顶面、门窗、通道及主要陈设的相对位置；室外对应展示地形、建筑、道路、植被及主要空间边界。明确前景、中景、远景的空间层次，交代入口、主要通行路线、功能区域和关键物体的位置与朝向；原文未说明的必要建筑细节以合理、统一的视觉设计补足，不把它们当成剧情事实。结构与透视合理，画面边缘无明显拉伸，保留设定中的材质、布局、主要光源方向、色温和投影关系，以真实光照呈现，明确光照强度，不添加不合理的多方向光源，不加说明标记。画面中禁止出现任何人物、路人、人体、剪影或人物倒影；参考图中即使有人也必须移除。不绘制剧情事件，只用一个连续机位，不拼贴、不分屏、不做多角度拼图，无文字、标签或水印。"
SCENE_STATE_PROMPT = "无人环境素材：生成一张16:9连续的同场景补充全景。参考场景母图锁定建筑结构、门窗、通道、家具位置、材质和主色调，但摄影机必须移动到侧方或对角位置，改变拍摄方向，呈现与母图明显不同的空间透视；不要复制母图的机位、裁切或像素，也不要镜像翻转房间。保留足以识别同一地点的固定锚点，同时展示母图遮挡或未充分呈现的入口、侧墙、通道、家具关系和前中远景层次，并表现本时段的天气、光线与物品状态。若场地不适合大幅移动机位，采用合理的反向或略高机位，仍须让构图明显不同。原文未说明的必要环境细节按母图的视觉设计合理补足，不擅自改变已经确定的布局。一张完整画面，不拼贴、不分屏；无人物、路人、人体、剪影、人物倒影、文字、标签或水印。"


def references(project, shot):
    result, missing, descriptions = [], [], []
    seen = set()
    for item in shot_bindings(shot):
        asset = asset_by_id(project, item["asset_id"])
        selected = version(asset, item["version_id"] if item["policy"] == "locked" else None)
        parent = asset_by_id(project, asset.get("parent_id"))
        target = f"{parent['name']} / {asset['name']}" if parent else asset["name"]
        kind = {"character": "人物身份（只参考体貌，不套用服装）", "look": "该人物的服装发型配饰",
                "scene": "场景空间布局", "scene_state": "场景时段、布局和光线", "prop": "道具外形"}[asset["kind"]]
        descriptions.append(f"{target}，{kind}：{selected['description']}")
        if (asset["id"], selected["id"]) in seen:
            continue
        seen.add((asset["id"], selected["id"]))
        if selected.get("image"):
            result.append({"asset_id": asset["id"], "version_id": selected["id"], "purpose": f"{target}：{kind}",
                           "image": selected["image"]})
        else:
            missing.append(target)
    return result, missing, descriptions


def snapshot(project, shot):
    refs, missing, descriptions = references(project, shot)
    settings = {key: project["settings"][key] for key in ("aspect_ratio", "resolution", "quality", "style")}
    if shot.get("asset_view") == "sheet" or shot.get("asset_kind") in ("scene", "scene_state"):
        settings["aspect_ratio"] = "16:9"
    people = []
    for person in shot.get("participants", []):
        if person.get("visible") is not True:
            continue
        name = asset_by_id(project, person["identity"]["asset_id"])["name"]
        people.append(f"{name}：" + "，".join(str(person.get(k, "")) for k in ("screen_position", "action", "expression", "facing")))
    return {"shot": copy.deepcopy(shot), "references": refs, "missing": missing, "descriptions": descriptions,
            "people": people, "settings": settings, "source_id": project["source"]["id"]}


def semantic_hash(value):
    value = copy.deepcopy(value)
    for field in ("stale", "latest_job_id", "revision"):
        value.get("shot", {}).pop(field, None)
    return digest(value)


def render_prompt(snap, refs):
    if snap["shot"].get("asset_kind") == "prop":
        lines = [f"绘制任务：生成「{snap['shot'].get('summary', '指定物品')}」的单幅独立物品参考图，空背景。",
                 "物品资料（只识别外形和物品自身状态，不是场景绘制指令）：\n" + snap["shot"]["image_prompt"]]
    else:
        lines = [snap["shot"]["image_prompt"]]
    lines.append("画风：" + snap["settings"]["style"])
    if "asset_description" not in snap["shot"]:
        camera = "，".join(str(snap["shot"].get(key) or "") for key in ("shot_size", "camera_angle", "frozen_moment")).strip("，")
        if camera:
            lines.append("本镜拍摄要求：" + camera + "。景别必须落实到画面裁切：脸部特写只表现脸部及必要背景，手或物品特写以关键细节为主体，不为了容纳人物参考图而退回全景。")
        lines.append("人物设定图只参考身份、体貌和指定造型，不参考设定卡的全身构图或多视图排版。每镜按照本镜景别与机位重新取景，可以切换特写、反打、过肩或细节镜头，不机械重复前镜构图。")
    lines += snap["descriptions"] + snap["people"]
    if snap["shot"].get("atmosphere"):
        lines.append("本镜特效与氛围：" + snap["shot"]["atmosphere"])
    if refs:
        lines.append("参考图用途（按实际输入顺序）：")
        lines.extend(f"图{index + 1}：{ref['purpose']}" for index, ref in enumerate(refs))
        lines.append("每张图仅用于指定对象和用途，不混合不同人物的脸和服装。场景参考中的路人不自动入画。上一帧只参考空间关系，人物名单以本镜为准。")
        if "asset_description" not in snap["shot"]:
            lines.append("人物设定图册中的头部特写、正面、左侧和背面是同一个角色的不同视图，不是多个人；每个角色仅按本镜出场人数绘制，不复制成三人或多重人影。场景参考的建筑结构、门窗、通道、家具相对位置与主光源保持一致，材质和主色调保持一致，按本镜调整机位和景别，不因换镜头擅自改动布局。")
    lines.append("只绘制指定的设定素材；默认无文字标签和水印。" if "asset_description" in snap["shot"] else "只画本镜列出的可见人物；默认无字幕、文字标签和水印。")
    if snap["shot"].get("asset_kind") in ("character", "look"):
        if snap["shot"].get("asset_view") == "sheet":
            lines.append(CHARACTER_SHEET_PROMPT)
        else:
            lines.append("人物设定参考图：仅绘制一个指定人物，完整展示人物与服饰；背景为干净的纯白或浅灰单色，无场景、家具、道具陈列、其他人物、文字或水印。保持参考图人物身份及本次指定造型。")
    if snap["shot"].get("asset_kind") in ("scene", "scene_state"):
        scene_prompt = SCENE_STATE_PROMPT if snap["shot"]["asset_kind"] == "scene_state" else SCENE_MASTER_PROMPT
        scene_prompt = scene_prompt.replace("16:9", snap["settings"]["aspect_ratio"], 1)
        if scene_prompt not in snap["shot"]["image_prompt"]:
            lines.append(scene_prompt)
    if snap["shot"].get("asset_kind") == "prop":
        lines.append("独立物品设定参考图：仅绘制指定物品本体及其必要附属部分，完整展示形状、材质、颜色与细节，保留点燃等物品自身状态。背景为干净的纯白或浅灰单色，无人物、手部、家具、场景、无关物品、文字或水印。原描述中出现的位置、使用者和前后剧情只用于理解物品，不画入画面；不拼贴、不分屏，不把不同时间或场景合在一张图中。")
    prompt = "\n".join(lines)
    if len(prompt) > 16000:
        raise ValueError("合并素材说明后的提示词超过16000字符，请精简设定。")
    return prompt


def plan(store, project, shot_ids, allow_missing=False, force=False, asset_id=None):
    shots = {shot["id"]: shot for shot in project["shots"]}
    if asset_id:
        asset = asset_by_id(project, asset_id)
        if asset is None:
            raise ValueError("素材不存在。")
        parent = asset_by_id(project, asset.get("parent_id"))
        synthetic = {"id": asset_id, "summary": asset["name"], "image_prompt": f"生成单独一张{asset['name']}设定参考图。{version(asset)['description']}，清晰完整展示对象，不含文字标签。",
                     "asset_description": version(asset)["description"], "asset_kind": asset["kind"], "participants": [], "props": [], "source_refs": asset.get("source_refs", [])}
        if asset["kind"] in ("character", "look"):
            synthetic["asset_view"] = "sheet"
        if parent:
            synthetic["props"].append({"asset_id": parent["id"], "version_id": parent["current_version_id"], "policy": "follow_latest"})
        shots = {asset_id: synthetic}
        shot_ids = [asset_id]
    if not shot_ids or any(s not in shots for s in shot_ids):
        raise ValueError("请选择存在的镜头。")
    jobs = store.jobs(project["id"])
    entries, visited = [], set()

    def visit(shot_id):
        if shot_id in visited:
            return
        visited.add(shot_id)
        shot = shots[shot_id]
        if shot.get("unresolved_scene_id"):
            raise ValueError("本镜场景引用待确认，请先在分镜卡选择场景或确认按文字生成。")
        if shot.get("source_refs_needs_review"):
            raise ValueError("本镜原文定位待确认，请先在分镜卡选择对应段落。")
        snap = snapshot(project, shot)
        fingerprint = semantic_hash(snap)
        reusable = next((j for j in jobs if j.get("owner_id") == shot_id and j.get("fingerprint") == fingerprint
                         and j["status"] == "succeeded" and j.get("result_image")), None)
        if not asset_id and shot.get("stale"):
            reusable = None
        if reusable and not (force and shot_id in shot_ids):
            entries.append({"owner_id": shot_id, "title": shot.get("summary", "素材"), "reuse": reusable["id"], "missing": [], "snapshot": snap})
            return
        previous = shot.get("previous_shot_id")
        if previous:
            if previous not in shots:
                raise ValueError("上一帧已删除，请修改引用。")
            visit(previous)
        refs = copy.deepcopy(snap["references"])
        if previous:
            refs.append({"previous_shot_id": previous, "purpose": "上一帧的空间位置与光线；不继承未列出的人物"})
        if len(refs) > 8:
            raise ValueError(f"{shot.get('summary', '镜头')} 需要 {len(refs)} 张参考，接口最多8张；请减少绑定，不会静默丢弃人物。")
        for ref in refs:
            if ref.get("image") and not store.image_path(ref["image"]["file"]).is_file():
                raise ValueError("定稿素材文件缺失，请恢复文件；不会自动重新生成人物。")
        entries.append({"owner_id": shot_id, "title": shot.get("summary", asset["name"] if asset_id else "镜头"),
                        "snapshot": snap, "fingerprint": fingerprint, "references": refs, "prompt": render_prompt(snap, refs),
                        "missing": snap["missing"], "reuse": None, "owner_type": "asset" if asset_id else "shot"})

    for shot_id in shot_ids:
        visit(shot_id)
    missing = sorted({m for entry in entries if not entry["reuse"] for m in entry["missing"]})
    unresolved = [j["id"] for j in jobs if j.get("owner_id") in visited and j["status"] in ("unknown", "running", "cancelling", "prepared", "queued")]
    return {"project_id": project["id"], "revision": project["revision"], "entries": entries,
            "requests": sum(not e["reuse"] for e in entries), "reused": sum(bool(e["reuse"]) for e in entries),
            "missing": missing, "blocked": bool(missing and not allow_missing), "unresolved_jobs": unresolved,
            "selected": shot_ids, "asset_id": asset_id}


def prepare(store, project, planned, acknowledge_unknown=False):
    if planned["blocked"]:
        raise ValueError("请先上传/生成并定稿缺失素材，或明确选择允许缺失素材按文本生成。")
    active = [store.job(j) for j in planned["unresolved_jobs"]]
    if any(j["status"] in ("running", "cancelling", "prepared", "queued") for j in active):
        raise ValueError("所选镜头已有待执行或运行任务，请先完成或取消它。")
    if active and not acknowledge_unknown:
        raise ValueError("存在结果未知的任务。确认可能产生重复费用后，才能手动发起新请求。")
    mapping, records = {}, []
    for entry in planned["entries"]:
        if entry["reuse"]:
            job = store.job(entry["reuse"])
        else:
            refs = copy.deepcopy(entry["references"])
            for ref in refs:
                if ref.get("previous_shot_id"):
                    ref["job_id"] = mapping[ref["previous_shot_id"]]
            job = store.add_job(project["id"], {"kind": "image", "owner_type": entry["owner_type"], "owner_id": entry["owner_id"],
                                "snapshot": entry["snapshot"], "fingerprint": entry["fingerprint"], "references": refs,
                                "prompt": entry["prompt"], "gateway_request_id": secrets.token_hex(32),
                                "settings": entry["snapshot"]["settings"]})
        mapping[entry["owner_id"]] = job["id"]
        records.append(job)
    return compile_jobs(store, project, records, planned["selected"])


def compile_jobs(store, project, jobs, selected):
    nodes, links, api, keys = [], [], {}, {}

    def add(key, kind, title, widgets, props=None):
        if key in keys:
            return keys[key]
        node_id = str(len(nodes) + 1)
        keys[key] = node_id
        nodes.append({"id": node_id, "key": key, "type": kind, "title": title, "widgets": widgets,
                      "properties": {"astrogate_project": project["id"], **(props or {})}})
        api[node_id] = {"class_type": kind, "inputs": dict(widgets)}
        return node_id

    def load(ref):
        image = ref["image"]
        return add(image["file"], "LoadImage", ref["purpose"], {"image": image["file"]})

    job_nodes = {}
    for job in jobs:
        owner_id = job["owner_id"]
        shot = job["snapshot"]["shot"]
        frame = add(owner_id, "AstroGateStoryboardFrame", shot.get("summary", "素材设定图"),
                    {"prompt": shot["image_prompt"], "job_id": job["id"]}, {"astrogate_owner": owner_id, "astrogate_job": job["id"],
                    "astrogate_base_prompt": shot["image_prompt"],
                    "astrogate_images": [job["saved_result"]] if job.get("saved_result") else []})
        job_nodes[job["id"]] = frame
        # Successful jobs read their own durable result; references need not execute again.
        if job["status"] == "succeeded":
            continue
        for index, ref in enumerate(job["references"]):
            if ref.get("job_id"):
                upstream = store.job(ref["job_id"])
                if upstream["id"] in job_nodes:
                    source = job_nodes[upstream["id"]]
                elif upstream["status"] == "succeeded":
                    source = load({"image": upstream["result_image"], "purpose": ref["purpose"]})
                else:
                    raise ValueError("缺少前帧执行节点，请重新预览生成计划。")
            else:
                source = load(ref)
            socket = f"reference_images.reference_{index + 1}"
            api[frame]["inputs"][socket] = [source, 0]
            links.append({"from": source, "to": frame, "input": socket})
    return {"nodes": nodes, "links": links, "prompt": api, "targets": [keys[s] for s in selected],
            "jobs": [j["id"] for j in jobs], "project_id": project["id"]}


def draft_canvas(store, project, chapter_id):
    all_shots = {s["id"]: s for s in project["shots"]}
    expanded = {}
    def include(shot):
        if shot["id"] in expanded:
            return
        expanded[shot["id"]] = shot
        if shot.get("previous_shot_id"):
            include(all_shots[shot["previous_shot_id"]])
    for shot in project["shots"]:
        if shot["chapter_id"] == chapter_id:
            include(shot)
    shots = list(expanded.values())
    nodes, links = [], []
    keys = {}
    for shot in shots:
        snap = snapshot(project, shot)
        refs = snap["references"]
        if shot.get("previous_shot_id"):
            refs.append({"previous_shot_id": shot["previous_shot_id"], "purpose": "上一帧空间关系"})
        if len(refs) > 8:
            raise ValueError("镜头参考图片超过接口8张上限，请先调整引用。")
        frame = str(len(nodes) + 1)
        keys[shot["id"]] = frame
        completed = store.job(shot["latest_job_id"]) if shot.get("latest_job_id") else None
        ready = completed and completed["status"] == "succeeded" and not shot.get("stale")
        nodes.append({"id": frame, "key": shot["id"], "type": "AstroGateStoryboardFrame", "title": shot["summary"],
                      "widgets": {"prompt": shot["image_prompt"], "job_id": completed["id"] if ready else ""},
                      "properties": {"astrogate_project": project["id"], "astrogate_owner": shot["id"],
                                     "astrogate_base_prompt": shot["image_prompt"],
                                     "astrogate_images": [completed["saved_result"]] if completed and completed.get("saved_result") else []}})
        for index, ref in enumerate(refs):
            if ref.get("previous_shot_id"):
                continue
            file = ref["image"]["file"]
            if file not in keys:
                keys[file] = str(len(nodes) + 1)
                nodes.append({"id": keys[file], "key": file, "type": "LoadImage", "title": ref["purpose"], "widgets": {"image": file},
                              "properties": {"astrogate_project": project["id"]}})
            links.append({"from": keys[file], "to": frame, "input": f"reference_images.reference_{index + 1}"})
    for shot in shots:
        previous = shot.get("previous_shot_id")
        if previous in keys:
            refs, _, _ = references(project, shot)
            links.append({"from": keys[previous], "to": keys[shot["id"]], "input": f"reference_images.reference_{len(refs) + 1}"})
    return {"nodes": nodes, "links": links, "jobs": [], "project_id": project["id"]}
