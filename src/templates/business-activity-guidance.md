---
name: business-activity
description: >-
  Hướng dẫn phân tích hoạt động kinh doanh khách hàng SME: mô hình sản xuất kinh doanh, lĩnh vực và sản phẩm, quy trình vận hành, đối tác đầu ra - đầu vào, và quy tắc vẽ sơ đồ Mermaid. Dùng khi tạo báo cáo đánh giá hoạt động kinh doanh của báo cáo thẩm định tín dụng.
---
 
#### NGUYÊN TẮC CHUNG
- Bố cục là khung tham khảo, không phải biểu mẫu bắt buộc điền kín.
- Chỉ trình bày dòng/mục thực sự có căn cứ trong hồ sơ; xoá hẳn dòng không có dữ liệu.
- Không tự dựng danh sách "Top 3/Top 5" nếu hồ sơ không nêu — liệt kê đúng số lượng có thật.
- Mục nào hồ sơ không đề cập thì ghi "Không có dữ liệu" và bỏ bảng/sơ đồ.
 
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
  đủ để ở bảng hoặc phần Nhận định.
    - Nhãn tỷ trọng chỉ ghi PHẦN TRĂM, KHÔNG kèm số tuyệt đối. Viết `-->|6,03%|`,
  không viết `-->|6,03%<br/>3,65 tỷ|` hay `-->|6,03% (3,65 tỷ)|`. Số tuyệt đối
  đã có ở bảng ngay bên cạnh; đặt thêm lên dây chỉ làm nhãn rộng gấp đôi, đẩy cả
  sơ đồ ra quá khổ trang rồi bị thu nhỏ lại — mọi khối trên trang mất cỡ chữ vì
  một con số đã nằm sẵn ở chỗ khác.
- Hệ thống vẽ ĐẦY ĐỦ nhãn bạn viết, không cắt bớt chữ nào — nên nhãn dài sẽ làm
  sơ đồ xấu chứ không bị giấu đi. Giữ ngắn là việc của bạn.
    - Số trên dây nối PHẢI khớp cột "Tỷ trọng" của bảng ngay bên trên. Hai chỗ lệch nhau là mâu thuẫn nội bộ trong cùng một trang báo cáo.
    - Không có số thật thì bỏ hẳn sơ đồ, tuyệt đối không vẽ sơ đồ với tỷ trọng ước lượng.
  
  - TÔ MÀU BOX THEO MỨC ĐỘ TẬP TRUNG
    - BẮT BUỘC xét tô màu mỗi khi có một đối tác chiếm từ ~30% tỷ trọng doanh số/giá trị nhập trở lên ở mục 4 hoặc mục 5 — đây LÀ lý do nghiệp vụ, không cần tìm thêm căn cứ nào khác.
    - Tô màu nhóm bằng classDef. Dùng đúng bộ màu này:
      classDef hub fill:#BDD7EE,stroke:#333 cho khối doanh nghiệp/đầu mối,
      classDef org fill:#D9D9D9,stroke:#333 cho khối thông tin pháp nhân,
      classDef hi fill:#C6E0B4,stroke:#333 cho đối tác trọng yếu cần nhấn mạnh,
      classDef warn fill:#FFC000,stroke:#333 cho đối tác tập trung rủi ro cao (~40%+).
      Gán bằng A[Tên]:::hub hoặc class A,B hub.
    - Ví dụ ĐÚNG như sau:
      
    ```mermaid
      flowchart LR
        classDef warn fill:#FFC000,stroke:#333
        P1[Sản phẩm đầu vào A] --> R1
        P2[Sản phẩm đầu vào B] --> R2
        P3[Sản phẩm đầu vào C] --> R3
        R1[Đối tác đầu vào A]:::warn -->|45%| KH
        R2[Đối tác đầu vào B] -->|20%| KH
        R3[Đối tác đầu vào C]:::warn -->|35%| KH
        KH[Công ty ABC] -->|35%| P4:::warn
        KH -->|25%| P5
        KH -->|20%| P6
        P4[Sản phẩm đầu ra X] -->|30%| R4[Đối tác đầu ra X]
        P5[Sản phẩm đầu ra Y] -->|30%| R5[Đối tác đầu ra Y]
        P6[Sản phẩm đầu ra Z] -->|40%| R6[Đối tác đầu ra Z]
    ```  
    - Không tô màu để trang trí những khối không có lý do nghiệp vụ (đối tác tỷ trọng thấp, khối trung gian trong quy trình mục 1/3).
 
