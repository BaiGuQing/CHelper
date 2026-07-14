# CHelper

面向 IDA Pro 9.x 的本地大模型反编译辅助插件。

CHelper 从 Hex-Rays 提取当前函数的伪 C 代码，通过 OpenAI 兼容接口调用
llama.cpp、vLLM、Ollama 或其他模型服务，对代码进行变量命名、表达式简化、
控制流整理、注释补充和反混淆辅助。生成结果显示在独立查看器中，不会直接
覆盖 IDA 原始伪代码。

> AI 生成的代码只能作为逆向分析参考。即使开启质量检测，也不能替代人工
> 核对、动态调试、符号执行或其他语义验证手段。

## 主要功能

- 在 Hex-Rays 伪代码窗口中一键调用本地大模型。
- 后台执行模型请求，避免长时间阻塞 IDA 主界面。
- 支持 llama.cpp、vLLM、Ollama 和 OpenAI 兼容接口。
- 支持完整函数重写和保守变量命名两种工作方式。
- 自动清理 Markdown、`<think>`、`<thinking>` 等模型输出外壳。
- 支持 Qwen、DeepSeek 等模型的 thinking/reasoning 模式。
- 可检测模型重复退化、不完整函数、语法错误和语义锚点变化。
- 支持仅冻结 `byte_*`、`g_*`、`dword_*` 等 IDA 全局符号。
- 模型候选未通过检测时，可单独打开候选窗口进行人工分析。
- 模型不可用时，可回退到本地确定性变量命名。
- 通过安全流程并成功显示的结果可以写入磁盘缓存。
- 结果查看器支持语法配色、`Ctrl+A`、`Ctrl+C` 和双击跳转符号。

## 环境要求

| 项目 | 要求 |
|---|---|
| IDA Pro | 9.0 或更高版本 |
| Hex-Rays | 已安装并可正常使用 F5 反编译 |
| Python | IDA 自带 Python 3 |
| Python 依赖 | `requests` |
| 模型接口 | OpenAI 兼容的 `/v1/chat/completions` |
| 推荐后端 | llama.cpp server |
| 操作系统 | 当前主要面向 Windows |

## 安装

### 1. 放置插件

将整个 `CHelper` 目录放到 IDA 插件目录：

```text
IDA Professional 9.4/
└── plugins/
    └── CHelper/
        ├── CHelper.py
        ├── config.json
        ├── handler.py
        ├── llm_client.py
        ├── processor.py
        └── ...
```

CHelper 是子目录包插件，不要只复制单个 `CHelper.py`。

### 2. 安装依赖

使用 IDA 对应的 Python 环境安装：

```powershell
python -m pip install requests
```

### 3. 准备模型

当前配置使用：

```text
models/CHelper-Qwen3.5-4B-v1.Q4_K_M.gguf
```

模型名称和路径在 [config.json](./config.json) 中设置：

```jsonc
"model": "CHelper-Qwen3.5-4B-v1",
"model_path": "models/CHelper-Qwen3.5-4B-v1.Q4_K_M.gguf"
```

### 4. 准备 llama-server

将 `llama-server.exe` 放入：

```text
CHelper/.llama_bin/llama-server.exe
```

当 `auto_start` 为 `true` 时，插件会自动检测并启动服务。

### 5. 重启 IDA

正常加载时，IDA 输出窗口会出现类似信息：

```text
[CHelper] v1.4.0 加载成功
[CHelper] 在反编译窗口按 Ctrl+Alt+C 优化代码
[CHelper] LLM API: http://127.0.0.1:8080/v1/chat/completions
```

## 快速使用

1. 在 IDA 中定位目标函数。
2. 按 `F5` 打开 Hex-Rays 伪代码窗口。
3. 按 `Ctrl+Alt+C` 优化当前函数。
4. 等待模型服务生成结果。
5. 在 CHelper 结果标签页中人工核对代码。

### 快捷键

| 快捷键 | 作用 |
|---|---|
| `Ctrl+Alt+C` | 优化当前函数，允许使用缓存 |
| `Ctrl+Alt+R` | 强制重新生成，忽略缓存 |
| `Ctrl+A` | 在结果查看器中全选 |
| `Ctrl+C` | 复制查看器选中内容 |

