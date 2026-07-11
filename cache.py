# -*- coding: utf-8 -*-
"""
缓存管理 - 避免重复优化同一函数
"""

import os
import json
import hashlib
import time
from typing import Optional, Dict, Any


class OptimizationCache:
    """优化结果缓存

    根据函数地址 + 伪代码 hash 缓存优化结果
    缓存格式: {
        "hash": "...",
        "timestamp": 1234567890,
        "function_name": "...",
        "address": "...",
        "original_code": "...",
        "optimized_code": "...",
        "stats": {...}
    }
    """

    def __init__(self, cache_dir: str, max_age_days: int = 30):
        """初始化缓存

        Args:
            cache_dir: 缓存目录路径
            max_age_days: 缓存最大有效期（天）
        """
        self.cache_dir = cache_dir
        self.max_age_seconds = max_age_days * 86400
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except Exception as e:
            print(f"[CHelper Cache] 创建缓存目录失败: {e}")

    def _compute_hash(self, address: str, pseudocode: str) -> str:
        """计算缓存键的哈希值

        Args:
            address: 函数地址
            pseudocode: 伪C代码

        Returns:
            SHA256 哈希值
        """
        content = f"{address}:{pseudocode}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _get_cache_path(self, cache_hash: str) -> str:
        """获取缓存文件路径

        Args:
            cache_hash: 缓存哈希值

        Returns:
            缓存文件完整路径
        """
        return os.path.join(self.cache_dir, f"{cache_hash}.json")

    def get(self, address: str, pseudocode: str) -> Optional[Dict[str, Any]]:
        """从缓存获取优化结果

        Args:
            address: 函数地址
            pseudocode: 伪C代码

        Returns:
            缓存的优化结果，未命中返回 None
        """
        cache_hash = self._compute_hash(address, pseudocode)
        cache_path = self._get_cache_path(cache_hash)

        if not os.path.exists(cache_path):
            return None

        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 检查缓存是否过期
            timestamp = data.get("timestamp", 0)
            if time.time() - timestamp > self.max_age_seconds:
                # 缓存过期，删除并返回 None
                os.remove(cache_path)
                return None

            return data

        except Exception as e:
            print(f"[CHelper Cache] 读取缓存失败: {e}")
            return None

    def set(
        self,
        address: str,
        pseudocode: str,
        optimized_code: str,
        function_name: str = "",
        stats: Optional[Dict[str, Any]] = None
    ):
        """保存优化结果到缓存

        Args:
            address: 函数地址
            pseudocode: 原始伪C代码
            optimized_code: 优化后的代码
            function_name: 函数名
            stats: 统计信息
        """
        cache_hash = self._compute_hash(address, pseudocode)
        cache_path = self._get_cache_path(cache_hash)

        data = {
            "hash": cache_hash,
            "timestamp": time.time(),
            "function_name": function_name,
            "address": address,
            "original_code": pseudocode,
            "optimized_code": optimized_code,
            "stats": stats or {}
        }

        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CHelper Cache] 保存缓存失败: {e}")

    def clear(self) -> int:
        """清空所有缓存

        Returns:
            删除的缓存文件数量
        """
        count = 0
        try:
            for filename in os.listdir(self.cache_dir):
                if filename.endswith('.json'):
                    filepath = os.path.join(self.cache_dir, filename)
                    os.remove(filepath)
                    count += 1
        except Exception as e:
            print(f"[CHelper Cache] 清空缓存失败: {e}")

        return count

    def cleanup_expired(self) -> int:
        """清理过期缓存

        Returns:
            删除的缓存文件数量
        """
        count = 0
        try:
            now = time.time()
            for filename in os.listdir(self.cache_dir):
                if not filename.endswith('.json'):
                    continue

                filepath = os.path.join(self.cache_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    timestamp = data.get("timestamp", 0)
                    if now - timestamp > self.max_age_seconds:
                        os.remove(filepath)
                        count += 1
                except Exception:
                    # 损坏的缓存文件也删除
                    os.remove(filepath)
                    count += 1

        except Exception as e:
            print(f"[CHelper Cache] 清理缓存失败: {e}")

        return count

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息

        Returns:
            统计信息字典
        """
        stats = {
            "total_entries": 0,
            "total_size_bytes": 0,
            "expired_entries": 0
        }

        try:
            now = time.time()
            for filename in os.listdir(self.cache_dir):
                if not filename.endswith('.json'):
                    continue

                filepath = os.path.join(self.cache_dir, filename)
                stats["total_entries"] += 1
                stats["total_size_bytes"] += os.path.getsize(filepath)

                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    timestamp = data.get("timestamp", 0)
                    if now - timestamp > self.max_age_seconds:
                        stats["expired_entries"] += 1
                except Exception:
                    stats["expired_entries"] += 1

        except Exception as e:
            print(f"[CHelper Cache] 获取统计失败: {e}")

        return stats


# 全局缓存实例
_global_cache: Optional[OptimizationCache] = None


def init_cache(config) -> OptimizationCache:
    """初始化全局缓存

    Args:
        config: Config 实例

    Returns:
        OptimizationCache 实例
    """
    global _global_cache

    if _global_cache is None:
        cache_dir = config.get("plugin.cache_dir", "")
        if not cache_dir:
            # 默认缓存目录
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            cache_dir = os.path.join(plugin_dir, ".cache")

        # 支持相对路径
        if not os.path.isabs(cache_dir):
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            cache_dir = os.path.join(plugin_dir, cache_dir)

        max_age_days = config.get("plugin.cache_max_age_days", 30)
        _global_cache = OptimizationCache(cache_dir, max_age_days)

        # 启动时清理过期缓存
        if config.get("plugin.cache_cleanup_on_start", True):
            expired = _global_cache.cleanup_expired()
            if expired > 0:
                print(f"[CHelper Cache] 清理了 {expired} 个过期缓存")

    return _global_cache


def get_cache() -> Optional[OptimizationCache]:
    """获取全局缓存实例

    Returns:
        OptimizationCache 实例，未初始化返回 None
    """
    return _global_cache
