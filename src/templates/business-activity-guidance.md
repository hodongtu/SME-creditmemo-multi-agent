---
name: business-activity
description: >-
  Hướng dẫn phân tích hoạt động kinh doanh khách hàng SME: mô hình sản xuất kinh doanh, lĩnh vực và sản phẩm, quy trình vận hành, đối tác đầu ra - đầu vào, và quy tắc vẽ sơ đồ Mermaid. Dùng khi tạo báo cáo đánh giá hoạt động kinh doanh của báo cáo thẩm định tín dụng.
---
 
#### NGUYÊN TẮC CHUNG
- Được BỎ DÒNG không có dữ liệu, KHÔNG được đổi CỘT của bảng trong bố cục; mục nào
  hồ sơ không đề cập thì bỏ luôn bảng/sơ đồ của mục đó.
- Không tự dựng danh sách "Top 3/Top 5" nếu hồ sơ không nêu — liệt kê đúng số lượng có thật.
- Số trên dây nối sơ đồ làm tròn như mọi phần trăm khác (xem NUMBER FORMAT RULE).
- MẪU SỐ CỦA MỌI CỘT "Tỷ trọng" (mục 2, 3, 4) VÀ MỌI SỐ TRÊN DÂY NỐI SƠ ĐỒ:
  - Mẫu số nằm ở `totals` của khối [EXTRACTED DETAIL LEDGER], LẤY ĐÚNG CỘT ĐANG TÍNH
  của ĐÚNG TÀI KHOẢN mà mục đó yêu cầu:

  | Mục | Tài khoản | Đọc bảng `sorted_by` | Mẫu số `totals` |
  |---|---|---|---|
  | 1 sơ đồ, nhánh đầu ra | 131 | `debit_movement` | `debit_movement` |
  | 1 sơ đồ, nhánh đầu vào | 331 | `credit_movement` | `credit_movement` |
  | 2 sản phẩm/dịch vụ | sổ kho (155/156/154…) | `outflow_value` | `outflow_value` |
  | 3 Đầu ra | 131 | `debit_movement` | `debit_movement` |
  | 4 Đầu vào | 331 | `credit_movement` | `credit_movement` |

  Khối `[EXTRACTED DETAIL LEDGER]` đưa mỗi tài khoản kèm NHIỀU bảng xếp hạng trong
  `rankings`, mỗi bảng khai `sorted_by` là cột nó đã sắp theo. **Lấy đúng bảng có
  `sorted_by` bằng cột ghi ở dòng trên** — sổ 131 và sổ 331 mỗi sổ có ba bảng, và
  lấy nhầm bảng thì cả thứ tự đối tác lẫn tỷ trọng đều sai. Sơ đồ mục 1 và bảng
  mục 3/mục 4 đọc CÙNG một bảng, nên hai chỗ khớp nhau là chuyện tự nhiên chứ
  không phải việc phải đối chiếu bằng tay.

  - TỬ SỐ VÀ MẪU SỐ PHẢI CÙNG MỘT NGUỒN. Dòng đọc từ sổ chi tiết thì mẫu số cũng
  phải từ sổ chi tiết. TUYỆT ĐỐI KHÔNG lấy một chỉ tiêu BCTC (doanh thu thuần, phải
  thu khách hàng, phải trả người bán…) làm mẫu số cho các dòng đọc từ sổ: hai bên
  khác phạm vi nên phép chia đó vô nghĩa dù hai số đều có thật.
  - KHÔNG CỘNG CÁC DÒNG CỦA MỘT BẢNG LẠI ĐỂ LÀM MẪU SỐ. Mỗi bảng trong `rankings`
  chỉ chứa 5 dòng lớn nhất theo một cột, không phải toàn bộ tài khoản. `totals` nằm
  ở MỨC TÀI KHOẢN, ngoài `rankings`, và chỉ nó mới là số của cả tài khoản. Cũng
  không cộng các bảng lại với nhau — một đối tác lớn có mặt ở nhiều bảng, cộng vào
  là đếm nó nhiều lần.
  - CỘT TỶ TRỌNG KHÔNG CẦN CỘNG THÀNH 100%, và thường sẽ nhỏ hơn vì chỉ liệt kê 5
  đối tác lớn nhất. Đó là ĐÚNG. Không co giãn các con số cho tổng thành 100%, không
  thêm dòng "Khác" để bù cho đủ.
  - MỘT Ô TỶ TRỌNG VƯỢT 100% GẦN NHƯ LUÔN LÀ MẪU SỐ SAI: một đối tác không thể lớn
  hơn cả tài khoản chứa nó. Gặp con số như vậy thì quay lại lấy đúng khoá `totals`,
  đừng đăng nó. Chỉ một trường hợp >100% là thật: tài khoản có dòng giá trị ÂM làm
  tổng nhỏ đi — khi đó phải nói rõ trong câu văn ngay dưới bảng là vì sao.
  - THIẾU KHOÁ `totals` CẦN DÙNG thì ô tỷ trọng ĐỂ TRỐNG. Không mượn tổng của BCTC,
  của tài khoản khác, hay của cột khác để lấp chỗ đó.
  - HỒ SƠ KHÔNG CÓ TÀI KHOẢN MÀ MỤC YÊU CẦU thì ghi "Không có dữ liệu" và bỏ bảng.
  KHÔNG thay bằng tài khoản khác cho có số — mục 4 hỏi sổ 331, điền sổ 338 hay sổ
  341 vào đó thì cả bảng lẫn mẫu số đều sai theo.
  - CÂU VĂN dùng lại đúng con số phần trăm đã ghi trong bảng, không tính lại bằng
  mẫu số khác.
 
