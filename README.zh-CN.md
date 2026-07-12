# CHelper — IDA Pro 9.x 反编译代码 AI 优化插件

[English](README.md) | [简体中文](README.zh-CN.md)

CHelper 是一个 IDA Pro 插件，利用**本地大语言模型**将 Hex-Rays 反编译器
生成的伪 C 代码重写为更干净、更易读、更接近人类手写的代码——包括 OLLVM
反混淆、表达式简化、变量智能重命名、自动添加注释，一键搞定。

> **为什么用本地模型？** 代码不会离开你的机器。所有推理都通过
> OpenAI 兼容 API 访问自托管模型（llama.cpp / vLLM / Ollama）。
> 如需在线模型，也可切换到任意 OpenAI 兼容端点。

---

## 功能特性

- **一键优化** —— 在反编译窗口按 `Ctrl+Alt+C`，当前函数交给后台线程
  调用 LLM，完成后结果在新查看器标签页弹出，IDA 界面保持响应。
- **非阻塞异步流水线** —— 代码提取与结果展示在 IDA 线程执行，只有
  网络请求与纯 Python 后处理在后台工作线程运行。服务还在预热时发出的
  请求会自动排队，就绪后自动开始。
- **多种本地后端** —— 内置 llama.cpp server、vLLM、Ollama，或任意
  OpenAI 兼容端点（本地或在线）。
- **推理模型感知** —— 自动剥离 DeepSeek-R1 / VibeThinker 等推理模型
  输出的 `think…/think` 思考块，并支持当模型把答案塞进思考块时从中提取代码
  的回退逻辑。`llm.reasoning: "off"` 与 `reasoning_budget: 0` 让
  llama.cpp 优先输出代码，不再为推理 token 占预算。
- **OLLVM 反混淆** —— 简化控制流平坦化、虚假控制流、魔数除法。只有
  检测到真实的 OLLVM 模式时才下发反混淆指令，避免小模型对普通代码
  "幻觉式简化"。
- **表达式简化 & 智能命名** —— 折叠冗余运算，根据上下文推断有意义的
  变量名。
- **三段式结果分类** —— 每个结果都会标记为 `模型保守美化`、
  `模型全量重写` 或 `本地安全美化`，并在查看器标题区显示。
- **优化结果查看器** —— 结果在 IDA 自定义查看器中显示，配色与原生
  伪代码窗口对齐，重命名后的局部变量仍沿用 Hex-Rays 的语义颜色，
  支持双击跳转到已加载地址和 IDA 符号，且永不修改原始伪代码视图。
- **磁盘缓存** —— 相同函数重复优化瞬间返回。缓存按 prompt 版本、
  模型与生成参数命名空间隔离，切换模型或关键配置后自动失效旧缓存。
  `Ctrl+Alt+R` 强制跳过缓存重新生成。
- **自动启动 llama.cpp / vLLM** —— 插件加载时自动拉起后端进程，
  轮询直到 API 就绪，卸载时自动清理。
- **安全质量门** —— 拒绝丢失函数签名、调用、字符串/数值常量或
  IDA 全局符号的输出；拒绝引入未验证的调用/全局；拒绝不完整片段、
  被截断输出和空白等价无改动。无效结果永不缓存，也永不覆盖原始
  伪代码。
- **有界质量修复** —— 当模型完整输出未通过语义校验时，CHelper 会
  以原始伪代码为唯一权威进行一次严格修复重试，然后才回退。
- **本地安全美化兜底** —— 若模型输出完全不可用，会执行一次确定性、
  保持语法的本地重命名（如 `input_buffer`、`scan_result`、
  `input_cursor`），并再次执行全部语法与语义校验，结果明确标记。
- **快捷键冲突自动解决** —— 优先选择 `Ctrl+Alt+C` / `Ctrl+Alt+R`
  （旧版 `Ctrl+Shift+C` / `Ctrl+Shift+R` 与 IDA 内置动作冲突），
  检测到与其他动作冲突时自动选取空闲的回退键。
- **右键菜单** —— 通过 `UI_Hooks` 在伪代码窗口右键菜单附加
  *CHelper → 优化伪C代码* / *优化伪C代码（强制刷新）*。
- **配置校验** —— 加载时校验 `config.json`，在 IDA 输出窗口以友好
  格式报错并弹出警告对话框。
