# CHelper — AI-Powered Decompiler Code Optimizer for IDA Pro 9.x

[English](README.md) | [简体中文](README.zh-CN.md)

CHelper is an IDA Pro plugin that uses a **local LLM** (large language model) to
rewrite Hex-Rays pseudo-C output into cleaner, more readable, human-like code —
deobfuscating OLLVM, simplifying expressions, improving variable names, and
adding comments, all with a single hotkey.

> **Why local?** No code leaves your machine. Everything runs against a
> self-hosted model via an OpenAI-compatible API (llama.cpp / vLLM / Ollama).
> An OpenAI-compatible online endpoint is also supported if you opt in.

---

## Features

- **One-hotkey optimize** — `Ctrl+Alt+C` in the decompiler view sends the
  current function to the LLM on a background worker thread and pops the result
  up in a new viewer tab when ready. IDA's UI stays responsive.
- **Non-blocking async pipeline** — extraction and presentation run on the IDA
  thread; only the network request and pure-Python post-processing run off the
  UI thread. Requests issued while the backend is still warming up are queued
  and auto-start when the service becomes ready.
- **Local model backends** — bundled llama.cpp server, vLLM, Ollama, or any
  OpenAI-compatible endpoint (local or online).
- **Reasoning-model aware** — automatically strips `<think>…</think>` blocks
  emitted by DeepSeek-R1 / VibeThinker style reasoning models, with a fallback
  that recovers the final code when the model puts the answer inside the think
  block. `llm.reasoning: "off"` and `reasoning_budget: 0` keep llama.cpp focused
  on code instead of burning tokens on reasoning.
- **OLLVM deobfuscation** — flattens control-flow flattening, removes bogus
  branches, simplifies magic-number divisions. The deobfuscation instruction is
  only issued when a concrete OLLVM pattern is actually detected, avoiding
  hallucinated "simplifications" of ordinary code.
- **Expression simplification & smart renaming** — collapses redundant
  arithmetic and infers meaningful names from context.
- **Three-tier result classification** — every result is labeled as
  `model_local_rename` (conservative), `model_full_rewrite`, or
  `local_readability_fallback`, and the label is shown in the viewer header.
- **Optimized-code viewer** — syntax-highlighted result in an IDA custom
  viewer that mirrors the native pseudocode color palette, carries Hex-Rays'
  semantic token colors over to renamed locals, supports double-click jumps to
  loaded addresses and IDA symbols, and never mutates the original pseudocode
  view.
- **Disk cache** — repeated optimization of the same function is instant.
  Cache entries are namespaced by prompt version, model, and generation
  parameters, so switching models or key settings invalidates stale entries
  automatically. `Ctrl+Alt+R` forces a cache bypass for a single function.
- **Auto-start llama.cpp / vLLM** — the plugin can spawn the backend process
  for you on load, poll until the API is ready, and clean it up on unload.
- **Safety quality gate** — rejects outputs that lose the function signature,
  calls, string/numeric constants, or IDA global symbols; rejects unverified
  new calls/globals; rejects incomplete fragments, truncated outputs, and
  whitespace-only no-ops. Invalid outputs are never cached and never overwrite
  the original pseudocode.
- **Bounded quality repair** — when a complete model answer fails the semantic
  guard, CHelper runs one stricter repair attempt (using the original pseudocode
  as the only authority) before falling back.
- **Local readability fallback** — if the model returns nothing usable, a
  deterministic, syntax-preserving local pass renames high-confidence local
  variables (e.g. `input_buffer`, `scan_result`, `input_cursor`) and re-runs
  all syntax and semantic checks. The result is clearly labeled.
- **Hotkey conflict resolution** — `Ctrl+Alt+C` / `Ctrl+Alt+R` are chosen over
  the legacy `Ctrl+Shift+C` / `Ctrl+Shift+R` (which collide with IDA built-ins);
  conflicts with other actions are detected automatically and a free fallback
  is selected.
- **Right-click popup menu** — *CHelper → 优化伪C代码* / *优化伪C代码（强制刷新）*
  attached to the pseudocode widget popup via `UI_Hooks`.
- **Config validation** — `config.json` is validated on load; friendly errors
  are shown in IDA's Output window and a warning dialog.
- **Regression test suite** — pure-Python modules (cache, processor, llm_client,
  presenter, service_manager, config_validator, handler) are covered by
  `tests/` and can be run without IDA.
