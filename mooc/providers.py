"""适配层：声音与数字人都藏在统一接口后面，换供应商只改这里。

TTS:    enroll(sample_wav) -> voice_id ； synth(text, out_wav, ssml=False)
Avatar: make(photo, audio_wav, out_mp4) -> 计费秒数 ； output -> "plain" | "greenscreen"

真实接口（CosyVoice / OmniHuman）按公开文档编写，尚未用真实账号跑通，开通后先用 3 分钟样片验证。
"""
import json, subprocess, tempfile, time
from pathlib import Path
import requests
from .conf import CFG, env
from .media import ff, to_wav, wav_seconds
from .storage import public_url


# ---------- 声音 ----------
class MockTTS:
    """macOS say（婷婷）代替克隆声音，只为跑通流程。"""
    model = "mock-say"
    def enroll(self, sample): return "mock-voice"
    def synth(self, text, out, ssml=False):
        with tempfile.TemporaryDirectory() as td:
            aiff = Path(td) / "a.aiff"
            rate = int(220 * CFG["tts"]["speech_rate"])
            r = subprocess.run(["say", "-v", "Tingting", "-r", str(rate), "-o", str(aiff), text], capture_output=True)
            if r.returncode or not aiff.exists():  # 非 macOS：按估算时长生成静音
                from .script import est_seconds
                ff("-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono", "-t", max(est_seconds(text), 1), aiff)
            to_wav(aiff, out)


class CosyVoiceTTS:
    """阿里云百炼 CosyVoice。复刻需北京地域；音色 ID 只复刻一次，存进 profile。"""
    def __init__(self, voice_id=None):
        import dashscope
        dashscope.api_key = env("DASHSCOPE_API_KEY")
        self.model, self.voice_id = CFG["tts"]["model"], voice_id

    def enroll(self, sample):
        from dashscope.audio.tts_v2 import VoiceEnrollmentService
        svc = VoiceEnrollmentService()
        vid = svc.create_voice(target_model=self.model, prefix="teacher", url=public_url(sample, "voice"))
        for _ in range(60):
            st = svc.query_voice(voice_id=vid)
            status = st.get("status") if isinstance(st, dict) else None
            if status == "OK": return vid
            if status in ("UNDEPLOYED", "FAILED"): raise RuntimeError(f"声音复刻失败：{st}")
            time.sleep(5)
        raise TimeoutError("声音复刻超时")

    def synth(self, text, out, ssml=False):
        from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
        t = CFG["tts"]
        s = SpeechSynthesizer(model=self.model, voice=self.voice_id, format=AudioFormat.WAV_24000HZ_MONO_16BIT,
                              speech_rate=t["speech_rate"], volume=t["volume"], pitch_rate=t["pitch"])
        audio = s.call(text)
        if not audio: raise RuntimeError(f"语音合成无返回：{getattr(s, 'get_last_request_id', lambda: '')()}")
        raw = Path(out).with_suffix(".raw.wav"); raw.write_bytes(audio)
        to_wav(raw, out); raw.unlink()


# ---------- 数字人 ----------
class MockAvatar:
    """照片 + 轻微呼吸感缩放，代替数字人驱动。"""
    model, output = "mock-photo", "plain"
    def make(self, photo, audio, out):
        ff("-loop", 1, "-framerate", CFG["layout"]["fps"], "-i", photo, "-i", audio, "-filter_complex",
           "[0]scale=720:960:force_original_aspect_ratio=increase,crop=720:960,"
           "zoompan=z='1.03+0.012*sin(2*PI*on/60)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=720x960:fps=25[v]",
           "-map", "[v]", "-map", "1:a", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", out)
        return wav_seconds(audio)


class OmniHumanAvatar:
    """火山引擎 即梦 AI · OmniHuman：一张图 + 一段音频 → 带口型、表情、肢体动作的视频。"""
    model = "omnihuman"
    def __init__(self):
        from volcengine.visual.VisualService import VisualService
        self.vs = VisualService(); self.vs.set_ak(env("VOLC_ACCESS_KEY")); self.vs.set_sk(env("VOLC_SECRET_KEY"))
        self.c, self.output = CFG["avatar"], CFG["avatar"]["output"]
        self._checked = set()

    def _task(self, req_key, body):
        r = self.vs.cv_submit_task({"req_key": req_key, **body})
        if r.get("code") != 10000: raise RuntimeError(f"提交失败：{r.get('message')}（{r.get('code')}）")
        tid, t0 = r["data"]["task_id"], time.time()
        while time.time() - t0 < self.c["timeout"]:
            time.sleep(self.c["poll_interval"])
            g = self.vs.cv_get_result({"req_key": req_key, "task_id": tid})
            d = g.get("data") or {}
            if d.get("status") == "done": return d
            if g.get("code") != 10000 or d.get("status") in ("not_found", "expired"):
                raise RuntimeError(f"生成失败：{g.get('message')} {d.get('status')}")
        raise TimeoutError(f"任务 {tid} 超时")

    def make(self, photo, audio, out):
        img = public_url(photo, "avatar")
        if photo not in self._checked:  # 主体检测：照片不合格时把供应商的原因回显出来
            d = self._task(self.c["detect_req_key"], {"image_url": img})
            resp = json.loads(d.get("resp_data") or "{}")
            if resp.get("status") not in (None, 1): raise RuntimeError(f"照片未通过主体检测：{resp}")
            self._checked.add(photo)
        d = self._task(self.c["submit_req_key"], {"image_url": img, "audio_url": public_url(audio, "audio")})
        with requests.get(d["video_url"], stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(out, "wb") as f:
                for b in r.iter_content(1 << 20): f.write(b)
        return wav_seconds(audio)


def tts(voice_id=None):
    return {"mock": MockTTS, "cosyvoice": lambda: CosyVoiceTTS(voice_id)}[CFG["tts_provider"]]()

def avatar():
    return {"mock": MockAvatar, "omnihuman": OmniHumanAvatar}[CFG["avatar_provider"]]()
