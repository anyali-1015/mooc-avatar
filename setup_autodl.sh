#!/usr/bin/env bash
# 在 AutoDL 的 RTX 4090 实例上运行一次：bash setup_autodl.sh
# 装系统工具、本项目依赖、LatentSync 及其权重。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA=/root/autodl-tmp            # AutoDL 数据盘，关机后保留

# AutoDL 的学术加速（访问 GitHub / HuggingFace 更快）；没有就跳过
if [ -f /etc/network_turbo ]; then source /etc/network_turbo; fi

echo "== 1/4 系统工具：LibreOffice、中文字体 =="
apt-get update -y
apt-get install -y libreoffice-impress fonts-noto-cjk espeak-ng

echo "== 2/4 本项目依赖（装在基础环境） =="
pip install -r "$HERE/requirements.txt"

echo "== 3/4 下载 LatentSync =="
cd "$DATA"
[ -d LatentSync ] || git clone https://github.com/bytedance/LatentSync.git
cd LatentSync

echo "== 4/4 安装 LatentSync 环境和权重（官方脚本） =="
export HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com}   # 国内用 HuggingFace 镜像
source /root/miniconda3/etc/profile.d/conda.sh
source setup_env.sh
if [ ! -f checkpoints/latentsync_unet.pt ] || [ ! -f checkpoints/whisper/tiny.pt ]; then
  pip install -U "huggingface_hub[cli]"
  huggingface-cli download ByteDance/LatentSync-1.6 latentsync_unet.pt whisper/tiny.pt --local-dir checkpoints
fi

echo
echo "== 安装完成。请核对以下三项，与 config.yaml 的 lipsync 部分对上 =="
echo "python：     $(conda run -n latentsync which python 2>/dev/null || echo '没找到 latentsync 环境，请看 setup_env.sh 实际建的环境名')"
echo "unet 配置：  $(ls configs/unet/ | tr '\n' ' ')"
echo "推理参数："; conda run -n latentsync python -m scripts.inference --help || true
echo
echo "下一步：cp .env.example .env，填 DASHSCOPE_API_KEY 和 APP_PASSWORD，然后 bash start.sh"
