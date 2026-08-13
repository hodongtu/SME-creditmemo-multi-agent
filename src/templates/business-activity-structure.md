## Báo cáo phân tích hoạt động kinh doanh

### {{TenKhachHang}}

- **Mã số thuế**: {{MaSoThue}}
- **Số đăng ký kinh doanh**: {{SoDangKyKinhDoanh}}
- **Ngành nghề kinh doanh**: {{NganhNghe}}

### Nguồn thông tin

- **Hồ sơ**:
    - {{TenFile}}
- **Kỳ/Năm phân tích**: {{Ky}}
- **Mức độ tin cậy**: {{MucDoTinCay}}

---

## 1. Mô hình sản xuất kinh doanh

```mermaid
flowchart LR
  A[Đầu vào] --> B[Sản xuất] --> C[Tồn kho] --> D[Đầu ra] --> E[Thu tiền]
```

**Nhận định**:

## 2. Lĩnh vực kinh doanh và sản phẩm

| Lĩnh vực kinh doanh | Sản phẩm/Dịch vụ | Tỷ trọng doanh thu {{Nam-1}} | Tỷ trọng doanh thu {{Nam}} |
|---|---|---:|---:|

**Nhận định**:

## 3. Quy trình vận hành

```mermaid
flowchart TD
  A[Nhận đơn hàng] --> B[Ký hợp đồng] --> C[Sản xuất] --> D[Giao hàng & thanh toán]
```

**Nhận định**:

## 4. Đầu ra

| Đầu ra chính | Mặt hàng | Doanh số {{Nam}} | Tỷ trọng | Phương thức ký HĐ | Phương thức giao hàng | Phương thức thanh toán |
|---|---|---:|---:|---|---|---|

```mermaid
flowchart LR
  classDef warn fill:#FFC000,stroke:#333
  classDef hi fill:#C6E0B4,stroke:#333
  KH[{{TenKhachHang}}] -->|{{TyTrongDauRa1}}| R1[{{DoiTacDauRa1}}]
  KH -->|{{TyTrongDauRa2}}| R2[{{DoiTacDauRa2}}]
```

**Nhận định**:

## 5. Đầu vào

| Đầu vào chính | Mặt hàng | Doanh số {{Nam}} | Tỷ trọng | Phương thức ký HĐ | Phương thức giao hàng | Phương thức thanh toán |
|---|---|---:|---:|---|---|---|

```mermaid
flowchart LR
  classDef warn fill:#FFC000,stroke:#333
  classDef hi fill:#C6E0B4,stroke:#333
  V1[{{DoiTacDauVao1}}] -->|{{TyTrongDauVao1}}| KH[{{TenKhachHang}}]
  V2[{{DoiTacDauVao2}}] -->|{{TyTrongDauVao2}}| KH
```

**Nhận định**:

## 6. Kết luận

**Nhận định**:
