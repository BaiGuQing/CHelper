# -*- coding: utf-8 -*-
"""
常量定义 - 避免魔法数字和字符串
"""

# ===== 重度混淆检测 =====

# OLLVM 魔数除法检测阈值
HEAVY_OBF_PATTERN_THRESHOLD = 2

# ===== 退化检测 =====

# 重复行触发截断的阈值
DEGENERATION_THRESHOLD = 4

# 语义安全检查默认允许的最小输出长度比例
DEFAULT_MINIMUM_OUTPUT_RATIO = 0.45

# ===== LLM 参数默认值 =====

# 默认温度
DEFAULT_TEMPERATURE = 0.3

# 默认最大 token 数
DEFAULT_MAX_TOKENS = 4096

# 默认超时（秒）
DEFAULT_TIMEOUT = 180

# 默认重复惩罚
DEFAULT_REPEAT_PENALTY = 1.15

# 默认频率惩罚
DEFAULT_FREQUENCY_PENALTY = 0.3

# 默认存在惩罚
DEFAULT_PRESENCE_PENALTY = 0.3

# 默认 top_p
DEFAULT_TOP_P = 0.9

# 默认 top_k
DEFAULT_TOP_K = 40

# 默认 min_p
DEFAULT_MIN_P = 0.05

# ===== 网络重试 =====

# 最大重试次数
MAX_RETRY_ATTEMPTS = 3

# 初始重试延迟（秒）
INITIAL_RETRY_DELAY = 1.0

# 重试延迟倍数（指数退避）
RETRY_DELAY_MULTIPLIER = 2.0

# 最大重试延迟（秒）
MAX_RETRY_DELAY = 30.0

# ===== 缓存 =====

# 默认缓存有效期（天）
DEFAULT_CACHE_MAX_AGE_DAYS = 30

# ===== 服务管理 =====

# 默认启动超时（秒）
DEFAULT_STARTUP_TIMEOUT = 180

# 服务检测轮询间隔（秒）
SERVICE_POLL_INTERVAL = 3.0

# API 就绪检测超时（秒）
API_READY_TIMEOUT = 3.0

# 端口连接超时（秒）
PORT_CONNECT_TIMEOUT = 2.0

# ===== IDA 配置 =====

# 支持的最低 IDA 版本（主版本号）
MIN_IDA_MAJOR_VERSION = 9

# 默认函数最大大小（字符数）
DEFAULT_MAX_FUNCTION_SIZE = 10000

# ===== 语法检查 =====

# 正则表达式：函数签名检测
FUNCTION_SIGNATURE_PATTERN = (
    r'^\s*(?:(?:const|volatile|static|inline|extern|unsigned|signed|long|short|'
    r'struct|enum|union|class|__declspec\([^)]*\))\s+)*'
    # The separator between return type and function name may be whitespace
    # (`int foo`) or a pointer declarator (`char *__fastcall foo`).
    r'[A-Za-z_]\w*(?:\s+\*+\s*|\s+|\s*\*+\s*)'
    r'(?:(?:__cdecl|__stdcall|__fastcall|__thiscall|__vectorcall)\s*)?'
    r'[A-Za-z_]\w*\s*\('
)

# 正则表达式：IDA 变量命名模式
IDA_VAR_PATTERN = r'^[a-zA-Z]\d*$|^[a-z]+_\d+$'

# ===== 调试 =====

# 调试输出目录名
DEBUG_OUTPUT_DIR = ".debug"

# 请求/响应保存文件名前缀
DEBUG_REQUEST_PREFIX = "request_"
DEBUG_RESPONSE_PREFIX = "response_"

# ===== 日志 =====

# 默认日志文件名
DEFAULT_LOG_FILENAME = "chelper.log"

# 日志时间格式
LOG_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# ===== C 语言关键字和类型（用于语法高亮和检测） =====

C_KEYWORDS = frozenset([
    'auto', 'break', 'case', 'const', 'continue', 'default',
    'do', 'else', 'enum', 'extern', 'for', 'goto',
    'if', 'inline', 'register', 'restrict', 'return',
    'sizeof', 'static', 'struct', 'switch', 'typedef',
    'union', 'volatile', 'while', '_Bool', '_Complex',
    '_Imaginary', 'asm', '__cdecl', '__stdcall', '__fastcall',
])

C_TYPES = frozenset([
    'int', 'char', 'void', 'float', 'double', 'short', 'long',
    'unsigned', 'signed', 'bool', 'true', 'false', 'NULL',
    'size_t', 'ssize_t', 'wchar_t', 'ptrdiff_t',
    'int8_t', 'int16_t', 'int32_t', 'int64_t',
    'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t',
    '__int8', '__int16', '__int32', '__int64',
    'BYTE', 'WORD', 'DWORD', 'QWORD', 'BOOL', 'HANDLE',
    'HMODULE', 'LPVOID', 'LPCSTR', 'LPSTR', 'PVOID',
    'LPCWSTR', 'LPWSTR', 'HINSTANCE', 'HWND', 'HKEY',
])

# ===== OLLVM 混淆特征正则（预编译） =====

# 会在需要的模块中预编译这些模式
OLLVM_MAGIC_PATTERNS = [
    r'0x[CcCcCcCcCcCcCcCc]{16}',  # 除以 5/10 等魔数
    r'0x[AaAaAaAaAaAaAaAa]{16}',
    r'0x[0-9A-Fa-f]{16}\s*[uU]?[lL]{2}',  # 64 位魔数乘法
    r'unsigned\s+__int128',
    r'>>\s*63',
    r'__int128',
    r'\(\s*int\s*\)\s*\w+\s*>>',  # 算术右移当除法
]

# ===== 保守模式 =====

# 保守模式选项
CONSERVATIVE_MODE_AUTO = "auto"
CONSERVATIVE_MODE_ON = "on"
CONSERVATIVE_MODE_OFF = "off"
