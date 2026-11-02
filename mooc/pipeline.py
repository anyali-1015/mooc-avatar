"""流水线：一页 PPT = 一段讲稿 = 一段音频 = 一段数字人视频。
每一步的产物按输入的哈希命名缓存：输入没变就直接复用，不重复计费；改了哪页只重做哪页。"""
import shutil
from pathlib import Path
from . import db, ppt
from .conf import CFG, sha, file_hash, lesson_dir, page_dir, profile_dir
from .media import ff, concat, split_wav, wav_seconds, watermark_png
from .script import apply_lexicon, sentences, est_seconds
from . import providers, check

L = CFG["layout"]


class Paused(Exception): pass


# ---------- 素材建档 ----------
REF_SENTENCE = "同学们好，欢迎来到这门课。今天我们先回顾上节课的重点，再进入新的内容。"


def setup_profile(name, photo_src, voice_src, consent_text):
    d = profile_dir()
    photo = d / ("photo" + Path(photo_src).suffix.lower()); shutil.copy(photo_src, photo)
    sample = d / "voice_sample.wav"
    from .media import to_wav; to_wav(voice_src, sample)
    db.set_profile(name=name, photo=str(photo), voice_sample=str(sample),
                   consent_text=consent_text, consent_at=db.now())


def clone_voice():
    """只复刻一次：音色 ID 和模型版本存进 profile，之后整门课都用它；再生成一条锁定参数的参考句供一致性检查。"""
    pr = db.profile()
    t = providers.tts()
    vid = t.enroll(pr["voice_sample"])
    ref = profile_dir() / "ref_sentence.wav"
    providers.tts(vid).synth(REF_SENTENCE, ref)
    db.set_profile(voice_id=vid, tts_model=t.model, ref_audio=str(ref))
    db.charge("tts", len(REF_SENTENCE), len(REF_SENTENCE) / 1e4 * CFG["price"]["tts_cny_per_10k_chars"], note="参考句")
    return vid


# ---------- PPT 导入 ----------
def import_pptx(pptx_src, title, pdf=None):
    lid = db.run("insert into lessons(title,created_at) values(?,?)", title, db.now())
    d = lesson_dir(lid); pptx = d / "source.pptx"; shutil.copy(pptx_src, pptx)
    slides = ppt.parse(pptx)
    how = ppt.render(pptx, slides, [page_dir(lid, s["idx"]) / "slide.png" for s in slides], pdf)
    db.run("update lessons set pptx=?, render=? where id=?", str(pptx), how, lid)
    for s in slides:
        db.run("insert into pages(lesson_id,idx,title,text,notes,script) values(?,?,?,?,?,?)",
               lid, s["idx"], s["title"], s["text"], s["notes"], s["notes"])
    return lid


def rerender(lid, pdf):
    """导入后补传 PDF，替换背景图（只影响合成，不会重做配音和数字人）。"""
    ps = db.pages(lid)
    slides = [{"idx": p["idx"], "title": p["title"], "text": p["text"]} for p in ps]
    how = ppt.render(db.one("select pptx from lessons where id=?", lid)["pptx"], slides,
                     [page_dir(lid, p["idx"]) / "slide.png" for p in ps], pdf)
    db.run("update lessons set render=? where id=?", how, lid)
    return how


# ---------- 缓存键 ----------
def _audio_key(p, pr, lex):
    return sha(p["script"], {k: v for k, v in lex.items() if k in p["script"]}, CFG["tts"], CFG["tts_provider"], pr.get("voice_id"))

def _avatar_key(audio, pr): return sha(Path(audio), Path(pr["photo"]), CFG["avatar_provider"], CFG["avatar"])

def _clip_key(p, slide, audio, avatar):
    return sha(Path(slide), Path(audio), Path(avatar) if avatar else "voice-over", L)


# ---------- 预算 ----------
def page_cost(p):
    secs = p["audio_s"] or est_seconds(p["script"])
    return (secs * CFG["price"]["avatar_cny_per_second"] if p["on_camera"] else 0.0) + \
        len(p["script"]) / 1e4 * CFG["price"]["tts_cny_per_10k_chars"], secs


def estimate(lid, pids=None):
    """只统计还没生成过（缓存里没有）的部分，这才是这次真正要花的钱。"""
    pr, lex, tot_s, cam_s, cost = db.profile(), db.lexicon(), 0.0, 0.0, 0.0
    for p in db.pages(lid):
        if pids is not None and p["id"] not in pids: continue
        d = page_dir(lid, p["idx"])
        ak = _audio_key(p, pr, lex)
        audio = d / f"audio_{ak[:12]}.wav"
        secs = wav_seconds(audio) if audio.exists() else est_seconds(p["script"])
        tot_s += secs
        if not audio.exists(): cost += len(p["script"]) / 1e4 * CFG["price"]["tts_cny_per_10k_chars"]
        if p["on_camera"]:
            cam_s += secs
            if not (audio.exists() and (d / f"avatar_{_avatar_key(audio, pr)[:12]}.mp4").exists()):
                cost += secs * CFG["price"]["avatar_cny_per_second"]
    return {"seconds": tot_s, "camera_seconds": cam_s, "cost": cost}


