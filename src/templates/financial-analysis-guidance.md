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
- Mục 1: xác định xu hướng doanh thu, giá vốn, chi phí, lợi nhuận. Lấy số tuyệt đối.
  - CỘT "% DTT" — BẮT BUỘC TÍNH VÀ ĐIỀN:
    - MỌI dòng từ "Giá vốn hàng bán" trở xuống PHẢI có số phần trăm ở cột này,
    ở MỌI cột năm có dữ liệu. Khung để trống các ô đó vì chúng là việc của bạn,
    không phải vì chúng được phép bỏ trống. Chỉ để trống khi chính ô giá trị
    tiền của dòng đó cũng trống.
    - Mẫu số là DOANH THU THUẦN CỦA CHÍNH KỲ ĐÓ. Không dùng doanh thu bán hàng,
    không dùng tổng doanh thu, không mượn doanh thu thuần của kỳ khác.
    - Ba dòng đầu bảng đã được điền sẵn trong khung ("—", "—", "100%"). Đó là
    ba dòng DUY NHẤT được điền sẵn; chúng không có nghĩa là cả cột đã xong.
    - Dòng "Doanh thu thuần" luôn là 100% — khung đã điền sẵn, giữ nguyên. Nếu
    một phép tính của bạn làm dòng này khác 100% thì bạn đã dùng sai mẫu số.
    - Hai dòng phía trên nó ("Doanh thu bán hàng và cung cấp dịch vụ", "Các khoản
    giảm trừ doanh thu") giữ nguyên dấu "—" của khung. Dấu này nghĩa là PHÉP TÍNH
    KHÔNG ÁP DỤNG cho dòng đó — nó không phải số 0, cũng không phải thiếu dữ liệu,
    nên KHÔNG thay bằng "-", không để trống, và không ghi "Không có dữ liệu trong
    hồ sơ" vào đó.
    - Câu văn ở mục 1.1–1.3 khi nhắc một tỷ lệ phải dùng đúng con số đã ghi ở cột
    "% DTT" của bảng, không tính lại bằng mẫu số khác. Viết rõ "trên doanh thu
    thuần" khi nêu tỷ lệ.
    - "% DTT" của dòng "Lợi nhuận gộp" và "Biên lợi nhuận gộp" ở mục 1.4 là CÙNG
    MỘT tỷ lệ. Hai chỗ lệch nhau là mâu thuẫn nội bộ trong cùng một trang báo cáo.
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
 
- Mục 2.1 — CỘT "% TTS" và "% TNV", BẮT BUỘC TÍNH VÀ ĐIỀN:
    - MỌI dòng có số tiền PHẢI có phần trăm ở cột bên cạnh, ở mọi cột năm có dữ
    liệu. Khung để trống các ô đó vì chúng là việc của bạn.
    - Bên TÀI SẢN mẫu số là TỔNG TÀI SẢN của chính kỳ đó; bên NGUỒN VỐN mẫu số là
    TỔNG NGUỒN VỐN của chính kỳ đó. Hai dòng tổng đã được điền sẵn 100% trong
    khung — đó là hai dòng duy nhất được điền sẵn, giữ nguyên.
    - MỘT mẫu số cho cả cột. Các dòng chi tiết ("Tiền và các khoản tương đương
    tiền", "Hàng tồn kho", "Phải thu ngắn hạn của khách hàng"...) vẫn chia cho
    TỔNG TÀI SẢN, KHÔNG chia cho "Tài sản ngắn hạn" hay bất kỳ dòng tiểu tổng nào.
    Tương tự bên nguồn vốn: không chia cho "Nợ ngắn hạn".
    - Vì bảng có cả dòng tiểu tổng ("Tài sản ngắn hạn", "Tài sản dài hạn", "Nợ
    phải trả", "Nợ ngắn hạn", "Nợ dài hạn", "Vốn chủ sở hữu") lẫn các dòng chi
    tiết nằm trong chúng, cộng cả cột phần trăm sẽ ra hơn 200%. Đó là ĐÚNG với bố
    cục này — không được co giãn các con số cho tổng thành 100%.
    - BÊN NGUỒN VỐN có ba mức, đừng lẫn vào nhau:
      - "Nợ phải trả" là mã 300 — tổng của Nợ ngắn hạn (310) và Nợ dài hạn (330).
      - "Vốn chủ sở hữu" là mã 400.
      - "Tổng nguồn vốn" là mã 440, KHÔNG phải mã 300. Đồng nhất thức:
      Nợ phải trả + Vốn chủ sở hữu = Tổng nguồn vốn = Tổng tài sản.
    - Khối [PRE-COMPUTED FINANCIAL METRICS] có sẵn cả "Nợ phải trả" lẫn "Tổng
    cộng nguồn vốn" thành hai dòng riêng. Chép đúng dòng vào đúng ô; đừng lấy Nợ
    phải trả điền vào ô Tổng nguồn vốn.
    - "Tổng tài sản" và "Tổng nguồn vốn" phải bằng nhau. Nếu số của bạn làm hai
    dòng đó khác nhau thì số liệu chưa cân, nêu rõ chứ không sửa số cho khớp.

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