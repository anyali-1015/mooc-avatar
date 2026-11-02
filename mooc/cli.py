"""命令行：网页界面能做的这里都能做，适合在 AutoDL 终端里后台跑批量。
  python -m mooc.cli sample                    生成样例 PPT / 绿幕底片 / 录音（仅测试用）
  python -m mooc.cli setup 录音 --name 姓名 --consent "授权说明"   建档并复刻声音
  python -m mooc.cli base 底片.mp4              处理底片（只做一次）
  python -m mooc.cli import 课件.pptx --title 第1讲 [--pdf 课件.pdf]
  python -m mooc.cli estimate 1
  python -m mooc.cli run 1 [--pages 3,5] [--reflow]
  python -m mooc.cli export 1
  python -m mooc.cli status                    进度、实测口型速度、剩余 GPU 时间
"""
import argparse, subprocess
from pathlib import Path
from . import db, pipeline
from .conf import ROOT, CFG
from .media import ff


def sample():
    from pptx import Presentation
    from pptx.util import Inches
    from PIL import Image, ImageDraw
    d = ROOT / "samples"; d.mkdir(exist_ok=True)
    prs = Presentation(); prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    data = [("第一讲 数据结构导论", "课程目标\n课程安排\n考核方式",
             "同学们好，欢迎来到数据结构这门课。这一讲我们先说清楚为什么要学数据结构，以及这学期怎么学。"),
            ("什么是数据结构", "数据元素之间的关系\n逻辑结构与存储结构\n算法 = 数据结构 + 操作",
             "数据结构研究的是数据元素之间的关系。同一组数据，可以用线性表来组织，也可以用树或图来组织，选哪种结构，决定了后面的算法好不好写、跑得快不快。"),
            ("课程安排", "每周两次课\n课后作业\n期末考试", "#无人\n这门课每周两次，每次课后有一份作业，期末是闭卷考试。"),
            ("本章小结", "逻辑结构\n存储结构\n下节课：线性表", "")]
    for title, body, notes in data:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = body
        if notes: s.notes_slide.notes_text_frame.text = notes
    prs.save(d / "sample.pptx")
    # 绿幕底片：卡通人像左右轻晃 12 秒，代替妈妈的真实录像
    im = Image.new("RGB", (1080, 1440), (0, 255, 0)); g = ImageDraw.Draw(im)
    g.ellipse((390, 260, 690, 620), fill=(236, 200, 176)); g.rounded_rectangle((260, 640, 820, 1440), 160, fill=(52, 72, 110))
    g.ellipse((470, 400, 500, 430), fill=(40, 40, 40)); g.ellipse((580, 400, 610, 430), fill=(40, 40, 40))
    g.rounded_rectangle((500, 510, 580, 530), 8, fill=(170, 80, 80))
    im.save(d / "frame.png")
    ff("-loop", 1, "-framerate", 25, "-i", d / "frame.png", "-t", 12, "-vf",
       "pad=iw+120:ih:60:0:0x00FF00,crop=1080:1440:'60+50*sin(2*PI*t/4)':0,scale=-2:1080,format=yuv420p",
       "-c:v", "libx264", "-preset", "veryfast", d / "base.mp4")
    subprocess.run(["say", "-v", "Tingting", "-o", str(d / "voice.aiff"), "大家好，我是这门课的老师。今天天气不错，我们开始上课吧。"])
    print("样例已生成：", d)


def status():
    f, measured = pipeline.speed()
    rest = 0.0
    for l in db.q("select * from lessons order by id"):
        ps = db.pages(l["id"]); stale = pipeline.stale_pages(l["id"])
        e = pipeline.estimate(l["id"], [p["id"] for p in stale]); rest += e["gpu_seconds"]
        dur = sum(p["audio_s"] or 0 for p in ps)
        print(f"{l['title']}：{len(ps)} 页 | 完成 {len(ps) - len(stale)} | 时长 {dur / 60:.1f} 分钟 | "
              f"空讲稿 {sum(not p['script'].strip() for p in ps)} | 画外音 {sum(not p['on_camera'] for p in ps)}")
    print(f"\n口型速度：处理 1 秒音频约需 {f:.1f} 秒 GPU（{'实测' if measured else '假设值，跑一页样片后变成实测'}）")
    print(f"剩余预计 {rest / 3600:.1f} 小时 GPU，约 ¥{pipeline.gpu_cost(rest):.0f}；已花 ¥{db.spent():.1f} / 上限 ¥{pipeline.budget():.0f}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("sample"); sp.add_parser("status")
    s = sp.add_parser("setup"); s.add_argument("voice")
    s.add_argument("--name", default="老师"); s.add_argument("--consent", required=True)
    s = sp.add_parser("base"); s.add_argument("video")
    s = sp.add_parser("import"); s.add_argument("pptx"); s.add_argument("--title"); s.add_argument("--pdf")
    for c in ("estimate", "run", "export"):
        s = sp.add_parser(c); s.add_argument("lesson", type=int); s.add_argument("--pages", default="")
        if c == "run": s.add_argument("--reflow", action="store_true", help="重新连续分配底片起点（之后的页重做口型）")
    a = ap.parse_args()
    if a.cmd == "sample": return sample()
    if a.cmd == "status": return status()
    if a.cmd == "setup":
        pipeline.setup_profile(a.name, a.voice, a.consent)
        print("音色 ID：", pipeline.clone_voice()); return
    if a.cmd == "base": return pipeline.prep_base(a.video)
    if a.cmd == "import":
        lid = pipeline.import_pptx(a.pptx, a.title or Path(a.pptx).stem, a.pdf)
        print(f"课时 {lid}，{len(db.pages(lid))} 页"); return
    pids = [p["id"] for p in db.pages(a.lesson) if str(p["idx"]) in a.pages.split(",")] or None
    if a.cmd == "estimate":
        e = pipeline.estimate(a.lesson, pids)
        print(f"总时长 {e['seconds'] / 60:.1f} 分钟，出镜 {e['camera_seconds'] / 60:.1f} 分钟，"
              f"GPU 约 {e['gpu_seconds'] / 3600:.1f} 小时，本次预计 ¥{e['cost']:.1f}；"
              f"已花 ¥{db.spent():.1f} / 上限 ¥{pipeline.budget():.0f}")
    elif a.cmd == "run":
        if a.reflow: pipeline.reflow(a.lesson)
        print(pipeline.run(a.lesson, pids))
    elif a.cmd == "export": print(*pipeline.export(a.lesson), sep="\n")


if __name__ == "__main__": main()
