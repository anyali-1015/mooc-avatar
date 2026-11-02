"""流水线：一页 PPT = 一段讲稿 = 一段音频 = 一段口型视频。
底片是妈妈本人的真实画面，只按新配音重画嘴部；每页接着上一页结尾截取底片，翻页时动作连续。
每一步的产物按输入的哈希命名缓存：输入没变就直接复用，不重复花钱；改了哪页只重做哪页。"""
import json, shutil
from pathlib import Path
import numpy as np
from . import db, ppt
from .conf import CFG, sha, file_hash, lesson_dir, page_dir, profile_dir
from .media import ff, concat, to_wav, write_wav, wav_array, wav_seconds, video_seconds, watermark_png
from .script import apply_lexicon, sentences, est_seconds, strip_marker, subtitle_cues
from . import providers, check

L, T, LS, P = CFG["layout"], CFG["tts"], CFG["lipsync"], CFG["price"]


class Paused(Exception): pass


# ---------- 素材建档 ----------
REF_SENTENCE = "同学们好，欢迎来到这门课。今天我们先回顾上节课的重点，再进入新的内容。"


def setup_profile(name, voice_src, consent_text):
    sample = profile_dir() / "voice_sample.wav"
    to_wav(voice_src, sample)
    db.set_profile(name=name, voice_sample=str(sample), consent_text=consent_text, consent_at=db.now())


def clone_voice():
    """只复刻一次：音色 ID 和模型版本存进 profile，之后整门课都用它；再生成一条锁定参数的参考句供一致性检查。"""
    pr = db.profile()
    t = providers.tts()
    vid = t.enroll(pr["voice_sample"])
    ref = profile_dir() / "ref_sentence.wav"
    raw = ref.with_suffix(".raw.wav")
    providers.tts(vid).synth(REF_SENTENCE, raw); to_wav(raw, ref); raw.unlink()
    db.set_profile(voice_id=vid, tts_model=t.model, ref_audio=str(ref))
    db.charge("tts", len(REF_SENTENCE), len(REF_SENTENCE) / 1e4 * P["tts_cny_per_10k_chars"], note="参考句")
    return vid


