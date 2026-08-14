---
name: business-activity
description: >-
  Hướng dẫn phân tích hoạt động kinh doanh khách hàng SME: mô hình sản xuất kinh doanh, lĩnh vực và sản phẩm, quy trình vận hành, đối tác đầu ra - đầu vào, và quy tắc vẽ sơ đồ Mermaid.
  Dùng khi lập phần đánh giá hoạt động kinh doanh của báo cáo thẩm định tín dụng.
---

Hướng dẫn phân tích hoạt động kinh doanh. Đây là chỉ dẫn nội bộ — KHÔNG được chép
bất kỳ dòng nào của phần này vào báo cáo.

CÁCH DÙNG BỐ CỤC:
- Bố cục là khung tham khảo, không phải biểu mẫu bắt buộc điền kín.
- Chỉ trình bày dòng/mục thực sự có căn cứ trong hồ sơ; xoá hẳn dòng không có dữ liệu.
- Không tự dựng danh sách "Top 3/Top 5" nếu hồ sơ không nêu — liệt kê đúng số lượng có thật.
- Mục nào hồ sơ không đề cập thì ghi "Không có dữ liệu trong hồ sơ" và bỏ bảng/sơ đồ.

SƠ ĐỒ (mục 1, 3):
- Bắt buộc dùng Mermaid, KHÔNG vẽ bằng ký tự ASCII.
- Mục 3: tối đa 5 khối; nhãn ngắn, dùng <br/> để xuống dòng trong khối.
- Luôn dùng `flowchart LR` cho mọi sơ đồ trong Business Activity — KHÔNG dùng
  `flowchart TD`, kể cả cho quy trình tuần tự ở mục 3.
- Chỉ đưa vào sơ đồ những khâu có căn cứ; kèm số liệu thật khi có (công suất, giá trị
  tồn kho, số ngày phải thu/phải trả). Không đủ thông tin thì bỏ sơ đồ.
- Mục 4 và mục 5 KHÔNG vẽ sơ đồ nữa — chỉ còn bảng + Nhận định.

MỤC 1 — CHUỖI SẢN PHẨM/ĐỐI TÁC:
- Khung mẫu mục 1 có sẵn 9 khối theo đúng thứ tự cố định — ĐỌC KỸ chiều của từng
  khối, đây là lỗi hay bị nhầm nhất (mô hình hay tự đảo ngược 2 vai trò dưới đây):

    `{{SanPhamDauVao}} -> {{KhachHangDauVao}} -> Đầu vào -> Sản xuất -> Tồn kho ->
    Đầu ra -> Thu tiền -> {{KhachHangDauRa}} -> {{SanPhamDauRa}}`

  - `{{KhachHangDauVao}}` LÀ NHÀ CUNG CẤP (bên BÁN nguyên liệu CHO công ty) — lấy
    đúng tên ở bảng mục 5 "Đầu vào chính", đối tác đứng đầu cột Tỷ trọng.
    `{{SanPhamDauVao}}` lấy từ cột "Mặt hàng" cùng dòng đó.
  - `{{KhachHangDauRa}}` LÀ KHÁCH MUA (bên MUA hàng TỪ công ty) — lấy đúng tên ở
    bảng mục 4 "Đầu ra chính", đối tác đứng đầu cột Tỷ trọng. `{{SanPhamDauRa}}`
    lấy từ cột "Mặt hàng" cùng dòng đó.
  - TUYỆT ĐỐI không đảo ngược hai vai trò này: nhà cung cấp (mục 5) luôn đứng ở
    nửa ĐẦU chuỗi, trước "Đầu vào"; khách mua (mục 4) luôn đứng ở nửa CUỐI chuỗi,
    sau "Thu tiền". Đối chiếu lại với đúng dòng đầu bảng mục 4/5 trước khi chốt.
- 5 khối ở giữa (Đầu vào...Thu tiền) là nhãn quy trình cố định, GIỮ NGUYÊN nguyên
  văn, không đổi, không xoá.
- Không có căn cứ cho một bên (ví dụ hồ sơ không nêu tên nhà cung cấp) thì bỏ ĐÚNG 2
  khối của bên đó (sản phẩm + khách hàng), nối thẳng vào/ra "Đầu vào"/"Thu tiền" —
  không bịa tên, không để trống ngoặc vuông.
