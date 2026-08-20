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

print("\n8. Phong cách: màu theo tầng, bóng vẽ tay")
import re as _re  # noqa: E402
pairs = _re.findall(r'fill="(#[0-9a-f]{6})" stroke="(#[0-9a-f]{6})"', one["svg"])
check("mỗi tầng một màu khác nhau", len({p for p in pairs}) >= 4, f"{len(set(pairs))} tổ hợp")
check("dùng đúng bảng LEVEL_COLOURS",
      all(p in G.LEVEL_COLOURS for p in pairs), str([p for p in pairs if p not in G.LEVEL_COLOURS][:2]))

shadows = _re.findall(r'fill="' + G.SHADOW_COLOUR + r'" opacity="([\d.]+)"', one["svg"])
check("mỗi khối có một bóng vẽ tay", len(shadows) == len(pairs), f"{len(shadows)} bóng / {len(pairs)} khối")
check("bóng mờ, không đặc", all(0 < float(o) < 0.3 for o in shadows), str(set(shadows)))
check("KHÔNG dùng feDropShadow (WeasyPrint bỏ qua không báo)",
      "feDropShadow" not in one["svg"])

print("\n9. Style do sơ đồ tự khai vẫn thắng bảng màu theo tầng")
warn = measure("flowchart LR\n  A[Khách A] --> B[Doanh nghiệp]\n  style A fill:#FFC000,stroke:#333")
check("giữ nguyên màu cảnh báo tập trung", "#FFC000" in warn["svg"])

print("\n10. Lề quanh chữ đồng đều ở MỌI khối, kể cả nhãn dài")
import re as _r  # noqa: E402
src10 = ("flowchart LR\n  A[Gỗ MDF phủ Melamine] --> B[Đầu ra] --> C[Công ty TNHH Gỗ An Cường] "
         "--> D[Thu tiền]")
svg10 = G.render_svg(_parse(src10))
chart10 = _parse(src10)
pads = []
for nid, lab in chart10.labels.items():
    lines = G._text_lines(lab)
    tw = max(G.text_width(l, G.FONT_SIZE) for l in lines)
    w = min(G.NODE_MAX_WIDTH, max(G.NODE_MIN_WIDTH, tw + 2 * G.NODE_PADDING_X))
    pads.append(round((w - tw) / 2, 1))
check("lề mỗi bên như nhau ở mọi khối", len(set(pads)) == 1, f"{sorted(set(pads))}px")
check("lề đúng bằng NODE_PADDING_X", pads[0] == G.NODE_PADDING_X, f"{pads[0]} vs {G.NODE_PADDING_X}")
check("đo bằng font thật, không đếm ký tự", G._measure_font() is not None)

print("\n11. Dây nối vào cùng một tầng thì dừng ở cùng một mép")
svg11 = G.render_svg(_parse(
    "flowchart LR\n  A[Nhà cung cấp A] --> M[Nhà máy]\n  B[Nhà cung cấp B] --> M\n"
    "  M --> C[Khách hàng lớn 78%]\n  M --> D[Khách hàng khác]"))
lefts = {round(float(x)) for x, y in
         _r.findall(r'<rect x="([\d.]+)" y="([\d.]+)"', svg11)[1::2]}
check("3 tầng -> đúng 3 mép trái", len(lefts) == 3, str(sorted(lefts)))
ends = {round([(float(a), float(b)) for a, b in
               _r.findall(r"[ML]([\d.-]+) ([\d.-]+)", d)][-1][0])
        for d in _r.findall(r'<path d="([^"]+)"', svg11)}
check("dây nối kết thúc ở đúng các mép đó", ends <= lefts, f"{sorted(ends)} vs {sorted(lefts)}")

print("\n12. Dây nối dài ngắn theo nhãn, và chừa chỗ cho mũi tên")
for name, source, label in (
    ("ngắn", "flowchart LR\n  A[Đầu vào] -->|giao hàng| B[Sản xuất]", "giao hàng"),
    ("vừa", "flowchart LR\n  A[Đầu vào] -->|thanh toán 30 ngày| B[Sản xuất]", "thanh toán 30 ngày"),
    ("dài", "flowchart LR\n  A[X] -->|thanh toán trong vòng 45 ngày kể từ ngày nghiệm thu| B[Y]",
     "thanh toán trong vòng 45 ngày kể từ ngày nghiệm thu"),
):
    svg12 = G.render_svg(_parse(source))
    boxes12 = sorted({(float(x), float(x) + float(w)) for x, w in
                      re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg12)})[1::2]
    widest12 = max(G.text_width(l, G.EDGE_FONT_SIZE)
                   for l in G._text_lines(label))
    gap12 = boxes12[1][0] - boxes12[0][1]
    side = (gap12 - widest12 - G.ARROW_LENGTH) / 2
    check(f"nhãn {name}: chừa đúng {G.EDGE_LABEL_CLEARANCE}px mỗi bên",
          abs(side - G.EDGE_LABEL_CLEARANCE) < 0.5, f"{side:.1f}px")