#### QUY TẮC VẼ SƠ ĐỒ
- SƠ ĐỒ (mục 1 và mục 5):
  - Bắt buộc dùng Mermaid, KHÔNG vẽ bằng ký tự ASCII.
  - Tối đa 30 khối; nhãn ngắn, dùng `<br/>` để xuống dòng trong khối.
  - Ưu tiên sử dụng flowchart LR.
  - Chỉ đưa vào sơ đồ những khâu có căn cứ; kèm số liệu thật khi có (công suất, giá trị tồn kho, số ngày phải thu/phải trả). Không đủ thông tin thì bỏ sơ đồ.
 
  - SỐ LIỆU TRÊN DÂY NỐI
    - Đặt số liệu lên chính dây nối bằng cú pháp: `A[KH] -->|32%| B[Đầu ra]`.
    - Nhãn dây nối cũng xuống dòng được bằng `<br/>`.
    - Nhãn trên MŨI TÊN (dạng `-->|nhãn|`) TỐI ĐA 10 TỪ. Nó chú thích một mũi tên;
  dài hơn thì nhãn cao hơn cả sợi dây và át mất sơ đồ. Viết "trả chậm 30 ngày",
  không viết "thanh toán trong vòng 30 ngày kể từ ngày nghiệm thu". Chi tiết đầy
  đủ để ở bảng hoặc phần bình luận.
    - Số trên dây nối phải khớp cột Tỷ trọng của bảng,
  kể cả cách làm tròn — hai chỗ lệch nhau là mâu thuẫn trong cùng một trang.
    - Nhãn tỷ trọng chỉ ghi PHẦN TRĂM, KHÔNG kèm số tuyệt đối. Viết `-->|6,03%|`,
  không viết `-->|6,03%<br/>3,65 tỷ|` hay `-->|6,03% (3,65 tỷ)|`. Số tuyệt đối
  đã có ở bảng ngay bên cạnh; đặt thêm lên dây chỉ làm nhãn rộng gấp đôi, đẩy cả
  sơ đồ ra quá khổ trang rồi bị thu nhỏ lại — mọi khối trên trang mất cỡ chữ vì
  một con số đã nằm sẵn ở chỗ khác.
- Hệ thống vẽ ĐẦY ĐỦ nhãn bạn viết, không cắt bớt chữ nào — nên nhãn dài sẽ làm
  sơ đồ xấu chứ không bị giấu đi. Giữ ngắn là việc của bạn.
    - Số trên dây nối PHẢI khớp cột "Tỷ trọng" của bảng ngay bên trên. Hai chỗ lệch nhau là mâu thuẫn nội bộ trong cùng một trang báo cáo.
    - Không có số thật thì bỏ hẳn sơ đồ, tuyệt đối không vẽ sơ đồ với tỷ trọng ước lượng.
  
  - ĐỐI TÁC TẬP TRUNG
    - KHÔNG tự chọn màu, KHÔNG viết classDef, KHÔNG gắn `:::`. Hệ thống tự tô đối
  tác nào chiếm từ 40% tỷ trọng trở lên, đọc thẳng con số trên dây nối, và tô bằng
  đúng một bộ màu cho cả báo cáo. Bạn tự tô thì khối đó thoát khỏi bảng màu theo
  tầng của hệ thống và trang báo cáo sẽ có hai ba bộ màu lẫn lộn.
    - Việc của bạn là ghi ĐÚNG tỷ trọng lên dây nối. Có số đúng thì phần tô màu tự
  xảy ra.
  - HÌNH DẠNG KHỐI
    - Khối doanh nghiệp được thẩm định (tầng giữa) viết bằng `{{{{Tên công ty}}}}` — hệ thống
  vẽ nó thành hình lục giác để tách chủ thể khỏi các đối tác.
    - MỌI khối còn lại — sản phẩm và đối tác, cả đầu vào lẫn đầu ra — viết bằng `[Tên]`. Nếu
  khối nào cũng lục giác thì không còn gì nổi bật.
    - Ví dụ ĐÚNG — không một dòng màu nào, hàng rào ```mermaid KHÔNG thụt đầu dòng:

```mermaid
flowchart LR
  P1[Sản phẩm đầu vào A] --> R1
  P2[Sản phẩm đầu vào B] --> R2
  P3[Sản phẩm đầu vào C] --> R3
  R1[Đối tác đầu vào A] -->|45%| KH
  R2[Đối tác đầu vào B] -->|20%| KH
  R3[Đối tác đầu vào C] -->|35%| KH
  KH{{{{Công ty ABC}}}} -->|35%| P4
  KH -->|25%| P5
  KH -->|20%| P6
  P4[Sản phẩm đầu ra X] -->|30%| R4[Đối tác đầu ra X]
  P5[Sản phẩm đầu ra Y] -->|30%| R5[Đối tác đầu ra Y]
  P6[Sản phẩm đầu ra Z] -->|40%| R6[Đối tác đầu ra Z]
