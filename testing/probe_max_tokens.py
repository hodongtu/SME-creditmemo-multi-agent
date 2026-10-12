"""Trần completion token thật của mỗi model, hỏi chính API thay vì tra tài liệu.

Gửi một max_tokens vô lý; API từ chối bằng 400 và nêu đúng trần của nó. Rẻ vì
request bị chặn trước khi model chạy — không tốn token nào.

Chạy trên đúng gateway đang dùng:
    python3 testing/probe_max_tokens.py                  # mọi MODEL_* trong .env
    python3 testing/probe_max_tokens.py claude-haiku-4-5 # một model cụ thể

Cần thiết vì trần khác nhau tới 8 lần giữa các model trong cùng một .env
(gpt-4o-mini 16.384, gpt-5.4-mini 128.000), và vượt trần là lỗi 400 chặn cả
lượt chạy chứ không phải bị cắt bớt.
"""

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv                       # noqa: E402
load_dotenv(REPO / ".env")

from langchain_core.messages import HumanMessage     # noqa: E402
from langchain_openai import ChatOpenAI              # noqa: E402

ABSURD = 999_999


def ceiling(model: str) -> str:
    client = ChatOpenAI(
        model=model,
        base_url=os.getenv("OPENAI_API_BASE"),
        max_tokens=ABSURD,
        max_retries=0,
        timeout=30,
    )
    try:
        client.invoke([HumanMessage(content="x")])
    except Exception as exc:
        text = str(exc)
        found = re.search(r"at most (\d+)", text) or re.search(r"max_tokens.*?(\d{4,})", text)
        if found:
            return f"{int(found.group(1)):,} token"
        return f"(API không nêu con số) {text[:110]}"
    return f"chấp nhận {ABSURD:,} — trần cao hơn thế, hoặc gateway bỏ qua tham số"


def main() -> int:
    models = sys.argv[1:] or sorted(
        {v for k, v in os.environ.items() if k.startswith("MODEL_") and v}
    )
    print(f"gateway: {os.getenv('OPENAI_API_BASE')}\n")
    width = max(len(m) for m in models)
    for model in models:
        print(f"   {model:<{width}}  {ceiling(model)}")
    print(
        f"\n   Đặt LLM_MAX_TOKENS <= trần THẤP NHẤT trong số model đang dùng."
        f"\n   Vượt trần là lỗi 400 chặn cả lượt chạy, không phải bị cắt bớt."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
