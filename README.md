# IndexTTS-GUI2

AI 语音合成任务管理桌面工具，支持 IndexTTS 引擎批量生成、任务队列调度与全局音频播放（GPT-SoVITS 引擎接口已预留，暂未接入）。

## 功能特性

- **任务集管理**：项目级别的任务组织，自动持久化配置
- **插件化引擎**：IndexTTS 引擎已完整接入，GPT-SoVITS 接口已预留（待后续实现）
- **任务队列**：批量提交、串行调度、状态追踪
- **全局播放器**：一键试听生成的音频与参考音频
- **Material Design**：暗色主题，现代简洁的桌面体验

## 环境要求

- Python 3.11+
- uv (Python 包管理工具)

## 快速开始

```bash
# 安装 uv（如已安装可跳过）
pip install uv

# 创建虚拟环境并安装依赖
uv sync

# 运行应用
uv run python -m src.main
```

## 项目结构

```
indextts-gui2/
├── pyproject.toml      # 项目配置与依赖
├── src/
│   ├── main.py         # 应用入口
│   ├── app.py          # 主窗口
│   ├── ui/             # UI 组件
│   ├── core/           # 业务逻辑
│   ├── engines/        # 引擎适配器
│   ├── utils/          # 工具函数
│   └── resources/      # 静态资源
└── tests/              # 测试用例
```

详细的分层职责、任务状态机、持久化提交顺序和线程所有权见
[ARCHITECTURE.md](ARCHITECTURE.md)。仓库中保留的 `src/indextts_batch_gui`
是 v1 数据兼容实现，与当前 v2 入口使用不同的任务集格式；不要让两个版本共用同一任务集目录。

## 打包

```bash
python build_exe.py
```
