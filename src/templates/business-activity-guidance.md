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

SƠ ĐỒ (mục 1, 3, 4, 5):
- Bắt buộc dùng Mermaid, KHÔNG vẽ bằng ký tự ASCII.
- Tối đa 5 khối; nhãn ngắn, dùng <br/> để xuống dòng trong khối.
- Ưu tiên `flowchart LR` cho mô hình kinh doanh; `flowchart TD` cho quy trình tuần tự.
- Chỉ đưa vào sơ đồ những khâu có căn cứ; kèm số liệu thật khi có (công suất, giá trị
  tồn kho, số ngày phải thu/phải trả). Không đủ thông tin thì bỏ sơ đồ.

SỐ LIỆU TRÊN DÂY NỐI:
- Đặt số liệu lên chính dây nối bằng cú pháp ống: `A[Kho] -->|45 ngày| B[Đầu ra]`.
  Chỉ dùng dạng ống này, không dùng dạng `A -- 45 ngày --> B`.
- Nhãn dây nối cũng xuống dòng được bằng <br/>: `-->|32%<br/>12,4 tỷ|`.
- Mục 1: đặt số ngày vòng quay lên dây nối khi tính được — số ngày tồn kho giữa Sản
  xuất và Tồn kho, số ngày phải thu giữa Đầu ra và Thu tiền.
- Mục 4 và 5: vẽ doanh nghiệp ở giữa, mỗi đối tác lớn là một khối, tỷ trọng doanh số
  đặt trên dây nối. Đầu ra thì mũi tên đi từ doanh nghiệp ra đối tác; đầu vào thì đi
  từ đối tác vào doanh nghiệp.
- Số trên dây nối PHẢI khớp cột "Tỷ trọng" của bảng ngay bên trên. Hai chỗ lệch nhau
  là mâu thuẫn nội bộ trong cùng một trang báo cáo.
- Không có số thật thì bỏ hẳn sơ đồ, tuyệt đối không vẽ sơ đồ với tỷ trọng ước lượng.

TÔ MÀU BOX THEO MỨC TẬP TRUNG (áp dụng cho MỌI sơ đồ có đối tác — mục 4, mục 5, và
sơ đồ nhiều tầng nếu có, KHÔNG riêng gì sơ đồ nhiều tầng):
- BẮT BUỘC xét tô màu mỗi khi có một đối tác chiếm từ ~40% tỷ trọng doanh số/giá trị
  nhập trở lên ở mục 4 hoặc mục 5 — đây LÀ lý do nghiệp vụ, không cần tìm thêm căn cứ
  nào khác. Đây là lỗi hay bị bỏ sót nhất: sơ đồ mục 4/5 vẫn hay bị vẽ toàn khối một
  màu mặc định dù bảng ngay bên trên đã có một đối tác vượt xa các đối tác còn lại.
- Khung mẫu mục 4 và mục 5 đã có sẵn 2 dòng `classDef warn ...` / `classDef hi ...`
  ở đầu khối mermaid — GIỮ NGUYÊN 2 dòng đó dù không dùng đến lần nào (không gán vào
  node nào thì không hiện ra, hoàn toàn vô hại). Khi có đối tác đạt ngưỡng ~40%+, chỉ
  cần thêm hậu tố `:::warn` hoặc `:::hi` ngay sau ngoặc vuông của đúng node đó, ví dụ
  `R1[{{DoiTacDauRa1}}]` → `R1[{{DoiTacDauRa1}}]:::warn`.
- Tô màu nhóm bằng classDef. Dùng đúng bộ màu này:
  `classDef hub fill:#BDD7EE,stroke:#333` cho khối doanh nghiệp/đầu mối,
  `classDef org fill:#D9D9D9,stroke:#333` cho khối thông tin pháp nhân,
  `classDef hi fill:#C6E0B4,stroke:#333` cho đối tác trọng yếu cần nhấn mạnh,
  `classDef warn fill:#FFC000,stroke:#333` cho đối tác tập trung rủi ro cao (~40%+).
  Gán bằng `A[Tên]:::hub` hoặc `class A,B hub`.
- Ví dụ ĐÚNG, một đối tác đầu ra chiếm 55%:
  ```mermaid
  flowchart LR
    classDef warn fill:#FFC000,stroke:#333
    KH[Công ty ABC] -->|55%| R1[Đối tác X]:::warn
    KH -->|25%| R2[Đối tác Y]
    KH -->|20%| R3[Đối tác Z]
  ```
- Không tô màu để trang trí những khối không có lý do nghiệp vụ (đối tác tỷ trọng
  thấp, khối trung gian trong quy trình mục 1/3). Nêu rõ lý do tô màu ở phần
  **Nhận định** ngay dưới sơ đồ.

SƠ ĐỒ NHIỀU TẦNG (chuỗi cung ứng):
- Vẽ được dạng nhiều tầng: nhà cung cấp -> doanh nghiệp -> kênh phân phối -> đối tác cuối.
- TỐI ĐA 5 TẦNG và 20 khối. Vượt mức này thì bản PDF phải thu nhỏ tới mức chữ
  không đọc nổi trên khổ A4 — hãy tách thành 2 sơ đồ (một cho đầu vào, một cho
  đầu ra) thay vì cố nhồi vào một hình.
- Áp dụng đúng quy tắc tô màu ở mục "TÔ MÀU BOX THEO MỨC TẬP TRUNG" phía trên cho
  từng tầng.

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
