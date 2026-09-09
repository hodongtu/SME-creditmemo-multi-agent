---
name: financial-analysis
description: >-
  Hướng dẫn phân tích tài chính doanh nghiệp SME từ báo cáo tài chính. Dùng khi lập phần phân tích tài chính của báo cáo thẩm định tín dụng.
---
 
#### NGUYÊN TẮC CHUNG
- Được BỎ DÒNG không có dữ liệu, KHÔNG được đổi CỘT: giữ nguyên số cột và tên cột
  của mọi bảng trong bố cục, còn dòng nào hồ sơ không nêu thì xoá hẳn dòng đó.
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

- MỌI CỘT MANG ĐẦU ĐỀ "Tỷ trọng" — QUY TẮC MẪU SỐ:
  Áp cho mục 1.1, 2.2.1a, 2.2.1b, 2.2.1c, 2.2.2a, 2.2.2b. (Các cột "% DTT",
  "% TTS", "% TNV" có quy tắc riêng ở mục 1 và mục 2.1 — đừng lẫn vào nhau.)
  - Mẫu số nằm ở `totals` của khối [EXTRACTED DETAIL LEDGER], LẤY ĐÚNG CỘT ĐANG
  TÍNH của ĐÚNG TÀI KHOẢN mà mục đó yêu cầu. Bảng dưới đây ghi rõ từng mục lấy
  khoá nào; không mục nào được dùng khoá của mục khác.
  - TỬ SỐ VÀ MẪU SỐ PHẢI CÙNG MỘT NGUỒN. Dòng đọc từ sổ chi tiết thì mẫu số cũng
  phải từ sổ chi tiết. TUYỆT ĐỐI KHÔNG lấy một chỉ tiêu BCTC làm mẫu số cho các
  dòng đọc từ sổ: hai bên khác phạm vi, số dư chi tiết và số dư trên bảng cân đối
  thường lệch nhau rất xa, nên phép chia đó vô nghĩa dù hai số đều có thật.
  - KHÔNG CỘNG CÁC DÒNG CỦA MỘT BẢNG LẠI ĐỂ LÀM MẪU SỐ. Mỗi bảng trong `rankings`
  chỉ chứa 5 dòng lớn nhất theo một cột, không phải toàn bộ tài khoản; cộng chúng
  lại ra số dư của vài đối tác lớn nhất và dùng nó làm mẫu số sẽ thổi phồng mọi tỷ
  trọng. `totals` nằm ở MỨC TÀI KHOẢN, ngoài `rankings`, và chỉ nó mới là số của cả
  tài khoản.
  - Cũng KHÔNG cộng các bảng lại với nhau. Một đối tác lớn xuất hiện ở cả ba bảng
  của sổ 131, nên cộng ba bảng sẽ đếm nó ba lần.
  - CỘT TỶ TRỌNG KHÔNG CẦN CỘNG THÀNH 100%, và thường sẽ nhỏ hơn — phần còn lại của
  tài khoản không được liệt kê. Đó là ĐÚNG. Không co giãn các con số cho tổng thành
  100%, không thêm dòng "Khác" để bù cho đủ.
  - MỘT Ô TỶ TRỌNG VƯỢT 100% GẦN NHƯ LUÔN LÀ MẪU SỐ SAI: một đối tác không thể lớn
  hơn cả tài khoản chứa nó. Gặp con số như vậy thì quay lại lấy đúng khoá `totals`,
  đừng đăng nó. Chỉ một trường hợp >100% là thật: tài khoản có dòng giá trị ÂM làm
  tổng nhỏ đi — khi đó phải nói rõ trong câu văn ngay dưới bảng là vì sao.
  - THIẾU KHOÁ `totals` CẦN DÙNG thì ô tỷ trọng ĐỂ TRỐNG, theo EVIDENCE RULE. Không
  mượn tổng của BCTC, của tài khoản khác, hay của cột khác để lấp chỗ đó.
  - HỒ SƠ KHÔNG CÓ TÀI KHOẢN MÀ MỤC YÊU CẦU thì ghi "Không có dữ liệu" và bỏ bảng.
  KHÔNG thay bằng tài khoản khác cho có số: mục "Trả trước người bán" hỏi sổ 331,
  điền sổ 338 vào đó thì cả bảng lẫn mẫu số đều sai theo, và người đọc không có
  cách nào biết.
  - CÂU VĂN DƯỚI BẢNG dùng lại đúng con số phần trăm đã ghi trong bảng, không tính
  lại bằng mẫu số khác — cùng quy tắc đã áp cho cột "% DTT".

  | Mục | Tài khoản | Đọc bảng `sorted_by` | Mẫu số `totals` |
  |---|---|---|---|
  | 1.1 sản phẩm/dịch vụ | sổ kho (155/156/154…) | `outflow_value` | `outflow_value` |
  | 1.1 khách hàng đầu ra | 131 | `debit_movement` | `debit_movement` |
  | 2.2.1a Phải thu khách hàng | 131 | `closing_debit` | `closing_debit` |
  | 2.2.1b Trả trước người bán | 331 | `closing_debit` | `closing_debit` |
  | 2.2.1c Hàng tồn kho | sổ kho | `closing_value` | `closing_value` |
  | 2.2.2a Người mua trả tiền trước | 131 | `closing_credit` | `closing_credit` |
  | 2.2.2b Phải trả người bán | 331 | `closing_credit` | `closing_credit` |

  Khối `[EXTRACTED DETAIL LEDGER]` đưa mỗi tài khoản kèm NHIỀU bảng xếp hạng trong
  `rankings`, mỗi bảng khai `sorted_by` là cột nó đã sắp theo. **Lấy đúng bảng có
  `sorted_by` bằng cột ghi ở dòng trên, đừng lấy bảng khác cho tiện** — bảng sắp
  theo dư nợ và bảng sắp theo phát sinh nợ là hai danh sách khác nhau, dùng nhầm
  thì cả thứ tự dòng lẫn tỷ trọng đều sai. Một đối tác xuất hiện ở nhiều bảng là
  bình thường, không phải trùng lặp.

  Bảng nào có hai cột năm thì mỗi cột chia cho `totals` của CHÍNH KỲ ĐÓ, không mượn
  tổng của kỳ kia.

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
    - Bảng sản phẩm lấy bảng xếp hạng `sorted_by: "outflow_value"` của sổ kho, cột
    "Tỷ trọng" chia cho `totals.outflow_value`. Bảng khách hàng đầu ra lấy bảng
    `sorted_by: "debit_movement"` của sổ 131, chia cho `totals.debit_movement`. Đọc kỹ QUY TẮC MẪU SỐ ở đầu phần này trước khi điền hai cột đó.
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
    - a. Dựa trên sổ/tài khoản 131, liệt kê top 5 khách hàng có *dư nợ cuối kỳ* lớn nhất trong năm báo cáo — lấy nguyên bảng xếp hạng `sorted_by: "closing_debit"`, đã sắp sẵn đúng thứ tự. Cột "Tỷ trọng" = dư nợ cuối kỳ của dòng chia cho `totals.closing_debit` của chính sổ 131. Cảnh báo dấu hiệu tồn đọng, chậm luân chuyển nếu có các signals sau:
      - Giá trị thu hồi công nợ < Số dư phải thu đầu kỳ.
      - Khoản phải thu cảnh báo = Số dư Nợ phải thu đầu kỳ − Giá trị phát sinh Có.
  
    - b. Dựa trên sổ/tài khoản 331, liệt kê top 5 nhà cung cấp có *dư nợ cuối kỳ* lớn nhất trong năm báo cáo — lấy bảng `sorted_by: "closing_debit"` của sổ 331. Cột "Tỷ trọng" = dư nợ cuối kỳ của dòng chia cho `totals.closing_debit` của chính sổ 331. Mục này là bên NỢ của sổ 331; sổ 331 không có số dư nợ cuối kỳ thì ghi "Không có dữ liệu", không lấy sổ 338 hay sổ 341 điền vào.
    - c. Dựa trên các sổ/tài khoản 152, 153, 154, 155, 156, liệt kê top 5 sản phẩm/dịch vụ có dư nợ/tồn kỳ cuối kỳ lớn nhất trong năm và so sánh với năm trước — lấy bảng `sorted_by: "closing_value"`, KHÔNG lấy bảng `outflow_value` (đó là bảng của mục 1.1). Cột "Tỷ trọng" = tồn cuối kỳ của dòng chia cho `totals.closing_value` của chính sổ kho đó.
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
    - a. Dựa trên sổ/tài khoản 131, liệt kê top 5 khách hàng có *dư có cuối kỳ* lớn nhất trong năm báo cáo — lấy bảng `sorted_by: "closing_credit"` của sổ 131. Cột "Tỷ trọng" = dư có cuối kỳ của dòng chia cho `totals.closing_credit` của chính sổ 131 — KHÔNG chia cho chỉ tiêu "Người mua trả tiền trước ngắn hạn" của BCTC. Sổ 131 không có số dư có cuối kỳ thì ghi "Không có dữ liệu", không lấy sổ khác điền vào.
    - b. Dựa trên sổ/tài khoản 331, liệt kê top 5 nhà cung cấp có dư có cuối kỳ lớn nhất trong năm và so sánh với năm trước — lấy bảng `sorted_by: "closing_credit"` của sổ 331. Cột "Tỷ trọng" = dư có cuối kỳ của dòng chia cho `totals.closing_credit` của chính sổ 331 — KHÔNG chia cho chỉ tiêu "Phải trả người bán ngắn hạn" của BCTC, và KHÔNG dùng tổng của sổ 341.
    - c. Đối chiếu dữ liệu tín dụng tại CIC hoặc sổ vay nợ (mã 341). Mô tả xu hướng dư nợ. Mục này là văn xuôi, không có bảng; sổ 341 chỉ có một bảng xếp hạng `sorted_by: "closing_credit"` — dùng nó để gọi tên các bên cho vay lớn nhất, và `totals.closing_credit` cho tổng dư nợ vay.
    - d. Đánh giá cơ cấu và xu hướng, so sánh với thông tin vốn điều lệ.
    - e. Liệt kê các khoản mục chiếm >10% tổng nguồn vốn, tập trung vào các khoản mục lớn và có mức độ biến động vượt quá 20% kỳ trước. Lưu ý: các khoản mục này không bao gồm các mục a, b, c, d ở trên
 
- Mục 3: phân tích diễn biến và xu hướng, nhận diện nhóm chỉ số suy giảm nhanh và
  mạnh trong 2 năm tài chính gần nhất.
- Mục 4: kết quả đạt được và khả năng duy trì; biến động tài chính trọng yếu có phù
  hợp với mô hình kinh doanh và các quyết định trong kỳ hay không.