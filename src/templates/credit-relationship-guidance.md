---
name: credit-relationship
description: >-
  Hướng dẫn phân tích quan hệ tín dụng: dư nợ và hạn mức hiện tại, tình trạng trả nợ và nhóm nợ, đối chiếu dữ liệu nội bộ T24 với CIC/bureau, và các dấu hiệu cảnh báo.
  Dùng khi tạo báo cáo quan hệ tín dụng của báo cáo thẩm định tín dụng.
---
 
#### NGUYÊN TẮC CHUNG
- Bố cục được cung cấp là khung tham khảo, không phải biểu mẫu bắt buộc điền kín.
- Không lặp giá trị của kỳ này sang kỳ khác để lấp ô trống.
- Bảng biểu chỉ hiển thị những kỳ/năm có số liệu.
- Nếu không có dữ liệu T24 hoặc CIC, ghi rõ "Không có dữ liệu" và nêu giới hạn này trong phần Nhận định — không suy đoán dư nợ hay nhóm nợ.
 
#### TRỌNG TÂM PHÂN TÍCH
 
- Dư nợ hiện tại, hạn mức, loại hình cấp tín dụng, kỳ hạn, tình trạng trả nợ, nợ quá
  hạn, mức độ sử dụng hạn mức.
- Bảng mục 1: hai cột "Hạn mức (tỷ VNĐ)" và "Dư nợ hiện tại (tỷ VNĐ)" đã ghi đơn vị
  ngay trong tên cột — mỗi ô CHỈ ghi số trần, làm tròn 2 chữ số thập phân (ví dụ
  "56,07"), KHÔNG lặp lại "tỷ VNĐ" trong từng ô.
- Mục "Diễn biến dư nợ 12 tháng": biểu đồ được hệ thống chèn tự động từ dữ liệu đã
  trích xuất — KHÔNG tự vẽ, KHÔNG chép lại bảng số vào báo cáo. Việc của bạn là phần
  Nhận định:
  - Nêu xu hướng đọc được (dư nợ đạt đỉnh tháng nào rồi giảm/tăng ra sao, biên độ dao
    động, dư nợ cuối kỳ so với đầu kỳ), kèm con số và tháng cụ thể.
  - Đối chiếu dư nợ với doanh thu: dư nợ tăng nhanh hơn doanh thu, hoặc doanh thu sụt
    mà dư nợ giữ nguyên, là dấu hiệu cần nêu ở mục Dấu hiệu cảnh báo.
  - Tháng nào thiếu số liệu báo cáo thì nói rõ là thiếu, KHÔNG coi là dư nợ bằng 0 và
    KHÔNG suy diễn giá trị cho tháng đó.
  - Chỉ nhận xét trong phạm vi các tháng có trong dữ liệu, không mở rộng ra ngoài.
  - Doanh thu VAT trên biểu đồ nay lấy từ tờ khai thuế GTGT thật trong hồ sơ (xem
    khối 
vat-doanh-thu``` bên dưới) — dùng được làm căn cứ đối chiếu dư nợ/doanh
    thu như bình thường. Hồ sơ không có tờ khai GTGT thì biểu đồ chỉ còn dư nợ,
    không suy diễn doanh thu. Tháng lấy từ tờ khai QUÝ (chia đều cho 3 tháng) là số
    ƯỚC LƯỢNG — khi nhận xét về tháng đó phải nói rõ.
 
#### KHỐI DỮ LIỆU VAT (```vat-doanh-thu```):
- Hồ sơ có tài liệu tờ khai thuế GTGT (`to_khai_thue_gtgt`) thì NGAY SAU phần Nhận
  định của mục "Diễn biến dư nợ 12 tháng", xuất thêm một khối:
  ```vat-doanh-thu
  01/2025: 31400000000
  02/2025: 28750000000
  Q1/2026: 85000000000 (quy)
  
  Mỗi dòng một kỳ khai: MM/YYYY: <số đồng> cho tờ khai THÁNG, hoặc
  QN/YYYY: <số đồng> (quy) cho tờ khai QUÝ — đọc đúng loại kỳ ghi trên tờ khai,
  KHÔNG tự quy đổi tháng thành quý hay ngược lại. Nhiều tờ khai trong hồ sơ thì
  liệt kê hết các kỳ đọc được trong cùng một khối.
- Lấy đúng dòng TỔNG DOANH THU hàng hoá, dịch vụ bán ra chịu thuế GTGT trên tờ khai
  — KHÔNG lấy số thuế GTGT phải nộp, KHÔNG lấy doanh thu không chịu thuế. Dấu ","
  trong số là phân cách hàng nghìn, không phải thập phân.
- Đây là khối DỮ LIỆU NỘI BỘ để hệ thống đọc lại và vẽ biểu đồ — không phải nội
  dung trình bày cho người đọc, không cần in đậm, không cần gắn mã [^N] lên chính
  khối này (số liệu vẫn nên được nhắc lại bằng văn xuôi có [^N] trong Nhận định
  như citation bình thường).
- Không có tài liệu tờ khai GTGT nào trong hồ sơ thì KHÔNG xuất khối này — không
  bịa số, không để khối rỗng.
- Đối chiếu thông tin nội bộ (T24) với thông tin từ CIC/bureau; nêu rõ mọi chênh lệch.
- Dấu hiệu cảnh báo: nợ quá hạn, đòn bẩy cao trên nhiều ngân hàng, nhiều khoản vay
  song song, cơ cấu lại nợ, hoặc bản ghi bureau không nhất quán.
- Chỉ nhận xét và đánh giá trên dữ liệu được cung cấp. KHÔNG tự tạo ra số liệu quan hệ
  tín dụng.