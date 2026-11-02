"""本地操作界面：看进度、试听、预览、重做某一页。  运行：streamlit run app.py"""
import tempfile, threading
from pathlib import Path
import pandas as pd
import streamlit as st
from mooc import db, pipeline, script
from mooc.conf import CFG, page_dir

st.set_page_config(page_title="数字人慕课制作", page_icon="🎓", layout="wide")


@st.cache_resource
def worker(): return {"thread": None, "stop": False, "log": [], "result": ""}


W = worker()
def busy(): return W["thread"] is not None and W["thread"].is_alive()


def save_upload(f, suffix=None):
    p = Path(tempfile.mkdtemp()) / (f.name if not suffix else "x" + suffix)
    p.write_bytes(f.getbuffer()); return p


def start(lid, pids=None):
    W.update(stop=False, log=[], result="")
    def go():
        try: W["result"] = pipeline.run(lid, pids, log=W["log"].append, should_stop=lambda: W["stop"])
        except Exception as e: W["result"] = f"出错：{e}"
    W["thread"] = threading.Thread(target=go, daemon=True); W["thread"].start()


STATUS = {"new": "⚪ 未生成", "audio": "🔵 配音中", "avatar": "🔵 数字人生成中", "compose": "🔵 合成中",
          "done": "🟢 完成", "failed": "🔴 失败", "paused": "🟡 暂停（预算）"}

# ---------- 侧栏 ----------
pr = db.profile()
lessons = db.q("select * from lessons order by id")
with st.sidebar:
    st.title("🎓 数字人慕课")
    mode = "真实接口" if CFG["avatar_provider"] != "mock" else "演示模式（mock，不花钱）"
    st.caption(f"{mode}\n\n声音：{CFG['tts_provider']} · 数字人：{CFG['avatar_provider']}")
    view = st.radio("步骤", ["① 素材建档", "② 导入 PPT", "③ 讲稿与试听", "④ 读法词典", "⑤ 批量生成", "⑥ 预览与导出"])
    lid = None
    if lessons:
        names = {l["id"]: l["title"] for l in lessons}
        lid = st.selectbox("当前课时", list(names), format_func=names.get, index=len(lessons) - 1)
    st.divider()
    st.metric("已花费", f"¥{db.spent():,.2f}", help="按 config.yaml 里的单价记账，以供应商账单为准")
    st.caption(f"预算上限 ¥{pipeline.budget():,.0f}")
    if busy(): st.info("后台生成中…")

# ---------- ① 素材建档 ----------
if view == "① 素材建档":
    st.header("素材建档")
    st.write("全程只用**一张固定参考图**和**一次声音复刻**。照片：正面、光线均匀、半身、衣着固定；录音：安静环境 10–20 秒。")
    c1, c2 = st.columns(2)
    with c1:
        if pr.get("photo"): st.image(pr["photo"], width=260, caption="当前参考图")
        photo = st.file_uploader("参考照片", type=["jpg", "jpeg", "png"])
    with c2:
        if pr.get("voice_sample"): st.audio(pr["voice_sample"]); st.caption("当前声音样本")
        voice = st.file_uploader("声音样本", type=["wav", "mp3", "m4a", "aiff"])
    name = st.text_input("出镜教师", pr.get("name") or "")
    consent_text = st.text_area("授权说明", pr.get("consent_text") or
        f"本人同意使用我的照片和声音，生成课程「{CFG.get('course', '')}」的数字人讲课视频；"
        "知悉素材会上传至阿里云、火山引擎用于生成，课程完成后删除。")
    ok = st.checkbox("本人已阅读并同意上述授权", value=bool(pr.get("consent_at")))
    if st.button("保存素材", type="primary", disabled=not (ok and name and (photo or pr.get("photo")) and (voice or pr.get("voice_sample")))):
        pipeline.setup_profile(name, save_upload(photo) if photo else pr["photo"],
                               save_upload(voice) if voice else pr["voice_sample"], consent_text)
        st.success("已保存"); st.rerun()
    if pr.get("consent_at"): st.caption(f"授权记录于 {pr['consent_at']}")
    st.subheader("声音复刻（只做一次）")
    if pr.get("voice_id"):
        st.write(f"音色 ID：`{pr['voice_id']}` · 模型：`{pr.get('tts_model')}`")
        if pr.get("ref_audio"): st.audio(pr["ref_audio"]); st.caption("锁定参数的参考句，一致性检查以它为准")
        st.warning("更换音色会让已生成的所有页失效、需重新付费生成。")
    if st.button("复刻声音" if not pr.get("voice_id") else "重新复刻", disabled=not pr.get("voice_sample")):
        with st.spinner("复刻中…"):
            try: pipeline.clone_voice(); st.rerun()
            except Exception as e: st.error(str(e))