- **回归测试套件** —— 纯 Python 模块（cache、processor、llm_client、
  presenter、service_manager、config_validator、handler）由 `tests/`
  覆盖，可在无 IDA 环境下运行。
- **支持 IDA 9.0+。**

---

## 环境要求

| 组件 | 说明 |
|------|------|
| IDA Pro | 9.0 或更高版本（SDK ≥ 900） |
| Hex-Rays | 反编译器（F5）已安装 |
| Python | IDA 自带的 Python 3 |
| 操作系统 | Windows（内置 llama.cpp 为 `.exe`）；Linux/macOS 需自行提供 `llama-server` / `vllm` |
| GPU | 推荐 CUDA 显卡以获得可接受的延迟；纯 CPU 可用但较慢 |
| 磁盘 | 约 1–6 GB，取决于所选模型 |

---

## 安装

### 1. 获取代码

```bash
git clone https://github.com/BaiGuQing/CHelper.git
```

插件依赖 `requests`。如果 IDA 自带的 Python 环境中尚未安装，请先执行：

```bash
python -m pip install -r requirements.txt
```

### 2. 复制文件到 IDA 插件目录

仓库包含一个**引导加载器**（`loader/CHelper.py`）和**插件包**
（其余所有文件）。IDA 9.x 只自动加载 `plugins/` 目录*直接*下的 `.py`
文件，不扫描子目录，因此两者都需要：

```
<IDA>/plugins/
  ├── CHelper.py            ← 引导加载器 （来自 repo/loader/CHelper.py）
  └── CHelper/              ← 插件包      （来自 repo 根目录）
        ├── __init__.py
        ├── CHelper.py
        ├── config.py
        ├── config.json
        ├── llm_client.py
        ├── handler.py
        ├── …
        ├── tests/          ← 回归测试（运行时不需要）
        ├── .llama_bin/     ← （可选）llama.cpp 二进制，见第 4 步
        └── <model>.gguf    ← （可选）模型权重，见第 3 步
```

**具体操作：**

1. 复制 `loader/CHelper.py` → `<IDA>/plugins/CHelper.py`
2. 复制仓库其余文件（`__init__.py`、`CHelper.py`、`config.py`、
   `config.json`、`*.py`、`requirements.txt`、`tests/`）→
   `<IDA>/plugins/CHelper/`

> **IDA 插件目录位置**
> - **Windows：** `C:\Program Files\IDA Pro 9.x\plugins\`
> - **Linux：** `~/.idapro/plugins/`  *（或 `$IDA/plugins/`）*
> - **macOS：** `/Applications/IDA Pro 9.x/idabin/plugins/`

### 3. 下载模型

模型权重**不**包含在仓库中（文件过大不适合 Git）。下载推荐的 GGUF
文件之一，放到 `<IDA>/plugins/CHelper/` 目录下：

| 模型 | 大小 | 格式 | 说明 |
|------|------|------|------|
| DeepSeek-R1-SFT / Distill-Qwen-1.5B | ~1.1 GB | GGUF Q4_K_M | 仅适合试用；不可靠地保持复杂反编译语义 |
| VibeThinker-3B | ~2 GB | GGUF / safetensors | 可用于轻量整理，仍需审查 |
| **qwen2.5-coder:7b** | ~4.7 GB | GGUF / Ollama | 推荐起点：代码能力、速度与质量均衡 |
| deepseek-coder:6.7b | ~3.8 GB | GGUF / Ollama | 快，显存占用低 |

> 默认 `config.json` 以 `auto_start: true` 和本地
> `DeepSeek-R1-SFT-Q4_K_M.gguf` 模型路径配合内置 llama.cpp server。
> 如需使用已运行的 Ollama 服务，请设置 `auto_start: false`、
> `api_url: "http://127.0.0.1:11434/v1/chat/completions"`、
> `model: "qwen2.5-coder:7b"`。

### 4.（可选）下载 llama.cpp server 二进制

插件可以自动启动本地 `llama-server`。预编译的 Windows 二进制（CUDA 12）
单独分发——下载 `.llama_bin/` 文件夹，放到
`<IDA>/plugins/CHelper/.llama_bin/` 内。

插件查找的关键文件是：

```
CHelper/.llama_bin/llama-server.exe
```

如果你已有 `llama-server` 在 `PATH` 中，或更倾向用 vLLM / Ollama，
可以跳过此步并调整 `config.json`（见下文）。

### 5. 重启 IDA

启动后应在输出窗口看到：

```
[CHelper] v1.3.0 加载成功
[CHelper] 在反编译窗口按 Ctrl+Alt+C 优化代码
[CHelper] 在反编译窗口按 Ctrl+Alt+R 强制刷新（忽略缓存）
[CHelper] LLM API: http://127.0.0.1:8000/v1/chat/completions
[CHelper] 模型: DeepSeek-R1-SFT-Q4_K_M.gguf
```

如果 `auto_start` 开启，插件会在后台启动 `llama-server` 并等待就绪
（首次加载到显存约 30–90 秒）。服务就绪前发出的请求会自动排队，
就绪后自动开始。

---

## 使用方法

1. 在 IDA 中打开目标程序。
2. 按 `F5` 用 Hex-Rays 反编译函数。
3. **在伪代码窗口中按 `Ctrl+Alt+C`**
   （或右键菜单 → *CHelper -> 优化伪C代码*）。
4. 等待几秒钟 LLM 处理。等待框显示进度，可用 IDA 取消按钮中止。
5. 优化后的代码在新查看器标签页打开
   （`CHelper - <函数名> @ <地址>`），标题区显示结果类型、函数名、
   地址、行数变化与重命名的局部变量数量。
6. 与原始伪代码对照，手动应用重命名/注释。在查看器中双击已加载
   地址或 IDA 符号可跳转。

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+Alt+C` | 优化当前函数（有缓存时使用缓存） |
| `Ctrl+Alt+R` | 强制重新优化，忽略缓存 |

