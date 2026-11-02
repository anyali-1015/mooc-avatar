"""FFmpeg 与音频小工具。ffmpeg 二进制来自 imageio-ffmpeg，不需要系统安装。"""
import subprocess, wave
from pathlib import Path
import numpy as np
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont
from .conf import CFG

FF = imageio_ffmpeg.get_ffmpeg_exe()


def ff(*args):
    r = subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *map(str, args)],
                       capture_output=True, text=True)
    if r.returncode: raise RuntimeError("ffmpeg 失败：" + r.stderr.strip()[-800:])


def to_wav(src, dst, sr=None, norm=True):
    """转 16bit 单声道 wav；norm 时统一响度（loudnorm）。"""
    sr = sr or CFG["tts"]["sample_rate"]
    af = ["-af", f"loudnorm=I={CFG['tts']['loudness_lufs']}:TP=-1.5:LRA=11"] if norm else []
    ff("-i", src, *af, "-ar", sr, "-ac", 1, dst)


def write_wav(a, sr, dst):
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())


def video_seconds(p): return imageio_ffmpeg.count_frames_and_secs(str(p))[1]


def wav_seconds(p):
    with wave.open(str(p)) as w: return w.getnframes() / w.getframerate()


def wav_array(p):
    with wave.open(str(p)) as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    return a, sr




def concat(parts, dst, copy=True):
    lst = Path(dst).with_suffix(".txt")
    lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in parts), "utf-8")
    if copy: ff("-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", dst)
    else: ff("-f", "concat", "-safe", 0, "-i", lst, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", dst)
    lst.unlink()


def frames(video, n, size=None):
    """均匀抽 n 帧（PIL Image）。"""
    import tempfile
    out = []
    with tempfile.TemporaryDirectory() as td:
        ff("-i", video, "-vf", f"fps=2", Path(td) / "f%04d.png")
        fs = sorted(Path(td).glob("f*.png"))
        for f in [fs[int(i * (len(fs) - 1) / max(n - 1, 1))] for i in range(n)] if fs else []:
            im = Image.open(f).convert("RGB"); out.append(im.resize(size) if size else im.copy())
    return out


FONTS = [CFG["layout"]["font"], "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"]


def font(size):
    for f in FONTS:
        try: return ImageFont.truetype(f, size)
        except Exception: pass
    return ImageFont.load_default()


def watermark_png(text, dst):
    f = font(30)
    w, h = int(f.getlength(text)) + 32, 52
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 10, fill=(0, 0, 0, 110))
    d.text((16, 8), text, font=f, fill=(255, 255, 255, 230))
    im.save(dst)
