# 数字人慕课制作工具

自用的本地流水线：**一页 PPT = 一段讲稿 = 一段音频 = 一段数字人视频**，音画对齐由音频时长直接决定。成片是 1080p MP4（内嵌软字幕 + AIGC 元数据）加 SRT，自己上传到慕课平台。

## 运行

```bash
cd ~/Downloads/mooc-avatar
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/streamlit run app.py          # 打开 http://localhost:8501
```

默认是**演示模式**（`config.yaml` 里 `tts_provider` / `avatar_provider` 都是 `mock`）：声音用 macOS「婷婷」，数字人用照片轻微晃动，不花钱，用来跑通流程。`python -m mooc.cli sample` 可以生成一套测试素材。

## 操作步骤（界面左侧 ①–⑥）

1. **素材建档**：上传正面半身照、10–20 秒干净录音，记录本人授权，复刻声音（只做一次）。
2. **导入 PPT**：上传 .pptx，最好同时上传同一份导出的 PDF（背景版式最准）。讲稿优先取备注。
3. **讲稿与试听**：逐页校对讲稿、试听；关键页开「数字人出镜」，其余页画外音可降本。
4. **读法词典**：多音字写拼音（`chong2 fu4`），术语写替换读法（`SQL` → `S Q L`）。
5. **批量生成**：显示待生成时长和预计费用，设预算上限；断点续跑，超预算自动暂停。
6. **预览与导出**：逐页预览，一致性检查标出的页可以单独重做；导出整节。

批量也可用命令行（适合放云服务器后台跑）：`python -m mooc.cli run 1`，见 `mooc/cli.py`。

## 切换到真实接口

1. `cp .env.example .env`，填入百炼 API Key、火山引擎 AK/SK、OSS 密钥。
2. `config.yaml`：`tts_provider: cosyvoice`、`avatar_provider: omnihuman`、`storage: oss`；按商务确认的结果改 `avatar.max_audio_seconds`、`avatar.output`（能出绿幕就改 `greenscreen`）、`price.avatar_cny_per_second`。
3. 先做 3 分钟样片验收，再放量。

**注意**：CosyVoice / OmniHuman 的适配代码按公开文档写成，还没有用真实账号跑过；OmniHuman 的 `req_key` 也要以开通后的官方文档为准。第一次接入时预计要按实际返回微调 `mooc/providers.py`。

## 结构

| 文件 | 作用 |
|---|---|
| `config.yaml` | 所有锁定参数：模型版本、语速音量、版式、单价、预算、检查阈值 |
| `mooc/providers.py` | 适配层：声音（mock / CosyVoice）、数字人（mock / OmniHuman），换供应商只改这里 |
| `mooc/pipeline.py` | 建档、导入、逐页配音 → 数字人 → 合成 → 检查、预算、导出 |
| `mooc/ppt.py` | python-pptx 读文字与备注；PDF → LibreOffice → Keynote（默认关）→ 文字版兜底 |
| `mooc/fonts.conf` | LibreOffice 转换用的字体配置：macOS 上不加它中文会整段丢失；微软雅黑、宋体等 Windows 字体映射到 macOS 自带字体 |
| `mooc/check.py` | 一致性粗检（形象缩略图相关 / 声音频带特征），可替换成人脸识别、说话人识别模型 |
| `mooc/db.py` | SQLite：每页状态、费用账本、词典 |
| `app.py` | Streamlit 操作界面 |

每一步产物按输入哈希命名缓存在 `data/lessons/<课时>/pages/<页>/`：讲稿、词典、参数没变就直接复用，不重复计费；改了哪页只重做哪页。
