"""Проверка, что модель отвечает и правильно судит о лидах.

    python rognar/test_model.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_env
from rognar import classify

CASES = [
    ("Ребят, хотим с мужем взять квартиру в Рио у моря, бюджет до 250к долларов. К кому обратиться?", True),
    ("Procuro apartamento em Copacabana para investir, quanto custa?", True),
    ("Продам свою двушку в Астане, срочно, звоните", False),
    ("Кто-нибудь знает, где тут поесть нормально?", False),
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    load_env()
    failures = 0

    for text, expected in CASES:
        verdict = classify(text)
        if verdict is None:
            print("✗ модель не ответила — смотри предупреждение выше")
            return 1

        got = bool(verdict.get("is_lead"))
        ok = got == expected
        failures += not ok
        score = verdict.get("score", "?")
        summary = str(verdict.get("summary") or "")[:46]
        print(f"{'✓' if ok else '✗'} лид={str(got):<5} {str(score):>3}/100  {text[:40]:<42} {summary}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} — {'модель судит верно' if not failures else 'ЕСТЬ ПРОМАХИ'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