> 若任一快捷键与其他动作冲突，CHelper 会自动选取空闲回退键
> （如 `Ctrl+Alt+Shift+C`），并在加载时打印最终使用的快捷键。

### 结果类型

| 查看器标题 | 含义 |
|-----------|------|
| `CHelper 模型保守美化` | 模型仅重命名局部标识符 + 加注释（保守模式） |
| `CHelper 模型全量重写` | 模型重写了表达式/控制流/局部变量（全量模式） |
| `CHelper 本地安全美化` | 模型输出不可用；改用确定性本地重命名兜底 |

### 缓存

结果按函数地址、伪代码、函数上下文以及模型/配置指纹、prompt 版本、
缓存 schema 命名空间缓存到磁盘。重复优化同一函数瞬间返回，切换模型
或关键生成参数后会自动失效旧缓存。要清空缓存，删除插件目录下的
`.cache/` 文件夹，或对单个函数按 `Ctrl+Alt+R`。

---

## 配置说明

编辑插件目录下的 `config.json`。主要字段：

### `llm` —— 模型与后端

```jsonc
{
  "llm": {
    "api_url": "http://127.0.0.1:8000/v1/chat/completions",
    "model": "DeepSeek-R1-SFT-Q4_K_M.gguf",
    "temperature": 0.0,
    "max_tokens": 2048,
    "timeout": 300,
    "api_key": "",

    "strip_reasoning": true,          // 剥离推理模型的 think…/think 思考块
    "reasoning_tags": ["think", "thinking"],
    "reasoning": "off",               // llama.cpp：禁用推理 token，优先输出代码
    "reasoning_budget": 0,            // 立即结束 think，减少推理 token 占用

    "auto_start": true,               // 加载时启动 llama-server / vllm
    "backend": "llama_cpp",           // "llama_cpp" | "vllm" | "openai"
    "model_path": "DeepSeek-R1-SFT-Q4_K_M.gguf",
    "vllm_binary": "vllm",
    "llama_server_binary": "",        // 空则自动查找 .llama_bin/llama-server.exe
    "n_gpu_layers": -1,              // -1 = 全部 offload 到 GPU
    "context_size": 8192,
    "startup_timeout": 180,

    // 生成质量控制
    "repeat_penalty": 1.05,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "send_extended_parameters": false,// true 时向 llama.cpp 发送 top_k/min_p
    "top_p": 0.95,
    "top_k": 40,
    "min_p": 0.0,
    "seed": null,                      // 设为整数可复现采样

    // 健壮性 / 安全
    "conservative_mode": "off",        // "off" | "on" | "auto"
    "degeneration_guard": true,        // 截断重复行退化输出
    "quality_guard": true,             // 拒绝丢失语义锚点的输出
    "minimum_output_ratio": 0.30,      // 拒绝低于此比例的输出
    "restore_unused_parameter_signature": true,
    "local_readability_fallback": true,// 原样/不安全输出时启用确定性局部命名兜底
    "quality_repair_attempts": 1,      // 质量门拒绝后仅自动严格修复一次
    "max_retry_attempts": 3,           // HTTP 重试
    "retry_delay": 1.0
  }
}
```

