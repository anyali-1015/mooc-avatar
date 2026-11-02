"""讲稿：读法词典、断句出字幕、（可选）大模型起草。"""
import re
import requests
from .conf import CFG, env

PINYIN = re.compile(r"^[a-zü]+[1-5](\s+[a-zü]+[1-5])*$", re.I)


def apply_lexicon(text, lex, ssml=False):
    """词典项：读法是带声调数字的拼音（如 "chong2 qing4"）时，SSML 模式输出 <phoneme>；
    否则当作替换文本（如 "SQL" → "S Q L"、"μ" → "缪"）。字幕仍用原文。"""
    for term, reading in lex.items():
        if not term: continue
        if PINYIN.match(reading.strip()):
            rep = f'<phoneme alphabet="py" ph="{reading.strip()}">{term}</phoneme>' if ssml else term
        else:
            rep = reading
        text = text.replace(term, rep)
    return f"<speak>{text}</speak>" if ssml and "<phoneme" in text else text


def sentences(text, max_len=22):
    """按句号等断句，过长的再按逗号切，作为字幕行。"""
    out = []
    for s in re.split(r"(?<=[。！？!?；;\n])", text):
        s = s.strip()
        if not s: continue
        while len(s) > max_len:
            cut = max((s.rfind(c, 0, max_len) for c in "，,、："), default=-1)
            cut = cut + 1 if cut > 4 else max_len
            out.append(s[:cut].strip()); s = s[cut:].strip()
        if s: out.append(s)
    return out


def est_seconds(text): return len(re.sub(r"\s", "", text)) / CFG["tts"]["chars_per_minute"] * 60


def draft(title, text, prev_script=""):
    """用通义千问起草本页讲稿（需开启 llm.enabled 和 DASHSCOPE_API_KEY）。结果只是草稿，必须人工校对。"""
    c = CFG["llm"]
    prompt = (f"你是一位大学教师，正在为慕课录制讲解。请为下面这页 PPT 写一段口语化讲稿，"
              f"约 {int(CFG['tts']['chars_per_minute'] * 1.5)} 字，不要念标题编号，不要使用 markdown。\n"
              f"上一页讲稿结尾：{prev_script[-80:]}\n本页标题：{title}\n本页内容：\n{text}")
    r = requests.post(c["base_url"] + "/chat/completions", timeout=120,
                      headers={"Authorization": "Bearer " + env("DASHSCOPE_API_KEY")},
                      json={"model": c["model"], "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


NO_AVATAR = "#无人"


def strip_marker(text):
    """讲稿首行写 #无人 的页只配音不出镜：返回 (是否出镜, 去掉标记的讲稿)。"""
    lines = (text or "").strip().splitlines()
    if lines and lines[0].strip() == NO_AVATAR: return False, "\n".join(lines[1:]).strip()
    return True, (text or "").strip()


def subtitle_cues(sent, start, end, max_chars):
    """一句话按逗号拆成不超过 max_chars 的字幕行，时间按字数比例分配。"""
    parts = sentences(sent.rstrip("。；;"), max_chars) or [sent]
    total = sum(len(p) for p in parts) or 1
    out, t = [], start
    for p in parts:
        d = (end - start) * len(p) / total
        out.append((t, t + d, p)); t += d
    return out
