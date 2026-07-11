# CHelper — IDA Pro 9.x 反编译代码 AI 优化插件

[English](README.md) | [简体中文](README.zh-CN.md)

CHelper 是一个 IDA Pro 插件，利用**本地大语言模型**将 Hex-Rays 反编译器
生成的伪 C 代码重写为更干净、更易读、更接近人类手写的代码——包括 OLLVM
反混淆、表达式简化、变量智能重命名、自动添加注释，一键搞定。

> **为什么用本地模型？** 代码不会离开你的机器。所有推理都通过
> OpenAI 兼容 API 访问自托管模型（llama.cpp / vLLM / Ollama）。

---

## 功能特性

- **一键优化** —— 在反编译窗口按 `Ctrl+Shift+C`，当前函数即交给 LLM
  处理，结果在新查看器标签页弹出。
- **多种本地后端** —— 内置 llama.cpp server、vLLM，或任何
  OpenAI 兼容端点（如 Ollama）。
- **推理模型感知** —— 自动剥离 DeepSeek-R1 / VibeThinker 等推理模型
  输出的 `<think>…</think>` 思考块。
- **OLLVM 反混淆** —— 简化控制流平坦化、虚假控制流、魔数除法。
- **表达式简化 & 智能命名** —— 折叠冗余运算，根据上下文推断有意义的变量名。
- **前后对比查看器** —— 结果在 IDA 自定义查看器中语法高亮显示，
  同时复制到剪贴板。
- **磁盘缓存** —— 相同函数重复优化瞬间返回。
  `Ctrl+Shift+R` 强制跳过缓存重新生成。
- **自动启动 llama.cpp / vLLM** —— 插件加载时自动拉起后端进程，
  卸载时自动清理。
- **重试、退化检测、保守模式** —— 针对小模型重复输出、复杂混淆失败
  等情况做了健壮性处理。
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
        ├── .llama_bin/     ← （可选）llama.cpp 二进制，见第 4 步
        └── <model>.gguf    ← （可选）模型权重，见第 3 步
```

**具体操作：**

1. 复制 `loader/CHelper.py` → `<IDA>/plugins/CHelper.py`
2. 复制仓库其余文件（`__init__.py`、`CHelper.py`、`config.py`、
   `config.json`、`*.py`、`requirements.txt`）→ `<IDA>/plugins/CHelper/`

> **IDA 插件目录位置**
> - **Windows：** `C:\Program Files\IDA Pro 9.x\plugins\`
> - **Linux：** `~/.idapro/plugins/`  *（或 `$IDA/plugins/`）*
> - **macOS：** `/Applications/IDA Pro 9.x/idabin/plugins/`

### 3. 下载模型

模型权重**不**包含在仓库中（文件过大不适合 Git）。下载推荐的 GGUF
文件之一，放到 `<IDA>/plugins/CHelper/` 目录下：

| 模型 | 大小 | 格式 | 说明 |
|------|------|------|------|
| **DeepSeek-R1-Distill-Qwen-1.5B** | ~1.1 GB | GGUF Q4_K_M | 轻量推理模型，单卡机器的不错默认选择 |
| VibeThinker-3B | ~2 GB | GGUF / safetensors | 3B 推理模型，代码能力强 |
| qwen2.5-coder:7b | ~4.7 GB | GGUF / Ollama | 速度与质量均衡，非推理模型 |
| deepseek-coder:6.7b | ~3.8 GB | GGUF / Ollama | 快，显存占用低 |

> 默认 `config.json` 指向 `DeepSeek-R1-SFT-Q4_K_M.gguf`。
> 如果使用不同的文件名，请相应修改 `llm.model_path`。

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
[CHelper] v1.1.0 加载成功
[CHelper] 在反编译窗口按 Ctrl+Shift+C 优化代码
[CHelper] 在反编译窗口按 Ctrl+Shift+R 强制刷新（忽略缓存）
[CHelper] LLM API: http://localhost:8000/v1/chat/completions
[CHelper] 模型: DeepSeek-R1-Distill-Qwen-1.5B
```

如果 `auto_start` 开启，插件会在后台启动 `llama-server` 并等待就绪
（首次加载到显存约 30–90 秒）。

---

## 使用方法

1. 在 IDA 中打开目标程序。
2. 按 `F5` 用 Hex-Rays 反编译函数。
3. **在伪代码窗口中按 `Ctrl+Shift+C`**
   （或右键菜单 → *CHelper -> 优化伪C代码*）。
4. 等待几秒钟 LLM 处理。
5. 优化后的代码在新查看器标签页打开
   （`CHelper - <函数名> @ <地址>`），同时已复制到剪贴板。