**`conservative_mode`** 控制重写风险：
- `"off"` —— 允许全量重写、删除冗余和调整控制流；质量门仍检查函数
  签名、输出完整性、长度和新增调用/全局。
- `"on"` *（默认推荐用于反编译伪代码）* —— 只重命名局部变量 + 加注释，
  不改逻辑。
- `"auto"` —— 仅检测到魔数除法等硬骨头时使用保守模式；建议配合 7B+
  代码模型。

如果希望全量模式完全不做语义锚点检查，可另外设置
`"quality_guard": false`，但这会接受模型删除原始调用、全局符号和常量
的结果，建议只在确认模型可靠时使用。

### `plugin` —— 界面与运行时

```jsonc
{
  "plugin": {
    "hotkey": "Ctrl+Alt+C",
    "hotkey_force": "Ctrl+Alt+R",
    "max_function_size": 10000,
    "debug": false,                   // 保存 LLM 请求/响应到 .debug/
    "log_file": "chelper.log",
    "enable_cache": true,
    "cache_dir": ".cache",
    "cache_max_age_days": 30,
    "cache_cleanup_on_start": true
  }
}
```

### `optimization` —— LLM 应做什么

```jsonc
{
  "optimization": {
    "deobfuscate_ollvm": true,
    "simplify_expressions": true,
    "improve_naming": true,
    "add_comments": true,
    "unroll_simple_loops": false,
    "rewrite_control_flow": true
  }
}
```

`rewrite_control_flow: true` 会允许模型主动重写指针循环、哨兵边界和
分支结构，但提示词仍要求核对边界、终止条件、返回值和副作用，避免
改变实际行为。

### 使用 Ollama

```bash
ollama pull qwen2.5-coder:7b
ollama serve
```

```jsonc
{
  "llm": {
    "backend": "llama_cpp",
    "api_url": "http://localhost:11434/v1/chat/completions",
    "model": "qwen2.5-coder:7b",
    "auto_start": false,
    "strip_reasoning": false,
    "conservative_mode": "on"
  }
}
```

### 使用 vLLM 替代 llama.cpp

```jsonc
{
  "llm": {
    "backend": "vllm",
    "api_url": "http://localhost:8000/v1/chat/completions",
    "model": "VibeThinker-3B",
    "model_path": "/path/to/VibeThinker-3B"   // safetensors 目录
  }
}
```

### 使用 OpenAI 兼容在线模型

插件使用标准的 `POST /v1/chat/completions` 请求格式，OpenAI、DeepSeek、
OpenRouter 以及其他兼容服务都可以使用同一配置方式：

```jsonc
{
  "llm": {
    "backend": "openai",
    "api_url": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "api_key": "sk-替换为你的密钥",
    "auto_start": false,
    "send_extended_parameters": false,
    "conservative_mode": "on"
  }
}
```

使用其他服务时，只需替换 `api_url`、`model` 和 `api_key`。`api_url`
可以填写完整的 `/v1/chat/completions` 地址，也可以只填写 `/v1` 基地址，
插件会自动补全。密钥仅保存在本地 `config.json`，不要提交到代码仓库。

---

## 项目结构

