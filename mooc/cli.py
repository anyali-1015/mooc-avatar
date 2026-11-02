"""命令行：适合放在云服务器后台跑批量。
  python -m mooc.cli sample                    生成样例 PPT / 照片 / 录音（仅测试用）
  python -m mooc.cli setup 照片 录音 --name 姓名 --consent "授权说明"
  python -m mooc.cli import 课件.pptx --title 第1讲 [--pdf 课件.pdf]
  python -m mooc.cli estimate 1
  python -m mooc.cli run 1 [--pages 3,5]
  python -m mooc.cli export 1
"""
import argparse, subprocess
from pathlib import Path
from . import db, pipeline
from .conf import ROOT


def sample():
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from PIL import Image, ImageDraw
    d = ROOT / "samples"; d.mkdir(exist_ok=True)
    prs = Presentation(); prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    data = [("第一讲 数据结构导论", "课程目标\n课程安排\n考核方式",
             "同学们好，欢迎来到数据结构这门课。这一讲我们先说清楚为什么要学数据结构，以及这学期怎么学。"),
            ("什么是数据结构", "数据元素之间的关系\n逻辑结构与存储结构\n算法 = 数据结构 + 操作",
             "数据结构研究的是数据元素之间的关系。同一组数据，可以用线性表来组织，也可以用树或图来组织，选哪种结构，决定了后面的算法好不好写、跑得快不快。"),
            ("本章小结", "逻辑结构\n存储结构\n下节课：线性表", "")]
    for title, body, notes in data:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = body
        if notes: s.notes_slide.notes_text_frame.text = notes
    prs.save(d / "sample.pptx")
    im = Image.new("RGB", (900, 1200), (214, 222, 230)); g = ImageDraw.Draw(im)
    g.ellipse((300, 180, 600, 540), fill=(236, 200, 176)); g.rounded_rectangle((170, 560, 730, 1200), 160, fill=(52, 72, 110))
    g.ellipse((380, 320, 410, 350), fill=(40, 40, 40)); g.ellipse((490, 320, 520, 350), fill=(40, 40, 40))
    im.save(d / "photo.jpg")
    subprocess.run(["say", "-v", "Tingting", "-o", str(d / "voice.aiff"), "大家好，我是这门课的老师。今天天气不错，我们开始上课吧。"])
    print("样例已生成：", d)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("sample")
    s = sp.add_parser("setup"); s.add_argument("photo"); s.add_argument("voice")
    s.add_argument("--name", default="老师"); s.add_argument("--consent", required=True)
    s = sp.add_parser("import"); s.add_argument("pptx"); s.add_argument("--title"); s.add_argument("--pdf")
    for c in ("estimate", "run", "export"):
        s = sp.add_parser(c); s.add_argument("lesson", type=int); s.add_argument("--pages", default="")
    a = ap.parse_args()
    if a.cmd == "sample": return sample()
    if a.cmd == "setup":
        pipeline.setup_profile(a.name, a.photo, a.voice, a.consent)
        print("音色 ID：", pipeline.clone_voice()); return
    if a.cmd == "import":
        lid = pipeline.import_pptx(a.pptx, a.title or Path(a.pptx).stem, a.pdf)
        print(f"课时 {lid}，{len(db.pages(lid))} 页"); return
    pids = [p["id"] for p in db.pages(a.lesson) if str(p["idx"]) in a.pages.split(",")] or None
    if a.cmd == "estimate":
        e = pipeline.estimate(a.lesson, pids)
        print(f"总时长 {e['seconds'] / 60:.1f} 分钟，出镜 {e['camera_seconds'] / 60:.1f} 分钟，"
              f"本次预计 ¥{e['cost']:.0f}；已花 ¥{db.spent():.0f} / 上限 ¥{pipeline.budget():.0f}")
    elif a.cmd == "run": print(pipeline.run(a.lesson, pids))
    elif a.cmd == "export": print(*pipeline.export(a.lesson), sep="\n")


if __name__ == "__main__": main()
