#!/usr/bin/env bash
# 在 AutoDL 上启动网页：bash start.sh
# 之后在 AutoDL 控制台点「自定义服务」打开 6006 端口对应的地址，用 APP_PASSWORD 登录。
# 关掉终端也会继续运行；停止：bash start.sh stop
set -e
cd "$(dirname "$0")"
PORT=${PORT:-6006}
if [ "$1" = "stop" ]; then pkill -f "streamlit run app.py" && echo 已停止; exit 0; fi

[ -f .env ] && set -a && source .env && set +a
[ -n "$APP_PASSWORD" ] || { echo "请先在 .env 里设置 APP_PASSWORD（网页登录密码）"; exit 1; }

export DATA_DIR=${DATA_DIR:-/root/autodl-tmp/mooc-data}        # 素材、成片、数据库都在数据盘，关机不丢
export TTS_PROVIDER=${TTS_PROVIDER:-cosyvoice}
export LIPSYNC_ENGINE=${LIPSYNC_ENGINE:-latentsync}
mkdir -p "$DATA_DIR"

pkill -f "streamlit run app.py" 2>/dev/null || true
nohup streamlit run app.py --server.port "$PORT" --server.address 0.0.0.0 --server.headless true \
  --server.maxUploadSize 2000 --browser.gatherUsageStats false > "$DATA_DIR/web.log" 2>&1 &
sleep 3
echo "已启动（端口 $PORT，日志 $DATA_DIR/web.log）。到 AutoDL 控制台「自定义服务」打开网页。"
