# BÁO CÁO PHÂN TÍCH HOẠT ĐỘNG KINH DOANH
---
&nbsp;
 
| Thông tin chung | &nbsp; |
| --------------- | ------ |
| *Tên khách hàng* | {{TenKhachHang}} |
| *Nguồn dữ liệu* | {{TenFile}} |
| *Ngành nghề kinh doanh* | {{NganhNghe}} |
| *Mức độ tin cậy* | {{MucDoTinCay}} |
 
## 1. Mô hình sản xuất kinh doanh
 
```mermaid
flowchart LR
  SP1[{{SanPhamDauVao1}}] --> V1[{{DauVao1}}]
  SP2[{{SanPhamDauVao2}}] --> V2[{{DauVao2}}]
  SP3[{{SanPhamDauVao3}}] --> V3[{{DauVao3}}]
  SP4[{{SanPhamDauVao4}}] --> V4[{{DauVao4}}]
  SP5[{{SanPhamDauVao5}}] --> V5[{{DauVao5}}]
  V1 -->|{{TyTrong}}| KH[{{TenKhachHang}}]
  V2 -->|{{TyTrong}}| KH
  V3 -->|{{TyTrong}}| KH
  V4 -->|{{TyTrong}}| KH
  V5 -->|{{TyTrong}}| KH
  KH -->|{{TyTrong}}| R1[{{DauRa1}}]
  KH -->|{{TyTrong}}| R2[{{DauRa2}}]
  KH -->|{{TyTrong}}| R3[{{DauRa3}}]
  KH -->|{{TyTrong}}| R4[{{DauRa4}}]
  KH -->|{{TyTrong}}| R5[{{DauRa5}}]
  R1 --> SR1[{{SanPhamDauRa1}}]
  R2 --> SR2[{{SanPhamDauRa2}}]
  R3 --> SR3[{{SanPhamDauRa3}}]
  R4 --> SR4[{{SanPhamDauRa4}}]
  R5 --> SR5[{{SanPhamDauRa5}}]
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
 
```mermaid
flowchart LR
  A[Nhận đơn hàng] --> B[Ký hợp đồng] --> C[Sản xuất] --> D[Giao hàng & thanh toán]
```
 
## 6. Kết luận
 
*Nhận định*:
 
*Ưu điểm*:
 
*Nhược điểm*: