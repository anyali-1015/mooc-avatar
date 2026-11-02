"""适配层：声音与口型都藏在统一接口后面，换实现只改这里。

TTS:     enroll(sample_wav) -> voice_id ； synth(text, out_wav, ssml=False)   （输出未做响度统一）
LipSync: make(base_clip, audio_wav, out_mp4) -> 实际花掉的 GPU 秒数

CosyVoice 和 LatentSync 按公开文档编写，尚未用真实账号 / GPU 跑通，开通后先用 2–3 页样片验证。
"""
import base64, shlex, subprocess, tempfile, time
from pathlib import Path
import requests
from .conf import CFG, env
from .media import ff, to_wav


# ---------- 声音 ----------
class MockTTS:
    """macOS say（婷婷）/ Linux espeak-ng 代替克隆声音，只为跑通流程。"""
    model = "mock-say"
    def enroll(self, sample): return "mock-voice"
    def synth(self, text, out, ssml=False):
        with tempfile.TemporaryDirectory() as td:
            aiff = Path(td) / "a.wav"
            rate = int(220 * CFG["tts"]["speech_rate"])
            for cmd in (["say", "-v", "Tingting", "-r", str(rate), "-o", str(aiff.with_suffix(".aiff")), text],
                        ["espeak-ng", "-v", "cmn", "-s", str(int(rate * 0.8)), "-w", str(aiff), text]):
                try: subprocess.run(cmd, capture_output=True, timeout=120)
                except FileNotFoundError: continue
                if aiff.with_suffix(".aiff").exists(): aiff = aiff.with_suffix(".aiff")
                if aiff.exists(): break
            if not aiff.exists():  # 两者都没有：按估算时长生成静音
                from .script import est_seconds
                ff("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", max(est_seconds(text), 1), aiff)
            to_wav(aiff, out, norm=False)


class CosyVoiceTTS:
    """阿里云百炼 CosyVoice。复刻用北京地域，样本以 Base64 直接上传，不需要对象存储。"""
    def __init__(self, voice_id=None):
        self.model, self.voice_id, self.c = CFG["tts"]["model"], voice_id, CFG["dashscope"]

    def _enroll_call(self, body):
        ws, region = self.c.get("workspace_id"), self.c.get("region", "cn-beijing")
        url = (f"https://{ws}.{region}.maas.aliyuncs.com" if ws else "https://dashscope.aliyuncs.com") + \
            "/api/v1/services/audio/tts/customization"
        r = requests.post(url, timeout=120, json={"model": "voice-enrollment", "input": body},
                          headers={"Authorization": "Bearer " + env("DASHSCOPE_API_KEY")})
        if r.status_code != 200: raise RuntimeError(f"百炼接口返回 {r.status_code}：{r.text[:300]}")
        return r.json().get("output", {})

    def enroll(self, sample):
        with tempfile.TemporaryDirectory() as td:  # 16k 单声道、20 秒以内
            wav = Path(td) / "s.wav"
            ff("-i", sample, "-t", 20, "-ac", 1, "-ar", 16000, wav)
            data = base64.b64encode(wav.read_bytes()).decode()
        vid = self._enroll_call({"action": "create_voice", "target_model": self.model, "prefix": "teacher",
                                 "audio": f"data:audio/wav;base64,{data}"})["voice_id"]
        for _ in range(60):
            st = self._enroll_call({"action": "query_voice", "voice_id": vid}).get("status")
            if st == "OK": return vid
            if st == "UNDEPLOYED": raise RuntimeError("复刻未通过审核，请换一段更安静、更干净的录音再试")
            time.sleep(5)
        raise TimeoutError(f"声音复刻仍在处理中，稍后再试（voice_id={vid}）")

    def synth(self, text, out, ssml=False):
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
        dashscope.api_key = env("DASHSCOPE_API_KEY")
        if self.c.get("workspace_id"):
            dashscope.base_websocket_api_url = \
                f"wss://{self.c['workspace_id']}.{self.c.get('region', 'cn-beijing')}.maas.aliyuncs.com/api-ws/v1/inference"
        t, err = CFG["tts"], None
        for i in range(4):  # 网络或限流：退避重试
            try:
                audio = SpeechSynthesizer(model=self.model, voice=self.voice_id, format=AudioFormat.WAV_24000HZ_MONO_16BIT,
                                          speech_rate=t["speech_rate"], volume=t["volume"], pitch_rate=t["pitch"]).call(text)
                if audio: break
            except Exception as e: err = e
            time.sleep(2 * (i + 1))
        else: raise RuntimeError(f"语音合成失败：{err or '无返回'}（{text[:20]}…）")
        raw = Path(out).with_suffix(".raw.wav"); raw.write_bytes(audio)
        to_wav(raw, out, norm=False); raw.unlink()


def tts(voice_id=None):
    return {"mock": MockTTS, "cosyvoice": lambda: CosyVoiceTTS(voice_id)}[CFG["tts_provider"]]()


# ---------- 口型 ----------
class LipSync:
    """latentsync：调用 LatentSync 只重画嘴部；none：原样使用底片（本机测试、画外音页）。"""
    def __init__(self, engine=None):
        self.c = CFG["lipsync"]; self.engine = engine or self.c["engine"]

    def make(self, clip, audio, out):
        fps = CFG["base"]["fps"]
        if self.engine == "none":
            ff("-i", clip, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", out)
            return 0.0
        with tempfile.TemporaryDirectory() as td:
            wav16, raw = Path(td) / "a16k.wav", Path(td) / "raw.mp4"
            ff("-i", audio, "-ac", 1, "-ar", 16000, wav16)
            q = lambda p: shlex.quote(str(Path(p).resolve()))
            cmd = self.c["command"].format(python=self.c["python"], unet_config=self.c["unet_config"], ckpt=self.c["ckpt"],
                                           steps=self.c["steps"], guidance=self.c["guidance"],
                                           video=q(clip), audio=q(wav16), out=q(raw))
            t0 = time.time()
            r = subprocess.run(cmd, shell=True, cwd=self.c["latentsync_dir"], capture_output=True, text=True)
            gpu = time.time() - t0
            if r.returncode or not raw.exists():
                raise RuntimeError("LatentSync 失败：" + ((r.stderr or r.stdout) or "")[-600:])
            # 统一回 25fps，换回原始配音，长度对齐音频
            ff("-i", raw, "-i", audio, "-map", "0:v", "-map", "1:a", "-r", fps, "-c:v", "libx264",
               "-preset", "veryfast", "-crf", 16, "-c:a", "aac", "-shortest", out)
        return gpu
