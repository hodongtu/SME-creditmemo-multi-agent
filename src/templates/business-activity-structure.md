# BÁO CÁO PHÂN TÍCH HOẠT ĐỘNG KINH DOANH
---
&nbsp;
 
| Thông tin chung | &nbsp; |
| --------------- | ------ |
| *Tên khách hàng* | {{TenKhachHang}} |
| *Nguồn dữ liệu* | - {{TenFile}} |
| *Ngành nghề kinh doanh* | {{NganhNghe}} |
| *Mức độ tin cậy* | {{MucDoTinCay}} |
 
## 1. Mô hình sản xuất kinh doanh
 
```
mermaid
flowchart LR
  P1[{{SanPhamDauVao}}] --> KV[{{KhachHangDauVao}}] --> C[{{KhachHang}}] --> KCR[{{KhachHangDauRa}}] --> P2[{{SanPhamDauRa}}]
```
 
*Nhận định*:
 
## 2. Lĩnh vực kinh doanh và sản phẩm
 
| Ngành | Sản phẩm/Dịch vụ | Tỷ trọng doanh thu năm [Year-1] | Tỷ trọng doanh thu năm [Year] |
|---|---|---:|---:|
 
(Đơn vị: tỷ VNĐ)
 
*Nhận định*:
 
## 3. Đầu ra
 
| Đầu ra chính | Mặt hàng | Doanh số {{Nam}} | Tỷ trọng | Phương thức ký HĐ | Phương thức giao hàng | Phương thức thanh toán |
|---|---|---:|---:|---|---|---|
 
(Đơn vị: tỷ VNĐ)
 
*Nhận định*:
 
## 4. Đầu vào
 
| Đầu vào chính | Mặt hàng | Doanh số {{Nam}} | Tỷ trọng | Phương thức ký HĐ | Phương thức giao hàng | Phương thức thanh toán |
|---|---|---:|---:|---|---|---|
 
(Đơn vị: tỷ VNĐ)
 
*Nhận định*:
 
## 5. Quy trình vận hành
 
```
mermaid
flowchart LR
  A[Nhận đơn hàng] --> B[Ký hợp đồng] --> C[Sản xuất] --> D[Giao hàng & thanh toán]
```
 
## 6. Kết luận
 
*Nhận định*:
 
*Ưu điểm*:
 
*Nhược điểm*: