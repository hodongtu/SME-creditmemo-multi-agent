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

SƠ ĐỒ NHIỀU TẦNG (chuỗi cung ứng):
- Vẽ được dạng nhiều tầng: nhà cung cấp -> doanh nghiệp -> kênh phân phối -> đối tác cuối.
- TỐI ĐA 5 TẦNG và 20 khối. Vượt mức này thì bản PDF phải thu nhỏ tới mức chữ
  không đọc nổi trên khổ A4 — hãy tách thành 2 sơ đồ (một cho đầu vào, một cho
  đầu ra) thay vì cố nhồi vào một hình.
- Tô màu nhóm bằng classDef, đặt ngay đầu khối mermaid. Dùng đúng bộ màu này:
  `classDef hub fill:#BDD7EE,stroke:#333` cho khối doanh nghiệp/đầu mối,
  `classDef org fill:#D9D9D9,stroke:#333` cho khối thông tin pháp nhân,
  `classDef hi fill:#C6E0B4,stroke:#333` cho đối tác trọng yếu cần nhấn mạnh,
  `classDef warn fill:#FFC000,stroke:#333` cho đối tác tập trung rủi ro cao.
  Gán bằng `A[Tên]:::hub` hoặc `class A,B hub`.
- Không tô màu để trang trí. Màu chỉ dùng khi có lý do nghiệp vụ nêu được ở
  phần **Nhận định** ngay dưới sơ đồ.

TRỌNG TÂM PHÂN TÍCH TỪNG MỤC:
- Mục 1: sản phẩm, đầu vào, đầu ra, tồn kho, phương thức thanh toán, quy trình tiền hàng.
- Mục 2: mô tả sản phẩm/dịch vụ theo từng lĩnh vực, tỷ trọng đóng góp doanh thu.
- Mục 4 và 5: tối đa 3 đối tác lớn nhất theo tỷ trọng doanh thu, kèm phương thức ký hợp
  đồng, giao hàng, thanh toán.
- Mục 6: vị thế sản phẩm/dịch vụ trong thị trường mục tiêu và khả năng duy trì 3-5 năm
  tới; ưu nhược điểm của phương thức mua bán, chính sách tồn kho, vận hành, so với
  thông lệ ngành; sức khoẻ tài chính và nhu cầu tín dụng.