- Số ngày vòng quay vẫn đặt trên dây nối như trước khi tính được — số ngày tồn kho
  giữa Sản xuất và Tồn kho, số ngày phải thu giữa Đầu ra và Thu tiền. Cú pháp ống:
  `A[Kho] -->|45 ngày| B[Đầu ra]`, không dùng dạng `A -- 45 ngày --> B`. Nhãn dây nối
  xuống dòng được bằng <br/>.

Ví dụ ĐÚNG (khớp đúng chiều với dòng đầu bảng mục 4/5 tương ứng):
  Bảng mục 5 (Đầu vào chính): "Công ty TNHH Gỗ An Cường | Gỗ MDF phủ Melamine | 100%"
  Bảng mục 4 (Đầu ra chính): "Chuỗi siêu thị nội thất Nhà Xinh | Bàn ghế, tủ kệ | 58%"
  ```mermaid
  flowchart LR
    P1[Gỗ MDF phủ Melamine] --> KV[Công ty TNHH Gỗ An Cường] --> A[Đầu vào] --> B[Sản xuất] --> C[Tồn kho] --> D[Đầu ra] --> E[Thu tiền] --> KR[Chuỗi siêu thị nội thất Nhà Xinh] --> P2[Bàn ghế, tủ kệ]
  ```
  (Nhà cung cấp Gỗ An Cường ở ĐẦU chuỗi vì họ bán nguyên liệu vào; khách mua Nhà
  Xinh ở CUỐI chuỗi vì họ mua thành phẩm ra — không đảo ngược.)

SƠ ĐỒ NHIỀU TẦNG (chuỗi cung ứng, mở rộng của mục 1 khi hồ sơ đủ chi tiết):
- Vẽ được dạng nhiều tầng: nhà cung cấp -> doanh nghiệp -> kênh phân phối -> đối tác cuối.
- TỐI ĐA 5 TẦNG và 20 khối. Vượt mức này thì bản PDF phải thu nhỏ tới mức chữ
  không đọc nổi trên khổ A4 — hãy tách thành 2 sơ đồ (một cho đầu vào, một cho
  đầu ra) thay vì cố nhồi vào một hình.

TÀI LIỆU THAM KHẢO NGÀNH (nếu evidence có khối "Reference Document filename:
...pptx (tài liệu tham khảo ngành, không phải hồ sơ khách hàng)"):
- Đây là KIẾN THỨC CHUNG của cả ngành (đặc điểm mô hình kinh doanh, biên lợi
  nhuận, chu kỳ vốn lưu động... điển hình của ngành), được hệ thống tự chọn
  dựa trên ngành nghề của khách hàng — KHÔNG phải hồ sơ riêng của khách hàng
  này.
- CHỈ dùng để SO SÁNH/ĐỐI CHIẾU với số liệu cụ thể đọc được từ hồ sơ khách
  hàng, ví dụ: "biên lợi nhuận gộp ngành thường ở mức 12-18%[^N], trong khi
  khách hàng đạt 9,5%[^M], thấp hơn mặt bằng chung". TUYỆT ĐỐI không trình bày
  một con số/đặc điểm lấy từ tài liệu ngành như thể đó là sự thật riêng của
  khách hàng này.
- Nếu dùng để đưa ra một nhận định cụ thể, vẫn gắn mã [^N] như citation bình
  thường theo CITATION RULE — tên file lấy từ dòng "Reference Document
  filename:" như mọi tài liệu khác.
- Không có khối này trong evidence (hệ thống không xác định được ngành hoặc
  chưa nạp tài liệu ngành) thì bỏ qua, không tự suy diễn đặc điểm ngành từ
  kiến thức ngoài hồ sơ.

TRỌNG TÂM PHÂN TÍCH TỪNG MỤC:
- Mục 1: sản phẩm, đầu vào, đầu ra, tồn kho, phương thức thanh toán, quy trình tiền hàng.
- Mục 2: mô tả sản phẩm/dịch vụ theo từng lĩnh vực, tỷ trọng đóng góp doanh thu.
- Mục 4 và 5: tối đa 3 đối tác lớn nhất theo tỷ trọng doanh thu, kèm phương thức ký hợp
  đồng, giao hàng, thanh toán.
- Mục 6: vị thế sản phẩm/dịch vụ trong thị trường mục tiêu và khả năng duy trì 3-5 năm
  tới; ưu nhược điểm của phương thức mua bán, chính sách tồn kho, vận hành, so với
  thông lệ ngành; sức khoẻ tài chính và nhu cầu tín dụng.