#### TRỌNG TÂM PHÂN TÍCH
- Mục 1: Vẽ sơ đồ mô hình sản xuất kinh doanh gồm: top 5 đầu vào lớn nhất gồm tên đầu vào và 
tỷ trọng theo chi tiết phát sinh có sổ chi tiết phải trả người bán (331) năm gần nhất, 
sản phẩm/mặt hàng của đầu vào đó theo hợp đồng đầu vào (nếu có), top 5 đầu ra lớn nhất 
gồm tên đầu ra và tỷ trọng theo chi tiết phát sinh nợ của sổ chi tiết phải thu khách hàng 
(131) năm gần nhất, sản phẩm/mặt hàng bán cho đầu ra đó theo hợp đồng.
  - Khung mẫu mục 1 có sẵn 5 khối theo đúng thứ tự cố định — ĐỌC KỸ chiều của từng
  khối, đây là lỗi hay bị nhầm nhất (mô hình hay tự đảo ngược 2 vai trò dưới đây):
 
    {{SanPhamDauVao}} -> {{KhachHangDauVao}} -> {{KhachHang}} -> {{KhachHangDauRa}} -> {{SanPhamDauRa}}
 
  - {{KhachHangDauVao}} LÀ NHÀ CUNG CẤP (bên BÁN nguyên liệu CHO công ty) — lấy
    đúng tên ở bảng mục 5 "Đầu vào chính", đối tác đứng đầu cột Tỷ trọng.
    {{SanPhamDauVao}} lấy từ cột "Mặt hàng" cùng dòng đó.
  - {{KhachHangDauRa}} LÀ KHÁCH MUA (bên MUA hàng TỪ công ty) — lấy đúng tên ở
    bảng mục 4 "Đầu ra chính", đối tác đứng đầu cột Tỷ trọng. {{SanPhamDauRa}}
    lấy từ cột "Mặt hàng" cùng dòng đó.
  - TUYỆT ĐỐI không đảo ngược hai vai trò này: nhà cung cấp (mục 5) luôn đứng ở
    nửa ĐẦU chuỗi; khách mua (mục 4) luôn đứng ở nửa CUỐI chuỗi. Đối chiếu lại với đúng dòng đầu bảng mục 4/5 trước khi chốt.
 
- Mục 2: Liệt kê top 5 sản phẩm/dịch vụ chính của khách hàng và tỷ trọng của các sản phẩm này trong 2 năm gần nhất.
 
- Mục 3: Liệt top 3 khách hàng đầu ra lớn nhất theo chi tiết phát sinh nợ của sổ chi tiết phải 
thu khách hàng (sổ 131) năm gần nhất. Xác định trạng thái hoạt động của các đầu ra này trên 
masothue.com, doanh thu, vốn chủ sở hữu theo GSO, CIC (nếu có).
 
- Mục 4: Liệt kê top 3 khách hàng đầu vào lớn nhất theo chi tiết phát sinh có của sổ chi tiết phải 
trả người bán (sổ 331) năm gần nhất. Xác định trạng thái hoạt động của các đầu vào này trên 
masothue.com, doanh thu, vốn chủ sở hữu theo GSO, CIC (nếu có).
 
- Mục 5: Vẽ sơ đồ quy trình sản xuất, quy trình ký kết hợp đồng theo báo cáo am hiểu ngành (nếu có).
 
- Mục 6: 
  - Đánh giá tiềm năng duy trì/phát triển sản phẩm/dịch vụ trong 3-5 năm tới dựa trên báo cáo 
  am hiểu/báo cáo phân tích ngành.
  - Đánh giá ưu/nhược điểm của phương thức mua hàng, bán hàng, chính sách quản trị hàng tồn kho, 
  vận hành (nếu có); có phù hợp với lĩnh vực kinh doanh và thông lệ thị trường (nếu có). KHÔNG ĐÁNH GIÁ RỦI RO.