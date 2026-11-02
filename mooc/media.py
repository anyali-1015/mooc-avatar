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


def to_wav(src, dst, sr=None):
    """转 16bit 单声道 wav，并统一响度（loudnorm）。"""
    sr = sr or CFG["tts"]["sample_rate"]
    ff("-i", src, "-af", f"loudnorm=I={CFG['tts']['loudness_lufs']}:TP=-1.5:LRA=11", "-ar", sr, "-ac", 1, dst)


def wav_seconds(p):
    with wave.open(str(p)) as w: return w.getnframes() / w.getframerate()


def wav_array(p):
    with wave.open(str(p)) as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    return a, sr


def split_wav(src, max_s, outdir):
    """按上限切段，切点选在目标位置 ±3 秒内最安静的 50ms，避免切在字中间。"""
    a, sr = wav_array(src)
    total = len(a) / sr
    if total <= max_s: return [Path(src)]
    n = int(np.ceil(total / (max_s - 3)))
    step, win, cuts = total / n, int(0.05 * sr), [0]
    for i in range(1, n):
        t = i * step
        lo, hi = int(max(t - 3, 0) * sr), int(min(t + 3, total) * sr)
        seg = a[lo:hi]
        e = np.convolve(seg ** 2, np.ones(win), "valid") if len(seg) > win else seg ** 2
        cuts.append(lo + int(np.argmin(e)) + win // 2)
    cuts.append(len(a))
    out = []
    for i in range(len(cuts) - 1):
        p = Path(outdir) / f"{Path(src).stem}_part{i}.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((a[cuts[i]:cuts[i + 1]] * 32767).astype(np.int16).tobytes())
        out.append(p)
    return out


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


def font(size):
    try: return ImageFont.truetype(CFG["layout"]["font"], size)
    except Exception: return ImageFont.load_default()


def watermark_png(text, dst):
    f = font(30)
    w, h = int(f.getlength(text)) + 32, 52
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 10, fill=(0, 0, 0, 110))
    d.text((16, 8), text, font=f, fill=(255, 255, 255, 230))
    im.save(dst)
