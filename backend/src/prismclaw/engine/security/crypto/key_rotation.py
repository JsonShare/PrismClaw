"""P4-SEC-02：密钥轮换 Cron Job。

定期执行 Ed25519 / HMAC 密钥轮换 + 过期密钥清理。
支持 APScheduler / systemd timer / K8s CronJob 调度。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from prismclaw.engine.security.crypto.hsm_signer import (
    LocalEd25519Signer,
    KeyRotationJob,
    SignerProtocol,
)

logger = logging.getLogger(__name__)


class KeyRotationCronJob:
    """密钥轮换 Cron Job 入口。

    调度方式：
      1. APScheduler: scheduler.add_job(job.run, 'cron', hour=3)
      2. systemd timer: ExecStart=python -m prismclaw.engine.security.crypto.key_rotation
      3. K8s CronJob: command: ["python", "-m", "prismclaw.engine.security.crypto.key_rotation"]
    """

    def __init__(
        self,
        signer: SignerProtocol | None = None,
        *,
        rotation_interval_days: int = 90,
        compatibility_window_days: int = 7,
    ) -> None:
        self._signer = signer or LocalEd25519Signer(
            rotation_interval_days=rotation_interval_days,
            compatibility_window_days=compatibility_window_days,
        )
        self._job = KeyRotationJob(
            self._signer,
            rotation_interval_days=rotation_interval_days,
            compatibility_window_days=compatibility_window_days,
        )

    def run(self) -> dict[str, Any]:
        """执行一次密钥轮换检查。返回操作报告。"""
        report: dict[str, Any] = {
            "timestamp": time.time(),
            "signer_type": type(self._signer).__name__,
            "rotated": False,
            "new_version": None,
            "cleaned_keys": [],
        }

        # 检查并轮换
        new_version = self._job.check_and_rotate()
        if new_version is not None:
            report["rotated"] = True
            report["new_version"] = new_version
            logger.info("KeyRotationCronJob: rotated to %s", new_version)

        # 清理过期密钥
        expired = self._job.cleanup_expired_keys()
        if expired:
            report["cleaned_keys"] = expired
            logger.info("KeyRotationCronJob: cleaned up expired keys: %s", expired)

        return report


def main() -> None:
    """CLI 入口（供 systemd timer / K8s CronJob 调用）。"""
    logging.basicConfig(level=logging.INFO)
    env = os.getenv("PRISMCLAW_DEPLOYMENT_ENV", "development").lower()
    if env == "production":
        logger.info("KeyRotationCronJob: running in production mode")

    job = KeyRotationCronJob()
    report = job.run()
    print(f"Key rotation report: {report}")


if __name__ == "__main__":
    main()