def budget(): return float(db.kv("budget", CFG["price"]["budget_cny"]))


# ---------- 单页 ----------
def tts_page(p, pr=None, lex=None):
    pr, lex = pr or db.profile(), lex if lex is not None else db.lexicon()
    if not p["script"].strip(): raise ValueError("讲稿为空")
    d = page_dir(p["lesson_id"], p["idx"])
    audio = d / f"audio_{_audio_key(p, pr, lex)[:12]}.wav"
    if not audio.exists():
        ssml = CFG["tts_provider"] == "cosyvoice"
        providers.tts(pr.get("voice_id")).synth(apply_lexicon(p["script"], lex, ssml), audio)
        db.charge("tts", len(p["script"]), len(p["script"]) / 1e4 * CFG["price"]["tts_cny_per_10k_chars"], p["lesson_id"], p["id"])
    db.set_page(p["id"], audio=str(audio), audio_s=wav_seconds(audio))
    return audio


def avatar_page(p, audio, pr, prov):
    d = page_dir(p["lesson_id"], p["idx"])
    out = d / f"avatar_{_avatar_key(audio, pr)[:12]}.mp4"
    if out.exists(): return out
    secs = wav_seconds(audio)
    cost = secs * CFG["price"]["avatar_cny_per_second"]
    if db.spent() + cost > budget():
        raise Paused(f"预算不足：已花 ¥{db.spent():.0f}，本页需 ¥{cost:.0f}，上限 ¥{budget():.0f}")
    parts = []
    for i, a in enumerate(split_wav(audio, CFG["avatar"]["max_audio_seconds"], d)):
        v = d / f"{out.stem}_part{i}.mp4"
        billed = prov.make(pr["photo"], a, v)
        db.charge("avatar", billed, billed * CFG["price"]["avatar_cny_per_second"], p["lesson_id"], p["id"], prov.model)
        parts.append(v)
    if len(parts) == 1: parts[0].rename(out)
    else: concat(parts, out, copy=False); [x.unlink() for x in parts]
    for x in d.glob(f"{audio.stem}_part*.wav"): x.unlink()
    return out


def compose_page(p, audio, avatar, output="plain"):
    d = page_dir(p["lesson_id"], p["idx"]); slide = d / "slide.png"
    out = d / f"clip_{_clip_key(p, slide, audio, avatar)[:12]}.mp4"
    if out.exists(): return out
    W, H, F = L["width"], L["height"], L["fade"]
    secs = wav_seconds(audio); total = secs + L["page_pad"]
    inputs = ["-loop", 1, "-framerate", L["fps"], "-i", slide, "-i", audio]
    fc = [f"[0]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:white,setsar=1,format=yuv420p[bg]"]
    last = "bg"
    if avatar:
        inputs += ["-i", avatar]
        green = output == "greenscreen"
        x = f"main_w-overlay_w-{L['avatar_margin']}" if L["avatar_side"] == "right" else str(L["avatar_margin"])
        y = "main_h-overlay_h" if green else f"main_h-overlay_h-{L['avatar_margin']}"
        fc.append(f"[2]scale=-2:{L['avatar_height']}" + (",chromakey=0x00FF00:0.12:0.06" if green else "") +
                  f",format=yuva420p,fade=t=in:st=0:d={F}:alpha=1,fade=t=out:st={max(secs - F, 0):.3f}:d={F}:alpha=1[av]")
        fc.append(f"[{last}][av]overlay=x={x}:y={y}:eof_action=pass[v1]"); last = "v1"
    if L.get("watermark"):
        wm = lesson_dir(p["lesson_id"]) / "watermark.png"
        if not wm.exists(): watermark_png(L["watermark"], wm)
        inputs += ["-i", wm]
        fc.append(f"[{last}][{3 if avatar else 2}]overlay=main_w-overlay_w-30:30[v2]"); last = "v2"
    fc.append(f"[1]apad=pad_dur={L['page_pad'] + 1}[a]")
    ff(*inputs, "-filter_complex", ";".join(fc), "-map", f"[{last}]", "-map", "[a]", "-t", f"{total:.3f}",
       "-r", L["fps"], "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-ar", 48000, "-ac", 2, out)
    return out


