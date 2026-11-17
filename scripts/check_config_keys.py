# -*- coding: utf-8 -*-
"""校验 .env.example 里的键都能被 app/config.py 识别。

为什么需要这个检查：
    Settings 里声明了 extra="ignore"，写错的键会被**静默丢弃**，
    表现为「明明在 .env 里配了，程序却不生效」，这类问题只能靠机械校验兜住。

用法：
    python scripts/check_config_keys.py
退出码 0 = 通过，1 = 存在 config.py 不认识的键。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
CONFIG_PY = ROOT / "app" / "config.py"

# 别名：环境变量名与字段名不同的情况在这里登记
ALIASES = {
    "rag_rrf_k": "rag_rrf_k",
}


def load_keys() -> set:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return {
        m.group(1).strip().lower()
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=", text, re.M)
    }


def load_fields() -> set:
    text = CONFIG_PY.read_text(encoding="utf-8")
    # 只抓 Settings 类里 4 空格缩进的 pydantic 字段（不抓 @property / def）
    return {
        m.group(1)
        for m in re.finditer(r"^ {4}([a-z][a-z0-9_]*)\s*:", text, re.M)
    }


def main() -> int:
    if not ENV_EXAMPLE.exists():
        print(f"[FAIL] 找不到 {ENV_EXAMPLE}")
        return 1
    if not CONFIG_PY.exists():
        print(f"[FAIL] 找不到 {CONFIG_PY}")
        return 1

    keys = load_keys()
    fields = load_fields()

    unknown = sorted(k for k in keys if k not in fields and k not in ALIASES)
    missing = sorted(
        f
        for f in fields
        if f not in keys
        and f not in {"model_config"}
        and f not in ALIASES.values()
    )

    print(f".env.example 共 {len(keys)} 个键，config.py 共 {len(fields)} 个字段")

    if unknown:
        print("[FAIL] 以下键 config.py 不认识（会被静默忽略）：")
        for k in unknown:
            print(f"    - {k}")
        return 1

    # 反向检查只提示，不失败：有些字段确实不需要出现在模板里
    if missing:
        print("[INFO] config.py 中未出现在 .env.example 的字段（可接受）：")
        print("    " + ", ".join(missing))

    print("[OK] 配置键校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
