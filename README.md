# CHelper — AI-Powered Decompiler Code Optimizer for IDA Pro 9.x

[English](README.md) | [简体中文](README.zh-CN.md)

CHelper is an IDA Pro plugin that uses a **local LLM** (large language model) to
rewrite Hex-Rays pseudo-C output into cleaner, more readable, human-like code —
deobfuscating OLLVM, simplifying expressions, improving variable names, and
adding comments, all with a single hotkey.

> **Why local?**  No code leaves your machine. Everything runs against a
> self-hosted model via an OpenAI-compatible API (llama.cpp / vLLM / Ollama).

---

## Features

- **One-hotkey optimize** — `Ctrl+Shift+C` in the decompiler view sends the
  current function to the LLM and pops the result up in a new viewer tab.
- **Local model backends** — bundled llama.cpp server, vLLM, or any
  OpenAI-compatible endpoint (e.g. Ollama).
- **Reasoning-model aware** — automatically strips `<think>…</think>` blocks
  emitted by DeepSeek-R1 / VibeThinker style reasoning models.
- **OLLVM deobfuscation** — flattens control-flow flattening, removes bogus
  branches, simplifies magic-number divisions.
- **Expression simplification & smart renaming** — collapses redundant
  arithmetic and infers meaningful names from context.
- **Before/after diff viewer** — syntax-highlighted result in an IDA custom
  viewer; code is also copied to the clipboard.
- **Disk cache** — repeated optimization of the same function is instant.
  `Ctrl+Shift+R` forces a cache bypass.
- **Auto-start llama.cpp / vLLM** — the plugin can spawn the backend process for
  you on load and clean it up on unload.
- **Retry, degeneration guard, conservative mode** — robust against small
  models that repeat themselves or choke on heavy obfuscation.
- **IDA 9.0+ support.**

---

## Requirements

| Component | Details |
|-----------|---------|
| IDA Pro | 9.0 or later (SDK ≥ 900) |
| Hex-Rays | Decompiler (F5) installed |
| Python | IDA's bundled Python 3 |
| OS | Windows (bundled llama.cpp binaries are `.exe`); Linux/macOS work if you supply your own `llama-server` / `vllm` |
| GPU | Recommended (CUDA) for acceptable latency, CPU-only works but is slow |
| Disk | ~1–6 GB depending on the model you choose |

---

## Installation

### 1. Get the code

```bash
git clone https://github.com/BaiGuQing/CHelper.git
```

### 2. Copy files into the IDA plugins directory

The repository ships a **bootstrap loader** (`loader/CHelper.py`) plus the
**plugin package** (everything else). IDA 9.x only auto-loads `.py` files that
sit *directly* in the `plugins/` directory, so you need both:

```
<IDA>/plugins/
  ├── CHelper.py            ← bootstrap loader  (from repo/loader/CHelper.py)
  └── CHelper/              ← plugin package    (from repo root)
        ├── __init__.py
        ├── CHelper.py
        ├── config.py
        ├── config.json
        ├── llm_client.py
        ├── handler.py
        ├── …
        ├── .llama_bin/     ← (optional) llama.cpp binaries, see step 4
        └── <model>.gguf    ← (optional) model weights, see step 3
```

**Concrete steps:**

1. Copy `loader/CHelper.py` → `<IDA>/plugins/CHelper.py`
2. Copy the rest of the repo (`__init__.py`, `CHelper.py`, `config.py`,
   `config.json`, `*.py`, `requirements.txt`) → `<IDA>/plugins/CHelper/`

