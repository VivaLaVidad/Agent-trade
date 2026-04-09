"""
core.security — 硬件机器码鉴权 & AES-256-GCM 加解密
─────────────────────────────────────────────────────
职责边界：
  1. 采集多维硬件标识，生成/校验 HMAC 签名的 Machine Token
  2. 提供 AES-256-GCM 对称加解密（字节级 & 字符串级）
  3. FastAPI 依赖注入鉴权守卫（X-Hardware-Token 请求头）
  4. 全局单例密码器 get_cipher()，供 ORM 加密列透明调用
"""

from __future__ import annotations

import hashlib
import hmac
import os
import platform
import secrets
import subprocess
import uuid
from functools import lru_cache
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Header, HTTPException
from pydantic_settings import BaseSettings

from core.logger import get_logger

logger = get_logger(__name__)

_NONCE_SIZE = 12
_KEY_SIZE = 32


# ─── Configuration ───────────────────────────────────────────
class SecuritySettings(BaseSettings):
    MACHINE_SECRET_SALT: str = "OmniEdge_v1_salt"
    AES_MASTER_KEY: str = ""  # hex-encoded 32-byte key

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache()
def _settings() -> SecuritySettings:
    return SecuritySettings()


# ═════════════════════════════════════════════════════════════
#  硬件机器码 (Machine Fingerprint)
# ═════════════════════════════════════════════════════════════
class MachineAuth:

    @staticmethod
    def _collect_hardware_ids() -> list[str]:
        """采集多维硬件标识，组合后抗单点伪造"""
        ids: list[str] = []

        ids.append(str(uuid.getnode()))

        ids.append(platform.node())

        if platform.system() == "Windows":
            try:
                out = subprocess.check_output(
                    "wmic csproduct get uuid",
                    shell=True, timeout=5, stderr=subprocess.DEVNULL,
                ).decode().strip().split("\n")
                if len(out) >= 2:
                    ids.append(out[-1].strip())
            except Exception:
                pass

            try:
                out = subprocess.check_output(
                    "wmic baseboard get serialnumber",
                    shell=True, timeout=5, stderr=subprocess.DEVNULL,
                ).decode().strip().split("\n")
                if len(out) >= 2:
                    ids.append(out[-1].strip())
            except Exception:
                pass
        else:
            for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    with open(path) as f:
                        ids.append(f.read().strip())
                        break
                except FileNotFoundError:
                    continue

        return [i for i in ids if i]

    @classmethod
    def get_machine_id(cls) -> str:
        """生成当前机器的唯一指纹 (SHA-256)"""
        raw_ids = cls._collect_hardware_ids()
        salt = _settings().MACHINE_SECRET_SALT.encode()
        payload = "|".join(sorted(raw_ids)).encode()
        return hashlib.sha256(salt + payload).hexdigest()

    @classmethod
    def generate_token(cls) -> str:
        """基于机器码生成 HMAC 签名 Token"""
        machine_id = cls.get_machine_id()
        salt = _settings().MACHINE_SECRET_SALT.encode()
        return hmac.new(salt, machine_id.encode(), hashlib.sha256).hexdigest()

    @classmethod
    def verify_token(cls, token: str) -> bool:
        expected = cls.generate_token()
        return hmac.compare_digest(token, expected)