6. 与原始伪代码对照，手动应用重命名/注释。

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+Shift+C` | 优化当前函数（有缓存时使用缓存） |
| `Ctrl+Shift+R` | 强制重新优化，忽略缓存 |

### 缓存

结果按函数地址 + 伪代码哈希缓存到磁盘。重复优化同一函数瞬间返回。
要清空缓存，删除插件目录下的 `.cache/` 文件夹，或对单个函数按
`Ctrl+Shift+R`。

---

## 配置说明

编辑插件目录下的 `config.json`。主要字段：

### `llm` —— 模型与后端

```jsonc
{
  "llm": {
    "api_url": "http://localhost:8000/v1/chat/completions",
    "model": "DeepSeek-R1-Distill-Qwen-1.5B",
    "temperature": 0.2,
    "max_tokens": 4096,
    "timeout": 180,

    "strip_reasoning": true,          // 剥离 <think>…</think> 块
    "reasoning_tags": ["think"],

    "auto_start": true,               // 自动启动 llama-server / vllm
    "backend": "llama_cpp",           // "llama_cpp" | "vllm"
    "model_path": "DeepSeek-R1-SFT-Q4_K_M.gguf",
    "n_gpu_layers": -1,              // -1 = 全部 offload 到 GPU
    "context_size": 8192,
    "startup_timeout": 180,

    // 生成质量控制
    "repeat_penalty": 1.2,
    "top_p": 0.9,
    "top_k": 40,
    "min_p": 0.05,

    // 健壮性
    "conservative_mode": "auto",      // "auto" | "on" | "off"
    "degeneration_guard": true,
    "max_retry_attempts": 3,
    "retry_delay": 1.0
  }
}
```

**`conservative_mode`** 控制重度混淆的处理方式：
- `"off"` —— 总是全量重写（复杂 OLLVM 可能失败）
- `"on"` —— 只重命名变量 + 加注释，不改逻辑
- `"auto"` *（推荐）* —— 自动检测魔数除法等硬骨头，对这些函数回退到保守模式

### `plugin` —— 界面与运行时

```jsonc
{
  "plugin": {
    "hotkey": "Ctrl+Shift+C",
    "hotkey_force": "Ctrl+Shift+R",
    "max_function_size": 10000,
    "debug": false,                   // 保存 LLM 请求/响应到 .debug/
    "log_file": "chelper.log",
    "enable_cache": true,
    "cache_dir": ".cache",
    "cache_max_age_days": 30
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
    "unroll_simple_loops": false
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
    "strip_reasoning": false
  }
}
```

---

## 项目结构

```
CHelper/                     ← 仓库根目录 = 插件包
├── loader/
│   └── CHelper.py           ← 引导加载器（复制到 plugins/CHelper.py）
├── __init__.py              ← 包初始化，导出 PLUGIN_ENTRY
├── CHelper.py               ← 主插件类（IDA plugin_t）
├── handler.py               ← 快捷键处理 & 优化流程编排
├── extractor.py             ← 伪代码 & 上下文提取
├── llm_client.py            ← LLM API 客户端 + Prompt 构建
├── processor.py             ← 后处理（语法清理、退化检测）
├── presenter.py             ← 结果查看器 & 进度对话框
├── service_manager.py       ← 自动启动 llama-server / vllm 子进程
├── cache.py                 ← 磁盘缓存（函数哈希 → 结果）
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

---

## 常见问题

**插件无法加载**
- 确认 IDA ≥ 9.0 且 Hex-Rays 已安装。
- 检查 `loader/CHelper.py` 是否已复制到 `plugins/CHelper.py`
  （不是放在 `CHelper/` 子目录里面）。
- 查看 IDA 输出窗口的错误信息。

**LLM 调用失败**
- 如果开启了 `auto_start`，首次使用时模型加载到显存需要时间
  （约 30–90 秒）。仍在启动时会提示"模型服务正在启动中"。
- 手动测试端点：`curl http://localhost:8000/v1/models`
- 确保 `llm.model` 与服务器报告的名称一致。
- 查看 `chelper.log` 了解详情。

**输出中混入 `<think>` 内容**
- 设置 `llm.strip_reasoning: true`，并确保标签在
  `llm.reasoning_tags` 列表中。

**优化效果不理想**
- 尝试更大的模型（7B / 14B）。
- 降低 `temperature`（0.1–0.3）使输出更确定。
- 设置 `conservative_mode: "auto"` 避免搞坏重度混淆。
- 开启 `plugin.debug: true`，检查 `.debug/` 下的请求/响应转储。

**函数太大**
- 调大 `plugin.max_function_size`，但注意 LLM 超时 / 上下文限制。
- 考虑先手动拆分函数。

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