> **IDA plugins directory locations**
> - **Windows:** `C:\Program Files\IDA Pro 9.x\plugins\`
> - **Linux:** `~/.idapro/plugins/`  *(or `$IDA/plugins/`)*
> - **macOS:** `/Applications/IDA Pro 9.x/idabin/plugins/`

### 3. Download a model

Model weights are **not** included in the repo (too large for Git). Download one
of the recommended GGUF files and place it inside `<IDA>/plugins/CHelper/`:

| Model | Size | Format | Notes |
|-------|------|--------|-------|
| **DeepSeek-R1-Distill-Qwen-1.5B** | ~1.1 GB | GGUF Q4_K_M | Lightweight reasoning model; good default for single-GPU machines |
| VibeThinker-3B | ~2 GB | GGUF / safetensors | 3 B reasoning model, strong on code |
| qwen2.5-coder:7b | ~4.7 GB | GGUF / Ollama | Balanced speed & quality, non-reasoning |
| deepseek-coder:6.7b | ~3.8 GB | GGUF / Ollama | Fast, lower VRAM |

> The default `config.json` points to `DeepSeek-R1-SFT-Q4_K_M.gguf`.
> If you use a different file name, update `llm.model_path` accordingly.

### 4. (Optional) Download llama.cpp server binaries

The plugin can auto-launch a local `llama-server` for you. Pre-built Windows
binaries (CUDA 12) are distributed separately — download the `.llama_bin/`
folder and place it inside `<IDA>/plugins/CHelper/.llama_bin/`.

The key file it looks for is:

```
CHelper/.llama_bin/llama-server.exe
```

If you already have `llama-server` on your `PATH`, or you prefer vLLM / Ollama,
you can skip this step and adjust `config.json` (see below).

### 5. Restart IDA

On startup you should see in the Output window:

```
[CHelper] v1.1.0 加载成功
[CHelper] 在反编译窗口按 Ctrl+Shift+C 优化代码
[CHelper] 在反编译窗口按 Ctrl+Shift+R 强制刷新（忽略缓存）
[CHelper] LLM API: http://localhost:8000/v1/chat/completions
[CHelper] 模型: DeepSeek-R1-Distill-Qwen-1.5B
```

If `auto_start` is on, the plugin will spawn `llama-server` in the background
and wait for it to be ready (first load into VRAM takes ~30–90 s).

---

## Usage

1. Open the target binary in IDA.
2. Press `F5` to decompile a function with Hex-Rays.
3. **In the pseudocode window, press `Ctrl+Shift+C`**
   (or right-click → *CHelper → 优化伪C代码*).
4. Wait a few seconds for the LLM to process.
5. The optimized code opens in a new viewer tab
   (`CHelper - <function> @ <addr>`) and is copied to the clipboard.
6. Compare with the original pseudocode and manually apply renames/comments.

### Hotkeys

| Hotkey | Action |
|--------|--------|
| `Ctrl+Shift+C` | Optimize current function (uses cache if available) |
| `Ctrl+Shift+R` | Force re-optimize, bypassing the cache |

### Cache

Results are cached on disk keyed by function address + pseudocode hash.
Re-optimizing an unchanged function is instant. To clear the cache, delete the
`.cache/` directory inside the plugin folder, or press `Ctrl+Shift+R` for a
single function.

---

## Configuration

Edit `config.json` in the plugin directory. Key fields:

### `llm` — model & backend

```jsonc
{
  "llm": {
    "api_url": "http://localhost:8000/v1/chat/completions",
    "model": "DeepSeek-R1-Distill-Qwen-1.5B",
    "temperature": 0.2,
    "max_tokens": 4096,
    "timeout": 180,

    "strip_reasoning": true,          // remove <think>…</think> blocks
    "reasoning_tags": ["think"],

    "auto_start": true,               // auto-launch llama-server / vllm
    "backend": "llama_cpp",           // "llama_cpp" | "vllm"
    "model_path": "DeepSeek-R1-SFT-Q4_K_M.gguf",
    "n_gpu_layers": -1,              // -1 = offload all to GPU
    "context_size": 8192,
    "startup_timeout": 180,

    // generation quality
    "repeat_penalty": 1.2,
    "top_p": 0.9,
    "top_k": 40,
    "min_p": 0.05,

    // robustness
    "conservative_mode": "auto",      // "auto" | "on" | "off"
    "degeneration_guard": true,
    "max_retry_attempts": 3,
    "retry_delay": 1.0
  }
}
```

**`conservative_mode`** controls how heavy obfuscation is handled:
- `"off"` — always full rewrite (may fail on complex OLLVM)
- `"on"` — only rename variables + add comments, never touch logic
- `"auto"` *(recommended)* — auto-detect magic-number division etc. and
  fall back to conservative mode for those functions

### `plugin` — UI & runtime

```jsonc
{
  "plugin": {
    "hotkey": "Ctrl+Shift+C",
    "hotkey_force": "Ctrl+Shift+R",
    "max_function_size": 10000,
    "debug": false,                   // save LLM req/resp to .debug/
    "log_file": "chelper.log",
    "enable_cache": true,
    "cache_dir": ".cache",
    "cache_max_age_days": 30
  }
}
```

### `optimization` — what the LLM should do

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

### Using vLLM instead of llama.cpp

```jsonc
{
  "llm": {
    "backend": "vllm",
    "api_url": "http://localhost:8000/v1/chat/completions",
    "model": "VibeThinker-3B",
    "model_path": "/path/to/VibeThinker-3B"   // safetensors directory
  }
}
```

### Using Ollama

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

## Project Structure

```
CHelper/                     ← repo root = plugin package
├── loader/
│   └── CHelper.py           ← bootstrap loader (copy to plugins/CHelper.py)
├── __init__.py              ← package init, exports PLUGIN_ENTRY
├── CHelper.py               ← main plugin class (IDA plugin_t)
├── handler.py               ← hotkey handler & optimization orchestration
├── extractor.py             ← pseudocode & context extraction
├── llm_client.py            ← LLM API client + prompt builder
├── processor.py             ← post-processing (syntax cleanup, degeneration guard)
├── presenter.py             ← result viewer & progress dialog
├── service_manager.py       ← auto-start llama-server / vllm subprocess
├── cache.py                 ← disk cache (function hash → result)
├── config.py                ← config loader with defaults
├── config_validator.py      ← startup config validation
├── constants.py             ← shared constants & regex patterns
├── logger.py                ← unified logging
├── result.py                ← Result/ErrorCode error handling
├── config.json              ← user-editable configuration
├── requirements.txt
├── .gitignore
├── README.md                ← (this file)
└── README.zh-CN.md
```

---

## Troubleshooting

**Plugin doesn't load**
- Verify IDA ≥ 9.0 and Hex-Rays is installed.
- Check `loader/CHelper.py` was copied to `plugins/CHelper.py` (not inside the
  `CHelper/` subdirectory).
- Look for errors in IDA's Output window.

**LLM call fails**
- If using `auto_start`, give the model time to load into VRAM on first use
  (~30–90 s). You'll see "模型服务正在启动中" if it's still starting.
- Test the endpoint manually:
  `curl http://localhost:8000/v1/models`