```
CHelper/                     ← 仓库根目录 = 插件包
├── loader/
│   └── CHelper.py           ← 引导加载器（复制到 plugins/CHelper.py）
├── tests/                   ← 回归测试（无 IDA 也可运行）
│   ├── __init__.py
│   ├── test_config_validator.py
│   ├── test_handler_quality_repair.py
│   ├── test_llm_client.py
│   ├── test_presenter.py
│   ├── test_processor.py
│   └── test_service_manager.py
├── __init__.py              ← 包初始化，延迟导出 PLUGIN_ENTRY
├── CHelper.py               ← 主插件类（IDA plugin_t）+ 右键菜单钩子
├── handler.py               ← 异步优化流程编排 & 质量修复
├── extractor.py             ← 伪代码、上下文 & 语义颜色提取
├── llm_client.py            ← LLM API 客户端 + Prompt/修复 Prompt 构建
├── processor.py             ← 后处理、tokenizer、语义锚点校验
├── presenter.py             ← 自定义查看器（C 语法高亮 + 双击跳转）
├── service_manager.py       ← 自动启动 llama-server / vllm 子进程
├── cache.py                 ← 命名空间磁盘缓存（schema v3）
├── config.py                ← 配置加载（含默认值）
├── config_validator.py      ← 启动时配置校验
├── constants.py             ← 共享常量 & 正则模式
├── logger.py                ← 统一日志
├── result.py                ← Result/ErrorCode 错误处理
├── config.json              ← 用户可编辑配置
├── requirements.txt
├── .gitignore
├── README.md                ← （英文）
└── README.zh-CN.md          ← （本文件）
```

### 运行测试

纯 Python 模块可在无 IDA 环境下测试。在仓库根目录执行：

```bash
python -m unittest discover -s tests -v
# 或安装了 pytest 时：
pytest tests/
```

---

## 常见问题

**插件无法加载**
- 确认 IDA ≥ 9.0 且 Hex-Rays 已安装。
- 检查 `loader/CHelper.py` 是否已复制到 `plugins/CHelper.py`
  （不是放在 `CHelper/` 子目录里面）。
- 查看 IDA 输出窗口的错误信息。配置校验错误还会弹出警告对话框。

**LLM 调用失败**
- 如果开启了 `auto_start`，首次使用时模型加载到显存需要时间
  （约 30–90 秒）。仍在启动时会提示"模型服务正在启动中"；排队的
  请求会在服务就绪后自动开始。
- 手动测试端点：`curl http://localhost:8000/v1/models`
- 确保 `llm.model` 与服务器报告的名称一致。
- 查看 `chelper.log` 了解详情。

**输出中混入 `think` 内容**
- 设置 `llm.strip_reasoning: true`，并确保标签在
  `llm.reasoning_tags` 列表中。
- llama.cpp 后端建议同时设置 `llm.reasoning: "off"` 和
  `reasoning_budget: 0`。

**优化效果不理想**
- 1–3B 推理模型不适合可靠地重写反编译逻辑；建议改用代码专用的 7B+
  Instruct 模型（例如 `qwen2.5-coder:7b`）。
- 保持 `llm.reasoning: "off"`、`temperature: 0.0`、
  `conservative_mode: "on"` 和 `quality_guard: true`；质量门拒绝完整
  结果时会自动进行一次更严格的修复，仍不安全的结果不会覆盖或缓存
  原始伪代码。
- 如果模型原样返回或未通过校验，CHelper 会明确标记为"本地安全美化"：
  仅重命名有确定证据的局部变量，并再次执行语法和语义校验。
- 使用 `Ctrl+Alt+R` 强制刷新，或重启 IDA，使新的服务参数和缓存 schema
  生效。
- 开启 `plugin.debug: true`，检查 `.debug/` 下的请求/响应转储；请注意
  其中可能包含待分析代码。

**函数太大**
- 调大 `plugin.max_function_size`，但注意 LLM 超时 / 上下文限制。
- 考虑先手动拆分函数。

**快捷键无效**
- 可能其他动作占用了该键。CHelper 在加载时会打印最终使用的快捷键；
  若输出窗口出现"快捷键 … 冲突"提示，请在 IDA 的 Shortcut editor 中
  手动绑定一个空闲键。

---

## 性能参考

| 模型 | GPU | 每函数耗时 |
|------|-----|-----------|
| DeepSeek-R1-Distill-Qwen-1.5B | RTX 3060 | ~2–5 秒 |
| VibeThinker-3B | RTX 3060 | ~4–8 秒 |
| qwen2.5-coder:7b | RTX 3060 | ~3–5 秒 |
| deepseek-coder:6.7b | RTX 3060 | ~2–4 秒 |

---

## 许可证

MIT License

## 作者

**BaiGuQing**

## 免责声明

本插件通过 AI 模型生成的代码**仅供参考**。请务必在使用前手动验证
输出。进行逆向工程时请遵守相关法律法规。