```

#### TRỌNG TÂM PHÂN TÍCH
- Mục 1: Vẽ sơ đồ mô hình sản xuất kinh doanh theo đúng khung của bố cục: sản phẩm
đầu vào -> NHIỀU NHẤT 5 đối tác đầu vào -> doanh nghiệp -> NHIỀU NHẤT 5 đối tác đầu
ra -> sản phẩm đầu ra.
  - Đầu vào: lấy bảng `sorted_by: "credit_movement"` của sổ 331 — đã sắp sẵn theo
  phát sinh CÓ, lấy từ trên xuống cho tới hết 5 dòng.
  - Đầu ra: lấy bảng `sorted_by: "debit_movement"` của sổ 131 — đã sắp sẵn theo
  phát sinh NỢ, lấy từ trên xuống cho tới hết 5 dòng.
  - Hồ sơ có ít hơn 5 đối tác một bên thì XOÁ HẲN các dòng thừa trong khung — liệt kê
  đúng số có thật, KHÔNG bịa tên và KHÔNG gộp phần còn lại thành một khối "khác" nếu
  hồ sơ không nêu như vậy.
  - Tên đối tác và tỷ trọng phải TRÙNG KHỚP bảng mục 3 và mục 4. Sơ đồ là hình vẽ của
  hai bảng đó, lệch nhau là mâu thuẫn nội bộ trong cùng một trang.
  - CHIỀU của sơ đồ là lỗi hay bị nhầm nhất — đọc kỹ hai vai trò dưới đây:
    - Bên TRÁI, mũi tên ĐI VÀO doanh nghiệp, LÀ NHÀ CUNG CẤP (bên BÁN nguyên liệu CHO
    công ty) — lấy đúng tên ở bảng MỤC 4 "Đầu vào".
    - Bên PHẢI, mũi tên ĐI RA từ doanh nghiệp, LÀ KHÁCH MUA (bên MUA hàng TỪ công ty)
    — lấy đúng tên ở bảng MỤC 3 "Đầu ra".
    - TUYỆT ĐỐI không đảo ngược: nhà cung cấp (mục 4) luôn ở bên trái, khách mua
    (mục 3) luôn ở bên phải. Đối chiếu lại với hai bảng trước khi chốt.
  - Sơ đồ có 5 tầng: sản phẩm đầu vào -> đối tác đầu vào -> doanh nghiệp -> đối tác
  đầu ra -> sản phẩm đầu ra. Mỗi đối tác kèm ĐÚNG MỘT khối mặt hàng của riêng nó,
  lấy từ cột "Mặt hàng" cùng dòng ở bảng mục 3/mục 4. Đối tác nào hồ sơ không nêu mặt
  hàng thì XOÁ khối sản phẩm của riêng đối tác đó, không ghi "không rõ".
  - Tên mặt hàng giữ NGẮN (dưới 5 từ). Sơ đồ 5 tầng đã sát khổ trang; nhãn dài làm
  khối phải bọc thêm dòng và đẩy cả sơ đồ cao lên.
 
- Mục 2: Liệt kê NHIỀU NHẤT 5 sản phẩm/dịch vụ chính của khách hàng và tỷ trọng của các sản phẩm này trong 2 năm gần nhất — lấy bảng `sorted_by: "outflow_value"` của sổ kho, KHÔNG lấy bảng `closing_value` (đó là bảng của FA mục 2.2.1c). Tỷ trọng = doanh số xuất của mặt hàng chia cho `totals.outflow_value` của chính sổ kho, TỪNG KỲ một — cột năm nào chia cho tổng của năm đó, không mượn tổng của kỳ kia.
 
- Mục 3: Liệt kê NHIỀU NHẤT 5 khách hàng đầu ra lớn nhất — lấy bảng
`sorted_by: "debit_movement"` của sổ 131, đã sắp sẵn đúng thứ tự. Cột "Tỷ trọng" =
phát sinh nợ của dòng chia cho `totals.debit_movement` của chính sổ đó.

- Mục 4: Liệt kê NHIỀU NHẤT 5 khách hàng đầu vào lớn nhất — lấy bảng
`sorted_by: "credit_movement"` của sổ 331. Cột "Tỷ trọng" = phát sinh có của dòng
chia cho `totals.credit_movement` của chính sổ đó.

- Mục 3 và mục 4 dùng chung một luật về thông tin đối tác: nêu trạng thái hoạt động,
doanh thu, vốn chủ sở hữu CHỈ KHI hồ sơ có tài liệu chứng minh (hợp đồng, báo cáo
khảo sát, CIC, báo cáo ngành). KHÔNG tra cứu ngoài, KHÔNG dẫn masothue.com hay GSO
theo trí nhớ.
 
- Mục 5: Vẽ sơ đồ quy trình sản xuất, quy trình ký kết hợp đồng theo báo cáo am hiểu ngành (nếu có).
 
- Mục 6: 
  - Đánh giá tiềm năng duy trì/phát triển sản phẩm/dịch vụ trong 3-5 năm tới dựa trên báo cáo 
  am hiểu/báo cáo phân tích ngành.
  - Đánh giá ưu/nhược điểm của phương thức mua hàng, bán hàng, chính sách quản trị hàng tồn kho, 
  vận hành (nếu có); có phù hợp với lĩnh vực kinh doanh và thông lệ thị trường (nếu có). KHÔNG ĐÁNH GIÁ RỦI RO.