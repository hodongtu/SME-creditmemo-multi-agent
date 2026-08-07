---
name: financial-analysis
description: >-
  Hướng dẫn phân tích tài chính doanh nghiệp SME từ báo cáo tài chính: trọng tâm phân tích từng mục (kết quả kinh doanh, giá vốn, cơ cấu tài sản - nguồn vốn, chỉ số tài chính), quy tắc dùng bố cục, và công thức tính DSO/DPO/DIO/CCC, ROA/ROE, thanh toán nhanh.
  Dùng khi lập phần phân tích tài chính của báo cáo thẩm định tín dụng.
---

Hướng dẫn phân tích cho báo cáo phân tích tài chính. Đây là chỉ dẫn nội bộ —
KHÔNG được chép bất kỳ dòng nào của phần này vào báo cáo.

SỐ CỘT NĂM (quan trọng — người dùng có thể nộp nhiều bộ BCTC):
- Bố cục minh hoạ 3 cột năm ({{Nam1}}, {{Nam2}}, {{Nam3}}) vì 2 file BCTC thường
  cho 3 năm (mỗi file có năm hiện tại + năm so sánh, gối nhau 1 năm).
- Số cột năm THỰC TẾ phải bằng đúng số kỳ liệt kê ở dòng "Các kỳ báo cáo có dữ
  liệu" trong phần đầu input, và ở header bảng của khối
  [PRE-COMPUTED FINANCIAL METRICS]. Thêm hoặc bớt cột cho khớp — 2 kỳ thì 2 cột,
  4 kỳ thì 4 cột.
- Sắp xếp các cột theo thứ tự thời gian TĂNG DẦN (năm cũ nhất bên trái).
- Nhớ chỉnh cả hàng phân cách |---| cho đúng số cột, và các cột "Tăng/Giảm" so
  sánh từng cặp năm liền kề.
- Một chỉ tiêu có thể chỉ có dữ liệu ở vài kỳ: để trống ô của kỳ thiếu, KHÔNG lấy
  số của kỳ khác điền vào.

CÁCH DÙNG BỐ CỤC:
- Bố cục được cung cấp là khung tham khảo, không phải biểu mẫu bắt buộc điền kín.
- Chỉ trình bày dòng/cột/kỳ thực sự có số liệu trong hồ sơ. Xoá hẳn dòng không có
  dữ liệu thay vì điền "-", "0" hay số ước lượng.
- Không lặp giá trị của kỳ này sang kỳ khác để lấp ô trống.
- Bảng biểu chỉ hiển thị những kỳ/năm có số liệu.
- Nếu cả một bảng không có dữ liệu, bỏ bảng đó và ghi "Không có dữ liệu trong hồ sơ".

TRỌNG TÂM PHÂN TÍCH TỪNG MỤC:
- Mục 1: xác định xu hướng doanh thu, giá vốn, chi phí, lợi nhuận. Tỷ trọng tính
  trên doanh thu thuần, lấy số tuyệt đối.
- Mục 1.1: nếu hồ sơ có sổ chi tiết, liệt kê tối đa 5 sản phẩm/dịch vụ lớn nhất
  (tài khoản 152, 153, 154, 155, 156) và tối đa 5 khách hàng có doanh số phát sinh
  nợ lớn nhất (tài khoản 131). Đánh giá cơ cấu doanh thu, diễn biến so với kỳ trước
  và nguyên nhân. Chỉ liệt kê đúng số lượng thực có, không dựng đủ 5 dòng cho đẹp.
- Mục 1.2: đánh giá cơ cấu giá vốn, biến động các thành phần lớn so với kỳ trước.
- Mục 1.3: các khoản mục phát sinh hoặc biến động lớn trong kỳ.
- Mục 1.4: nhận định về chỉ số sinh lời.
- Mục 2.2.1: các khoản mục tài sản chiếm trên 10% tổng tài sản, ưu tiên khoản mục
  biến động quá 20% so với kỳ trước.
- Mục 2.2.2 đến 2.2.6: dựa trên tài khoản 131 (phải thu, người mua trả trước),
  331 (trả trước người bán, phải trả người bán), 152-156 (tồn kho); mỗi bảng tối đa
  5 dòng lớn nhất theo dư nợ cuối kỳ. Cột "Số ngày" = dư nợ cuối kỳ / phát sinh
  tương ứng * 365.
- Mục 3: phân tích diễn biến và xu hướng, nhận diện nhóm chỉ số suy giảm nhanh và
  mạnh trong 2 năm tài chính gần nhất.
- Mục 4: kết quả đạt được và khả năng duy trì; biến động tài chính trọng yếu có phù
  hợp với mô hình kinh doanh và các quyết định trong kỳ hay không.

CÔNG THỨC (dùng khi khối [PRE-COMPUTED FINANCIAL METRICS] không có sẵn chỉ số):
- Chỉ số thanh toán nhanh = (Tài sản ngắn hạn - Trả trước người bán - Phải thu khác) / Nợ ngắn hạn
- Vòng quay tài sản = Doanh thu / Tổng tài sản cuối kỳ
- ROA = Lợi nhuận sau thuế / Tổng tài sản cuối kỳ
- ROE = Lợi nhuận sau thuế / Vốn chủ sở hữu
- Số ngày phải thu (DSO) = Phải thu cuối kỳ / Doanh thu thuần * 365
- Số ngày phải trả (DPO) = Phải trả cuối kỳ / Giá vốn hàng bán * 365
- Số ngày tồn kho (DIO) = Tồn kho cuối kỳ / Giá vốn hàng bán * 365
- Số ngày trả trước người bán = Trả trước người bán cuối kỳ / Doanh thu thuần * 365
- Số ngày người mua trả trước = Người mua trả trước cuối kỳ / Giá vốn hàng bán * 365
- Chu kỳ tiền (CCC) = DSO + DIO + Số ngày trả trước người bán - DPO - Số ngày người mua trả trước
