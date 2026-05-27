# XimalayaDecrypt

将喜马拉雅客户端下载的离线音频解密、转码，并支持语音转文字。

## 功能

| 脚本 | 功能 |
|------|------|
| `decrypt.py` | 解密 `.xm` / `.x2m`，或将 M4A 等音频转为 MP3 |
| `transcribe.py` | 将 MP3 等音频转写为文字（基于 Whisper） |

## 项目简介

喜马拉雅官方客户端下载的音频通常无法直接在普通播放器中播放，常见形式包括：

| 来源 | 文件特征 | 说明 |
|------|----------|------|
| 手机端 | `.xm` / `.x2m` | 带 ID3 头的专有加密格式 |
| PC / 旧版客户端 | 无后缀或 `.m4a`，文件名为 MD5 哈希 | 部分为标准 M4A 容器，可直接转码 |

本项目的 `decrypt.py` 会自动识别输入文件格式，完成解密（如需要）并输出 MP3；`transcribe.py` 可将 MP3 等音频离线转写为中文文本。

> 仅供个人学习与研究，请勿用于商业或侵权用途。

## 分析过程

以仓库中的样本文件 `233a661da3c1766f999bd1fb270d2fa6` 为例，分析步骤如下。

### 1. 识别文件类型

```bash
file 233a661da3c1766f999bd1fb270d2fa6
# ISO Media, Apple iTunes ALAC/AAC-LC (.M4A) Audio
```

查看文件头可见标准 MP4/M4A 结构（`ftyp M4A`），而非 `.xm` 格式的 `ID3` 头。因此该样本属于**可直接解码的 M4A**，无需 AES 解密。

### 2. 验证音频可解码性

使用 `ffprobe` / `ffmpeg` 检测，样本为 HE-AACv2、44100 Hz 立体声，ffmpeg 可正常解码并转码。

### 3. XM 加密格式原理

手机端 `.xm` / `.x2m` 文件以 ID3v2 保存元数据，音频主体经多阶段加密。解密流程（参考 [aynakeya 的逆向分析](http://www.aynakeya.com/articles/ctf/reverse-with-puring-guessing-ximalaya/)）如下：

```
┌─────────────────────────────────────────────────────────┐
│  ID3 头（标题、专辑、trackId、IV、加密参数等）            │
├─────────────────────────────────────────────────────────┤
│  加密音频数据                                            │
└─────────────────────────────────────────────────────────┘
         │
         ▼  Stage 1：AES-256-CBC
         │  Key: ximalayaximalayaximalayaximalaya
         │  IV:  ID3 中的 TSRC 或 TENC 字段
         ▼
         ▼  Stage 2：Base64 解码 → AES-192-CBC → 得到 Base64 片段
         │  Key: 123456781234567812345678 前缀填充 + trackId
         │  IV:  Key 的前 16 字节
         ▼
         ▼  Stage 3：ID3 中 TSSE 字段 + Stage 2 结果拼接后 Base64 解码
         ▼
    明文音频（m4a / mp3 / flac 等）
         │
         ▼  ffmpeg 转码
       MP3 输出
```

本实现使用纯 Python（`pycryptodome`）完成上述 AES 步骤，**不依赖 WebAssembly**。

### 4. 脚本处理逻辑

```
输入文件
    │
    ├─ 以 ID3 开头 ──► XM 解密 ──► 临时明文音频 ──► ffmpeg ──► MP3
    │
    └─ ftyp / MP3 / FLAC ──► ffprobe 验证 ──► ffmpeg ──► MP3
```

## 环境要求

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/)（含 `ffprobe`，需在 PATH 中可用）
- 语音转文字功能首次运行会从 Hugging Face 下载 Whisper 模型（需联网）

Arch Linux 安装示例：

```bash
sudo pacman -S ffmpeg
```

有 NVIDIA GPU 时，`transcribe.py` 会自动尝试使用 CUDA 加速。

## 安装

```bash
git clone <本仓库地址>
cd XimalayaDecrypt

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 使用方法

### 基本用法

```bash
python decrypt.py <输入文件> [-o 输出.mp3] [-b 码率]
```

### 示例

**转换无后缀 M4A 缓存文件（本仓库样本）：**

```bash
python decrypt.py 233a661da3c1766f999bd1fb270d2fa6
# 输出：output/233a661da3c1766f999bd1fb270d2fa6.mp3
```

**解密手机端 XM 文件（自动以 ID3 标题命名）：**

```bash
python decrypt.py ~/Downloads/某专辑/001.xm
# 输出：output/标题.mp3
```

**指定输出路径与码率：**

```bash
python decrypt.py input.x2m -o ~/Music/播客.mp3 -b 320k
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `input` | 输入文件路径（必填） |
| `-o`, `--output` | 输出 MP3 路径；未指定时写入 `output/` 目录 |
| `-b`, `--bitrate` | MP3 码率，默认 `192k` |