- **IDA 9.0+ support.**

---

## Requirements

| Component | Details |
|-----------|---------|
| IDA Pro | 9.0 or later (SDK ≥ 900) |
| Hex-Rays | Decompiler (F5) installed |
| Python | IDA's bundled Python 3 |
| OS | Windows (bundled llama.cpp binaries are `.exe`); Linux/macOS work if you supply your own `llama-server` / `vllm` |
| GPU | Recommended (CUDA) for acceptable latency; CPU-only works but is slow |
| Disk | ~1–6 GB depending on the model you choose |

---

## Installation

### 1. Get the code

```bash
git clone https://github.com/BaiGuQing/CHelper.git
```

The plugin uses the `requests` package. If it is not already available in
IDA's Python environment, install it there before loading the plugin:

```bash
python -m pip install -r requirements.txt
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
        ├── tests/          ← regression tests (not required at runtime)
        ├── .llama_bin/     ← (optional) llama.cpp binaries, see step 4
        └── <model>.gguf    ← (optional) model weights, see step 3
```

**Concrete steps:**

1. Copy `loader/CHelper.py` → `<IDA>/plugins/CHelper.py`
2. Copy the rest of the repo (`__init__.py`, `CHelper.py`, `config.py`,
   `config.json`, `*.py`, `requirements.txt`, `tests/`) →
   `<IDA>/plugins/CHelper/`

> **IDA plugins directory locations**
> - **Windows:** `C:\Program Files\IDA Pro 9.x\plugins\`
> - **Linux:** `~/.idapro/plugins/`  *(or `$IDA/plugins/`)*
> - **macOS:** `/Applications/IDA Pro 9.x/idabin/plugins/`

### 3. Download a model

Model weights are **not** included in the repo (too large for Git). Download one
of the recommended GGUF files and place it inside `<IDA>/plugins/CHelper/`:

| Model | Size | Format | Notes |
|-------|------|--------|-------|
| DeepSeek-R1-SFT / Distill-Qwen-1.5B | ~1.1 GB | GGUF Q4_K_M | Trial-only; cannot reliably preserve complex decompiler semantics |
| VibeThinker-3B | ~2 GB | GGUF / safetensors | Suitable for light cleanup; still review every result |
| **qwen2.5-coder:7b** | ~4.7 GB | GGUF / Ollama | Recommended baseline: balanced code quality and speed |
| deepseek-coder:6.7b | ~3.8 GB | GGUF / Ollama | Fast, lower VRAM |

> The default `config.json` ships with `auto_start: true` and a local
> `DeepSeek-R1-SFT-Q4_K_M.gguf` model path for the bundled llama.cpp server.
> To use the already-running Ollama endpoint instead, set `auto_start: false`,
> `api_url: "http://127.0.0.1:11434/v1/chat/completions"`, and
> `model: "qwen2.5-coder:7b"`.

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
[CHelper] v1.3.0 加载成功
[CHelper] 在反编译窗口按 Ctrl+Alt+C 优化代码
[CHelper] 在反编译窗口按 Ctrl+Alt+R 强制刷新（忽略缓存）
[CHelper] LLM API: http://127.0.0.1:8000/v1/chat/completions
[CHelper] 模型: DeepSeek-R1-SFT-Q4_K_M.gguf
```

If `auto_start` is on, the plugin will spawn `llama-server` in the background
and wait for it to be ready (first load into VRAM takes ~30–90 s). Requests
issued before the service is ready are queued and auto-start when it becomes
available.

---

## Usage

1. Open the target binary in IDA.
2. Press `F5` to decompile a function with Hex-Rays.
3. **In the pseudocode window, press `Ctrl+Alt+C`**
   (or right-click → *CHelper → 优化伪C代码*).
4. Wait a few seconds for the LLM to process. A wait box shows progress and
   you can cancel with the IDA cancel button.
5. The optimized code opens in a new viewer tab
   (`CHelper - <function> @ <addr>`) with a header labeling the result kind,
   function name, address, line-count diff, and the number of renamed locals.
6. Compare with the original pseudocode and manually apply renames/comments.
   Double-click a loaded address or IDA symbol in the viewer to jump to it.

### Hotkeys

