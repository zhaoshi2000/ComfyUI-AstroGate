from .store import asset_by_id, version


def shot_video_prompt(project, shot):
    action = shot.get("video_action") or shot.get("frozen_moment") or shot["summary"]
    if any(word in action.lower() for word in ("bgm", "背景音乐", "配乐", "字幕", "画内文字")):
        action = shot.get("frozen_moment") or shot["summary"]
    camera = "，".join(part for part in (shot.get("shot_size"), shot.get("camera_angle")) if part)
    lines = ["以连接的分镜图像为首帧，保持其中人物身份、服装、构图、场景布局、材质和光线一致。",
             f"本镜景别与机位：{camera}。" if camera else "保持首帧景别与机位。",
             f"本镜动作与运镜：{action}。动作自然连贯，镜头只表现这一镜，不提前切入下一分镜。",
             "24fps、1/48 快门、正常速度，电影级真人实拍质感；不慢放、不加速、不抽帧。"]
    visible = {person["identity"]["asset_id"] for person in shot.get("participants", []) if person.get("visible") is True}
    dialogues = {item["id"]: item for item in project.get("dialogues", [])}
    for link in shot.get("dialogue_links", []):
        dialogue = dialogues.get(link.get("dialogue_id"))
        if not dialogue or link.get("needs_review") or dialogue["source"] == "adapted" and dialogue.get("status") != "confirmed":
            continue
        start, end = link.get("start"), link.get("end")
        if link.get("mode") == "audio_only":
            lines.append("上一镜的说话声只延续尾音，本镜不重新朗读整句。")
            continue
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(dialogue["text"]):
            continue
        character = asset_by_id(project, dialogue.get("character_id"))
        if not character:
            continue
        source = "画外音" if dialogue["type"] in ("narration", "voiceover", "inner") or character["id"] not in visible else "对白"
        lines.append(f"{character['name']}{source}：{{{dialogue['text'][start:end]}}}。画外说话人不因此出现在画面。")
    sounds = [cue for cue in shot.get("sound_cues", []) if not any(word in cue.lower() for word in ("bgm", "背景音乐", "配乐"))]
    if sounds:
        lines.append("只保留与动作相符的现场声：" + "、".join(sounds) + "。")
    lines.append("严禁 BGM、配乐、歌曲、字幕、新增画内文字和水印；不得额外增加人物、道具或镜头切换。")
    return "\n".join(lines)


