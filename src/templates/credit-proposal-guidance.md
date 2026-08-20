---
name: credit-proposal
description: >-
  Hướng dẫn phân tích đề xuất cấp tín dụng của khách hàng.
  Dùng khi tạo báo cáo đánh giá đề xuất cấp tín dụng của khách hàng.
---
 
#### NGUYÊN TẮC CHUNG
- Tuân thủ TUYỆT ĐỐI cấu trúc theo BỐ CỤC BÁO CÁO.
- Mọi số liệu tính toán ở Mục 1.2 đã được cung cấp sẵn trong khối 
[PRE-COMPUTED CREDIT PROPOSAL METRICS]. KHÔNG tự tính lại — chỉ ĐIỀN vào bảng.
- Nếu một giá trị trong khối = null → ghi "Không có dữ liệu".
- CHỈ thay đổi giá trị khi hồ sơ cung cấp số khác biệt rõ ràng (ghi rõ cơ sở).
 
#### CÁCH SỬ DỤNG DỮ LIỆU TỪ [BẢNG TÍNH NHU CẦU TÍN DỤNG]
- Bảng này đã được hệ thống TÍNH SẴN và đưa vào phần evidence dưới nhãn
  [BẢNG TÍNH NHU CẦU TÍN DỤNG]. Chép đúng các dòng và con số sang mục 1.2, GIỮ
  NGUYÊN cột "Nguồn". TUYỆT ĐỐI không tự tính lại, không làm tròn khác đi, không
  thêm bớt dòng.
- Cột "Nguồn" phải được nhắc lại trong phần *Nhận định* khi bạn bình luận về
  một con số: "mặc định" nghĩa là HỒ SƠ KHÔNG NÊU và hệ thống dùng tỷ lệ chính
  sách — nói rõ đó là giả định, không được trình bày như số liệu của khách hàng.
  "tính toán" là suy ra từ các dòng khác trong chính bảng.
- Phần lớn dòng bảo lãnh và LC thường mang nguồn "mặc định" vì giấy đề nghị
  không có các mục này. Khi đó Nhận định phải nêu rõ một câu rằng toàn bộ phần
  bảo lãnh/LC là ước tính theo tỷ lệ chính sách, cần khách hàng xác nhận.
- Nhu cầu vốn vay ra SỐ ÂM không phải lỗi: nghĩa là vốn lưu động ròng và dư nợ
  hiện hữu đã đủ tài trợ chu kỳ tiền. Giữ nguyên số âm và giải thích, không sửa
  thành 0 và không bỏ dòng.
 
#### TRỌNG TÂM PHÂN TÍCH
- Mục 1.1: Kế hoạch kinh doanh và nhu cầu cấp tín dụng
    - Tóm tắt kế hoạch kinh doanh: doanh thu, giá vốn, chi phí, lợi nhuận và
      nhu cầu khách hàng cung cấp theo đề nghị vay vốn.
    - Đánh giá khả thi: dựa trên năng lực doanh nghiệp, diễn biến thị trường,
      so sánh với kết quả kinh doanh thực tế các năm trước.
 
- Mục 1.2: Tính toán nhu cầu cấp tín dụng ngắn hạn
    - a. Hạn mức vay ngắn hạn
        - ĐIỀN giá trị đã tính sẵn ở [BẢNG TÍNH NHU CẦU TÍN DỤNG] vào
          bảng theo đúng cấu trúc ở mục BỐ CỤC BÁO CÁO.
        - Cột "Cơ sở tính toán": ghi nguồn dữ liệu thực tế cho mỗi dòng:
            + Doanh thu, Giá vốn: "BCTC năm [năm]" hoặc "Đề nghị vay vốn"
            + CCC, số ngày phải thu/tồn kho/phải trả: "BCTC năm [năm]"
            + Vốn lưu động ròng: "BCTC năm [năm]" 😊 TSNH - Nợ ngắn hạn)
            + Nguồn vốn khác: "CIC" hoặc "Đề nghị vay vốn" (dư nợ tại TCTD khác)
            + Nhu cầu VLĐ: "GVHB / (365/CCC)"
            + Nhu cầu vốn vay: "Nhu cầu VLĐ - VCSH - Nguồn khác"
        - Trường hợp khách hàng không phát sinh nhu cầu vay → bỏ mục này.
 
    - b. Hạn mức bảo lãnh
        - ĐIỀN giá trị đã tính sẵn ở [BẢNG TÍNH NHU CẦU TÍN DỤNG] vào
          bảng theo đúng cấu trúc ở mục BỐ CỤC BÁO CÁO.
        - Cột "Giá trị hợp đồng cần BL": ghi cơ sở là kế hoạch hợp đồng dự
          kiến hoặc doanh thu năm gần nhất (tùy nguồn dữ liệu thực tế).
        - Trường hợp khách hàng không phát sinh nhu cầu bảo lãnh → bỏ mục này.
 
    - c. Hạn mức LC
        - ĐIỀN giá trị đã tính sẵn ở [BẢNG TÍNH NHU CẦU TÍN DỤNG] vào
          bảng theo đúng cấu trúc ở mục BỐ CỤC BÁO CÁO.
        - Trường hợp khách hàng không phát sinh nhu cầu LC → bỏ mục này.
 
- Mục 2: Đề xuất tín dụng
    - ĐIỀN summary vào cột "Đề xuất do AI tạo ra".
    - Cột "Hạn mức đã cấp": lấy từ thông tin hạn mức hiện tại (CIC, đề nghị vay vốn).
    - Cột "Đề xuất của đơn vị kinh doanh": lấy từ báo cáo khảo sát / đề nghị vay vốn.
    - Cơ sở đề xuất cấu trúc hạn mức: đánh giá dựa trên tính toán hạn mức,
      nhu cầu của khách hàng và kế hoạch khai thác khách hàng của ĐVKD.
    - Cơ sở đề xuất tài sản bảo đảm: dựa trên phân luồng rủi ro, chính sách
      của Ngân hàng, mức độ rủi ro HĐKD và tương quan TSBĐ với TCTD khác.
 
- Mục 3: Đề xuất điều kiện tín dụng
    - Đề xuất dựa trên phân tích rủi ro và biện pháp giảm thiểu.
    - Tương ứng với chính sách, sản phẩm và chương trình kinh doanh của Ngân
      hàng từng thời kỳ.
    - Bao gồm 3 loại: điều kiện trước giải ngân, cam kết tín dụng, và điều
      kiện kiểm soát nội bộ — theo đúng cấu trúc ở mục BỐ CỤC BÁO CÁO.