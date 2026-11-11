"""P4-SEC-02：HsmSigner Protocol + Ed25519 密钥轮换。

MCP 注册表签名校验从 stub 替换为真实 Ed25519 签名 + HSM PKCS#11 支持。

核心架构：
  - SignerProtocol: 签名/验签接口（sign / verify）— 定义在 contracts
  - LocalEd25519Signer: 本地 Ed25519 签名（开发/测试用，密钥存文件系统）
  - HsmPkcs11Signer: HSM PKCS#11 签名（生产用，Phase 4b 接入真实 HSM 设备）
  - KeyRotationJob: 90 天密钥轮换 + 7 天双密钥兼容窗口

密钥轮换规则：
  - 主动钥（primary）：当前签名密钥
  - 前任钥（previous）：轮换后 7 天内仍可验签
  - key_version 递增：v1 → v2 → v3...
  - 轮换后所有已签名 MCP Meta 仍可通过前任钥验证
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from prismclaw.schema.agent.security import SignerProtocol

logger = logging.getLogger(__name__)


# ── SignerProtocol (re-exported from contracts) ───────────────────────


# ── KeyVersion 数据类 ────────────────────────────────────────────────


@dataclass(frozen=True)
class KeyVersion:
    """密钥版本信息。"""
    version: str              # "v1", "v2", ...
    public_key_hex: str       # Ed25519 公钥 hex
    created_at: float         # 创建时间
    rotated_at: float | None  # 轮换时间（None=当前主密钥）
    expires_at: float | None  # 过期时间（前任钥兼容窗口结束后）


# ── LocalEd25519Signer ───────────────────────────────────────────────


class LocalEd25519Signer:
    """本地 Ed25519 签名器（开发/测试用）。

    密钥对存储在内存中（可选持久化到文件系统）。
    支持 90 天密钥轮换 + 7 天双密钥兼容窗口。
    """

    def __init__(
        self,
        *,
        key_dir: str | None = None,
        rotation_interval_days: int = 90,
        compatibility_window_days: int = 7,
    ) -> None:
        self._key_dir = key_dir
        self._rotation_interval = rotation_interval_days * 86400
        self._compat_window = compatibility_window_days * 86400
        self._keys: dict[str, KeyVersion] = {}
        self._primary_version: str = ""
        # 多版本密钥存储 — 轮换时追加而非覆盖，保证 7 天兼容窗口
        self._private_keys: dict[str, Any] = {}
        self._hmac_secrets: dict[str, str] = {}
        self._public_keys_hex: dict[str, str] = {}
        # 每个版本使用的签名算法种类："ed25519" 或 "hmac"
        # verify 必须基于版本对应的算法，不能用当前主密钥类型判断，
        # 否则兼容窗口内跨算法家族的旧签名永远验不过。
        self._key_kind: dict[str, str] = {}
        self._private_key: Any = None       # 当前 Ed25519 主私钥
        self._hmac_secret: str = ""         # 当前 HMAC 主密钥

        # 生成初始密钥
        self._generate_key()

    def _generate_key(self) -> str:
        """生成新的 Ed25519 密钥对。"""
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        except ImportError:
            # fallback: 使用 HMAC-SHA256 作为签名（无 cryptography 库时）
            return self._generate_hmac_fallback()

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        public_hex = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

        # 计算版本号
        version = f"v{len(self._keys) + 1}"
        if self._primary_version:
            old = self._keys.get(self._primary_version)
            if old:
                # 标记旧密钥为前任钥
                self._keys[self._primary_version] = KeyVersion(
                    version=old.version,
                    public_key_hex=old.public_key_hex,
                    created_at=old.created_at,
                    rotated_at=time.time(),
                    expires_at=time.time() + self._compat_window,
                )

        kv = KeyVersion(
            version=version,
            public_key_hex=public_hex,
            created_at=time.time(),
            rotated_at=None,
            expires_at=None,
        )
        self._keys[version] = kv
        self._primary_version = version

        # 追加存储私钥（保留旧密钥以支持兼容窗口验签）
        self._private_key = private_key
        self._private_keys[version] = private_key
        self._public_keys_hex[version] = public_hex
        self._key_kind[version] = "ed25519"

        logger.info("LocalEd25519Signer: generated key %s (pub=%s...)", version, public_hex[:16])
        return version

    def _generate_hmac_fallback(self) -> str:
        """无 cryptography 库时的 HMAC-SHA256 回退签名。"""
        import secrets
        version = f"v{len(self._keys) + 1}"
        if self._primary_version:
            old = self._keys.get(self._primary_version)
            if old:
                self._keys[self._primary_version] = KeyVersion(
                    version=old.version,
                    public_key_hex=old.public_key_hex,
                    created_at=old.created_at,
                    rotated_at=time.time(),
                    expires_at=time.time() + self._compat_window,
                )

        secret = secrets.token_hex(32)
        public_hex = hashlib.sha256(secret.encode()).hexdigest()
        kv = KeyVersion(
            version=version,
            public_key_hex=public_hex,
            created_at=time.time(),
            rotated_at=None,
            expires_at=None,
        )
        self._keys[version] = kv
        self._primary_version = version
        self._hmac_secret = secret
        # 追加 HMAC 密钥（保留旧密钥以支持兼容窗口验签）
        self._hmac_secrets[version] = secret
        self._public_keys_hex[version] = public_hex
        self._key_kind[version] = "hmac"
        # HMAC 回退模式激活后，Ed25519 主私钥清空（后续签名走 HMAC 分支）
        self._private_key = None
        logger.info("LocalEd25519Signer: HMAC fallback key %s", version)
        return version

    def sign(self, payload: bytes) -> str:
        """对 payload 签名。"""
        if self._private_key is not None:
            # 真实 Ed25519 签名
            sig = self._private_key.sign(payload)
            return sig.hex()
        else:
            # HMAC-SHA256 回退
            import hmac as _hmac
            sig = _hmac.new(self._hmac_secret.encode(), payload, hashlib.sha256).hexdigest()
            return sig

    def verify(self, payload: bytes, signature: str, key_version: str | None = None) -> bool:
        """验证签名。支持前任钥在兼容窗口内验证。"""
        ver = key_version or self._primary_version
        kv = self._keys.get(ver)
        if kv is None:
            return False

        # 检查前任钥是否在兼容窗口内
        if kv.rotated_at is not None and kv.expires_at is not None:
            if time.time() > kv.expires_at:
                logger.warning("Key %s expired beyond compatibility window", ver)
                return False

        kind = self._key_kind.get(ver)
        try:
            if kind == "ed25519":
                # Ed25519 验证 — 使用存储的公钥而非从私钥派生
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                pub_hex = self._public_keys_hex.get(ver)
                if pub_hex is None:
                    return False
                public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
                public_key.verify(bytes.fromhex(signature), payload)
                return True
            elif kind == "hmac":
                # HMAC 回退验证
                import hmac as _hmac
                secret = self._hmac_secrets.get(ver)
                if secret is None:
                    return False
                expected = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
                return expected == signature
            else:
                logger.warning("Unknown key kind %r for version %s", kind, ver)
                return False
        except Exception:
            return False

    @property
    def key_version(self) -> str:
        return self._primary_version

    @property
    def public_key_hex(self) -> str:
        kv = self._keys.get(self._primary_version)
        return kv.public_key_hex if kv else ""

    def get_key_versions(self) -> list[KeyVersion]:
        """获取所有密钥版本。"""
        return list(self._keys.values())

    def get_primary_version(self) -> str:
        """获取当前主密钥版本号。"""
        return self._primary_version

    def remove_key_version(self, version: str) -> None:
        """移除指定版本的密钥（清理过期密钥时使用）。"""
        self._keys.pop(version, None)
        self._private_keys.pop(version, None)
        self._hmac_secrets.pop(version, None)
        self._public_keys_hex.pop(version, None)
        self._key_kind.pop(version, None)

    def rotate_key(self) -> str:
        """手动触发密钥轮换。"""
        return self._generate_key()


# ── HsmPkcs11Signer（Phase 4b 骨架）──────────────────────────────────


class HsmPkcs11Signer:
    """HSM PKCS#11 签名器 — Phase 4b 接入真实 HSM 设备。

    当 PKCS#11 库可用时（通过 PRISMCLAW_PKCS11_LIB 环境变量指定路径），
    使用真实 HSM 设备进行签名/验签；否则降级为 LocalEd25519Signer。

    PKCS#11 API：
      - C_SignInit / C_Sign — 签名
      - C_VerifyInit / C_Verify — 验签
      - C_GenerateKeyPair — 密钥生成
    """

    def __init__(self, *, slot_id: int = 0, pin: str = "", key_label: str = "prismclaw-ed25519") -> None:
        self._slot_id = slot_id
        self._pin = pin
        self._key_label = key_label
        self._fallback = LocalEd25519Signer()
        self._pkcs11_lib: Any = None
        self._pkcs11_session: Any = None
        self._pkcs11_key: Any = None
        self._pkcs11_available: bool = False

        # 尝试初始化 PKCS#11 session
        lib_path = os.getenv("PRISMCLAW_PKCS11_LIB", "").strip()
        if lib_path:
            self._init_pkcs11_session(lib_path)
        else:
            logger.info(
                "HsmPkcs11Signer: PRISMCLAW_PKCS11_LIB not set; "
                "using LocalEd25519Signer fallback"
            )

    def _init_pkcs11_session(self, lib_path: str) -> None:
        """初始化 PKCS#11 session。

        Args:
            lib_path: PKCS#11 库路径（.so / .dylib）。
        """
        try:
            import asn1crypto.pkcs11  # noqa: F401 — 检测 PKCS#11 支持
        except ImportError:
            logger.warning(
                "HsmPkcs11Signer: asn1crypto not installed; "
                "cannot use PKCS#11. Run: pip install asn1crypto"
            )
            return

        try:
            from pkcs11 import lib as pkcs11_lib, Mechanism, KeyType

            self._pkcs11_lib = pkcs11_lib(lib_path)
            token = self._pkcs11_lib.get_token(token_slot=self._slot_id)

            # 使用 pin 登录
            pin = self._pin or os.getenv("PRISMCLAW_PKCS11_PIN", "")
            self._pkcs11_session = token.open(user_pin=pin) if pin else token.open()

            # 按 label 查找密钥
            key = self._pkcs11_session.get_key(key_type=KeyType.ED25519, label=self._key_label)
            if key is None:
                # 尝试查找 RSA 密钥
                key = self._pkcs11_session.get_key(key_type=KeyType.RSA, label=self._key_label)
            self._pkcs11_key = key

            if key is not None:
                self._pkcs11_available = True
                logger.info(
                    "HsmPkcs11Signer: PKCS#11 session initialized "
                    "(lib=%s, slot=%d, key_label=%s)",
                    lib_path, self._slot_id, self._key_label,
                )
            else:
                logger.warning(
                    "HsmPkcs11Signer: key label '%s' not found in slot %d; "
                    "falling back to local signer",
                    self._key_label, self._slot_id,
                )
        except ImportError:
            logger.warning(
                "HsmPkcs11Signer: pkcs11 package not installed; "
                "cannot use PKCS#11. Run: pip install python-pkcs11"
            )
        except Exception as e:
            logger.warning(
                "HsmPkcs11Signer: PKCS#11 init failed (lib=%s): %s; "
                "falling back to local signer",
                lib_path, e,
            )
            self._pkcs11_session = None
            self._pkcs11_key = None
            self._pkcs11_available = False

    def sign(self, payload: bytes) -> str:
        """HSM 签名。PKCS#11 可用时使用 C_Sign，否则降级为本地签名。"""
        if self._pkcs11_available and self._pkcs11_key is not None:
            try:
                from pkcs11 import Mechanism
                # Ed25519 使用 EdDSA 机制；RSA 使用 SHA256_RSA_PKCS
                mechanism = Mechanism.EDDSA
                signature = self._pkcs11_key.sign(payload, mechanism=mechanism)
                return bytes(signature).hex()
            except Exception as e:
                logger.warning(
                    "HsmPkcs11Signer: C_Sign failed: %s; falling back to local", e,
                )

        return self._fallback.sign(payload)

    def verify(self, payload: bytes, signature: str, key_version: str | None = None) -> bool:
        """HSM 验签。PKCS#11 可用时使用 C_Verify，否则降级为本地验证。"""
        if self._pkcs11_available and self._pkcs11_key is not None:
            try:
                from pkcs11 import Mechanism
                mechanism = Mechanism.EDDSA
                return self._pkcs11_key.verify(
                    payload, bytes.fromhex(signature), mechanism=mechanism,
                )
            except Exception as e:
                logger.warning(
                    "HsmPkcs11Signer: C_Verify failed: %s; falling back to local", e,
                )

        return self._fallback.verify(payload, signature, key_version)

    @property
    def key_version(self) -> str:
        return self._fallback.key_version

    @property
    def public_key_hex(self) -> str:
        return self._fallback.public_key_hex

    def close(self) -> None:
        """关闭 PKCS#11 session。"""
        if self._pkcs11_session is not None:
            try:
                self._pkcs11_session.close()
                logger.info("HsmPkcs11Signer: PKCS#11 session closed")
            except Exception as e:
                logger.warning("HsmPkcs11Signer: failed to close PKCS#11 session: %s", e)
            finally:
                self._pkcs11_session = None
                self._pkcs11_key = None
                self._pkcs11_available = False


