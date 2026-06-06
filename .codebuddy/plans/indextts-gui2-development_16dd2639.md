---
name: indextts-gui2-development
overview: 基于 PySide6 + uv 构建 AI 语音合成任务管理桌面工具，支持 IndexTTS/GPT-SoVITS 多引擎批量生成、任务队列调度、全局播放器与 Material Design 风格 UI。
design:
  styleKeywords:
    - Material Design 3
    - Dark Theme
    - Professional
    - Clean
    - Modern
  fontSystem:
    fontFamily: Microsoft YaHei
    heading:
      size: 16px
      weight: 600
    subheading:
      size: 14px
      weight: 500
    body:
      size: 13px
      weight: 400
  colorSystem:
    primary:
      - "#1976D2"
      - "#2196F3"
      - "#64B5F6"
    background:
      - "#121212"
      - "#1E1E1E"
      - "#252525"
    text:
      - "#FFFFFF"
      - "#B0B0B0"
      - "#757575"
    functional:
      - "#4CAF50"
      - "#F44336"
      - "#FF9800"
      - "#2196F3"
      - "#9E9E9E"
todos:
  - id: init-project
    content: 初始化项目骨架：创建 pyproject.toml（uv 环境），定义依赖（pyside6, httpx, gradio_client, pyinstaller），初始化 src/ 包结构和 __init__.py
    status: completed
  - id: core-models
    content: 实现 core 层数据模型：Task 数据类（含 TaskStatus 枚举和状态机校验逻辑）、TaskSet 数据类（含目录扫描加载/保存）、ConfigManager 配置管理器（QSettings + JSON）
    status: completed
    dependencies:
      - init-project
  - id: engine-base-indextts
    content: 实现引擎层：BaseEngine 抽象基类（接口定义）、EngineRegistry 注册表、IndexTTS Gradio 适配器（含 api_detector 探测 + 连接测试 + 音频生成）、GPT-SoVITS 预留适配器框架
    status: completed
    dependencies:
      - core-models
  - id: audio-player
    content: 实现全局音频播放器：基于 QMediaPlayer 的 AudioPlayer 组件，支持播放/暂停、新播放自动停止旧播放、播放状态信号通知
    status: completed
    dependencies:
      - init-project
  - id: config-tab
    content: 实现配置 Tab 页面：引擎 URL 配置 UI（含连接测试按钮和状态指示灯）、任务集管理 UI（创建/切换/删除）、全局设置 UI（音频格式/队列间隔/超时），绑定 ConfigManager 读写
    status: completed
    dependencies:
      - engine-base-indextts
      - audio-player
  - id: task-tab
    content: 实现任务 Tab 页面：左侧 task_table（QTableWidget 含 checkbox/排序/状态颜色标识）、右侧 task_detail_panel（文案编辑/引擎选择/engine_config_widget 动态参数表单/操作按钮）、批量操作工具栏
    status: completed
    dependencies:
      - core-models
      - engine-base-indextts
      - audio-player
  - id: task-queue
    content: 实现任务队列调度器：TaskQueue（QThread + Queue），串行调度逻辑，状态流转与持久化，队列间隔控制，生成完成/失败信号通知 UI 刷新
    status: completed
    dependencies:
      - task-tab
  - id: main-window
    content: 实现主窗口 app.py：QMainWindow 集成全局播放器栏、横版 Tab 切换、QStackedWidget 内容区，Signal/Slot 连接全部模块
    status: completed
    dependencies:
      - config-tab
      - task-tab
      - task-queue
      - audio-player
  - id: material-theme
    content: 实现 Material Design 主题：编写 style.qss 样式表（暗色主题、颜色系统、圆角卡片、状态指示灯、滑块样式），应用到全局窗口
    status: completed
    dependencies:
      - main-window
  - id: packaging
    content: 实现打包脚本：编写 build_exe.spec（PyInstaller 配置），处理 gradio_client 隐藏导入和 QSS 资源打包，验证 exe 可执行
    status: completed
    dependencies:
      - main-window
      - material-theme
  - id: tests
    content: 编写测试用例：test_task（状态机流转/校验）、test_taskset（加载/保存）、test_queue（入队出队调度）、test_file_utils（文件命名 sanitize）、test_engine（API 探测 mock）
    status: completed
    dependencies:
      - core-models
      - engine-base-indextts
      - task-queue
---

## 项目概述