def video_prompt(project, chapter_id, analysis_id=None, jobs=()):
    shots = [shot for shot in project["shots"] if shot["chapter_id"] == chapter_id
             and (analysis_id is None or shot.get("analysis_id") == analysis_id)]
    if not shots:
        raise ValueError("本章没有可用的分镜。")
    dialogues = {item["id"]: item for item in project.get("dialogues", [])}
    jobs_by_id = {job["id"]: job for job in jobs}
    images, audios, image_keys, audio_keys, warnings = [], [], {}, {}, []

    def image_ref(asset, selected, image, purpose):
        if not image:
            return None
        key = (asset["id"], selected["id"], image["file"])
        if key not in image_keys:
            image_keys[key] = len(images) + 1
            images.append({"index": image_keys[key], "asset_id": asset["id"], "version_id": selected["id"],
                           "name": asset["name"], "purpose": purpose, "file": image["file"]})
        return image_keys[key]

    def voice_ref(character):
        voice_id = character.get("current_voice_id")
        voice = next((item for item in character.get("voice_versions", []) if item["id"] == voice_id), None)
        if not voice:
            return None
        if character["id"] not in audio_keys:
            audio_keys[character["id"]] = len(audios) + 1
            audios.append({"index": audio_keys[character["id"]], "character_id": character["id"],
                           "name": character["name"], "voice_id": voice["id"], "file": voice["file"]})
        return audio_keys[character["id"]]

    lines = ["严禁生成 BGM、配乐、歌曲、字幕、画内文字及水印；只保留明确列出的台词和现场声音。",
             "摄影机：ARRI ALEXA LF，ARRIRAW 4.5K Open Gate，ARRI Signature Prime 定焦镜头组。",
             "帧率：24fps，快门 1/48。全程正常速度，禁止慢动作、升格、抽帧、加速台词和速度渐变。",
             f"画幅：{project['settings'].get('aspect_ratio', '16:9')}，{project['settings'].get('resolution', '4K')}，电影级真人实拍质感。",
             "每个 [image] 和 [audio] 对应下方素材清单中一项独立素材；上传时严格按编号绑定，不复用其他角色的脸、造型或声音。",
             ""]
    shot_lines = []
    for number, shot in enumerate(shots, 1):
        scene = shot.get("scene")
        scene_number, scene_text = None, ""
        if scene:
            asset = asset_by_id(project, scene["asset_id"])
            selected = version(asset, scene["version_id"] if scene["policy"] == "locked" else None)
            scene_text = f"{asset['name']}：{selected['description']}"
            scene_asset, scene_version = asset, selected
            if asset["kind"] == "scene_state" and not selected.get("image"):
                parent = asset_by_id(project, asset.get("parent_id"))
                if parent:
                    scene_asset = parent
                    scene_version = version(parent, selected.get("parent_version_id"))
            scene_number = image_ref(scene_asset, scene_version, scene_version.get("image"), "场景空间与光线，不带入人物")
        people, character_numbers, look_numbers = [], [], []
        for person in shot.get("participants", []):
            if person.get("visible") is not True:
                continue
            binding = person["identity"]
            character = asset_by_id(project, binding["asset_id"])
            selected = version(character, binding["version_id"] if binding["policy"] == "locked" else None)
            sheet = selected.get("view_images", {}).get("sheet") or selected.get("image")
            number_in_manifest = image_ref(character, selected, sheet, "同一人物的身份与三视图，不复制成多人")
            look = person.get("look")
            look_text = ""
            if look:
                look_asset = asset_by_id(project, look["asset_id"])
                look_version = version(look_asset, look["version_id"] if look["policy"] == "locked" else None)
                look_text = f"；本镜造型：{look_asset['name']}，{look_version['description']}"
                look_number = image_ref(look_asset, look_version, look_version.get("image"), f"{character['name']}的本版服装、发型与配饰")
                if look_number:
                    look_numbers.append(look_number)
            people.append(f"{character['name']}：{selected['description']}{look_text}")
            if number_in_manifest:
                character_numbers.append(number_in_manifest)
        prop_numbers = []
        for binding in shot.get("props", []):
            asset = asset_by_id(project, binding["asset_id"])
            selected = version(asset, binding["version_id"] if binding["policy"] == "locked" else None)
            number_in_manifest = image_ref(asset, selected, selected.get("image"), "道具外形与材质")
            if number_in_manifest:
                prop_numbers.append(number_in_manifest)
        saved = jobs_by_id.get(shot.get("latest_job_id"))
        frame_number = None
        if saved and saved.get("status") == "succeeded" and saved.get("result_image"):
            frame = saved["result_image"]
            frame_key = (shot["id"], saved["id"], frame["file"])
            if frame_key not in image_keys:
                image_keys[frame_key] = len(images) + 1
                images.append({"index": image_keys[frame_key], "shot_id": shot["id"], "name": shot["summary"],
                               "purpose": "本镜已生成的首帧构图", "file": frame["file"]})
            frame_number = image_keys[frame_key]
        camera = "｜".join(part for part in (shot.get("shot_size"), shot.get("camera_angle")) if part)
        motion = shot.get("video_action") or shot.get("frozen_moment") or shot.get("summary", "")
        if any(word in motion.lower() for word in ("bgm", "背景音乐", "配乐", "字幕", "画内文字")):
            warnings.append(f"镜头{number:03d}的视频动作含配乐或字幕要求，已改用关键帧描述。")
            motion = shot.get("frozen_moment") or shot.get("summary", "")
        line = f"镜头{number:03d}【{camera or '机位待定'}】{shot['summary']}：{motion}"
        refs = [f"图{frame_number}首帧"] if frame_number else []
        if scene_number:
            refs.append(f"图{scene_number}场景")
        refs.extend(f"图{n}人物" for n in character_numbers)
        refs.extend(f"图{n}造型" for n in look_numbers)
        refs.extend(f"图{n}道具" for n in prop_numbers)
        if refs:
            line += "；参考：" + "、".join(refs)
        if scene_text:
            line += "；场景：" + scene_text
        if people:
            line += "；画面人物：" + "、".join(people)
        cues = [cue for cue in shot.get("sound_cues", []) if not any(word in cue.lower() for word in ("bgm", "背景音乐", "配乐"))]
        if len(cues) != len(shot.get("sound_cues", [])):
            warnings.append(f"镜头{number:03d}的配乐描述已从现场声中排除。")
        if cues:
            line += "；现场声：" + "、".join(cues)
        speech = []
        for link in shot.get("dialogue_links", []):
            dialogue = dialogues.get(link.get("dialogue_id"))
            if not dialogue:
                warnings.append(f"镜头{number:03d}有找不到的台词关联。")
                continue
            start, end = link.get("start"), link.get("end")
            valid_range = type(start) is int and type(end) is int and 0 <= start < end <= len(dialogue["text"])
            if link.get("needs_review") or not (valid_range or link["mode"] == "audio_only" and start is None and end is None):
                warnings.append(f"镜头{number:03d}的台词“{dialogue['text'][:12]}”范围待确认，未写入视频提示词。")
                continue
            if dialogue["source"] == "adapted" and dialogue.get("status") != "confirmed":
                warnings.append(f"镜头{number:03d}的 AI 改编台词待确认，未写入视频提示词。")
                continue
            character = asset_by_id(project, dialogue.get("character_id"))
            speaker = character["name"] if character else "说话人待确认"
            voice_number = voice_ref(character) if character else None
            kind = {"dialogue": "对白", "narration": "旁白", "voiceover": "画外音", "inner": "内心独白"}.get(dialogue["type"], dialogue["type"])
            if kind == "对白" and character and character["id"] not in {person["identity"]["asset_id"] for person in shot.get("participants", []) if person.get("visible") is True}:
                kind = "画外对白"
            if link["mode"] == "audio_only":
                speech.append(f"{speaker}的声音从上一镜自然延续，不重新朗读整句")
            else:
                speech.append(f"{speaker}{kind}{'（声线音频' + str(voice_number) + '）' if voice_number else ''}：{{{dialogue['text'][start:end]}}}")
        if speech:
            line += "；声音：" + "；".join(speech)
        shot_lines.append(line + (" [硬切]" if number < len(shots) else ""))
    if images:
        lines.append("图像素材（编号和上传顺序一致）：")
        lines.extend(f"图{item['index']} {item['name']} [image]：{item['purpose']}" for item in images)
        lines.append("")
    if audios:
        lines.append("角色声线（声音跟随人物身份，换装不换声线）：")
        lines.extend(f"音频{item['index']} {item['name']} [audio]" for item in audios)
        lines.append("")
    lines.extend(shot_lines)
    return {"prompt": "\n".join(lines), "images": images, "audios": audios, "warnings": warnings,
            "shot_ids": [shot["id"] for shot in shots]}