如果快捷键与其他 IDA 动作冲突，插件会尝试使用备用快捷键，并在输出窗口打印
最终绑定结果。

## 结果窗口

CHelper 不会直接修改 IDA 原始伪代码，而是打开独立查看器。

常见结果类型：

| 类型 | 含义 |
|---|---|
| 模型全量重写 | 模型生成的完整函数已被采用 |
| 模型辅助命名 | 模型只返回变量命名 JSON，由插件本地执行替换 |
| 本地安全美化 | 模型失败或被拒绝，改用本地确定性命名 |
| 模型候选（未通过安全检测） | 被拒绝的模型候选，仅供人工查看，不缓存 |

候选窗口只有在 `show_rejected_candidate` 开启且模型结果最终未被采用时才会
弹出。通过检测时只显示正式结果。

## 配置文件

[config.json](./config.json) 已按功能分组，并为每个配置项提供了中文注释。

配置加载器支持 JSONC 注释：

```jsonc
// 单行注释

/* 多行
   块注释 */
```

字符串中的 `http://`、`https://` 和类似注释的文本不会被误删。

## 工作模式

### 全量重写

```jsonc
"conservative_mode": "off"
```

模型可以重新组织表达式、循环、分支、调用、字符串和局部变量。实际允许范围
还取决于 `quality_guard` 和 `quality_guard_profile`。

适合：

- 控制流整理
- 表达式简化
- 变量重命名
- 注释生成
- 混淆代码辅助分析

### 保守命名

```jsonc
"conservative_mode": "on"
```

模型只返回局部变量命名 JSON，代码替换由插件在原始伪代码上完成。模型无法
直接重新生成控制流、调用或常量。

适合参数量较小或完整函数生成不稳定的模型。

### 自动模式

```jsonc
"conservative_mode": "auto"
```

插件根据检测到的代码特征选择工作方式。

## 安全总开关

### 完全关闭检测

```jsonc
"quality_guard": false
```

关闭后，插件会跳过：

- 重复退化检测
- 完整函数检测
- 括号和基础语法检测
- 函数签名恢复与校验
- 最低输出长度检测
- 调用、字符串、常量和全局符号检测
- 自动安全修复

模型输出只会经过 Markdown 和 reasoning 外壳清理，然后直接显示。

此时 `quality_guard_profile` 不生效。

### 开启检测

```jsonc
"quality_guard": true,
"quality_guard_profile": "balanced"
```

开启后，插件执行完整质量流程。检测范围由 profile 决定。

| Profile | 必须保留 | 可以修改 |
|---|---|---|
| `strict` | 调用、字符串、数值常量、IDA 全局符号 | 局部结构和命名 |
| `balanced` | 调用、字符串、IDA 全局符号 | 数值等价表达式、局部结构和命名 |
| `globals_only` | `g_*`、`byte_*`、`dword_*`、`qword_*` 等全局符号集合 | 调用、字符串、数值、局部变量和控制流 |

如果只希望保持 `byte_2080` 这类变量不变：

```jsonc
"quality_guard": true,
"quality_guard_profile": "globals_only"
```

## 推理模式

当前 Qwen3.5 模型的聊天模板支持 `<think>` 和 `enable_thinking`。

推荐从有限预算开始：

```jsonc
"reasoning": "on",
"reasoning_effort": null,
"reasoning_budget": 512,
"strip_reasoning": true,
"max_tokens": 4096
```

说明：

- `reasoning` 是 llama.cpp 服务端推理开关。
- `reasoning_budget` 控制 thinking token 数量。
- `reasoning_effort` 设为 `null`，避免请求层再次发送 `none`。
- `strip_reasoning` 只影响展示，不会阻止模型进行推理。
- 推理会增加耗时，也可能挤占最终代码的输出 token。

修改 `reasoning` 或 `reasoning_budget` 后必须彻底重启 llama-server。仅重新按
快捷键不会改变已经运行的服务参数。

## 全量优化目标

`optimization` 配置只主要影响全量重写模式：

```jsonc
"optimization": {
    "deobfuscate_ollvm": true,
    "simplify_expressions": true,
    "rewrite_control_flow": true,
    "improve_naming": true,
    "add_comments": true,
    "unroll_simple_loops": false
}
```

其中 OLLVM 指令只有在插件检测到相关特征后才会加入 Prompt。

## 连接其他后端

### Ollama

