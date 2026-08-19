"""Flowcharts lay out into the page width instead of being scaled down to it.

The Business Activity section 1 supply chain is nine boxes in a row. It used to
come out 1,649px wide, get scaled to 45% to reach A4, and print at 4.7pt against
9.5pt body text — legible on screen, unreadable on paper.

The fix is the contract charts.py already uses: fix the width, let the content
arrange itself into it. What the constants say is then what prints.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import src.utils.graph_svg as G  # noqa: E402
from src.utils.diagrams import _parse  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"   {'✅' if ok else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


def measure(source):
    svg = G.render_svg(_parse(source))
    m = re.search(r'viewBox="0 0 (\d+) (\d+)" width="(\d+)" height="(\d+)"', svg)
    view_w, _, draw_w, _ = (int(x) for x in m.groups())
    # Boxes in one row are centred against the tallest, so a two-line label gives
    # its box a different y from its neighbours. Counting distinct y values would
    # report three rows where there are two — group into bands instead.
    ys = sorted(float(y) for y in re.findall(r'<rect x="[\d.]+" y="([\d.]+)"', svg))
    bands = []
    for y in ys:
        if not bands or y - bands[-1] > G.LINE_HEIGHT * 2:
            bands.append(y)
    return {
        "svg": svg,
        "width": view_w,
        "scale": draw_w / view_w,
        "font": G.FONT_SIZE * draw_w / view_w,
        "rows": len(bands),
    }


BA_SECTION_1 = (
    "flowchart LR\n  P1[Gỗ MDF phủ Melamine] --> KV[Công ty TNHH Gỗ An Cường] "
    "--> A[Đầu vào] --> B[Sản xuất] --> C[Tồn kho] --> D[Đầu ra] --> E[Thu tiền] "
    "--> KR[Chuỗi siêu thị nội thất Nhà Xinh] --> P2[Bàn ghế, tủ kệ]"
)
BA_SECTION_3 = (
    "flowchart LR\n  A[Nhận đơn hàng] --> B[Ký hợp đồng] --> C[Sản xuất] "
    "--> D[Giao hàng & thanh toán]"
)
WITH_EDGE_LABELS = (
    "flowchart LR\n  A[Đầu vào] -->|thanh toán 30 ngày| B[Sản xuất] "
    "-->|giao hàng| C[Đầu ra]"
)
BRANCHING = (
    "flowchart LR\n  A[Nhà cung cấp] --> B[Nhà máy]\n  A --> C[Kho]\n"
    "  B --> D[Khách hàng]\n  C --> D"
)

print("1. Sơ đồ mục 1 — không còn thu nhỏ")
one = measure(BA_SECTION_1)
check("scale = 1,00", abs(one["scale"] - 1.0) < 0.001, f"{one['scale']:.2f}")
check("cỡ chữ = đúng hằng số", abs(one["font"] - G.FONT_SIZE) < 0.01, f"{one['font']:.1f}px")
check("cỡ chữ trên GIẤY ≥ 10pt (px x 0,75)", one["font"] * G.PX_TO_PT >= 10.0,
      f"{one['font']:.1f}px = {one['font'] * G.PX_TO_PT:.1f}pt")
check("bọc thành nhiều dòng", one["rows"] >= 2, f"{one['rows']} dòng")
check("vừa khổ giấy", one["width"] <= G.PAGE_CONTENT_WIDTH, f"{one['width']}px")

print("\n2. Sơ đồ mục 3 — đủ hẹp, không bọc thừa")
three = measure(BA_SECTION_3)
check("vẫn 1 dòng", three["rows"] == 1, f"{three['rows']} dòng")
check("scale = 1,00", abs(three["scale"] - 1.0) < 0.001)

print("\n3. Sơ đồ có nhãn cạnh — khoảng cách rank vẫn rộng")
labelled = measure(WITH_EDGE_LABELS)
bare = measure("flowchart LR\n  A[Đầu vào] --> B[Sản xuất] --> C[Đầu ra]")
check("rộng hơn cùng sơ đồ không nhãn", labelled["width"] > bare["width"],
      f"{labelled['width']} vs {bare['width']}")
check("nhãn cạnh có mặt trong SVG", "thanh toán 30 ngày" in labelled["svg"])

print("\n4. Sơ đồ rẽ nhánh — không bọc dòng")
branch = measure(BRANCHING)
check("không bị bọc dòng (bố cục tầng giữ nguyên)",
      G._is_linear_chain.__call__ is not None and branch["scale"] == 1.0)
check("không thu nhỏ", abs(branch["scale"] - 1.0) < 0.001)

print("\n5. Chiều mũi tên qua chỗ bọc dòng")
paths = re.findall(r'<path d="([^"]+)"', one["svg"])
wrap = [p for p in paths if len(re.findall(r"[ML][\d.]+ [\d.]+", p)) == 4
        and float(re.findall(r"[ML]([\d.]+) ([\d.]+)", p)[0][1])
        < float(re.findall(r"[ML]([\d.]+) ([\d.]+)", p)[-1][1])]
check("có đúng 1 đường nối xuống dòng dưới", len(wrap) == 1, f"{len(wrap)} đường")
if wrap:
    pts = [(float(x), float(y)) for x, y in re.findall(r"[ML]([\d.]+) ([\d.]+)", wrap[0])]
    check("đi xuống rồi sang TRÁI (không đảo chiều chuỗi)",
          pts[-1][0] < pts[0][0] and pts[-1][1] > pts[0][1],
          f"x {pts[0][0]:.0f}->{pts[-1][0]:.0f}, y {pts[0][1]:.0f}->{pts[-1][1]:.0f}")

print("\n6. Sơ đồ vẫn quá rộng thì PHẢI nói ra, không im")
deep = "flowchart LR\n" + "\n".join(
    f"  N{i}[Công đoạn sản xuất số {i}] --> N{i+1}[Công đoạn sản xuất số {i+1}]"
    for i in range(1, 10)) + "\n  N1 --> N5"
wide = measure(deep)
check("bị thu nhỏ dưới ngưỡng đọc được", wide["font"] < G.MIN_READABLE_FONT,
      f"{wide['font']:.1f}px = {wide['font'] * G.PX_TO_PT:.1f}pt")
check("ghi chú HIỂN THỊ được (không phải HTML comment)",
      '<p class="mmd-note">' in wide["svg"])
check("không dùng lại comment ẩn", "<!-- diagram scaled" not in wide["svg"])

print("\n7. Bảng màu theo báo cáo, không phải mặc định mermaid")
check("bỏ tím #9370DB", "#9370DB" not in one["svg"])
check("dùng xanh của báo cáo #2f6f9f", "#2f6f9f" in one["svg"])

print("\n" + "=" * 66)
if fails:
    print("❌ HỎNG:", *fails, sep="\n   - ")
    sys.exit(1)
print("✅ Flowchart bố trí vào bề rộng trang, chữ đúng cỡ hằng số")
