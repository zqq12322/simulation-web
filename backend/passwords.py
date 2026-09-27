"""
口令哈希（只用标准库，不引入 passlib/bcrypt）。

为什么单独成模块
----------------
"怎么存口令"是那种**一旦写错、事后无法补救**的决定（泄露的弱哈希是永久可破解的），
所以它应该是一段短小、可被独立测试的代码，而不是散落在认证端点里。

算法选择
--------
``hashlib.scrypt``：内存硬（memory-hard），对 GPU/ASIC 暴力破解的抵抗远好于
PBKDF2，且是 Python **标准库**自带的（依赖 OpenSSL）。参数要显式给足：
本项目默认 ``n=2**14, r=8, p=1``（约 16 MB 内存/次），符合 RFC 7914 对交互式
登录的推荐量级。

存储格式
--------
::

    scrypt$n$r$p$<salt_hex>$<hash_hex>

把参数写进字符串，而不是写死在代码里。这样将来调参数时：

- **老口令仍能验证**（用它们自己记录的参数算一遍）；
- 新的口令用新参数；
- 需要的话可以判定 ``needs_rehash`` 并在登录成功后顺手升级。

如果只把 ``n/r/p`` 留在代码常量里，改一次就等于让所有老用户无法登录——
这是很常见的一次性事故。

两个容易被忽略的细节
--------------------
1. **每个用户独立随机盐**（``secrets.token_bytes``）。共用盐会让彩虹表/批量
   破解重新变得可行。
2. **比较用 ``secrets.compare_digest``**（常数时间），不用 ``==``：后者会在
   第一个不同的字节处提前返回，泄露"前几个字节猜对了"的时序信息。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Tuple

#: 由本模块生成的哈希前缀（用于将来区分其它算法）
ALGORITHM = "scrypt"

#: RFC 7914 对交互式登录推荐的量级；n=2**14 时单次约 16 MB 内存
DEFAULT_N = 2 ** 14
DEFAULT_R = 8
DEFAULT_P = 1
SALT_BYTES = 16
KEY_LENGTH = 32

#: scrypt 的参数上限，避免用畸形哈希串让服务端分配巨量内存
_MAX_N = 2 ** 20
_MAX_R = 32
_MAX_P = 16


def hash_password(password: str) -> str:
    """把口令哈希成可入库的字符串。"""
    if not isinstance(password, str) or not password:
        raise ValueError("口令不能为空")

    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=DEFAULT_N,
        r=DEFAULT_R,
        p=DEFAULT_P,
        dklen=KEY_LENGTH,
    )
    return "$".join(
        [ALGORITHM, str(DEFAULT_N), str(DEFAULT_R), str(DEFAULT_P),
         salt.hex(), digest.hex()]
    )


def _parse(stored: str) -> Tuple[int, int, int, bytes, bytes]:
    parts = str(stored).split("$")
    if len(parts) != 6 or parts[0] != ALGORITHM:
        raise ValueError("口令哈希格式无法识别")

    algorithm, raw_n, raw_r, raw_p, raw_salt, raw_hash = parts
    n, r, p = int(raw_n), int(raw_r), int(raw_p)
    # 参数来自数据库。即使数据库被写坏，也不允许它让服务端分配任意大的内存。
    if not (1 < n <= _MAX_N and 0 < r <= _MAX_R and 0 < p <= _MAX_P):
        raise ValueError("口令哈希参数超出允许范围")
    if not (n & (n - 1)) == 0:
        raise ValueError("口令哈希参数不合法：n 必须是 2 的幂")

    return n, r, p, bytes.fromhex(raw_salt), bytes.fromhex(raw_hash)


def verify_password(password: str, stored: str) -> bool:
    """
    校验口令。任何异常（格式坏、参数越界、哈希长度不对）都返回 ``False``，
    绝不抛给调用方——认证失败就是认证失败。
    """
    if not isinstance(password, str) or not password:
        return False

    try:
        n, r, p, salt, expected = _parse(stored)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
        )
    except (ValueError, TypeError, MemoryError):
        return False

    return hmac.compare_digest(digest, expected)


def needs_rehash(stored: str) -> bool:
    """
    该哈希是否应该用当前默认参数重算。

    调用方在**用户登录成功之后**顺手调它即可平滑升级参数，无需强制所有人改密。
    """
    try:
        n, r, p, _salt, _digest = _parse(stored)
    except (ValueError, TypeError):
        return True
    return (n, r, p) != (DEFAULT_N, DEFAULT_R, DEFAULT_P)