# ---------- ② 导入 PPT ----------
elif view == "② 导入 PPT":
    st.header("导入 PPT")
    st.write("讲稿优先取 PPT 备注。背景图建议同时上传 PowerPoint / WPS 导出的 **PDF**，版式最准；"
             "否则用 LibreOffice 转换，都没有时只生成文字版背景。")
    f = st.file_uploader("课件 .pptx", type=["pptx"])
    pdf = st.file_uploader("同一课件导出的 PDF（推荐）", type=["pdf"])
    title = st.text_input("课时名称", Path(f.name).stem if f else "")
    if st.button("导入", type="primary", disabled=not (f and title)):
        with st.spinner("解析与渲染中…"):
            new = pipeline.import_pptx(save_upload(f), title, save_upload(pdf) if pdf else None)
        st.success(f"已导入 {len(db.pages(new))} 页"); st.rerun()
    if lessons:
        st.subheader("已有课时")
        st.dataframe(pd.DataFrame([{"课时": l["title"], "页数": len(db.pages(l["id"])),
                                    "背景来源": l["render"], "导入时间": l["created_at"]} for l in lessons]), hide_index=True)
        if lid and db.one("select render from lessons where id=?", lid)["render"] == "text":
            st.warning("当前课时的背景是文字版。补传 PDF 可替换背景（不会重做配音和数字人）。")
            pdf2 = st.file_uploader("补传 PDF", type=["pdf"], key="pdf2")
            if pdf2 and st.button("替换背景"):
                st.success(f"已用 {pipeline.rerender(lid, save_upload(pdf2))} 替换"); st.rerun()

elif not lid:
    st.info("先在「② 导入 PPT」导入课件。")

# ---------- ③ 讲稿与试听 ----------
elif view == "③ 讲稿与试听":
    ps = db.pages(lid)
    st.header("讲稿与试听")
    total = sum(script.est_seconds(p["script"]) for p in ps)
    st.caption(f"{len(ps)} 页 · 讲稿 {sum(len(p['script']) for p in ps)} 字 · 估计 {total / 60:.1f} 分钟 · "
               f"出镜 {sum(p['on_camera'] for p in ps)} 页。关键页（开场、小结、重点）出镜，其余画外音可降本。")
    for p in ps:
        with st.container(border=True):
            c1, c2 = st.columns([2, 3])
            d = page_dir(lid, p["idx"])
            with c1:
                st.image(str(d / "slide.png"))
                st.caption(f"第 {p['idx']} 页 · {STATUS.get(p['status'], p['status'])}")
                cam = st.toggle("数字人出镜", bool(p["on_camera"]), key=f"cam{p['id']}")
                if cam != bool(p["on_camera"]): db.set_page(p["id"], on_camera=int(cam)); st.rerun()
            with c2:
                if not p["script"].strip(): st.warning("本页没有备注，需要起草讲稿")
                txt = st.text_area("讲稿", p["script"], key=f"s{p['id']}", height=160,
                                   help=f"约 {script.est_seconds(p['script']):.0f} 秒")
                b1, b2, b3 = st.columns(3)
                if txt != p["script"] and b1.button("保存", key=f"save{p['id']}", type="primary"):
                    db.set_page(p["id"], script=txt); st.rerun()
                if b2.button("试听", key=f"tts{p['id']}", disabled=not p["script"].strip()):
                    with st.spinner("合成中…"):
                        try: pipeline.tts_page(db.page(p["id"])); st.rerun()
                        except Exception as e: st.error(str(e))
                if CFG["llm"]["enabled"] and b3.button("AI 起草", key=f"llm{p['id']}"):
                    prev = next((x["script"] for x in ps if x["idx"] == p["idx"] - 1), "")
                    with st.spinner("起草中…"):
                        try: db.set_page(p["id"], script=script.draft(p["title"], p["text"], prev)); st.rerun()
                        except Exception as e: st.error(str(e))
                if p["audio"] and Path(p["audio"]).exists():
                    st.audio(p["audio"]); st.caption(f"{p['audio_s']:.1f} 秒")
                with st.expander("PPT 原文"): st.text(f"{p['title']}\n{p['text']}")

# ---------- ④ 读法词典 ----------
elif view == "④ 读法词典":
    st.header("读法词典")
    st.write("多音字和专业术语的读法。**读法**写带声调数字的拼音（如 `chong2 fu4`）时按拼音读；"
             "写其他文字时直接替换朗读（如 `SQL` → `S Q L`）。字幕仍显示原文。改动只会让包含该词的页重做配音。")
    lex = pd.DataFrame(db.q("select term as 词, reading as 读法 from lexicon order by term")) \
        if db.lexicon() else pd.DataFrame({"词": pd.Series(dtype=str), "读法": pd.Series(dtype=str)})
    ed = st.data_editor(lex, num_rows="dynamic", hide_index=True, width="stretch")
    if st.button("保存词典", type="primary"):
        db.run("delete from lexicon")
        for _, r in ed.dropna().iterrows():
            if str(r["词"]).strip(): db.run("insert or replace into lexicon values(?,?)", str(r["词"]).strip(), str(r["读法"]).strip())
        st.success("已保存")

