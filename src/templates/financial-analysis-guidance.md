---
name: financial-analysis
description: >-
  Hướng dẫn phân tích tài chính doanh nghiệp SME từ báo cáo tài chính. Dùng khi lập phần phân tích tài chính của báo cáo thẩm định tín dụng.
---
 
#### NGUYÊN TẮC CHUNG
- Bố cục được cung cấp là khung tham khảo, không phải biểu mẫu bắt buộc điền kín.
- Không lặp giá trị của kỳ này sang kỳ khác để lấp ô trống.
- Bảng biểu chỉ hiển thị những kỳ/năm có số liệu.
- Nếu cả một bảng không có dữ liệu, bỏ bảng đó và ghi "Không có dữ liệu".
- Tuân thủ TUYỆT ĐỐI cấu trúc bảng đã được định nghĩa.
- SỐ CỘT NĂM (quan trọng — người dùng có thể nộp nhiều bộ BCTC)
  - Bố cục minh hoạ 3 cột năm ({{Nam1}}, {{Nam2}}, {{Nam3}}) vì 2 file BCTC thường cho 3 năm (mỗi file có năm hiện tại + năm so sánh, gối nhau 1 năm).
  - Số cột năm THỰC TẾ phải bằng đúng số kỳ liệt kê ở dòng "Các kỳ báo cáo có dữ
  liệu" trong phần đầu input, và ở header bảng của khối
  [PRE-COMPUTED FINANCIAL METRICS]. Thêm hoặc bớt cột cho khớp — 2 kỳ thì 2 cột,
  4 kỳ thì 4 cột.
  - Sắp xếp các cột theo thứ tự thời gian TĂNG DẦN (năm cũ nhất bên trái).
  - Chỉnh cả hàng phân cách |---| cho đúng số cột, và các cột "Tăng/Giảm" so
  sánh từng cặp năm liền kề.
  - Một chỉ tiêu có thể chỉ có dữ liệu ở một số kỳ: để trống ô của kỳ thiếu, KHÔNG lấy
  số của kỳ khác điền vào.
 
#### TRỌNG TÂM PHÂN TÍCH
- Mục 1: xác định xu hướng doanh thu, giá vốn, chi phí, lợi nhuận. Tỷ trọng tính trên doanh thu thuần, lấy số tuyệt đối.
  - Mục 1.1:
    - Liệt kê top 5 sản phẩm/dịch vụ lớn nhất dựa trên hàng tồn kho (dùng số liệu phát sinh có/doanh số xuất trong kỳ), 
    ưu tiên sử dụng nguồn thông tin như sau:
      - Nếu khách hàng thuộc lĩnh vực Sản xuất: ưu tiên sổ/tài khoản 155, 156
      - Nếu khách hàng thuộc lĩnh vực Xây dựng/dịch vụ xây lắp: ưu tiên sổ/tài khoản 154, 156
      - Nếu khách hàng thuộc lĩnh vực Thương mại/Dịch vụ: ưu tiên sổ/tài khoản 156, 155
    - Liệt kê top 5 khách hàng có doanh số phát sinh nợ lớn nhất (nguồn: số liệu phát sinh nợ trong kỳ từ sổ/tài khoản 131). 
    - Đánh giá cơ cấu doanh thu, diễn biến so với kỳ trước
  và nguyên nhân. Chỉ liệt kê đúng số lượng thực có, không dựng đủ 5 dòng cho đẹp.
  - Mục 1.2: đánh giá cơ cấu giá vốn, biến động các thành phần lớn so với kỳ trước.
  - Mục 1.3: các khoản mục phát sinh hoặc biến động lớn trong kỳ.
  - Mục 1.4: nhận định về chỉ số sinh lời.
 
- Mục 2.2.1: 
    - a. Dựa trên sổ/tài khoản 131, liệt kê top 5 khách hàng có *dư nợ cuối kỳ* lớn nhất trong năm báo cáo. Cảnh báo dấu hiệu tồn đọng, chậm luân chuyển nếu có các signals sau:
      - Giá trị thu hồi công nợ < Số dư phải thu đầu kỳ.
      - Khoản phải thu cảnh báo = Số dư Nợ phải thu đầu kỳ − Giá trị phát sinh Có.
  
    - b. Dựa trên sổ/tài khoản 331, liệt kê top 5 nhà cung cấp có *dư nợ cuối kỳ* lớn nhất trong năm báo cáo.
    - c. Dựa trên các sổ/tài khoản 152, 153, 154, 155, 156, liệt kê top 5 sản phẩm/dịch vụ có dư nợ/tồn kỳ cuối kỳ lớn nhất trong năm và so sánh với năm trước.
      - Nếu khách hàng thuộc lĩnh vực Sản xuất: ưu tiên sổ/tài khoản 155, 156
      - Nếu khách hàng thuộc lĩnh vực Xây dựng/dịch vụ xây lắp: ưu tiên sổ/tài khoản 154, 156
      - Nếu khách hàng thuộc lĩnh vực Thương mại/Dịch vụ: ưu tiên sổ/tài khoản 156, 155
      - Cảnh báo dấu hiệu tồn đọng, chậm luân chuyển hàng tồn kho nếu có các signals sau:
        - Giá trị luân chuyển/bán hàng < Số dư hàng tồn kho đầu kỳ.
        - Hàng tồn kho cảnh báo = Số dư hàng tồn kho đầu kỳ − Giá trị xuất bán/phát sinh bán hàng.
    - d. Tài sản cố định và tài sản dở dang dài hạn: nêu quy mô, mức khấu hao, các
      khoản đầu tư/xây dựng cơ bản dở dang lớn và tiến độ (nếu hồ sơ có).
    - e. Liệt kê các khoản mục tài sản >10% tổng tài sản, tập trung vào các khoản mục lớn và có mức độ biến động vượt quá 20% kỳ trước. Lưu ý: các khoản mục này không bao gồm các mục a, b, c, d ở trên
- Mục 2.2.2:
    - a. Dựa trên sổ/tài khoản 131, liệt kê top 5 khách hàng có *dư có cuối kỳ* lớn nhất trong năm báo cáo.
    - b. Dựa trên sổ/tài khoản 331, liệt kê top 5 nhà cung cấp có dư có cuối kỳ lớn nhất trong năm và so sánh với năm trước.
    - c. Đối chiếu dữ liệu tín dụng tại CIC hoặc sổ vay nợ (mã 341). Mô tả xu hướng dư nợ.
    - d. Đánh giá cơ cấu và xu hướng, so sánh với thông tin vốn điều lệ.
    - e. Liệt kê các khoản mục chiếm >10% tổng nguồn vốn, tập trung vào các khoản mục lớn và có mức độ biến động vượt quá 20% kỳ trước. Lưu ý: các khoản mục này không bao gồm các mục a, b, c, d ở trên
 
- Mục 3: phân tích diễn biến và xu hướng, nhận diện nhóm chỉ số suy giảm nhanh và
  mạnh trong 2 năm tài chính gần nhất.
- Mục 4: kết quả đạt được và khả năng duy trì; biến động tài chính trọng yếu có phù
  hợp với mô hình kinh doanh và các quyết định trong kỳ hay không.