# ═════════════════════════════════════════════════════════════
#  AES-256-GCM 加解密
# ═════════════════════════════════════════════════════════════
class AESCipher:

    def __init__(self, key_hex: Optional[str] = None):
        raw_key = key_hex or _settings().AES_MASTER_KEY
        if not raw_key:
            raise RuntimeError("AES_MASTER_KEY 未配置，请在 .env 中设置 64 位 hex 字符串")
        self._aesgcm = AESGCM(bytes.fromhex(raw_key))

    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> bytes:
        """
        返回 nonce(12B) || ciphertext || tag(16B)
        可选 AAD 做关联数据认证
        """
        nonce = secrets.token_bytes(_NONCE_SIZE)
        ct = self._aesgcm.encrypt(nonce, plaintext, aad)
        return nonce + ct

    def decrypt(self, ciphertext: bytes, aad: Optional[bytes] = None) -> bytes:
        if len(ciphertext) < _NONCE_SIZE + 16:
            raise ValueError("密文长度不合法")
        nonce = ciphertext[:_NONCE_SIZE]
        ct = ciphertext[_NONCE_SIZE:]
        return self._aesgcm.decrypt(nonce, ct, aad)

    def encrypt_string(self, plaintext: str, *, encoding: str = "utf-8") -> bytes:
        """将 UTF-8 明文字符串加密为密文字节串（nonce ‖ ciphertext ‖ tag）

        Parameters
        ----------
        plaintext : str
            待加密的明文字符串
        encoding : str
            字符编码，默认 UTF-8

        Returns
        -------
        bytes
            nonce(12B) || 密文 || GCM-tag(16B) 的拼接字节串
        """
        return self.encrypt(plaintext.encode(encoding))

    def decrypt_string(self, ciphertext: bytes, *, encoding: str = "utf-8") -> str:
        """将密文字节串解密并还原为 UTF-8 明文字符串

        Parameters
        ----------
        ciphertext : bytes
            由 encrypt / encrypt_string 产生的密文字节串
        encoding : str
            字符编码，默认 UTF-8

        Returns
        -------
        str
            解密后的明文字符串

        Raises
        ------
        ValueError
            密文长度不合法
        cryptography.exceptions.InvalidTag
            密文被篡改或密钥不匹配
        """
        return self.decrypt(ciphertext).decode(encoding)

    @staticmethod
    def generate_key() -> str:
        """生成随机 AES-256 密钥（hex 编码，64 字符）"""
        return secrets.token_hex(_KEY_SIZE)


# ─── 全局单例密码器 ──────────────────────────────────────────
@lru_cache()
def get_cipher() -> AESCipher:
    """获取全局单例 AES-256-GCM 密码器（首次调用时从 .env 加载密钥）

    Returns
    -------
    AESCipher
        可复用的加解密实例，密钥生命周期与进程一致
    """
    return AESCipher()


