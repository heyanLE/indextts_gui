# IndexTTS Batch GUI

基于 PySide6 的 IndexTTS WebUI 桌面批量生成客户端。

本项目用于管理大规模 TTS 任务集，支持任务级参数配置、队列控制、JSON 持久化和音频播放。

## 项目来源

本项目基于 `index-tts/index-tts` 进行扩展开发：

- 上游仓库：https://github.com/index-tts/index-tts

## 功能亮点

- 基于任务集目录工作流（每个任务一个 JSON，音频输出在 `outputs/`）
- 支持直接填写 `WebUI URL`（优先），并兼容主机+端口回退
- 支持任务级详情编辑（文本、参考音频、情感控制、高级参数）
- 队列状态完整：`pending`、`queued`、`generating`、`done`、`failed`、`cancelled`
- 行级操作按任务 ID 生效（播放/生成/删除/详情）
- 批量控制：开始、暂停排队任务、重试失败任务
- 支持拖拽排序，并持久化 `order`
- 支持定稿标记（`is_final`），点击即保存
- Gradio 接口增强（端点发现、文件上传、选项值标准化）
- Windows 重生成文件锁处理（释放占用 + 重试写入）

## 使用流程

1. 打开或创建任务集。
2. 添加任务（单条添加或按行批量展开）。
3. 编辑任务详情参数。
4. 在任务列表中拖拽排序。
5. 启动批量生成或单任务生成。
6. 在任务列表中直接播放生成音频。

## 环境要求

- Python 3.10+
- Windows/macOS/Linux（开发环境）
- 可访问的 IndexTTS WebUI 服务

## 安装

```bash
pip install -e .[dev]
```

启动：

```bash
python -m indextts_batch_gui
```

## 配置说明

在应用 `设置 -> 全局设置` 中：

- `WebUI URL`：优先使用，例如 `http://127.0.0.1:7860`
- `主机(回退)` / `端口(回退)`：当 URL 为空时使用
- `并发数`：界面配置项（当前运行器按顺序执行）
- `超时(秒)`：请求超时

应用配置文件路径：

- `~/.indextts_batch_gui/app_config.json`

## 任务集目录结构

```text
<task_set>/
	defaults.json
	set_meta.json
	tasks/
		<task_id>.json
	outputs/
	refs/
```

任务 JSON 常见字段：

- `task_id`、`text`、`reference_audio`
- `config`（当前可编辑配置）
- `generated_config`（最近一次生成快照）
- `audio_file`、`status`、`progress`、`error`
- `needs_regen`、`last_generated_signature`
- `order`、`is_final`

## 远端 WebUI 兼容

调用 Gradio 前会自动标准化 `emo_control_method`，避免中英文选项不一致导致报错。

示例映射：

- `使用情感向量控制` -> `Use emotion vectors`
- `使用情感参考音频` -> `Use emotion reference audio`
- `与音色参考音频相同` -> `Same as the voice reference`

## 开发与测试

运行测试：

```bash
python -m pytest -q
```

Windows 打包：

```powershell
scripts\build_windows.ps1
```

## 项目结构

```text
src/indextts_batch_gui/
	api_client.py      # WebUI/Gradio 通信
	scheduler.py       # 批量调度与状态流转
	storage.py         # 任务集持久化
	models.py          # 数据模型
	ui/main_window.py  # 主界面
	audio.py           # 音频播放服务
```

## 注意事项

- 当前批量执行策略为顺序执行。
- 当 WebUI 不可达时，网络/API 错误会记录到任务 `error` 字段。
- 启动时若检测到 WebUI 空闲，可将残留 `generating` 状态回收为 `pending`。

## 贡献

欢迎提交 Issue 和 PR。

- 修改建议尽量聚焦、可验证。
- 提交前请运行 `python -m pytest -q`。
- 行为变更建议在 PR 描述中附简要说明。

## 许可证

当前仓库尚未添加 `LICENSE` 文件。

若计划公开开源，建议在发布前补充许可证（如 MIT / Apache-2.0）。