# ---------- ⑤ 批量生成 ----------
elif view == "⑤ 批量生成":
    st.header("批量生成")
    stale = pipeline.stale_pages(lid)
    e = pipeline.estimate(lid, [p["id"] for p in stale])
    c = st.columns(4)
    c[0].metric("待生成页", f"{len(stale)} / {len(db.pages(lid))}")
    c[1].metric("待生成时长", f"{e['seconds'] / 60:.1f} 分钟", f"出镜 {e['camera_seconds'] / 60:.1f} 分钟", delta_color="off")
    c[2].metric("本次预计费用", f"¥{e['cost']:,.0f}")
    c[3].metric("预算剩余", f"¥{pipeline.budget() - db.spent():,.0f}")
    b = st.number_input("总预算上限（元）", 0.0, value=pipeline.budget(), step=100.0)
    if b != pipeline.budget(): db.set_kv("budget", b); st.rerun()
    if db.spent() + e["cost"] > pipeline.budget():
        st.warning("按预估会超出预算：生成到上限时会自动暂停。可以减少出镜页或调高上限。")
    empty = [p["idx"] for p in stale if not p["script"].strip()]
    if empty: st.error(f"第 {empty} 页讲稿为空，会失败。先去「③ 讲稿与试听」补上。")
    if not pr.get("consent_at"): st.error("还没有素材和本人授权，先去「① 素材建档」。")
    s1, s2 = st.columns(2)
    if s1.button("▶ 开始 / 继续生成", type="primary", disabled=busy() or not stale or not pr.get("consent_at")):
        start(lid); st.rerun()
    if s2.button("■ 停止", disabled=not busy()): W["stop"] = True

    @st.fragment(run_every=2)
    def progress():
        ps = db.pages(lid)
        done = sum(p["status"] == "done" for p in ps)
        st.progress(done / max(len(ps), 1), f"{done}/{len(ps)} 页完成" + ("（运行中）" if busy() else ""))
        st.dataframe(pd.DataFrame([{
            "页": p["idx"], "状态": STATUS.get(p["status"], p["status"]), "出镜": "✓" if p["on_camera"] else "画外音",
            "时长(秒)": round(p["audio_s"] or 0, 1), "形象相似": p["face_sim"] and round(p["face_sim"], 2),
            "声音相似": p["voice_sim"] and round(p["voice_sim"], 2), "提示": p["flag"] or p["error"]} for p in ps]),
            hide_index=True, width="stretch")
        if W["log"]: st.code("\n".join(W["log"][-12:]))
        if W["result"] and not busy(): st.caption(f"上次运行结果：{W['result']}")
    progress()

# ---------- ⑥ 预览与导出 ----------
elif view == "⑥ 预览与导出":
    st.header("预览与导出")
    ps = db.pages(lid)
    flagged = [p for p in ps if p["flag"]]
    if flagged: st.warning("一致性检查标出的页：" + "，".join(f"第 {p['idx']} 页（{p['flag']}）" for p in flagged))
    for p in ps:
        with st.expander(f"第 {p['idx']} 页 · {p['title'] or ''} · {STATUS.get(p['status'], p['status'])}" + (f" · ⚠ {p['flag']}" if p["flag"] else "")):
            if p["clip"] and Path(p["clip"]).exists(): st.video(p["clip"])
            if p["error"]: st.error(p["error"])
            if st.button("重做这一页", key=f"redo{p['id']}", disabled=busy(),
                         help="删掉本页数字人缓存后重新生成，会再计一次费"):
                for f in page_dir(lid, p["idx"]).glob("avatar_*.mp4"): f.unlink()
                for f in page_dir(lid, p["idx"]).glob("clip_*.mp4"): f.unlink()
                db.set_page(p["id"], status="new"); start(lid, [p["id"]]); st.toast("已开始重做")
    st.divider()
    stale = pipeline.stale_pages(lid)
    if stale: st.info(f"还有 {len(stale)} 页未生成或讲稿已改动，先到「⑤ 批量生成」。")
    if st.button("导出整节 MP4 + SRT", type="primary", disabled=bool(stale) or busy()):
        with st.spinner("拼接中…"):
            try: st.session_state["out"] = [str(x) for x in pipeline.export(lid)]
            except Exception as e: st.error(str(e))
    if st.session_state.get("out"):
        mp4, srt = map(Path, st.session_state["out"])
        st.video(str(mp4), subtitles=str(srt))
        st.caption(f"已保存到 {mp4.parent}")
        d1, d2 = st.columns(2)
        d1.download_button("下载 MP4", mp4.read_bytes(), mp4.name, "video/mp4")
        d2.download_button("下载 SRT", srt.read_bytes(), srt.name, "text/plain")