# ═════════════════════════════════════════════════════════════
#  金融级幂等防护 (IdempotencyGuard)
# ═════════════════════════════════════════════════════════════
class IdempotencyGuard:
    """金融级幂等防护 —— 防止重复交易指令

    持久化使用 SQLAlchemy 模型 ``IdempotencyKey``（create_tables 时创建），
    支持 PostgreSQL / SQLite；主键冲突即视为重复请求。
    数据库不可用时降级为进程内 dict（单进程有效）。
    """

    _DEFAULT_TTL_SECONDS: int = 3600  # 60 分钟

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._local_cache: dict[str, float] = {}  # trade_id → unix expiry
        self._lock = None
        self._db_warned: bool = False

    def _get_lock(self):
        if self._lock is None:
            import asyncio
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def _utc_expiry(seconds: int):
        from datetime import datetime, timedelta, timezone

        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(tzinfo=None)

    async def check_and_acquire(self, trade_id: str) -> bool:
        import time
        from datetime import datetime, timezone

        async with self._get_lock():
            now_ts = time.time()
            expired = [k for k, v in self._local_cache.items() if v < now_ts]
            for k in expired:
                del self._local_cache[k]

            if trade_id in self._local_cache:
                logger.warning(
                    "幂等拦截(内存): trade_id=%s 在窗口内重复",
                    trade_id[:16],
                )
                return False

            db_inserted = await self._try_acquire_db(trade_id)
            if db_inserted is False:
                return False
            if db_inserted is True:
                self._local_cache[trade_id] = now_ts + self._ttl
                logger.info("幂等通过: trade_id=%s TTL=%ds (DB+内存)", trade_id[:16], self._ttl)
                return True

            # db_inserted is None → 表不可用，仅内存
            self._local_cache[trade_id] = now_ts + self._ttl
            logger.info("幂等通过: trade_id=%s TTL=%ds (仅内存)", trade_id[:16], self._ttl)
            return True

    async def _try_acquire_db(self, trade_id: str) -> bool | None:
        """True=新插入成功，False=已存在或冲突，None=跳过 DB"""
        try:
            from datetime import datetime, timezone

            from sqlalchemy import delete, select
            from sqlalchemy.exc import IntegrityError

            from database.models import AsyncSessionFactory, IdempotencyKey

            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

            async with AsyncSessionFactory() as session:
                await session.execute(
                    delete(IdempotencyKey).where(IdempotencyKey.expires_at < now_naive),
                )
                if await session.scalar(
                    select(IdempotencyKey.trade_id).where(
                        IdempotencyKey.trade_id == trade_id,
                        IdempotencyKey.expires_at > now_naive,
                    ),
                ) is not None:
                    logger.warning("幂等拦截(DB): trade_id=%s", trade_id[:16])
                    await session.rollback()
                    return False

                session.add(
                    IdempotencyKey(
                        trade_id=trade_id,
                        expires_at=self._utc_expiry(int(self._ttl)),
                    ),
                )
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    return False
            return True
        except Exception as exc:
            if not self._db_warned:
                logger.warning("幂等表不可用，降级内存模式: %s", exc)
                self._db_warned = True
            return None

    async def release(self, trade_id: str) -> None:
        from sqlalchemy import delete

        from database.models import AsyncSessionFactory, IdempotencyKey

        async with self._get_lock():
            self._local_cache.pop(trade_id, None)
        try:
            async with AsyncSessionFactory() as session:
                await session.execute(delete(IdempotencyKey).where(IdempotencyKey.trade_id == trade_id))
                await session.commit()
        except Exception:
            pass
        logger.info("幂等键已释放: trade_id=%s", trade_id[:16])

    async def check_db(self, trade_id: str) -> bool:
        try:
            from datetime import datetime, timezone

            from sqlalchemy import select

            from database.models import AsyncSessionFactory, IdempotencyKey

            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            async with AsyncSessionFactory() as session:
                return (
                    await session.scalar(
                        select(IdempotencyKey.trade_id).where(
                            IdempotencyKey.trade_id == trade_id,
                            IdempotencyKey.expires_at > now_naive,
                        ),
                    )
                    is not None
                )
        except Exception:
            return False


# 全局单例
_idempotency_guard: IdempotencyGuard | None = None


def get_idempotency_guard() -> IdempotencyGuard:
    """获取全局幂等防护单例"""
    global _idempotency_guard
    if _idempotency_guard is None:
        _idempotency_guard = IdempotencyGuard()
    return _idempotency_guard


# ═════════════════════════════════════════════════════════════
#  FastAPI 鉴权依赖
# ═════════════════════════════════════════════════════════════
async def require_machine_auth(
    x_hardware_token: str = Header(..., alias="X-Hardware-Token"),
) -> None:
    """FastAPI 路由鉴权依赖项 —— 校验 X-Hardware-Token 请求头

    用法：在路由装饰器中声明 ``dependencies=[Depends(require_machine_auth)]``
    校验失败返回 HTTP 403，不泄露内部校验细节。

    Parameters
    ----------
    x_hardware_token : str
        客户端通过 ``X-Hardware-Token`` 请求头传入的硬件机器码签名

    Raises
    ------
    HTTPException (403)
        Token 与当前机器指纹不匹配
    """
    if not MachineAuth.verify_token(x_hardware_token):
        logger.warning("机器码鉴权失败 token_prefix=%s", x_hardware_token[:8])
        raise HTTPException(status_code=403, detail="forbidden")