| Hotkey | Action |
|--------|--------|
| `Ctrl+Alt+C` | Optimize current function (uses cache if available) |
| `Ctrl+Alt+R` | Force re-optimize, bypassing the cache |

> If either hotkey collides with another action, CHelper automatically picks a
> free fallback (e.g. `Ctrl+Alt+Shift+C`) and prints the chosen key on load.

### Result kinds

| Label in viewer | Meaning |
|-----------------|---------|
| `CHelper 模型保守美化` | Model rewrote only local identifiers + added comments (conservative mode) |
| `CHelper 模型全量重写` | Model rewrote expressions / control flow / locals (full mode) |
| `CHelper 本地安全美化` | Model output was unusable; a deterministic local rename pass was applied instead |

### Cache

Results are cached on disk using the function address, pseudocode, function
context, model/config fingerprint, prompt version, and cache schema version.
Re-optimizing an unchanged function is instant. Switching models or key
generation parameters invalidates stale entries automatically. To clear the
cache entirely, delete the `.cache/` directory inside the plugin folder, or
press `Ctrl+Alt+R` for a single function.

---

## Configuration

Edit `config.json` in the plugin directory. Key fields:

### `llm` — model & backend

```jsonc
{
  "llm": {
    "api_url": "http://127.0.0.1:8000/v1/chat/completions",
    "model": "DeepSeek-R1-SFT-Q4_K_M.gguf",
    "temperature": 0.0,
    "max_tokens": 2048,
    "timeout": 300,
    "api_key": "",

    "strip_reasoning": true,          // strip <think>…</think> from reasoning models
    "reasoning_tags": ["think", "thinking"],
    "reasoning": "off",               // llama.cpp: prioritize code over thinking tokens
    "reasoning_budget": 0,            // end <think> immediately when supported

    "auto_start": true,               // spawn llama-server / vllm on load
    "backend": "llama_cpp",           // "llama_cpp" | "vllm" | "openai"
    "model_path": "DeepSeek-R1-SFT-Q4_K_M.gguf",
    "vllm_binary": "vllm",
    "llama_server_binary": "",        // empty = auto-find .llama_bin/llama-server.exe
    "n_gpu_layers": -1,               // -1 = offload all to GPU
    "context_size": 8192,
    "startup_timeout": 180,

    // generation quality
    "repeat_penalty": 1.05,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "send_extended_parameters": false,// set true to send top_k/min_p to llama.cpp
    "top_p": 0.95,
    "top_k": 40,
    "min_p": 0.0,
    "seed": null,                     // integer for reproducible sampling

    // robustness / safety
    "conservative_mode": "off",       // "off" | "on" | "auto"
    "degeneration_guard": true,       // truncate repeated-line degeneration
    "quality_guard": true,            // reject outputs missing semantic anchors
    "minimum_output_ratio": 0.30,     // reject outputs shorter than this ratio
    "restore_unused_parameter_signature": true,
    "local_readability_fallback": true,  // deterministic rename fallback
    "quality_repair_attempts": 1,     // bounded strict retry after a rejection
    "max_retry_attempts": 3,          // HTTP retries
    "retry_delay": 1.0
  }
}
```

**`conservative_mode`** controls rewrite risk:
- `"off"` — allow full rewrites, redundant-code removal, and control-flow
  changes; the quality gate still checks the signature, completeness, output
  size, and new calls/globals.
- `"on"` *(recommended for decompiler output)* — only rename local variables
  and add comments, never touch logic.
- `"auto"` — use conservative mode only for detected hard cases (OLLVM magic
  division, etc.); pair it with a 7B+ code model.

To disable semantic-anchor checks entirely in full mode, also set
`"quality_guard": false`. This accepts outputs that may remove original calls,
globals, or constants and should only be used with a trusted model.

### `plugin` — UI & runtime

```jsonc
{
  "plugin": {
    "hotkey": "Ctrl+Alt+C",
    "hotkey_force": "Ctrl+Alt+R",
    "max_function_size": 10000,
    "debug": false,                   // save LLM req/resp to .debug/
    "log_file": "chelper.log",
    "enable_cache": true,
    "cache_dir": ".cache",
    "cache_max_age_days": 30,
    "cache_cleanup_on_start": true
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
    "unroll_simple_loops": false,
    "rewrite_control_flow": true
  }
}
```