def check_page(p, audio, avatar, pr):
    ref = pr.get("ref_audio") or pr.get("voice_sample")
    vs = check.voice_similarity(audio, ref) if ref and Path(ref).exists() else None
    fs = check.face_similarity(avatar, pr["photo"]) if avatar else None
    db.set_page(p["id"], voice_sim=vs, face_sim=fs, flag=check.flags(fs, vs))


def process_page(pid, prov=None, log=print):
    p, pr, lex = db.page(pid), db.profile(), db.lexicon()
    if not pr.get("photo"): raise ValueError("请先在「素材建档」上传照片和声音")
    if not pr.get("consent_at"): raise ValueError("请先记录本人授权")
    prov = prov or providers.avatar()
    db.set_page(pid, status="audio", error="")
    audio = tts_page(p, pr, lex)
    p = db.page(pid)
    av = None
    if p["on_camera"]:
        db.set_page(pid, status="avatar")
        av = avatar_page(p, audio, pr, prov)
    db.set_page(pid, status="compose", avatar=str(av or ""))
    clip = compose_page(p, audio, av, getattr(prov, "output", "plain"))
    check_page(p, audio, av, pr)
    db.set_page(pid, status="done", clip=str(clip))
    log(f"第 {p['idx']} 页完成")


# ---------- 批量 ----------
def run(lid, pids=None, log=print, should_stop=lambda: False):
    """断点续跑：已完成且输入没变的页直接跳过；失败的页记录原因后继续下一页；预算不够就整体暂停。"""
    prov = providers.avatar()
    for p in db.pages(lid):
        if pids is not None and p["id"] not in pids: continue
        if should_stop(): log("已手动停止"); return "stopped"
        if p["status"] == "done" and Path(p["clip"] or "-").exists() and _is_current(p): continue
        try:
            process_page(p["id"], prov, log)
        except Paused as e:
            db.set_page(p["id"], status="paused", error=str(e)); log(str(e)); return "paused"
        except Exception as e:
            db.set_page(p["id"], status="failed", error=str(e)[:500]); log(f"第 {p['idx']} 页失败：{e}")
    return "ok"


def _is_current(p):
    """页面的成片是不是由当前讲稿 / 词典 / 参数生成的。"""
    pr, lex = db.profile(), db.lexicon()
    d = page_dir(p["lesson_id"], p["idx"])
    audio = d / f"audio_{_audio_key(p, pr, lex)[:12]}.wav"
    if not audio.exists(): return False
    av = (d / f"avatar_{_avatar_key(audio, pr)[:12]}.mp4") if p["on_camera"] else None
    if av is not None and not av.exists(): return False
    return Path(p["clip"]).name == f"clip_{_clip_key(p, d / 'slide.png', audio, av)[:12]}.mp4"


def stale_pages(lid): return [p for p in db.pages(lid) if p["status"] != "done" or not _is_current(p)]


# ---------- 导出 ----------
def _ts(t): h, r = divmod(t, 3600); m, s = divmod(r, 60); return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"


def export(lid):
    ps = db.pages(lid)
    bad = [p["idx"] for p in ps if p["status"] != "done" or not _is_current(p)]
    if bad: raise ValueError(f"还有页未生成或已过期：{bad}")
    out_dir = lesson_dir(lid) / "out"; out_dir.mkdir(exist_ok=True)
    srt, t, n = [], 0.0, 1
    for p in ps:
        lines = sentences(p["script"]); chars = sum(len(s) for s in lines) or 1
        cur = t
        for s in lines:
            dur = p["audio_s"] * len(s) / chars
            srt.append(f"{n}\n{_ts(cur)} --> {_ts(cur + dur)}\n{s}\n"); n += 1; cur += dur
        t += p["audio_s"] + L["page_pad"]
    title = db.one("select title from lessons where id=?", lid)["title"]
    srt_path = out_dir / f"{title}.srt"; srt_path.write_text("\n".join(srt), "utf-8")
    tmp = out_dir / "_concat.mp4"
    concat([p["clip"] for p in ps], tmp)
    mp4 = out_dir / f"{title}.mp4"
    pr = db.profile()
    ff("-i", tmp, "-i", srt_path, "-map", 0, "-map", 1, "-c", "copy", "-c:s", "mov_text",
       "-metadata:s:s:0", "language=chi", "-metadata", f"title={title}",
       "-metadata", f"comment=AIGC 人工智能生成合成内容；数字人形象与声音经本人授权（{pr.get('consent_at', '')}）；"
                    f"tts={pr.get('tts_model')} avatar={CFG['avatar_provider']}", mp4)
    tmp.unlink()
    return mp4, srt_path
