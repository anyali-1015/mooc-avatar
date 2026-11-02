"""PPT 导入：python-pptx 读每页文字与备注；渲染成 1080p 背景图。
渲染优先级：用户导出的 PDF → LibreOffice → Keynote（macOS）→ Pillow 文字版兜底（只有文字，没有原版式，仅供跑通流程）。"""
import os, shutil, subprocess, tempfile
from pathlib import Path
from pptx import Presentation
from PIL import Image, ImageDraw
from .conf import CFG
from .media import font

W, H = CFG["layout"]["width"], CFG["layout"]["height"]


def parse(pptx):
    out = []
    for i, s in enumerate(Presentation(pptx).slides, 1):
        title = s.shapes.title.text_frame.text.strip() if s.shapes.title is not None and s.shapes.title.has_text_frame else ""
        texts = []
        for sh in s.shapes:
            if sh.has_text_frame and sh != s.shapes.title:
                t = sh.text_frame.text.strip()
                if t: texts.append(t)
            if getattr(sh, "has_table", False) and sh.has_table:
                texts += [" | ".join(c.text for c in r.cells) for r in sh.table.rows]
        notes = s.notes_slide.notes_text_frame.text.strip() if s.has_notes_slide and s.notes_slide.notes_text_frame else ""
        out.append({"idx": i, "title": title, "text": "\n".join(texts), "notes": notes})
    return out


def _pdf_to_png(pdf, dsts):
    import pymupdf as fitz
    doc = fitz.open(pdf)
    for page, dst in zip(doc, dsts):
        pix = page.get_pixmap(matrix=fitz.Matrix(W / page.rect.width, W / page.rect.width))
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        _fit(im).save(dst)
    return len(doc) >= len(dsts)


def _fit(im):
    """等比缩放后居中放在 1920×1080 白底上。"""
    im = im.convert("RGB"); im.thumbnail((W, H), Image.LANCZOS)
    bg = Image.new("RGB", (W, H), "white"); bg.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
    return bg


def _soffice(pptx, td):
    exe = shutil.which("soffice") or shutil.which("libreoffice") or \
        ("/Applications/LibreOffice.app/Contents/MacOS/soffice" if Path("/Applications/LibreOffice.app").exists() else None)
    if not exe: return None
    # headless 模式用自带 fontconfig，看不到系统中文字体；fonts.conf 把系统字体目录和中文回退加进去
    env = {**os.environ, "FONTCONFIG_FILE": str(Path(__file__).with_name("fonts.conf"))}
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", td, str(pptx)], capture_output=True, timeout=600, env=env)
    pdf = Path(td) / (Path(pptx).stem + ".pdf")
    return pdf if pdf.exists() else None


def _keynote(pptx, td):
    if not Path("/Applications/Keynote.app").exists(): return None
    pdf = Path(td) / "keynote.pdf"
    script = f'''with timeout of 240 seconds
    tell application id "com.apple.iWork.Keynote"
      open POSIX file "{Path(pptx).resolve()}"
      repeat 60 times
        if (count of documents) > 0 then exit repeat
        delay 1
      end repeat
      set d to front document
      export d to POSIX file "{pdf}" as PDF
      close d saving no
    end tell
    end timeout'''
    try: subprocess.run(["osascript", "-e", script], capture_output=True, timeout=300)
    except subprocess.TimeoutExpired: return None
    return pdf if pdf.exists() else None


def _text_slide(p, dst):
    im = Image.new("RGB", (W, H), (250, 249, 246)); d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, 14), fill=(40, 70, 140))
    d.text((120, 110), p["title"] or f"第 {p['idx']} 页", font=font(72), fill=(30, 30, 30))
    y = 260
    for line in (p["text"] or "").splitlines()[:12]:
        d.text((140, y), line[:40], font=font(44), fill=(60, 60, 60)); y += 70
    im.save(dst)


def render(pptx, slides, dsts, pdf=None):
    """返回实际使用的渲染方式。pdf：用户从 PowerPoint / WPS / Keynote 导出的 PDF，版式最准，优先用。"""
    if pdf and _pdf_to_png(pdf, dsts): return "pdf"
    with tempfile.TemporaryDirectory() as td:
        for name, fn in (("libreoffice", _soffice), ("keynote", _keynote if CFG.get("ppt_keynote") else lambda *a: None)):
            pdf = fn(pptx, td)
            if pdf and _pdf_to_png(pdf, dsts): return name
    for p, dst in zip(slides, dsts): _text_slide(p, dst)
    return "text"
