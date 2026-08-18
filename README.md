# 图片压缩小工具（团队协作版）

一个本地 Web 图片压缩工具：支持 GIF 动图 / PNG / JPG / WebP / BMP / TIFF，可指定目标体积、尺寸缩放、最大颜色数、质量、帧率降低，支持单张、多张、整个文件夹、图片链接（多行 / CSV 表格）批量压缩，压缩后自动保存到 `output/` 文件夹。

**本仓库是多人协作项目**：所有功能改动都通过 分支 → PR 评审 → 合并 进入 `main`，请遵守下面的协作规范。

---

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

## 项目结构

| 文件 | 作用 | 改动后生效方式 |
| --- | --- | --- |
| `app.py` | 本地 Web 服务（`/compress` 单张、`/batch` 批量），处理 URL 下载、CSV、ZIP 打包、自动保存 | **需重启服务** |
| `compress.py` | 压缩核心：`compress_image()` 统一入口，GIF 抽帧、调色板量化、抖动、缩放 | **需重启服务** |
| `index.html` | 前端界面（上传/链接 Tab、尺寸参数、进度、前后对比） | **刷新浏览器即生效** |
| `run_app.bat` | 一键启动脚本（自动建环境装依赖） | — |
| `requirements.txt` | 依赖清单 | 新增依赖时更新 |
| `.github/ISSUE_TEMPLATE/` | Issue 模板（Bug / 新功能） | — |

---

## 团队协作规范

### 角色与权限

| 角色 | 权限 | 职责 |
| --- | --- | --- |
| 仓库管理员 | 管理仓库设置、分支保护、合并 PR | 审核合并、解决升级冲突 |
| 开发者 | 克隆、建分支、推送分支、提 PR | 开发功能、参与评审 |
| 评审者 | 评审 PR（每个 PR 至少 1 人评审通过才能合并） | 把关代码质量与兼容性 |

### Git 工作流（日常开发）

```bash
# 1. 首次加入
git clone https://github.com/xuxu-ya/image-compressor.git
cd image-compressor
git config user.name  "你的名字"
git config user.email "你的邮箱"

# 2. 每次开发前：同步最新代码
git pull

# 3. 从主干拉一条功能分支（命名见下）
git checkout -b feature/xxx

# 4. 修改代码 → 提交
git add .
git commit -m "feat: 描述你改了什么"

# 5. 推送到远程，然后到 GitHub 上发起 PR
git push -u origin feature/xxx
```

### 分支命名规范

| 前缀 | 用途 | 示例 |
| --- | --- | --- |
| `feature/` | 新功能 | `feature/batch-preview` |
| `fix/` | Bug 修复 | `fix/url-download-timeout` |
| `refactor/` | 重构（行为不变） | `refactor/compress-api` |
| `docs/` | 文档改动 | `docs/update-readme` |

### 提交信息规范（Conventional Commits）

```
类型(影响范围): 简述
```

- 类型：`feat` 新功能 / `fix` 修复 / `refactor` 重构 / `docs` 文档 / `style` 样式 / `perf` 性能
- 示例：`feat(index.html): 批量结果增加文件名排序`、`fix(app.py): 修复长链接下载超时`
- 一个提交只做一件事，不要混入无关改动。

### Pull Request 流程

1. 推送分支后，在 GitHub 上 `Compare & pull request`；
2. 标题用提交信息规范，描述里写：**改了什么、为什么改、怎么验证**；
3. 在 PR 中 @ 相关成员评审；**至少 1 人 Approve 后才能合并**；
4. 管理员合并（或开发者自行合并）后，所有人 `git pull` 同步；
5. 合并后删除已合并的分支，保持仓库干净。

### 分支保护（已启用）

`main` 分支受保护：
- 不允许直接 push，改动必须走 PR；
- PR 必须至少 1 人评审通过；
- 评审后再有新的 push，评审自动失效需重新通过。

### 代码约定与注意事项

- **别改别人正在改的文件**：开发前先 `git pull`；如果两个人同时改 `index.html`，提前在群里说一声。
- **冲突处理**：`git pull` 报冲突时不要乱删代码，在冲突文件里手动选择保留哪部分，或找改这块的人确认。
- **产物不入库**：`output/`、`venv/`、图片文件已被 `.gitignore` 排除，不要 `git add` 它们。
- **提交前自查**：`git status` 确认只包含你想提交的文件。
- **不要 push 到 main**：本地也养成习惯，一律在分支上开发。

---

## 让 AI 助手（WorkBuddy）快速接手

本仓库的 `.workbuddy/memory/`（如果保留）记录了历次功能决策与项目约定。让 AI 接手时直接说：

> 先读一下项目记忆文件，我们继续图片压缩小工具的开发

---

## 接口速查（给开发者）

- `POST /compress`：单张。参数放 query：`name`、`target`、`scale`、`width`、`height`、`max_colors`、`quality`、`allow_frame_skip`、`dither`，或 `url=<图片链接>` 服务端下载。响应头：`X-Compress-Meta`（JSON）、`X-Filename`、`X-Saved-Path`。
- `POST /batch`：multipart。字段：`files`（多文件）、`urls`（多行链接）、`params`（JSON 参数）。返回 ZIP（内含每张压缩结果 + `report.csv`），响应头：`X-Batch-Count`、`X-Batch-Manifest`。

---

## FAQ

**Q：前端改完没生效？** A：`index.html` 改完刷新浏览器即可，无需重启；但改 `app.py` / `compress.py` 必须重启 `run_app.bat`。

**Q：找不到 Python / 双击没反应？** A：先装 Python 3.10+ 并勾选 *Add python.exe to PATH*，再重新双击 `run_app.bat`。

**Q：`git pull` 冲突了怎么办？** A：别慌、别乱删。打开冲突文件（有 `<<<<<<<` 标记），和改这块的同事确认后手动合并，再 `git add` + `git commit`。

**Q：想给仓库提需求/报 bug？** A：在 GitHub Issues 里用模板新建 Issue，描述尽量包含复现步骤和截图。
