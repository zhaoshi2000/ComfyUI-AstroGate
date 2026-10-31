from pathlib import Path

import folder_paths
from comfy_api.latest import io
from comfy_execution.graph_utils import GraphBuilder


def model_files(category, prefix, preferred):
    names = [name for name in folder_paths.get_filename_list(category)
             if Path(name).name.startswith(prefix) and name.lower().endswith(".safetensors")]
    return names or [preferred]


def visual_prompt(prompt):
    lines = [line for line in prompt.splitlines() if not (
        "对白：{" in line or "画外音：{" in line or
        line.startswith("上一镜的说话声") or
        line.startswith("只保留与动作相符的现场声：") or
        line.startswith("严禁 BGM、")
    )]
    lines.append("只拍摄人物动作与场景，不叠加屏幕图形或字符。")
    return "\n".join(lines)


class AstroGateMiniMaxH3Video(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        preferred_model = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
        models = model_files("diffusion_models", "minimax_h3_fl2va_", preferred_model)
        encoders = model_files("text_encoders", "qwen3vl_32b_minimax_h3_", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
        video_vaes = model_files("vae", "minimax_h3_video_vae_", "minimax_h3_video_vae_fp16.safetensors")
        audio_vaes = model_files("vae", "minimax_h3_audio_vae_", "minimax_h3_audio_vae_fp32.safetensors")
        loras = ["none", *[name for name in folder_paths.get_filename_list("loras")
                           if Path(name).name.startswith("minimax_h3_fl2v_turbo_") and name.lower().endswith(".safetensors")]]
        return io.Schema(
            node_id="AstroGateMiniMaxH3Video", display_name="MiniMax H3 · 分镜图生视频", category="video/AstroGate",
            description="连接分镜首帧生成视频。默认静音；H3 原声可能与台词、语言不符。可连接已对齐的成品音轨。不会下载模型。",
            enable_expand=True,
            inputs=[io.Image.Input("first_frame", display_name="分镜首帧", raw_link=True),
                    io.String.Input("prompt", display_name="视频提示词", default="", multiline=True),
                    io.Combo.Input("model_name", display_name="MiniMax H3 模型", options=models,
                                   default=preferred_model if preferred_model in models else models[0]),
                    io.Combo.Input("text_encoder", display_name="文本编码器", options=encoders, default=encoders[0], advanced=True),
                    io.Combo.Input("video_vae", display_name="视频 VAE", options=video_vaes, default=video_vaes[0], advanced=True),
                    io.Combo.Input("audio_vae", display_name="音频 VAE", options=audio_vaes, default=audio_vaes[0], advanced=True),
                    io.Combo.Input("turbo_lora", display_name="加速 LoRA", options=loras, default=loras[1] if len(loras) > 1 else "none", advanced=True),
                    io.Float.Input("duration", display_name="时长（秒）", default=3.0, min=0.2, max=149.0),
                    io.Int.Input("width", display_name="视频宽度", default=1024, min=32, max=16384, step=32, advanced=True),
                    io.Int.Input("height", display_name="视频高度", default=576, min=32, max=16384, step=32, advanced=True),
                    io.Int.Input("steps", display_name="采样步数（0 自动）", default=0, min=0, advanced=True),
                    io.Int.Input("seed", display_name="种子", default=0, min=0, advanced=True),
                    io.Combo.Input("audio_mode", display_name="声音", options=["静音", "H3 原声"], default="静音", optional=True),
                    io.Audio.Input("soundtrack", display_name="已对齐音轨", optional=True, raw_link=True,
                                   tooltip="接入本镜已配好的完整音轨；角色声线样本不能直接当作台词。")],
            outputs=[io.Video.Output(display_name="视频")],
        )

    @classmethod
    def execute(cls, first_frame, prompt, model_name, text_encoder, video_vae, audio_vae, turbo_lora,
                duration, width, height, steps, seed, audio_mode="静音", soundtrack=None):
        if not prompt.strip():
            raise ValueError("请填写本镜视频提示词。")
        graph = GraphBuilder()
        model = graph.node("UNETLoader", unet_name=model_name, weight_dtype="default")
        if turbo_lora != "none":
            model = graph.node("LoraLoaderModelOnly", model=model.out(0), lora_name=turbo_lora, strength_model=1)
        clip = graph.node("CLIPLoader", clip_name=text_encoder, type="minimax", device="default")
        video = graph.node("VAELoader", vae_name=video_vae)
        frames = max(5, round(duration * 24))
        frames += (5 - frames % 17) % 17
        conditioned = graph.node("MiniMaxH3ImageToVideo", clip=clip.out(0), vae=video.out(0), first_frame=first_frame,
                                 prompt=prompt if audio_mode == "H3 原声" and soundtrack is None else visual_prompt(prompt),
                                 width=width, height=height, length=frames)
        guider = graph.node("BasicGuider", model=model.out(0), conditioning=conditioned.out(0))
        sampler = graph.node("KSamplerSelect", sampler_name="res_multistep")
        scheduler = graph.node("BasicScheduler", model=model.out(0), scheduler="simple",
                               steps=steps or (8 if turbo_lora != "none" else 20), denoise=1.0)
        noise = graph.node("RandomNoise", noise_seed=seed)
        sampled = graph.node("SamplerCustomAdvanced", noise=noise.out(0), guider=guider.out(0),
                             sampler=sampler.out(0), sigmas=scheduler.out(0), latent_image=conditioned.out(1))
        frames = graph.node("VAEDecode", samples=sampled.out(0), vae=video.out(0))
        if soundtrack is None and audio_mode == "H3 原声":
            audio = graph.node("VAELoader", vae_name=audio_vae)
            soundtrack = graph.node("VAEDecodeAudio", samples=sampled.out(0), vae=audio.out(0)).out(0)
        video_inputs = {"images": frames.out(0), "fps": 24, "bit_depth": 8, "color_space": "sRGB"}
        if soundtrack is not None:
            video_inputs["audio"] = soundtrack
        result = graph.node("CreateVideo", **video_inputs)
        return io.NodeOutput(result.out(0), expand=graph.finalize())
