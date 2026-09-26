"""e2e 登录态种子：幂等 upsert 三个角色账号（user / agent / admin）。

为什么需要它：`frontend/e2e/` 要跑登录态路由（工作台/管理端）就必须有可登录账号，
而 dev 库里那几个账号的密码**不在仓库里**（全仓 grep 邮箱 0 命中）。用注册接口造号会撞
`auth.py:32` 的 5 次/分钟 IP 限流，且只出 role=user，拿不到坐席/管理员视角。
故这里直接按模型写库——密码取环境变量 `E2E_PASSWORD`（**无默认值**：没给就 fail-fast，
也避免把测试口令写进仓库触发 secret 扫描）。

⚠️ 只对着"一次性库"跑：CI 用 fresh service 容器；本机验证请指 `POSTGRES_DB=lingxi_e2e`
   之类的独立库，别往开发卷里塞账号。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 放仓库根 scripts/ 而不是 backend/scripts/：`backend/**` 在付费评测的触发面里
# （.github/workflows/eval.yml 的 paths-filter），测试基建改动落在那儿每次 push 都要
# 白跑一次 LongCat 抽样。与 check_contracts.py / check_baseline_hashes.py 同一层。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402

#: 邮箱 → 角色。角色枚举以 `app.models.user.UserRole` 为准，这里只列 e2e 需要的三档。
ACCOUNTS: dict[str, UserRole] = {
    "e2e-user@lingxi.test": UserRole.user,
    "e2e-agent@lingxi.test": UserRole.agent,
    "e2e-admin@lingxi.test": UserRole.admin,
}


def main() -> int:
    password = os.environ.get("E2E_PASSWORD")
    if not password:
        print("::error::E2E_PASSWORD 未设置（e2e 种子账号口令必须由环境注入）", file=sys.stderr)
        return 2

    created = 0
    with SessionLocal() as db:
        for email, role in ACCOUNTS.items():
            user = db.query(User).filter(User.email == email).one_or_none()
            if user is None:
                db.add(User(email=email, password_hash=hash_password(password), role=role, status="active"))
                created += 1
                continue
            # 幂等重跑时把口令与角色对齐环境（CI 每次可能换密码）
            user.password_hash = hash_password(password)
            user.role = role
            user.status = "active"
        db.commit()

    print(f"e2e 账号就绪：{len(ACCOUNTS)} 个（本次新建 {created}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