```jsonc
"backend": "openai",
"auto_start": false,
"api_url": "http://127.0.0.1:11434/v1/chat/completions",
"model": "your-model"
```

### vLLM

```jsonc
"backend": "vllm",
"auto_start": true,
"model_path": "models/your-hf-model",
"model": "your-model"
```

### 在线 OpenAI 兼容接口

```jsonc
"backend": "openai",
"auto_start": false,
"api_url": "https://example.com/v1/chat/completions",
"model": "model-name",
"api_key": "your-api-key"
```

使用在线接口意味着伪代码会发送到第三方服务器，请自行评估代码隐私和合规
风险。

## 缓存

缓存默认保存在 `.cache/`。

缓存键包含：

- 函数地址和原始伪代码
- 函数上下文
- 模型名称和 API 地址
- 生成参数
- Prompt 版本
- 质量检测配置
- 优化目标

以下情况会跳过或失效缓存：

- 使用 `Ctrl+Alt+R`
- 原始伪代码发生变化
- 模型或重要配置发生变化
- Prompt/schema 版本升级

被拒绝、未显示或空白等价的结果不会写入缓存。

## 日志与调试

主要文件：

| 文件或目录 | 内容 |
|---|---|
| `chelper.log` | 插件业务日志 |
| `.debug/service.log` | llama-server/vLLM 输出 |
| `.debug/request_*.json` | 调试模式下保存的模型请求 |
| `.debug/response_*.json` | 调试模式下保存的模型响应 |
| `.cache/` | 已验证结果缓存 |

启用请求与响应转储：

```jsonc
"debug": true
```

调试文件可能包含待分析的完整伪代码，请勿随意上传。

## 常见问题

### 模型服务一直在启动

- 查看 `.debug/service.log`。
- 检查模型路径和 llama-server 路径。
- 检查显存是否足够。
- 调大 `startup_timeout`。

### 修改推理配置后没有变化

`reasoning`、`reasoning_budget` 和上下文大小属于服务启动参数。请关闭旧的
llama-server 进程并重启 IDA。

### 候选被误判为重复退化

当前检测会忽略连续闭合括号、变量声明和简单常量初始化，真正复杂的重复模板
需要连续达到阈值才会触发。如果仍然误报，请保留候选代码和日志以便复现。

### 同一函数无法再次打开结果窗口

IDA 的 `simplecustviewer_t` 不允许创建同名窗口。关闭旧的同名 CHelper 标签页
后重新生成。

### `Ctrl+A` 无法全选

新版查看器会显式触发 IDA 的 `SelectAll` 动作。更新插件并重启 IDA 后生效。

### 模型输出很差或经常截断

- 使用 `temperature: 0.0`。
- 增加 `max_tokens`。
- 关闭或限制 reasoning budget。
- 小模型优先使用 `conservative_mode: "on"`。
- 使用 `Ctrl+Alt+R` 排除旧缓存影响。

## 项目结构

```text
CHelper/
├── CHelper.py             # IDA 插件入口
├── handler.py             # 异步流程、模式路由、修复和兜底
├── extractor.py           # Hex-Rays 伪代码与上下文提取
├── llm_client.py          # Prompt 和 OpenAI 兼容请求
├── processor.py           # 输出清理与质量检测
├── presenter.py           # 结果查看器
├── service_manager.py     # llama.cpp/vLLM 服务管理
├── cache.py               # 磁盘缓存
├── config.py              # JSONC 配置加载
├── config_validator.py    # 配置校验
├── config.json            # 用户配置
├── models/                # 本地模型（不提交到 Git）
└── images/                # 文档图片和收款码
```

## 请我喝一杯咖啡

如果 CHelper 对你的逆向分析有所帮助，欢迎请我喝一杯咖啡。感谢你的支持，
也欢迎提交问题、改进建议和测试样例。

| 微信 | 支付宝 |
|---|---|
| <img src="./images/wechat.jpg" alt="微信收款码" width="280"> | <img src="./images/alipay.jpg" alt="支付宝收款码" width="280"> |

## 许可证

MIT License

## 作者

**BaiGuQing**

## 免责声明

本插件生成的代码仅供学习、研究和辅助分析。使用者应自行验证输出的正确性，
并确保逆向工程行为符合当地法律法规、软件许可协议和授权范围。
