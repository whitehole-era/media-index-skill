# Media Index

给 Codex 和 Claude Code 使用的照片、视频素材索引 Skill。它先在本地提取元数据和预览，
再由当前 agent 真正看图、撰写标注，最后生成可检索的 JSONL、SQLite 与 HTML 素材目录。

适合整理素材库、寻找某类画面和准备剪辑候选。它不包含音频转写、向量检索或自动成片。
视频标注来自抽样帧，不能证明已连续审看完整视频。

## 安装依赖

需要 Python **3.10 或更新版本**、FFmpeg（包括 `ffprobe`）和 Pillow。

macOS（使用 Homebrew）：

```bash
brew install python ffmpeg
```

Ubuntu / Debian：

```bash
sudo apt-get update
sudo apt-get install python3 python3-venv ffmpeg git
python3 --version
```

Linux 发行版提供的 Python 也需满足 3.10+。其他发行版可用其包管理器安装同等工具。

## 从 GitHub 安装

克隆一份仓库，两个 agent 共享这一份内容：

```bash
mkdir -p "$HOME/.local/share/agent-skills"
git clone https://github.com/whitehole-era/media-index-skill.git "$HOME/.local/share/agent-skills/media-index"
cd "$HOME/.local/share/agent-skills/media-index"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Codex 的用户 Skill 目录是 `~/.agents/skills/`；Claude Code 是 `~/.claude/skills/`。
下面的安装脚本只创建缺失的符号链接，发现已有目标会停止，不覆盖既有安装：

```bash
python3 - <<'PY'
from pathlib import Path

source = Path.home() / '.local/share/agent-skills/media-index'
targets = [
    Path.home() / '.agents/skills/media-index',
    Path.home() / '.claude/skills/media-index',
]
existing = [str(p) for p in targets if p.exists() or p.is_symlink()]
if existing:
    raise SystemExit('已有安装目标，请先检查：' + ', '.join(existing))
if not (source / 'SKILL.md').is_file():
    raise SystemExit('找不到克隆仓库中的 SKILL.md')
for target in targets:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)
    print(target)
PY
```

项目内共享也可使用 `.agents/skills/media-index/`（Codex）或 `.claude/skills/media-index/`
（Claude Code）。安装后的脚本需要使用仓库里的虚拟环境；运行前激活它：

```bash
cd "$HOME/.local/share/agent-skills/media-index"
source .venv/bin/activate
```

后续更新在这份克隆内运行 `git pull --ff-only`，两个符号链接会同时使用更新内容。

## 调用

Codex：输入 `$media-index`，或通过 `/skills` 选择。

Claude Code：输入 `/media-index`。

两者都可以根据 `description` 自动选择此 Skill。例如：

> 索引这个目录中的照片和视频，真正看抽帧后标注，找出夜晚摊位的候选镜头。

同一份 `SKILL.md` 只使用共同的 `name` 和 `description` frontmatter。
`agents/openai.yaml` 提供 Codex 的显示信息；Claude Code 使用同一份 Markdown 指令。

## 工作流程

从 Skill 仓库目录运行，输出目录应与素材目录分开：

```bash
python3 scripts/prepare.py --source /path/media --output /path/index --recursive
python3 scripts/index.py --index /path/index pending
```

准备阶段生成 `assets.json`、`windows.json`、`pending.json`、`previews/`、`sheets/`
和空的 `annotations/` 目录。图片按张处理；视频按 12 秒分窗，超过 300 秒的视频改用
20 秒窗，每窗在自身时长的 10%、50%、90% 各抽一帧。尾窗可以更短。

接下来必须由 agent 用现有视觉工具打开图片，逐窗检查三张抽帧，再写
`annotations/*.json` 数组。核心字段是 `id`、`description`、`tags`；观察事实与
`possible_uses` 剪辑建议分开。格式见 [references/annotations.md](references/annotations.md)。

完成视觉标注后：

```bash
python3 scripts/index.py --index /path/index build
python3 scripts/index.py --index /path/index validate
python3 scripts/index.py --index /path/index stats
python3 scripts/index.py --index /path/index search '夜晚 摊位' --kind video --limit 5 --match all --max-duration 12
python3 scripts/index.py --index /path/index show WINDOW_ID
```

`build` 在缺少标注时失败，不会把未看图的占位内容当作完成。检索支持空格分隔关键词、
`--match all` / `--match any`、素材类型和候选窗时长过滤。它使用关键词与少量同义词，
不生成 embedding。查到的时间段是候选窗口；确定剪辑点前仍需复核原片的动作与声音。

源文件改变后重新准备会得到新的 segment ID，需要重新看图标注，不能直接搬用旧 ID。
原始素材保持原位，预处理与索引输出写在指定目录。

## 模型与数据

无需额外 API key，无需更改 `base_url` 或模型配置，也不绑定某个视觉模型供应商。
预处理脚本在本地运行，不主动上传媒体。agent 看图使用当前环境已有的视觉工具；
如果当前 agent 使用托管模型，图片的处理遵从该环境现有的模型路由和数据规则。

音频没有转写，抽帧不能证明中间发生的动作、对话或声音质量。输出只应陈述看见的内容，
不要把索引覆盖全部时间窗描述成完整审片或已完成 ASR。

官方 Skill 文档：[Codex](https://developers.openai.com/codex/skills/) ·
[Claude Code](https://code.claude.com/docs/en/skills)

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
```

测试在临时目录生成合成素材，覆盖抽帧、标注校验、检索和增量更新。MIT License。