IndexTTS-GUI2 是一款桌面端 AI 语音合成任务管理工具，解决视频创作者在使用多个 TTS 引擎（IndexTTS、GPT-SoVITS 等）时面临的手动操作繁琐、文件管理混乱、多平台切换低效等痛点。

## 核心功能需求

### 任务集管理

- 任务集对应一个项目目录，目录内存储所有任务配置（tasks/*.json）和输出音频（outputs/）
- 支持创建、切换、删除任务集

### 任务管理

- 任务状态机：未开始 → 队列中 → 生成中 → 生成完成 / 生成失败，支持重新生成
- 任务列表：表格展示（ID、文案摘要、引擎、状态颜色标识）、排序、多选批量操作（批量生成/删除/下载）
- 任务详情：左右分栏布局，左侧列表，右侧详情面板（文案编辑、引擎选择、引擎专属参数配置）

### 任务队列调度

- 内部维护串行任务队列，批量生成后任务入队自动调度
- 队首调用引擎 API 生成，完成后自动出队处理下一个
- 队列中/生成中任务不可编辑

### 引擎配置

- 支持多引擎 URL 配置与连接测试
- IndexTTS：参考音频、目标文案、情感模式（与参考音频相同/上传情感参考音频/情感向量指定），情感向量含 8 个 0~1 滑块 + 控制权重
- GPT-SoVITS：预留适配器框架，待后续对接细化
- 引擎采用插件/适配器模式，新增引擎只需实现统一接口

### 全局播放器

- 顶部固定播放栏，全局单例
- 支持播放生成音频和参考音频
- 支持暂停，新播放自动停止旧播放

### 持久化

- JSON 文件持久化，任务配置保存在任务集目录
- 生成完成时保存 generation_config 快照，便于复现
- 文件命名规范：`{task_id}_{sanitized_text}.{ext}`

### UI 布局

- 横版 Tab 栏位于内容区域上方（配置 Tab / 任务列表 Tab）
- 配置 Tab：引擎 URL 配置卡片 + 任务集管理卡片 + 全局设置卡片
- 任务 Tab：左右分栏（左列表 + 右详情面板）
- Material Design 风格，状态颜色标识（灰/蓝/橙/绿/红）

## 技术栈

| 技术 | 选型 | 说明 |
| --- | --- | --- |
| 语言 | Python 3.11+ | 稳定版本，生态丰富 |
| GUI 框架 | PySide6 | Qt for Python，原生表格/媒体播放/Tab 切换 |
| 环境管理 | uv | 快速依赖管理，生成 lock 文件，方便迁移 |
| 音频播放 | QMediaPlayer (PySide6 内置) | 无需额外依赖，支持播放/暂停 |
| HTTP 客户端 | httpx | 异步支持，用于调用引擎 WebUI API |
| Gradio API 客户端 | gradio_client | 官方 Python 客户端，直接调用 Gradio 端点 |
| JSON 处理 | orjson 或标准库 json | 高性能 JSON 序列化 |
| 持久化 | JSON 文件 | 轻量，适合任务级配置 |
| 文件管理 | pathlib | 跨平台路径处理 |
| 打包分发 | PyInstaller | 生成独立 exe 可执行文件 |


## 项目目录结构

```
indextts-gui2/
├── pyproject.toml                 # uv 项目配置与依赖声明
├── uv.lock                        # uv 依赖锁定文件
├── build_exe.py                   # PyInstaller 打包脚本
├── build_exe.spec                 # PyInstaller spec 配置
├── README.md
├── requirements.md                # 需求文档（已存在）
├── src/
│   ├── __init__.py
│   ├── main.py                    # 应用入口，单实例检查
│   ├── app.py                     # 主窗口（QMainWindow），Tab框架 + 全局播放器
│   ├── resources/
│   │   ├── __init__.py
│   │   └── style.qss             # Material Design QSS 样式表
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── config_tab.py          # 配置 Tab 页（引擎配置 + 任务集管理 + 全局设置）
│   │   ├── task_list_tab.py       # 任务列表 Tab 页（左右分栏容器）
│   │   ├── task_detail_panel.py   # 任务详情右侧面板
│   │   ├── task_table.py          # 任务列表左侧表格（QTableWidget）
│   │   ├── audio_player.py        # 全局音频播放器组件（QMediaPlayer）
│   │   └── engine_config_widget.py # 引擎配置动态表单（情感模式切换）
│   ├── core/
│   │   ├── __init__.py
│   │   ├── task.py                # Task 数据类 + TaskStatus 枚举 + 状态机验证
│   │   ├── taskset.py             # TaskSet 数据类 + 目录扫描/加载
│   │   ├── task_queue.py          # 任务队列调度器（QThread + Queue）
│   │   └── config_manager.py      # 应用全局配置读写（QSettings + JSON）
│   ├── engines/
│   │   ├── __init__.py            # 引擎注册表（EngineRegistry）
│   │   ├── base_engine.py         # 引擎抽象基类（接口定义）
│   │   ├── indextts_engine.py     # IndexTTS Gradio 适配器
│   │   └── gpt_sovits_engine.py   # GPT-SoVITS 适配器（预留框架）
│   └── utils/
│       ├── __init__.py
│       ├── file_utils.py          # 文件命名 sanitize + 路径处理
│       └── api_detector.py        # Gradio API 类型探测与连接测试
└── tests/
    ├── __init__.py
    ├── test_task.py               # Task 模型单元测试
    ├── test_taskset.py            # TaskSet 加载/保存测试
    ├── test_queue.py              # 任务队列调度测试
    ├── test_file_utils.py         # 文件命名规范测试
    └── test_engine.py             # 引擎适配器测试
```

## 核心架构设计

### 分层架构

```mermaid
graph TD
    A[UI Layer - PySide6] --> B[Core Layer - 业务逻辑]
    B --> C[Engine Layer - 引擎适配器]
    B --> D[Persistence Layer - JSON 文件]
    A --> E[Player Layer - QMediaPlayer]
    
    subgraph UI
        A1[config_tab.py]
        A2[task_list_tab.py]
        A3[task_detail_panel.py]
        A4[task_table.py]
        A5[audio_player.py]
        A6[engine_config_widget.py]
    end
    
    subgraph Core
        B1[Task / TaskSet 模型]
        B2[TaskQueue 调度器]
        B3[ConfigManager]
    end
    
    subgraph Engines
        C1[BaseEngine ABC]
        C2[IndexTTSEngine]
        C3[GPTSoVITSEngine]
    end
```

### 数据模型

```python
from enum import Enum
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

class TaskStatus(str, Enum):
    PENDING = "未开始"
    QUEUED = "队列中"
    GENERATING = "生成中"
    COMPLETED = "生成完成"
    FAILED = "生成失败"

@dataclass
class Task:
    id: str                         # 短ID，如 "task_001"
    text: str                       # 目标文案
    engine: str                     # 引擎标识符
    status: TaskStatus = TaskStatus.PENDING
    engine_params: dict = field(default_factory=dict)
    output_audio_path: Optional[str] = None
    generation_config: Optional[dict] = None
    error_message: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

@dataclass
class TaskSet:
    id: str
    name: str
    directory: Path
    tasks: list[Task] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
```

### 引擎抽象接口

```python
from abc import ABC, abstractmethod

class BaseEngine(ABC):
    engine_id: str = ""
    engine_name: str = ""
    
    @abstractmethod
    async def test_connection(self, url: str) -> bool: ...
    
    @abstractmethod
    async def generate(self, url: str, params: dict) -> bytes: ...
    
    @abstractmethod
    def get_param_schema(self) -> dict: ...
    
    @abstractmethod
    def validate_params(self, params: dict) -> list[str]: ...
```

### 任务队列调度流程

```mermaid
flowchart TD
    A[批量生成] --> B[选中 PENDING 任务]
    B --> C[加入队列 Queue]
    C --> D[状态更新: QUEUED]
    D --> E[持久化 JSON]
    E --> F{队列非空?}
    F -->|是| G[取出队首任务]
    G --> H[状态更新: GENERATING]
    H --> I[调用 Engine.generate]
    I --> J{API 调用结果}
    J -->|成功| K[保存音频文件]
    K --> L[状态更新: COMPLETED]
    J -->|失败| M[记录 error_message]
    M --> N[状态更新: FAILED]
    L --> O[保存 generation_config]
    O --> P[UI 信号通知]
    N --> P
    P --> Q[队列间隔等待]
    Q --> F
    F -->|否| R[队列空闲]
```

### API 探测策略

```mermaid
flowchart TD
    A[引擎URL输入] --> B{访问 /info 或 /config}
    B -->|返回 Gradio 特征| C[标记为 Gradio API]
    B -->|非 Gradio| D{访问 /docs 或 /openapi.json}
    D -->|返回 OpenAPI| E[标记为 OpenAPI]
    D -->|均失败| F[标记URL不可用]
    C --> G[建立 gradio_client 连接]
    G --> H[获取可用端点列表]
    H --> I[映射参数到端点]
```

### 信号/槽通信机制

采用 PySide6 Signal/Slot 实现模块间解耦：

- `TaskQueue.generation_started(task_id)` → UI 更新状态行
- `TaskQueue.generation_completed(task_id, audio_path)` → UI 刷新 + 播放器可用
- `TaskQueue.generation_failed(task_id, error)` → UI 显示错误
- `AudioPlayer.play_requested(file_path)` → 停止旧音频，播放新音频

## 实现细节

### 性能与稳定性

- 任务队列使用 `QThread` + `queue.Queue`，避免阻塞 GUI 主线程
- HTTP 请求使用 `httpx` 异步客户端，设置超时（默认 120s）
- 队列间隔可配置（默认 2 秒），避免 API 限流
- 所有文件 IO 操作使用 `pathlib`，自动处理路径分隔符
- JSON 读写使用原子写入（先写临时文件，再 rename），防止写入中断损坏数据

### 打包策略

- 使用 PyInstaller 配套 spec 文件，显式声明 `--hidden-import`（PySide6 子模块、gradio_client）
- 静态资源（style.qss）通过 `--add-data` 打包进 exe
- 使用 `sys._MEIPASS` 在运行时正确访问打包后资源路径
- uv 锁定依赖版本，确保打包可重现

### 错误处理

- 引擎 API 调用异常捕获后标记 FAILED，error_message 存储详细错误
- 队列中未完成的任务应用关闭时提示用户
- 文件写入失败时重试 3 次，仍失败则通知用户
- QMediaPlayer 播放异常静默降级（提示音频不可用）

## 设计风格

采用 **Material Design 3** 风格，通过 PySide6 QSS 样式表实现。整体以暗色主题为主，辅以 Material 核心颜色系统，带来专业且现代的视觉体验。

## 全局框架布局

- 顶部：全局播放器栏，深色背景，包含播放/暂停按钮、播放进度条、当前播放文件名
- 播放器下方：横版 Tab 栏（Material Tab 风格），选中 Tab 有底部指示器和颜色高亮
- Tab 下方：内容区域，使用 QStackedWidget 切换配置 Tab 和任务 Tab

## 配置 Tab 页面

三个 GroupBox 卡片，带 Material 阴影和圆角：

- 引擎配置卡片：每个引擎独立行，URL 输入框 + 测试连接按钮 + 连接状态指示灯（绿色圆点 = 已连接，红色圆点 = 未连接）
- 任务集管理卡片：当前路径显示 + 浏览按钮 + 历史任务集列表（带文件夹图标和日期）
- 全局设置卡片：下拉框/输入框组合，保存按钮使用 Material 主色

## 任务 Tab 页面（左右分栏）

- 左侧任务列表（占 40%）：顶部批量操作工具栏（按钮组），下方 QTableWidget 表格，表头支持排序，每行含 checkbox、任务ID、文案摘要、引擎标签、状态标识（彩色圆点+文字）
- 右侧任务详情（占 60%）：上方文案多行文本框，中部引擎下拉选择器，下方动态引擎参数配置区（根据选中引擎切换），底部状态指示 + 操作按钮组（保存/下载/删除）

## 状态颜色系统

- 未开始：灰色 #9E9E9E
- 队列中：蓝色 #2196F3
- 生成中：橙色 #FF9800（带脉冲动画）
- 生成完成：绿色 #4CAF50
- 生成失败：红色 #F44336

## 交互细节

- 引擎参数区根据引擎类型和情感模式动态渲染（隐藏/显示滑块组）
- 情感向量滑块范围 0.00~1.00，步长 0.01，带数值标签
- 批量操作需先勾选任务，按钮在无选中时置灰
- 队列中/生成中的任务详情面板进入只读模式

## Agent Extensions

### Skill

- **find-skills**
- Purpose: 在开发过程中如遇到需要额外工具支持的需求（如 Gradio API 分析、打包问题排查），用于查找可安装的技能扩展
- Expected outcome: 发现并建议安装适合当前开发任务的技能工具