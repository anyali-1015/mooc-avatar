"""对象存储：数字人接口要从公网链接读取图片和音频。"""
from pathlib import Path
from .conf import CFG, env, file_hash


def public_url(path, prefix="mooc"):
    """上传并返回带时效的签名链接。local 模式只返回本地路径（仅 mock 可用）。"""
    if CFG["storage"] == "local": return Path(path).resolve().as_uri()
    if CFG["storage"] == "oss":
        import oss2
        b = oss2.Bucket(oss2.Auth(env("OSS_ACCESS_KEY_ID"), env("OSS_ACCESS_KEY_SECRET")),
                        env("OSS_ENDPOINT"), env("OSS_BUCKET"))
        key = f"{prefix}/{file_hash(path)[:16]}{Path(path).suffix}"
        if not b.object_exists(key): b.put_object_from_file(key, str(path))
        return b.sign_url("GET", key, 3600 * 6)
    raise ValueError(f"未知 storage：{CFG['storage']}")
