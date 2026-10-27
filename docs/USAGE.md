# Hướng dẫn sử dụng Skill Manager

Python 3 chuẩn, không cần cài thêm gói nào. Chạy từ thư mục gốc repo. Trạng thái và backup nằm trong `.local/manager/` (không commit).

## Quy trình an toàn

1. **Backup trước** các thư mục skill và `.local/manager/` của bạn (copy sang nơi ngoài repo).
2. **Xem hiện trạng** (chỉ đọc):
   ```powershell
   python omniagent.py discover
   ```
3. **Xem trước adopt** (chỉ đọc). Trạng thái: `eligible` (khớp SSOT, có thể nhận quản lý), `mismatch`, `unsafe`, `already_managed`, `install`:
   ```powershell
   python omniagent.py adopt --target "claude=$HOME\.claude\skills"
   python omniagent.py adopt --target "claude=$HOME\.claude\skills" --skill NAME --diff
   ```
4. **Nhận quản lý** các skill khớp (chỉ ghi metadata, không đụng thư mục skill):
   ```powershell
   python omniagent.py adopt --target "claude=$HOME\.claude\skills" --yes
   ```
5. **Xem kế hoạch cài** (chỉ đọc), rồi cài thử **một** skill:
   ```powershell
   python omniagent.py plan --target "claude=$HOME\.claude\skills"
   python omniagent.py apply --target "claude=$HOME\.claude\skills" --skill NAME --yes --skip-blocked
   ```
6. Kiểm tra agent có nhận skill, rồi cài phần còn lại bằng `apply --yes --skip-blocked`.

## Trạng thái trong `plan`

| Trạng thái | Ý nghĩa |
|---|---|
| `install` | Skill chưa có ở đích, sẽ được cài |
| `unchanged` | Đã quản lý và khớp SSOT |
| `update` / `update_adopted` | SSOT mới hơn; bản đã adopt cần thêm `--allow-update-adopted` |
| `blocked_unmanaged` | Đã có ở đích nhưng nội dung khác SSOT; không ghi đè |
| `blocked_vendor` | Có `node_modules`; update/rollback bị chặn để không xóa dependency |
| `conflict` | Bản đã cài bị sửa sau khi quản lý |

## Hoàn tác

```powershell
python omniagent.py rollback --transaction <ID> --yes
```

Rollback của `adopt` chỉ bỏ ghi nhận quản lý, không xóa thư mục skill. Rollback của `apply` xóa thư mục vừa cài và khôi phục bản trước; từ chối nếu thư mục đã bị sửa hoặc có `node_modules`. ID giao dịch được in ra khi chạy `adopt --yes` hoặc `apply --yes`.

## Audit

```powershell
python audit_skills.py --output .local/reports
python generate_report.py --input .local/reports/audit.json
```

Kết quả chứa đường dẫn máy và tên skill thật, nên nằm trong `.local/` (không commit).

## Kiểm thử

```powershell
python -I -m unittest discover -s tests
```

## Phục hồi giao dịch bị ngắt giữa chừng

Trước khi `apply` thay đổi bất kỳ thư mục nào, nó ghi một journal ở trạng thái
`in_progress` trong `.local/manager/transactions/`. Nếu máy tắt hoặc tiến trình bị
dừng giữa chừng, journal này còn lại. Khi đó `apply`, `adopt` và `rollback` đều từ
chối chạy cho tới khi xử lý xong:

```powershell
python omniagent.py recover            # xem trước, không ghi gì
python omniagent.py recover --yes      # đưa về trạng thái trước giao dịch
```

Với mỗi skill, `recover` phân loại: `revert` (đã cài bản mới, xóa và khôi phục từ
backup), `restore` (bản cũ đã bị dời đi, khôi phục từ backup), `untouched` (chưa đổi).
Nó **từ chối toàn bộ** nếu một skill không phải bản mới cũng không phải bản trước đó
(ví dụ đã bị sửa tay), hoặc đang có `node_modules`. Thư mục tạm `.omniagent-stage-*`
còn sót lại chỉ được liệt kê, cần tự kiểm tra rồi xóa.

Giai đoạn cuối của `apply` là hai pha: journal chuyển sang `committing` (kèm đúng các
bản ghi sẽ vào `state.json`), rồi ghi `state.json`, rồi journal thành `applied`. Nếu
dừng ở giữa, `recover` **hoàn tất** (roll forward) bằng cách ghi các bản ghi đó, sau khi
kiểm tra từng skill vẫn đúng nội dung mới; nếu một skill đã bị sửa tay thì từ chối. Cả
`in_progress` (revert) và `committing` (hoàn tất) đều chặn `apply`/`adopt`/`rollback` cho
tới khi chạy `recover`.