# ── KeyRotationJob ──────────────────────────────────────────────────


class KeyRotationJob:
    """90 天密钥自动轮换 Job。

    触发条件：
      - 主密钥创建超过 rotation_interval_days
      - 可通过 cron/scheduler 定期调用 check_and_rotate()

    轮换流程：
      1. 生成新密钥对
      2. 旧密钥标记为前任钥（兼容窗口 7 天）
      3. 兼容窗口结束后旧密钥过期
      4. 所有已签名 MCP Meta 在兼容窗口内仍可验证
    """

    def __init__(
        self,
        signer: SignerProtocol,
        *,
        rotation_interval_days: int = 90,
        compatibility_window_days: int = 7,
    ) -> None:
        self._signer = signer
        self._rotation_interval = rotation_interval_days * 86400
        self._compat_window = compatibility_window_days * 86400

    def check_and_rotate(self) -> str | None:
        """检查是否需要轮换，需要则执行。

        Returns:
            新密钥版本号，或 None（无需轮换）。
        """
        if not isinstance(self._signer, LocalEd25519Signer):
            # HsmPkcs11Signer 轮换由 HSM 管理
            return None

        primary_ver = self._signer.get_primary_version()
        kv = None
        for v in self._signer.get_key_versions():
            if v.version == primary_ver:
                kv = v
                break
        if kv is None:
            return None

        age = time.time() - kv.created_at
        if age >= self._rotation_interval:
            new_version = self._signer.rotate_key()
            logger.info(
                "KeyRotationJob: rotated key from %s to %s (age=%.0f days, threshold=%d days)",
                kv.version, new_version, age / 86400, self._rotation_interval // 86400,
            )
            return new_version

        return None

    def cleanup_expired_keys(self) -> list[str]:
        """清理过期前任钥（兼容窗口结束后）。

        同步清理 _keys / _private_keys / _hmac_secrets 三处引用，
        避免内存泄漏并保证过期签名确实无法再验签。
        """
        if not isinstance(self._signer, LocalEd25519Signer):
            return []

        expired: list[str] = []
        now = time.time()
        for kv in self._signer.get_key_versions():
            if kv.expires_at is not None and now > kv.expires_at:
                expired.append(kv.version)
                self._signer.remove_key_version(kv.version)
                logger.info("KeyRotationJob: cleaned up expired key %s", kv.version)
        return expired