`rewrite_control_flow: true` lets the model actively rewrite pointer loops,
sentinel bounds, and branch structure. The prompt still requires checking bounds,
termination, return values, and side effects so behavior remains equivalent.

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
    "strip_reasoning": false,
    "conservative_mode": "on"
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

### Using an OpenAI-compatible online model

CHelper sends the standard `POST /v1/chat/completions` request. OpenAI,
DeepSeek, OpenRouter, and other compatible providers use the same configuration:

```jsonc
{
  "llm": {
    "backend": "openai",
    "api_url": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "api_key": "sk-replace-with-your-key",
    "auto_start": false,
    "send_extended_parameters": false,
    "conservative_mode": "on"
  }
}
```

For another provider, replace `api_url`, `model`, and `api_key`. `api_url` may be
the full `/v1/chat/completions` endpoint or just the `/v1` base URL; CHelper
completes the endpoint automatically. The key stays in the local `config.json`;
do not commit it to the repository.

---

## Project Structure

```
CHelper/                     ← repo root = plugin package
├── loader/
│   └── CHelper.py           ← bootstrap loader (copy to plugins/CHelper.py)
├── tests/                   ← regression tests (run without IDA)
│   ├── __init__.py
│   ├── test_config_validator.py
│   ├── test_handler_quality_repair.py
│   ├── test_llm_client.py
│   ├── test_presenter.py
│   ├── test_processor.py
│   └── test_service_manager.py
├── __init__.py              ← package init, lazy PLUGIN_ENTRY
├── CHelper.py               ← main plugin class (IDA plugin_t) + popup hooks
├── handler.py               ← async optimization orchestration & quality repair
├── extractor.py             ← pseudocode, context & semantic color extraction
├── llm_client.py            ← LLM API client + prompt/repair prompt builder
├── processor.py             ← post-processing, tokenizer, semantic anchor guard
├── presenter.py             ← custom viewer with C syntax highlight & dbl-click
├── service_manager.py       ← auto-start llama-server / vllm subprocess
├── cache.py                 ← namespaced disk cache (schema v3)
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

### Running the tests

The pure-Python modules can be tested without IDA. From the repo root:

```bash
python -m unittest discover -s tests -v
# or, if pytest is available:
pytest tests/
```

---

## Troubleshooting

**Plugin doesn't load**
- Verify IDA ≥ 9.0 and Hex-Rays is installed.
- Check `loader/CHelper.py` was copied to `plugins/CHelper.py` (not inside the
  `CHelper/` subdirectory).
- Look for errors in IDA's Output window. Config validation errors also pop up
  a warning dialog.

**LLM call fails**
- If using `auto_start`, give the model time to load into VRAM on first use
  (~30–90 s). You'll see "模型服务正在启动中" if it's still starting; queued
  requests auto-start when the service is ready.
- Test the endpoint manually: `curl http://localhost:8000/v1/models`
- Ensure `llm.model` matches the name the server reports.
- Check `chelper.log` for details.

**`<think>` blocks leak into the output**
- Set `llm.strip_reasoning: true` and make sure the tag is listed in
  `llm.reasoning_tags`.
- For llama.cpp, also set `llm.reasoning: "off"` and `reasoning_budget: 0`.

**Optimization quality is poor**
- 1–3B reasoning models are not reliable for semantic decompiler rewrites; use a
  code-focused 7B+ Instruct model such as `qwen2.5-coder:7b`.
- Keep `llm.reasoning: "off"`, `temperature: 0.0`,
  `conservative_mode: "on"`, and `quality_guard: true`. A rejected complete
  answer gets one stricter repair attempt; unsafe output never overwrites or
  caches the original pseudocode.
- If the model returns the source unchanged or fails validation, CHelper can
  apply a clearly labelled local-only readability pass. It only renames
  high-confidence local variables and re-runs syntax and semantic checks.
- Use `Ctrl+Alt+R` to force a refresh, or restart IDA, so new service options
  and the cache schema take effect.
- Enable `plugin.debug: true` and inspect `.debug/` request/response dumps (they
  may contain the code under analysis).

**Function too large**
- Increase `plugin.max_function_size`, but beware LLM timeout / context limits.
- Consider splitting the function manually first.

**Hotkey doesn't work**
- Another action may own the configured key. CHelper prints the resolved
  hotkey on load; check the Output window for a "快捷键 … 冲突" message and
  bind a free key via IDA's Shortcut editor if needed.

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
