# 数字人慕课制作工具

妈妈录一段底片，生成时只按新配音重画嘴部，身体、手势、背景都是她的真实画面。
**一页 PPT = 一段讲稿 = 一段音频 = 一段口型视频**：PPT 做背景，妈妈抠像后站在右下角，配上她的复刻声音和字幕，导出整节课 MP4 + SRT。

- 声音：阿里云百炼 CosyVoice v3-plus 复刻（样本 Base64 直传，不需要对象存储）
- 口型：[LatentSync 1.6](https://github.com/bytedance/LatentSync)（字节开源），跑在 AutoDL 按小时租的 RTX 4090 上
- 网页：Streamlit，在 AutoDL 上通过「自定义服务」直接用浏览器打开，有登录密码

## 一、准备素材（只做一次）

**底片视频（3–10 分钟，越长手势越不重复）**
- 正面半身，绿幕，光线均匀，固定衣着，不要穿绿色衣服
- 头不要大幅转动，手不要挡脸；正常讲话，内容随意（嘴部会被重画）
- 中间留几段闭嘴微笑、点头；1080P、25fps

**声音样本**：安静房间里朗读 10–20 秒，录 3–5 段，试听后挑最像的一段。

**PPT**：讲稿写在每页备注里最省事；最好同时导出一份 PDF 一起上传，背景版式最准。

录之前请跟妈妈说清楚用途，取得她同意。

## 二、在 AutoDL 上部署

1. 在 [AutoDL](https://www.autodl.com/) 实名、充值，按量租一台 **RTX 4090（24GB）**，镜像选 PyTorch + Ubuntu。抢不到卡可用 Featurize。
2. 在实例的 JupyterLab 终端里：
   ```bash
   cd /root/autodl-tmp
   git clone https://github.com/anyali-1015/mooc-avatar.git   # 私有仓库，需要 GitHub 令牌；也可以把文件夹直接上传
   cd mooc-avatar
   bash setup_autodl.sh
   ```
3. 核对安装脚本最后打印的三项（python 路径、unet 配置文件名、推理参数名），和 `config.yaml` 的 `lipsync` 部分对上。参数名不同就改 `command` 模板。
4. 填密钥并启动网页：
   ```bash
   cp .env.example .env    # 填 DASHSCOPE_API_KEY 和 APP_PASSWORD
   bash start.sh
   ```
5. 在 AutoDL 控制台点该实例的 **「自定义服务」**，打开 6006 端口的地址，用 `APP_PASSWORD` 登录。手机和别的电脑都能打开。

素材、成片和数据库都放在数据盘 `/root/autodl-tmp/mooc-data`，关机不丢。**用完记得关机**，按秒计费；下次开机后 `bash start.sh` 即可。
配音、解析 PPT、合成都不需要 GPU；AutoDL 的「无卡模式开机」更便宜，可以先用它准备讲稿，跑口型时再切回有卡。

## 三、操作步骤（网页左侧 ①–⑥）

1. **素材建档**：上传底片并处理（做成正放倒放无缝循环），上传声音样本，记录本人授权，复刻声音。
2. **导入 PPT**：上传 .pptx（和 PDF），讲稿自动取备注。
3. **讲稿与试听**：逐页校对讲稿、试听；只配音不出镜的页关掉「出镜」，或讲稿首行写 `#无人`，省 GPU。
4. **读法词典**：多音字写拼音（`chong2 fu4`），术语写替换读法（`SQL` → `S Q L`），字幕仍显示原文。
5. **批量生成**：看预计 GPU 小时和费用，设预算上限；中断后接着跑，超预算自动暂停。
6. **预览与导出**：逐页预览，一致性检查标出的页可以单独重做；导出整节。

底片太大、网页上传慢时，用 JupyterLab 把视频传到服务器，再在终端运行 `python -m mooc.cli base 底片.mp4`。
所有步骤也都有命令行（`python -m mooc.cli --help`），`python -m mooc.cli status` 显示实测口型速度和剩余 GPU 时间。

## 四、先打样再放量

1. 先用一节课的 2–3 页跑通，让妈妈看像不像、嘴型自然不自然。
2. 不满意就调 `config.yaml` 里 `lipsync.steps`（越高越清晰越慢）和 `guidance`（越高口型越准，过高会抖），在「预览与导出」里重做那一页。
3. 「批量生成」页会把假设的口型速度换成实测值，据此估算 300 分钟的总 GPU 费用。
4. 满意后整门课都不要再换声音模型、音色、底片和 LatentSync 版本，这是稳定的关键。

## 五、本机试跑（不花钱）

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m mooc.cli sample      # 生成样例 PPT、绿幕底片、录音
.venv/bin/streamlit run app.py           # 打开 http://localhost:8501
```

默认 `tts_provider: mock`（macOS 婷婷 / Linux espeak-ng 发声）、`lipsync.engine: none`（直接用底片、不重画嘴），只为跑通流程。`start.sh` 会把两者切到 `cosyvoice` / `latentsync`。

## 六、结构

| 文件 | 作用 |
|---|---|
| `config.yaml` | 锁定参数：声音模型与语速、LatentSync 版本与步数、版式、单价、预算、检查阈值 |
| `mooc/providers.py` | 适配层：CosyVoice（复刻 + 合成）、LatentSync（重画嘴部） |
| `mooc/pipeline.py` | 底片循环、逐句配音、底片时间轴、口型、合成、检查、预算、导出 |
| `mooc/ppt.py` / `fonts.conf` | PPT 读文字与备注、渲染背景（PDF → LibreOffice → 文字版） |
| `mooc/check.py` | 一致性粗检（形象缩略图相关 / 声音频带特征） |
| `mooc/db.py` | SQLite：每页状态、底片起点、GPU 用时、账本、词典 |
| `app.py` | 网页界面（设置 `APP_PASSWORD` 后需要登录） |
| `setup_autodl.sh` / `start.sh` | AutoDL 安装、启动网页 |

每一步产物按输入哈希缓存在 `<数据目录>/lessons/<课时>/pages/<页>/`：改了哪页只重做哪页。

## 七、已知限制

- **LatentSync 和 CosyVoice 还没有用真实 GPU / 账号跑过**，命令模板和接口按公开文档写成，第一次上机可能要按实际报错微调。
- 口型模型只处理脸部区域，唇形、唇色细节可能和本人略有差别，要靠样片判断。
- 底片正放、倒放交替循环，手势会重复，也会出现倒放的动作；底片越长越不明显。
- 改稿后某页时长变了，下一页动作会有一次小跳变；介意就点「动作重新连续」（之后的页要重做口型）。
- 绿幕边缘若有绿边，调大 `layout.chroma_similarity`。
- 一致性检查是粗检，能抓到明显跑偏，抓不到细微走样。
- GPU 费用按每页实际推理时间记账，开机空闲的时间不算在内，以 AutoDL 账单为准。
