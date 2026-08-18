# 图片压缩小工具

一个本地 Web 图片压缩工具：支持 GIF 动图 / PNG / JPG / WebP / BMP / TIFF，可指定目标体积、尺寸缩放、最大颜色数、质量、帧率降低，支持单张、多张、整个文件夹、图片链接（多行 / CSV 表格）批量压缩，压缩后自动保存到 `output/` 文件夹。

## 快速开始（本机）

前置要求：Python 3.10+（[下载](https://www.python.org/downloads/)，安装时勾选 *Add python.exe to PATH*）。

```bat
:: 双击即可
run_app.bat
```

脚本会自动：检测 Python → 在本目录创建 `venv/` → 安装依赖（Pillow + numpy，清华镜像）→ 启动服务并自动打开浏览器 `http://127.0.0.1:8000`。

手动方式：

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
venv\Scripts\python app.py
```

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `app.py` | 本地 Web 服务（`/compress` 单张、`/batch` 批量），处理 URL 下载、CSV、ZIP 打包、自动保存 |
| `compress.py` | 压缩核心：`compress_image()` 统一入口，GIF 抽帧、调色板量化、抖动、缩放 |
| `index.html` | 前端界面（上传/链接 Tab、尺寸参数、进度、前后对比） |
| `run_app.bat` | 一键启动脚本（自动建环境装依赖） |
| `requirements.txt` | 依赖清单 |

## 多人协作流程（团队）

本项目使用 Git 协同开发，推荐流程：

### 1. 首次加入的成员

```bash
# 克隆仓库到本地
git clone <仓库地址>
cd gif-compressor

# 配置自己的身份（只影响本仓库）
git config user.name  "你的名字"
git config user.email "你的邮箱"
```

### 2. 日常开发（分支 + 合并）

```bash
# 同步最新代码
git pull

# 从主干拉一条自己的功能分支
git checkout -b feature/你的改动说明

# ... 修改代码（app.py / compress.py / index.html）...

# 提交
git add .
git commit -m "描述你改了什么"

# 推送到远程
git push origin feature/你的改动说明
```

### 3. 合并进主干（评审）

- 到 Gitee/GitHub 上对 `feature/xxx` 分支发起 **Pull Request（PR）**；
- 在 PR 里 @ 其他成员做**代码评审**，讨论通过后再合并到主分支；
- 合并后所有人 `git pull` 同步。

### 4. 注意事项

- **别改别人正在改的文件**：改 `index.html` 前先 `git pull`；前端改动刷新即生效，后端 `app.py` / `compress.py` 改动需重启服务。
- **冲突处理**：如果 `git pull` 报冲突，不要乱删代码，在冲突文件里手动选择保留哪部分，或找改这块的人确认。
- **产物不入库**：`output/`、`venv/`、图片文件已被 `.gitignore` 排除，不要 `git add` 它们。

## 让 AI 助手（WorkBuddy）快速接手

本仓库的 `.workbuddy/memory/`（如果保留）记录了历次功能决策与项目约定。让 AI 接手时直接说：

> 先读一下项目记忆文件，我们继续图片压缩小工具的开发

## 接口速查（给开发者）

- `POST /compress`：单张。参数放 query：`name`、`target`、`scale`、`width`、`height`、`max_colors`、`quality`、`allow_frame_skip`、`dither`，或 `url=<图片链接>` 服务端下载。响应头：`X-Compress-Meta`（JSON）、`X-Filename`、`X-Saved-Path`。
- `POST /batch`：multipart。字段：`files`（多文件）、`urls`（多行链接）、`params`（JSON 参数）。返回 ZIP（内含每张压缩结果 + `report.csv`），响应头：`X-Batch-Count`、`X-Batch-Manifest`。
