# Whisper 字幕生成 & 双语翻译工具

基于 faster-whisper 的本地字幕生成工具，支持 GPU 加速、自动切分字幕、以及调用硅基流动 API 生成中英双语字幕（含表情）。

---

## 功能

- 🎬 支持拖拽或浏览选择视频/音频文件
- ⚡ GPU 加速转写（CUDA）
- ✂️ 按字符数自动切分字幕，避免一行太长
- 🌐 调用硅基流动 API 翻译为中英双语字幕
- 😄 每行自动添加表情符号
- 💾 配置自动保存，模型路径/API Key 只需填一次
- 📁 输出文件名自动简化（视频名前8个词 + 日期）

---

## 环境要求

- Windows 10/11 64位
- Python 3.11（推荐，3.12+ 部分依赖不兼容）
- NVIDIA 显卡（可选，CPU 模式也可运行）

---

## 安装步骤

### 1. 安装 Python 3.11

前往 https://www.python.org/downloads/release/python-31115/ 下载 **Windows installer (64-bit)**。

安装时勾选 **"Add Python to PATH"**，安装完成后点击 **"Disable path length limit"**。

### 2. 安装依赖

```
pip install faster-whisper srt_equalizer srt tkinterdnd2
```

### 3. 下载 Whisper 模型

前往 Hugging Face 下载 faster-whisper 格式的模型，推荐：

- 小模型（快）：https://huggingface.co/Systran/faster-whisper-small
- 大模型（准）：https://huggingface.co/Systran/faster-whisper-large-v3

下载整个文件夹，记住路径，填入软件的"模型路径"。

---

## GPU 加速配置（CUDA）

如果不配置 CUDA，软件会自动回退到 CPU 模式，速度较慢。

### 问题背景

Windows 上通过 pip 安装的 `nvidia-cublas` 和 `nvidia-cudnn` 库路径不会自动被系统识别，需要手动提供 DLL 文件。

### 解决方案

**第一步**：前往以下地址下载 CUDA 库文件包：

https://github.com/Purfview/whisper-standalone-win/releases/tag/libs

根据你的情况下载对应版本：
- `cuBLAS.and.cuDNN_CUDA11_win_v4.7z` — 适用于大多数情况（包含 `cudnn_ops_infer64_8.dll`）
- `cuBLAS.and.cuDNN_CUDA12_win_v3.7z` — 如果你安装了 CUDA 12.x Toolkit

**第二步**：解压后，将所有 `.dll` 文件复制到你的 **FasterWhisperGUI 安装目录**（或任意已在 PATH 中的目录，如 `C:\Windows\System32`）。

**第三步**：在 `启动.bat` 中确保包含了 FasterWhisperGUI 目录路径：

```bat
set PATH=%PATH%;C:\software\fasterwhisperGUI
```

### 精度选择

| 精度 | 显存占用 | 速度 | 说明 |
|------|----------|------|------|
| float16 | 中 | 快 | 需要较新的 NVIDIA 显卡 |
| int8 | 低 | 快 | 推荐，兼容性最好 |
| float32 | 高 | 慢 | 精度最高，显存要求高 |
| cpu / int8 | — | 慢 | 无 GPU 时使用 |

---

## 双语翻译配置

翻译功能调用 [硅基流动](https://siliconflow.cn) API，需要注册并获取 API Key。

1. 前往 https://siliconflow.cn 注册账号
2. 在控制台生成 API Key
3. 填入软件的"API Key"输入框，自动保存

支持的模型（可在界面下拉选择，也可手动输入新模型名后按回车保存）：

- `deepseek-ai/DeepSeek-V4-Flash`（快，推荐）
- `deepseek-ai/DeepSeek-V3.2`
- `deepseek-ai/DeepSeek-V3.1-Terminus`
- `Qwen/Qwen3.6-35B-A3B`
- `Qwen/Qwen3.6-27B`
- `MiniMaxAI/MiniMax-M2.5`

---

## 使用方法

1. 双击 `启动.bat` 打开界面
2. 拖拽视频文件到拖拽区，或点击"浏览文件"选择
3. 填写/选择 Whisper 模型路径
4. 设置设备（cuda/cpu）、精度、语言、每段最大字符数
5. 如需双语字幕，勾选"启用双语翻译"，填入 API Key，选择翻译模型
6. 点击"▶ 开始"

输出文件保存在视频同目录（或指定目录），文件名格式：

```
视频名前8个词_YYYY-MM-DD_英文.srt
视频名前8个词_YYYY-MM-DD_双语.srt
```

---

## 文件说明

```
transcribe_gui.py   主程序
启动.bat            Windows 启动脚本
config.json         自动生成的配置文件（保存路径、API Key 等）
README.md           本文档
```

---

## 常见问题

**Q: 报错 `cublas64_12.dll is not found`**  
A: 见上方"GPU 加速配置"，下载对应 DLL 文件放入 FasterWhisperGUI 目录。

**Q: 报错 `cudnn_ops_infer64_8.dll is not found`**  
A: 下载 `cuBLAS.and.cuDNN_CUDA11_win_v4.7z`，解压后复制 DLL 到 FasterWhisperGUI 目录。

**Q: 翻译超时**  
A: 程序会自动重试 3 次，如果仍然失败可换更快的模型（如 `DeepSeek-V4-Flash`）或缩小每批翻译行数。

**Q: 字幕断句太长**  
A: 调小"最大字符数"，默认 42，可以改成 35 试试。

**Q: pip 不是内部命令**  
A: Python 安装时未勾选 Add to PATH，手动在 cmd 执行：
```
set PATH=%PATH%;C:\Users\你的用户名\AppData\Local\Programs\Python\Python311
set PATH=%PATH%;C:\Users\你的用户名\AppData\Local\Programs\Python\Python311\Scripts
```
