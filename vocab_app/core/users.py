"""多用户管理：每个用户一个独立的 SQLite 数据库，互不影响。

设计要点
--------
* 注册表 ``data/users.json``：显示名 → 相对 db 路径 + 当前用户。
  相对路径以 ``data`` 目录为基准，换机器/换目录整个 data 一起复制即可。
* 现有 ``data/vocab.db`` 首次运行自动登记为「默认用户」，**不移动文件**，
  老用户升级后数据原样保留。
* 新用户的 db 放到 ``data/users/u1.db / u2.db ...``，用序号命名避免
  中文用户名带来的文件系统编码问题；显示名只存在注册表里。
* 删除用户前先把它的 db 备份到 ``data/backup/users/``，再删文件，可恢复。
* 注册表用「临时文件 + os.replace」原子写，中途断电也不会写坏。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Optional

from .db import DATA_DIR, DEFAULT_DB_PATH, VocabDB, now

REGISTRY_PATH = os.path.join(DATA_DIR, "users.json")
USERS_DIR = os.path.join(DATA_DIR, "users")
BACKUP_USERS_DIR = os.path.join(DATA_DIR, "backup", "users")
DEFAULT_USER = "默认用户"


def _atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class UserManager:
    """用户注册表的读写；不负责具体词条逻辑。"""

    def __init__(self, registry_path: str | None = None):
        # 运行时取模块常量，便于测试注入临时目录（默认值会在定义时固化）
        self.registry_path = registry_path or REGISTRY_PATH
        self.data = self._load()

    # ------------------------------------------------------------ 注册表
    def _load(self) -> dict:
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("users"), dict) and data["users"]:
                # 兼容：任何在 data/ 下或绝对路径的旧注册项都接受
                return data
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        # 首次运行：把现有 vocab.db 登记为默认用户，绝不搬动文件
        data = {
            "version": 1,
            "active": DEFAULT_USER,
            "users": {DEFAULT_USER: os.path.basename(DEFAULT_DB_PATH)},
            "created_at": now(),
        }
        _atomic_write_json(self.registry_path, data)
        return data

    def _save(self) -> None:
        _atomic_write_json(self.registry_path, self.data)

    # ------------------------------------------------------------ 查询
    def users(self) -> list[str]:
        return list(self.data.get("users", {}).keys())

    def active_user(self) -> str:
        active = self.data.get("active", "")
        if active in self.data.get("users", {}):
            return active
        users = self.users()
        return users[0] if users else ""

    def set_active(self, name: str) -> None:
        if name in self.data.get("users", {}):
            self.data["active"] = name
            self._save()

    def db_path(self, name: str) -> Optional[str]:
        rel = self.data.get("users", {}).get(name)
        if not rel:
            return None
        return rel if os.path.isabs(rel) else os.path.join(DATA_DIR, rel)

    # ------------------------------------------------------------ 增删改
    def add_user(self, name: str) -> tuple[bool, str]:
        """新建用户，返回 (是否成功, 提示)。仅创建空数据库，不写任何词条。"""
        name = (name or "").strip()
        if not name:
            return False, "用户名不能为空"
        if len(name) > 24:
            return False, "用户名最多 24 个字"
        if name in self.data.get("users", {}):
            return False, f"已经有叫「{name}」的用户了"
        if re.search(r'[\\/:*?"<>|]', name):
            return False, "用户名不能包含 \\ / : * ? \" < > | 这些符号"

        os.makedirs(USERS_DIR, exist_ok=True)
        # 用序号做文件名，中文名只存在注册表里，避免编码/路径问题
        n = 1
        while os.path.exists(os.path.join(USERS_DIR, f"u{n}.db")) or any(
            v == f"users{os.sep}u{n}.db" for v in self.data["users"].values()
        ):
            n += 1
        rel = f"users{os.sep}u{n}.db"
        path = os.path.join(DATA_DIR, rel)
        try:
            VocabDB(path)  # 建库 + 建表，空白启动
        except Exception as e:
            return False, f"创建数据库失败：{e}"
        self.data["users"][name] = rel
        self.data["active"] = name
        self._save()
        return True, f"已创建用户「{name}」，这是一个全新的空白词库"

    def rename_user(self, old: str, new: str) -> tuple[bool, str]:
        new = (new or "").strip()
        if not new or len(new) > 24:
            return False, "用户名需为 1–24 个字"
        if new == old:
            return True, "名字没变"
        if new in self.data.get("users", {}):
            return False, f"已经有叫「{new}」的用户了"
        rel = self.data["users"].get(old)
        if not rel:
            return False, "找不到这个用户"
        # 按原名在字典中的位置重建，保持顺序
        rebuilt = {
            (new if k == old else k): (rel if k == old else v)
            for k, v in self.data["users"].items()
        }
        self.data["users"] = rebuilt
        if self.data.get("active") == old:
            self.data["active"] = new
        self._save()
        return True, f"已把「{old}」改名为「{new}」，数据完全不变"

    def delete_user(self, name: str) -> tuple[bool, str]:
        """删除用户：先备份 db，再删文件与注册项。不能删最后一个用户。"""
        if name not in self.data.get("users", {}):
            return False, "找不到这个用户"
        if len(self.data["users"]) <= 1:
            return False, "至少保留一个用户"
        rel = self.data["users"][name]
        path = rel if os.path.isabs(rel) else os.path.join(DATA_DIR, rel)
        try:
            os.makedirs(BACKUP_USERS_DIR, exist_ok=True)
            safe = re.sub(r'[^\w\u4e00-\u9fff-]', "_", name)[:24] or "user"
            stamp = now().replace(":", "").replace("-", "")
            backup = os.path.join(BACKUP_USERS_DIR, f"{safe}_{stamp}.db")
            if os.path.exists(path):
                src = VocabDB(path)
                src.backup_to(backup)
                del src
                for suffix in ("-wal", "-shm"):
                    p = path + suffix
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                os.remove(path)
        except Exception as e:
            return False, f"删除失败：{e}"
        del self.data["users"][name]
        if self.data.get("active") == name:
            self.data["active"] = self.users()[0]
        self._save()
        return True, f"已删除用户「{name}」，数据库已备份到 {backup}"
