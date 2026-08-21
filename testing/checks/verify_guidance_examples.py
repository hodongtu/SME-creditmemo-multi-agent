"""Every worked example in a guidance file must survive the parser it teaches.

A guidance file is not prose: the blocks it shows the model are copied into real
reports almost verbatim, so a broken example is a broken feature. Three shipped
in one template rewrite, all invisible until a report was generated:

- The ```vat-doanh-thu``` example lost its closing fence. vat_revenue._BLOCK
  requires one, so the VAT series silently vanished from the debt chart AND
  strip_vat_revenue_block stopped removing the block, printing an internal data
  channel into the report handed to the reader.
- Both mermaid fences in the business-activity layout were split across two
  lines, so neither block declared a language and no diagram was drawn.
- The mermaid example inside the guidance was indented four spaces, which
  markdown reads as a code block rather than a fence.

Each check below runs the example through the same code that will meet the
model's output, rather than eyeballing the markdown.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.agents.vat_revenue import (  # noqa: E402
    parse_vat_revenue_block,
    strip_vat_revenue_block,
)
from src.utils.diagrams import mermaid_to_html  # noqa: E402

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"   {'✅' if ok else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


def template(name: str) -> str:
    return (REPO / "src/templates" / f"{name}.md").read_text(encoding="utf-8")


print("1. Ví dụ khối ```vat-doanh-thu``` trong credit-relationship-guidance")
cr = template("credit-relationship-guidance")
block = re.search(r"( *```vat-doanh-thu\n.*?```)", cr, re.DOTALL)
check("tìm thấy ví dụ có đủ hàng rào mở và đóng", block is not None)
if block:
    # As a model would copy it: the example surrounded by ordinary report prose.
    report = f"*Nhận định*: dư nợ đạt đỉnh 07/2025.\n\n{block.group(1)}\n\nĐoạn sau.\n"
    parsed = parse_vat_revenue_block(report)
    stripped = strip_vat_revenue_block(report)
    check("parse_vat_revenue_block đọc được kỳ", bool(parsed), f"{len(parsed)} kỳ")
    check("có cả kỳ tháng lẫn kỳ quý đã tách",
          len(parsed) > len(re.findall(r"^\s*\d{1,2}/", block.group(1), re.M)),
          f"{sorted(parsed)}")
    check("strip_vat_revenue_block xoá sạch khối",
          "vat-doanh-thu" not in stripped and "31400000000" not in stripped)

print("\n2. Ví dụ Mermaid dựng được thành SVG")
for name in ("business-activity-guidance", "business-activity-structure"):
    body = template(name)
    fences = re.findall(r"^```mermaid$", body, re.M)
    rendered = mermaid_to_html(body)
    check(f"{name}: có hàng rào ```mermaid không thụt", bool(fences), f"{len(fences)}")
    check(f"{name}: mọi ví dụ ra SVG",
          rendered.count("<svg") == len(fences),
          f"{rendered.count('<svg')}/{len(fences)}")

print("\n3. Đối tác tập trung vẫn tự được tô sau khi bỏ classDef")
ba = template("business-activity-guidance")
example = re.search(r"```mermaid\n(.*?)```", ba, re.DOTALL)
check("ví dụ có dây nối ≥40%", example is not None and
      any(float(p.replace(",", ".")) >= 40
          for p in re.findall(r"\|(\d+(?:[.,]\d+)?)%\|", example.group(1))))
svg = mermaid_to_html(f"```mermaid\n{example.group(1)}```") if example else ""
check("hệ thống tô hồng đối tác tập trung", "#f7e2e5" in svg)
check("không còn màu do model tự chọn", "#FFC000" not in svg and "#BDD7EE" not in svg)

print("\n4. Guidance không viện dẫn khối dữ liệu không tồn tại")
SRC = (REPO / "src").rglob("*.py")
code = "\n".join(p.read_text(encoding="utf-8") for p in SRC)
for name in ("business-activity", "financial-analysis", "credit-proposal",
             "credit-relationship"):
    body = template(f"{name}-guidance")
    # Bracketed text with no lower-case letter: that is how the prompt names its
    # injected blocks. Written as "no lower case" rather than [A-Z]+ because the
    # real labels are Vietnamese — [BẢNG TÍNH NHU CẦU TÍN DỤNG] has no ASCII
    # upper-case run at all, and an [A-Z] class found nothing in the one file
    # that actually carried a dangling reference.
    labels = {m.strip() for m in re.findall(r"\[([^\]a-z]{6,60})\]", body)}
    missing = sorted(lbl for lbl in labels if f"[{lbl}]" not in code)
    check(f"{name}: mọi khối viện dẫn đều có trong mã",
          not missing, f"thiếu: {missing}" if missing else f"{len(labels)} khối")

print("\n" + "=" * 66)
if fails:
    print("❌ HỎNG:", *fails, sep="\n   - ")
    sys.exit(1)
print("✅ Mọi ví dụ trong guidance đều chạy qua được đúng bộ phân tích của nó")
