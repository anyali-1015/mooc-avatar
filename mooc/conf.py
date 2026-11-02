"""配置与路径。config.yaml 是锁定参数的唯一来源，密钥来自环境变量 / .env。"""
import hashlib, json, os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    f = ROOT / ".env"
    if not f.exists(): return
    for line in f.read_text("utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()
CFG = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
DATA = (ROOT / CFG["data_dir"]).resolve()
DATA.mkdir(parents=True, exist_ok=True)


def env(k, required=True):
    v = os.environ.get(k, "")
    if required and not v: raise RuntimeError(f"缺少环境变量 {k}（见 .env.example）")
    return v


def sha(*parts):
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, Path): p = file_hash(p)
        elif not isinstance(p, str): p = json.dumps(p, sort_keys=True, ensure_ascii=False)
        h.update(p.encode("utf-8")); h.update(b"\0")
    return h.hexdigest()


def file_hash(p):
    p = Path(p)
    if not p.exists(): return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def lesson_dir(lid): d = DATA / "lessons" / str(lid); d.mkdir(parents=True, exist_ok=True); return d
def page_dir(lid, idx): d = lesson_dir(lid) / "pages" / f"{idx:03d}"; d.mkdir(parents=True, exist_ok=True); return d
def profile_dir(): d = DATA / "profile"; d.mkdir(parents=True, exist_ok=True); return d
