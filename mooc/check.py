"""一致性检查：把每段视频抽帧与参考图比、每段音频与参考声音比，明显偏离的标出来重做。

当前是轻量的内置粗检（不需要 GPU / 额外模型）：
- 形象：灰度缩略图的归一化相关系数，能抓到“换了人 / 画面崩坏 / 构图大变”，抓不到细微的脸部走样；
- 声音：对数频带能量的统计特征余弦相似度，能抓到“换了音色 / 噪声 / 静音”，区分不了相近的人声。
需要更准时，可以替换 face_similarity / voice_similarity 为开源人脸识别（如 InsightFace）
和说话人识别（如 ECAPA-TDNN）的向量余弦相似度，接口不变。阈值在 config.yaml，需用样片实测后定。
"""
import numpy as np
from PIL import Image, ImageOps
from .conf import CFG
from .media import frames, wav_array


def _gray(im, size=(48, 64)):
    a = np.asarray(ImageOps.fit(im.convert("L"), size), dtype=np.float32).ravel()
    return (a - a.mean()) / (a.std() + 1e-6)


def face_similarity(video, photo):
    ref = _gray(Image.open(photo))
    fs = frames(video, 3)
    if not fs: return 0.0
    return float(np.mean([max(0.0, float(np.dot(ref, _gray(f)) / len(ref))) for f in fs]))


def _voice_vec(wav, bands=40):
    a, sr = wav_array(wav)
    n, hop = 1024, 512
    if len(a) < n * 4: return np.zeros(bands * 2)
    fr = np.lib.stride_tricks.sliding_window_view(a, n)[::hop] * np.hanning(n)
    spec = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    edges = np.geomspace(60, min(8000, sr / 2), bands + 1) / (sr / 2) * (n // 2)
    e = np.stack([spec[:, int(lo):max(int(hi), int(lo) + 1)].sum(1) for lo, hi in zip(edges[:-1], edges[1:])], 1)
    e = np.log(e + 1e-9)
    voiced = e.sum(1) > np.percentile(e.sum(1), 40)  # 去掉静音帧
    e = e[voiced] - e[voiced].mean(1, keepdims=True)
    return np.concatenate([e.mean(0), e.std(0)])


def voice_similarity(wav, ref):
    a, b = _voice_vec(wav), _voice_vec(ref)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def flags(face, voice):
    c, out = CFG["check"], []
    if face is not None and face < c["face_min_similarity"]: out.append(f"形象偏离 {face:.2f}")
    if voice is not None and voice < c["voice_min_similarity"]: out.append(f"声音偏离 {voice:.2f}")
    return "；".join(out)