- Ensure `llm.model` matches the name the server reports.
- Check `chelper.log` for details.

**`<think>` blocks leak into the output**
- Set `llm.strip_reasoning: true` and make sure the tag is listed in
  `llm.reasoning_tags`.

**Optimization quality is poor**
- Try a larger model (7B / 14B).
- Lower `temperature` (0.1–0.3) for more deterministic output.
- Set `conservative_mode: "auto"` to avoid botching heavy obfuscation.
- Enable `plugin.debug: true` and inspect `.debug/` request/response dumps.

**Function too large**
- Increase `plugin.max_function_size`, but beware LLM timeout / context limits.
- Consider splitting the function manually first.

---

## Performance Reference

| Model | GPU | Latency / function |
|-------|-----|--------------------|
| DeepSeek-R1-Distill-Qwen-1.5B | RTX 3060 | ~2–5 s |
| VibeThinker-3B | RTX 3060 | ~4–8 s |
| qwen2.5-coder:7b | RTX 3060 | ~3–5 s |
| deepseek-coder:6.7b | RTX 3060 | ~2–4 s |

---

## License

MIT License — see the `LICENSE` header in source files.

## Author

**BaiGuQing**

## Disclaimer

This plugin generates code via an AI model for **reference only**. Always
verify the output manually before relying on it. Follow all applicable laws
when performing reverse engineering.