### 语音转文字

使用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 在本地进行语音识别，默认语言为中文。

```bash
python transcribe.py <音频文件> [-o 输出.txt] [选项]
```

**转写 MP3 为纯文本：**

```bash
python transcribe.py output/233a661da3c1766f999bd1fb270d2fa6.mp3
# 输出：output/233a661da3c1766f999bd1fb270d2fa6.txt，同时在终端打印全文
```

**指定输出路径，并生成 SRT 字幕：**

```bash
python transcribe.py output/audio.mp3 -o output/文稿.txt --srt
# 字幕默认：output/audio.srt
```

**完整流程：解密 → 转 MP3 → 转文字**

```bash
python decrypt.py 233a661da3c1766f999bd1fb270d2fa6
python transcribe.py output/233a661da3c1766f999bd1fb270d2fa6.mp3
```

**转写参数：**

| 参数 | 说明 |
|------|------|
| `input` | 输入音频路径（必填，支持 mp3 / m4a / wav 等） |
| `-o`, `--output` | 输出文本路径，默认 `output/` 下与输入同名的 `.txt` |
| `--srt` | 输出 SRT 字幕；不跟路径时写入 `output/` 目录 |
| `-m`, `--model` | Whisper 模型：`tiny` / `base` / `small` / `medium` / `large-v3`，默认 `small` |
| `-l`, `--language` | 语言代码，默认 `zh` |
| `--device` | 推理设备：`auto` / `cpu` / `cuda`，默认 `auto` |
| `-t`, `--timestamps` | 在文本中附带每段起始时间戳 |
| `--model-path` | 本地模型目录，跳过在线下载 |
| `--hf-endpoint` | Hugging Face 镜像，如 `https://hf-mirror.com` |

模型越大识别越准，但速度更慢、占用更多内存/显存。中文播客推荐 `small` 或 `medium`。

## 项目结构

```
XimalayaDecrypt/
├── decrypt.py          # 解密 / 转 MP3
├── transcribe.py       # 语音转文字
├── requirements.txt    # Python 依赖
├── output/             # 默认输出目录（MP3、TXT、SRT）
└── README.md
```

## 常见问题

**Q: 提示「ffprobe 无法识别音频流」？**

文件可能已损坏，或使用了尚未支持的加密变体。请确认文件来自喜马拉雅客户端且下载完整。

**Q: XM 文件解密失败？**

确认文件以 `ID3` 开头且包含完整的 ID3 元数据（TIT2、TSIZ、TSSE 等）。部分新版格式若与已知算法不兼容，需进一步分析。

**Q: PC 端下载的 M4A 还需要解密吗？**

部分 PC 端文件已是标准 M4A，脚本会直接转 MP3；若 ffprobe 无法识别，则说明可能仍为加密格式，需确认具体客户端版本与文件来源。

**Q: 语音转文字首次运行很慢？**

首次使用会从 Hugging Face 下载 Whisper 模型（体积约 150 MB～3 GB，取决于 `-m` 选项），下载完成后离线可用。

**Q: 提示 SSL / 无法连接 Hugging Face？**

这是模型下载时的网络问题。脚本已改为单线程下载以避免并发 SSL 错误。若仍失败，可尝试：

```bash
# 使用国内镜像
python transcribe.py output/audio.mp3 --hf-endpoint https://hf-mirror.com

# 或设置环境变量
export HF_ENDPOINT=https://hf-mirror.com
python transcribe.py output/audio.mp3
```

也可手动下载模型后离线使用：

```bash
huggingface-cli download Systran/faster-whisper-small --local-dir ./models/faster-whisper-small
python transcribe.py output/audio.mp3 --model-path ./models/faster-whisper-small
```

**Q: 转写结果不准确？**

可尝试更大的模型（如 `-m medium`），或确认音频本身清晰、背景噪声较少。

## 参考

- [Reversing WebAssembly with pure guesswork - Ximalaya xm encryption](http://www.aynakeya.com/articles/ctf/reverse-with-puring-guessing-ximalaya/) — XM 加密算法分析
- [sld272/Ximalaya-XM-Decrypt](https://github.com/sld272/Ximalaya-XM-Decrypt) — 社区同类工具（使用 wasm 实现）
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) — 本地语音识别引擎