def prep_base(video_src, log=print):
    """底片统一成 25fps、去音轨，再接上倒放做成无缝循环：正放 → 倒放 → 正放……"""
    d = profile_dir() / "base"; shutil.rmtree(d, ignore_errors=True); d.mkdir()
    fwd, loop, fps = d / "forward.mp4", d / "loop.mp4", CFG["base"]["fps"]
    log("统一底片格式")
    ff("-i", video_src, "-an", "-r", fps, "-vf", f"scale=-2:{CFG['base']['height']},setsar=1,format=yuv420p",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", 16, "-force_key_frames", "expr:gte(t,n_forced*10)", fwd)
    seg = d / "seg"; seg.mkdir()  # 分成 10 秒小段逐段倒放，避免整段倒放占满内存
    ff("-i", fwd, "-c", "copy", "-f", "segment", "-segment_time", 10, "-reset_timestamps", 1, seg / "s%04d.mp4")
    parts = sorted(seg.glob("s*.mp4"))
    log(f"生成倒放片段（{len(parts)} 段）")
    for p in parts:
        ff("-i", p, "-vf", "reverse", "-c:v", "libx264", "-preset", "veryfast", "-crf", 16, p.with_name("r" + p.name[1:]))
    concat([fwd] + [p.with_name("r" + p.name[1:]) for p in reversed(parts)], loop, copy=False)
    shutil.rmtree(seg)
    ref = d / "ref_frame.png"; ff("-ss", 1, "-i", fwd, "-frames:v", 1, ref)
    db.set_profile(base_video=str(fwd), base_loop=str(loop), loop_sec=video_seconds(loop),
                   base_hash=file_hash(fwd), ref_frame=str(ref))
    log(f"底片就绪：正放 {video_seconds(fwd):.0f} 秒，循环 {video_seconds(loop):.0f} 秒")


# ---------- PPT 导入 ----------
def import_pptx(pptx_src, title, pdf=None):
    lid = db.run("insert into lessons(title,created_at) values(?,?)", title, db.now())
    d = lesson_dir(lid); pptx = d / "source.pptx"; shutil.copy(pptx_src, pptx)
    slides = ppt.parse(pptx)
    how = ppt.render(pptx, slides, [page_dir(lid, s["idx"]) / "slide.png" for s in slides], pdf)
    db.run("update lessons set pptx=?, render=? where id=?", str(pptx), how, lid)
    for s in slides:
        cam, body = strip_marker(s["notes"])
        db.run("insert into pages(lesson_id,idx,title,text,notes,script,on_camera) values(?,?,?,?,?,?,?)",
               lid, s["idx"], s["title"], s["text"], s["notes"], body, int(cam))
    return lid


def save_script(pid, text):
    cam, body = strip_marker(text)
    db.set_page(pid, script=body, **({} if cam else {"on_camera": 0}))


def rerender(lid, pdf):
    """导入后补传 PDF，替换背景图（只影响合成，不会重做配音和口型）。"""
    ps = db.pages(lid)
    slides = [{"idx": p["idx"], "title": p["title"], "text": p["text"]} for p in ps]
    how = ppt.render(db.one("select pptx from lessons where id=?", lid)["pptx"], slides,
                     [page_dir(lid, p["idx"]) / "slide.png" for p in ps], pdf)
    db.run("update lessons set render=? where id=?", how, lid)
    return how


# ---------- 缓存键 ----------
def _audio_key(p, pr, lex):
    return sha(p["script"], {k: v for k, v in lex.items() if k in p["script"]}, T, CFG["tts_provider"], pr.get("voice_id"))

def _lip_key(audio, pr, offset):
    return sha(Path(audio).name, pr.get("base_hash"), round(offset or 0, 3),
               {k: LS[k] for k in ("engine", "version", "steps", "guidance", "ckpt", "unet_config")})

def _clip_key(slide, audio, lip):
    return sha(Path(slide), Path(audio).name, Path(lip).name if lip else "voice-over", L)

def _audio_path(p, pr, lex): return page_dir(p["lesson_id"], p["idx"]) / f"audio_{_audio_key(p, pr, lex)[:12]}.wav"


# ---------- 底片时间轴 ----------
def assign_offsets(lid, reflow=False):
    """每页的底片起点 = 上一页起点 + 上一页时长，动作在页与页之间连续。
    已分配的起点不变（改稿只影响这一页）；reflow 时整节重新排，后面的页都要重做口型。"""
    les = db.one("select * from lessons where id=?", lid)
    start = les["start_cursor"]
    if start is None:  # 新课时接着上一节课的底片位置往下走
        start = float(db.kv("base_cursor", 0)); db.run("update lessons set start_cursor=? where id=?", start, lid)
    cur = start
    for p in db.pages(lid):
        if not p["audio_s"]: continue
        if reflow or p["base_offset"] is None:
            db.set_page(p["id"], base_offset=round(cur, 3)); p["base_offset"] = cur
        cur = p["base_offset"] + p["audio_s"]
    db.set_kv("base_cursor", max(float(db.kv("base_cursor", 0)), cur))


def cut_base(pr, offset, dur, out):
    ff("-stream_loop", -1, "-ss", f"{offset % pr['loop_sec']:.3f}", "-i", pr["base_loop"], "-t", f"{dur:.3f}",
       "-an", "-r", CFG["base"]["fps"], "-c:v", "libx264", "-preset", "veryfast", "-crf", 16, out)


# ---------- 预算 ----------
def speed():
    """处理 1 秒音频需要几秒 GPU：有实测用实测，没有用配置里的假设值。"""
    r = db.one("select sum(gpu_s) g, sum(audio_s) a from pages where gpu_s > 0")
    return (r["g"] / r["a"], True) if r and r["a"] else (float(P["gpu_speed_guess"]), False)


def budget(): return float(db.kv("budget", P["budget_cny"]))
def gpu_cost(gpu_s): return gpu_s / 3600 * P["gpu_cny_per_hour"]


def estimate(lid, pids=None):
    """只统计还没生成过（缓存里没有）的部分，这才是这次真正要花的钱。"""
    pr, lex, f = db.profile(), db.lexicon(), speed()[0]
    tot_s = cam_s = gpu_s = cost = 0.0
    for p in db.pages(lid):
        if pids is not None and p["id"] not in pids: continue
        audio = _audio_path(p, pr, lex)
        secs = wav_seconds(audio) if audio.exists() else est_seconds(p["script"])
        tot_s += secs
        if not audio.exists(): cost += len(p["script"]) / 1e4 * P["tts_cny_per_10k_chars"]
        if p["on_camera"] and L["avatar_mode"] != "none":
            cam_s += secs
            lip = page_dir(lid, p["idx"]) / f"lip_{_lip_key(audio, pr, p['base_offset'])[:12]}.mp4"
            if LS["engine"] != "none" and not (audio.exists() and p["base_offset"] is not None and lip.exists()):
                gpu_s += secs * f
    return {"seconds": tot_s, "camera_seconds": cam_s, "gpu_seconds": gpu_s, "cost": cost + gpu_cost(gpu_s)}


# ---------- 单页 ----------
def tts_page(p, pr=None, lex=None):
    """逐句合成再拼接：每句的起止时间是精确的，字幕直接用。"""
    pr, lex = pr or db.profile(), lex if lex is not None else db.lexicon()
    if not p["script"].strip(): raise ValueError("讲稿为空")
    audio = _audio_path(p, pr, lex); tl = audio.with_suffix(".json")
    if not (audio.exists() and tl.exists()):
        sr, prov = T["sample_rate"], providers.tts(pr.get("voice_id"))
        gap, tail = np.zeros(int(sr * T["sentence_pause_ms"] / 1000)), np.zeros(int(sr * T["page_pause_ms"] / 1000))
        chunks, timeline, t = [], [], 0.0
        for i, s in enumerate(sentences(p["script"], 120)):
            part = audio.with_name(f"{audio.stem}_s{i}.wav")
            prov.synth(apply_lexicon(s, lex, CFG["tts_provider"] == "cosyvoice"), part)
            a, _ = wav_array(part); part.unlink()
            timeline.append({"text": s, "start": round(t, 3), "end": round(t + len(a) / sr, 3)})
            chunks += [a, gap]; t += (len(a) + len(gap)) / sr
        chunks[-1] = tail
        raw = audio.with_suffix(".raw.wav"); write_wav(np.concatenate(chunks), sr, raw)
        to_wav(raw, audio); raw.unlink()  # 整页统一响度
        tl.write_text(json.dumps(timeline, ensure_ascii=False), "utf-8")
        db.charge("tts", len(p["script"]), len(p["script"]) / 1e4 * P["tts_cny_per_10k_chars"], p["lesson_id"], p["id"])
    db.set_page(p["id"], audio=str(audio), audio_s=wav_seconds(audio), timeline=tl.read_text("utf-8"))
    return audio


def lip_page(p, audio, pr, lip):
    d = page_dir(p["lesson_id"], p["idx"])
    out = d / f"lip_{_lip_key(audio, pr, p['base_offset'])[:12]}.mp4"
    if out.exists(): return out
    secs = wav_seconds(audio)
    if lip.engine != "none":
        cost = gpu_cost(secs * speed()[0])
        if db.spent() + cost > budget():
            raise Paused(f"预算不足：已花 ¥{db.spent():.1f}，本页预计 ¥{cost:.1f}，上限 ¥{budget():.0f}")
    clip = d / "base_clip.mp4"
    cut_base(pr, p["base_offset"], secs, clip)
    gpu = lip.make(clip, audio, out)
    clip.unlink()
    if gpu: db.charge("gpu", gpu, gpu_cost(gpu), p["lesson_id"], p["id"], f"LatentSync {LS['version']}")
    db.set_page(p["id"], gpu_s=gpu)
    return out


def compose_page(p, audio, lip):
    d = page_dir(p["lesson_id"], p["idx"]); slide = d / "slide.png"
    out = d / f"clip_{_clip_key(slide, audio, lip)[:12]}.mp4"
    if out.exists(): return out
    W, H, M = L["width"], L["height"], L["avatar_margin"]
    total = wav_seconds(audio)
    inputs = ["-loop", 1, "-framerate", L["fps"], "-i", slide, "-i", audio]
    fc = [f"[0]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:white,setsar=1,format=yuv420p[bg]"]
    last = "bg"
    if lip:
        inputs += ["-i", lip]
        key = L["avatar_mode"] == "chromakey"
        x = f"main_w-overlay_w-{M}" if L["avatar_side"] == "right" else str(M)
        y = "main_h-overlay_h" if key else f"main_h-overlay_h-{M}"  # 抠像后站在画面底边，小窗留边距
        fc.append(f"[2]scale=-2:{L['avatar_height']},setsar=1" +
                  (f",chromakey={L['chroma_color']}:{L['chroma_similarity']}:{L['chroma_blend']},despill=type=green" if key else "") +
                  ",format=yuva420p[av]")
        fc.append(f"[{last}][av]overlay=x={x}:y={y}:eof_action=repeat[v1]"); last = "v1"
    if L.get("watermark"):
        wm = lesson_dir(p["lesson_id"]) / "watermark.png"
        if not wm.exists(): watermark_png(L["watermark"], wm)
        inputs += ["-i", wm]
        fc.append(f"[{last}][{3 if lip else 2}]overlay=30:30[v2]"); last = "v2"
    ff(*inputs, "-filter_complex", ";".join(fc), "-map", f"[{last}]", "-map", "1:a", "-t", f"{total:.3f}",
       "-r", L["fps"], "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-ar", 48000, "-ac", 2, out)
    return out


def check_page(p, audio, lip, pr):
    ref = pr.get("ref_audio") or pr.get("voice_sample")
    vs = check.voice_similarity(audio, ref) if ref and Path(ref).exists() else None
    fs = check.face_similarity(lip, pr["ref_frame"]) if lip and pr.get("ref_frame") else None
    db.set_page(p["id"], voice_sim=vs, face_sim=fs, flag=check.flags(fs, vs))


def _wants_avatar(p): return bool(p["on_camera"]) and L["avatar_mode"] != "none"


def process_page(pid, lip=None, log=print):
    p, pr, lex = db.page(pid), db.profile(), db.lexicon()
    if not pr.get("consent_at"): raise ValueError("请先在「素材建档」记录本人授权")
    if _wants_avatar(p) and not pr.get("base_loop"): raise ValueError("请先在「素材建档」上传并处理底片")
    lip = lip or providers.LipSync()
    db.set_page(pid, status="audio", error="")
    audio = tts_page(p, pr, lex)
    assign_offsets(p["lesson_id"])
    p = db.page(pid)
    v = None
    if _wants_avatar(p):
        db.set_page(pid, status="avatar")
        v = lip_page(p, audio, pr, lip)
    db.set_page(pid, status="compose", avatar=str(v or ""))
    clip = compose_page(p, audio, v)
    check_page(p, audio, v, pr)
    db.set_page(pid, status="done", clip=str(clip))
    log(f"第 {p['idx']} 页完成")


# ---------- 批量 ----------
def run(lid, pids=None, log=print, should_stop=lambda: False):
    """断点续跑：已完成且输入没变的页直接跳过；失败的页记录原因后继续下一页；预算不够就整体暂停。"""
    lip = providers.LipSync()
    for p in db.pages(lid):
        if pids is not None and p["id"] not in pids: continue
        if should_stop(): log("已手动停止"); return "stopped"
        if p["status"] == "done" and Path(p["clip"] or "-").exists() and _is_current(p): continue
        try:
            process_page(p["id"], lip, log)
        except Paused as e:
            db.set_page(p["id"], status="paused", error=str(e)); log(str(e)); return "paused"
        except Exception as e:
            db.set_page(p["id"], status="failed", error=str(e)[:500]); log(f"第 {p['idx']} 页失败：{e}")
    return "ok"


def reflow(lid):
    """让整节动作重新连续：重排底片起点，之后的页会重做口型。"""
    assign_offsets(lid, reflow=True)


def _is_current(p):
    """页面的成片是不是由当前讲稿 / 词典 / 底片 / 参数生成的。"""
    pr, lex = db.profile(), db.lexicon()
    d = page_dir(p["lesson_id"], p["idx"])
    audio = _audio_path(p, pr, lex)
    if not audio.exists(): return False
    lip = None
    if _wants_avatar(p):
        if p["base_offset"] is None: return False
        lip = d / f"lip_{_lip_key(audio, pr, p['base_offset'])[:12]}.mp4"
        if not lip.exists(): return False
    return Path(p["clip"] or "-").name == f"clip_{_clip_key(d / 'slide.png', audio, lip)[:12]}.mp4"


def stale_pages(lid): return [p for p in db.pages(lid) if p["status"] != "done" or not _is_current(p)]


# ---------- 导出 ----------
def _ts(t):
    ms = int(round(t * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def export(lid):
    ps = db.pages(lid)
    bad = [p["idx"] for p in ps if p["status"] != "done" or not _is_current(p)]
    if bad: raise ValueError(f"还有页未生成或已过期：{bad}")
    out_dir = lesson_dir(lid) / "out"; out_dir.mkdir(exist_ok=True)
    cues, t = [], 0.0
    for p in ps:
        for s in json.loads(p["timeline"] or "[]"):
            cues += subtitle_cues(s["text"], t + s["start"], t + s["end"], L["subtitle_max_chars"])
        t += p["audio_s"]
    title = db.one("select title from lessons where id=?", lid)["title"]
    srt = out_dir / f"{title}.srt"
    srt.write_text("".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{x}\n\n" for i, (a, b, x) in enumerate(cues, 1)), "utf-8")
    tmp = out_dir / "_concat.mp4"
    concat([p["clip"] for p in ps], tmp)
    mp4 = out_dir / f"{title}.mp4"
    pr = db.profile()
    ff("-i", tmp, "-i", srt, "-map", 0, "-map", 1, "-c", "copy", "-c:s", "mov_text",
       "-metadata:s:s:0", "language=chi", "-metadata", f"title={title}",
       "-metadata", f"comment=AIGC 人工智能生成合成内容；形象与声音经本人授权（{pr.get('consent_at', '')}）；"
                    f"tts={pr.get('tts_model')} lipsync={LS['engine']} {LS['version']}", mp4)
    tmp.unlink()
    return mp4, srt