# nhãn phải nằm gọn giữa hộp nguồn và đầu mũi tên
svg12 = G.render_svg(_parse("flowchart LR\n  A[Đầu vào] -->|thanh toán 30 ngày| B[Sản xuất]"))
lx = float(re.search(r'<text x="([\d.]+)"[^>]*font-size="' + str(G.EDGE_FONT_SIZE), svg12).group(1))
boxes12 = sorted({(float(x), float(x) + float(w)) for x, w in
                  re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg12)})[1::2]
half = G.text_width("thanh toán 30 ngày", G.EDGE_FONT_SIZE) / 2
check("nhãn không chạm hộp nguồn", lx - half > boxes12[0][1], f"{lx - half:.1f} > {boxes12[0][1]:.1f}")
check("nhãn không chạm đầu mũi tên",
      lx + half < boxes12[1][0] - G.ARROW_LENGTH,
      f"{lx + half:.1f} < {boxes12[1][0] - G.ARROW_LENGTH:.1f}")

print("\n13. Nhãn dài được vẽ ĐẦY ĐỦ, không cắt, không dấu …")
long_label = "thanh toán trong vòng 45 ngày kể từ ngày nghiệm thu"
svg_long = G.render_svg(_parse(f"flowchart LR\n  X[Ký hợp đồng] -->|{long_label}| Y[Nghiệm thu]"))
lines_long = re.findall(
    r'<text x="[\d.]+" y="[\d.-]+" font-size="' + str(G.EDGE_FONT_SIZE) + r'"[^>]*>([^<]*)</text>',
    svg_long)
check("ghép các dòng lại đúng bằng nhãn gốc",
      " ".join(lines_long) == long_label, " ".join(lines_long))
check("không có dấu … ở bất kỳ dòng nào",
      not any("…" in line for line in lines_long), str(lines_long))
guidance = (REPO / "src/templates/business-activity-guidance.md").read_text(encoding="utf-8")
check("guidance đặt trần 10 từ cho nhãn mũi tên", "TỐI ĐA 10 TỪ" in guidance)
check("guidance nói rõ hệ thống vẽ đầy đủ", "vẽ ĐẦY ĐỦ nhãn" in guidance)

print("\n14. Nhãn nhiều dòng nằm gọn trong khung")
for label13 in ("giao hàng", "thanh toán trong vòng 45 ngày kể từ ngày nghiệm thu"):
    svg13 = G.render_svg(_parse(f"flowchart LR\n  X[Ký hợp đồng] -->|{label13}| Y[Nghiệm thu]"))
    h13 = int(re.search(r'viewBox="0 0 \d+ (\d+)"', svg13).group(1))
    ys13 = [float(m.group(1)) for m in
            re.finditer(r'<text x="[\d.]+" y="([\d.-]+)" font-size="'
                        + str(G.EDGE_FONT_SIZE), svg13)]
    top13 = min(ys13) - G.EDGE_FONT_SIZE
    check(f"{len(G._text_lines(label13))} dòng: không tràn trên",
          top13 >= 0, f"đỉnh chữ y={top13:.1f}")
    check(f"{len(G._text_lines(label13))} dòng: không tràn dưới",
          max(ys13) <= h13, f"đáy y={max(ys13):.1f} / khung {h13}")

print("\n15. Nhãn KHÔNG đè lên dây — có margin thật")
for label15 in ("giao hàng", "trả chậm 30 ngày",
                "thanh toán trong vòng 45 ngày kể từ ngày nghiệm thu"):
    svg15 = G.render_svg(_parse(f"flowchart LR\n  A[Đầu vào] -->|{label15}| B[Sản xuất]"))
    wire = float(re.findall(r"[ML][\d.-]+ ([\d.-]+)",
                            re.search(r'<path d="([^"]+)"', svg15).group(1))[0])
    ys15 = [float(m.group(1)) for m in re.finditer(
        r'<text x="[\d.]+" y="([\d.-]+)" font-size="' + str(G.EDGE_FONT_SIZE), svg15)]
    bottom = max(ys15) + G.EDGE_FONT_SIZE * G.DESCENDER_RATIO
    n = len(ys15)
    check(f"{n} dòng: đáy chữ cách dây ≥ {G.EDGE_LABEL_MARGIN}px",
          wire - bottom >= G.EDGE_LABEL_MARGIN - 0.1, f"{wire - bottom:.1f}px")
    check(f"{n} dòng: mọi dòng nằm TRÊN dây", max(ys15) < wire,
          f"đáy baseline {max(ys15):.1f} < dây {wire:.1f}")

print("\n" + "=" * 66)
if fails:
    print("❌ HỎNG:", *fails, sep="\n   - ")
    sys.exit(1)
print("✅ Flowchart bố trí vào bề rộng trang, chữ đúng cỡ hằng số")
