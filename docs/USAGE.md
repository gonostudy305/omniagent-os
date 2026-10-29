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
| `blocked_unmanaged` | Đã có thư mục cùng tên ở đích nhưng manager chưa cài hoặc adopt nó (`plan` không so nội dung; chạy `adopt` để biết có khớp SSOT không); không ghi đè |
| `blocked` | Mục ở đích là symlink/junction hoặc là file, không phải thư mục skill |
| `blocked_vendor` | Có `node_modules`; update/rollback bị chặn để không xóa dependency |
| `blocked_rendered` | Bản đã render cho một agent; `apply` thường sẽ ghi đè bằng bản chưa render nên bị chặn, dùng `--adapter-manifest` |
| `conflict` | Bản đã cài bị sửa sau khi quản lý, hoặc nay chứa file không đọc an toàn được (link, file giống credential) |
| `conflict_missing` | State ghi là đang quản lý nhưng thư mục đã bị xóa bên ngoài manager |

### Giải thích từng quyết định (`--explain`, chỉ đọc)

```powershell
python omniagent.py plan  --target "claude=$HOME\.claude\skills" --explain
python omniagent.py adopt --target "claude=$HOME\.claude\skills" --explain --json
```

Với mỗi skill, `--explain` cho biết: quyết định của riêng mục đó (`ready`, `needs_opt_in`, `blocked`, `no_change`), bản nào được giữ khi có xung đột (`authority`: `ssot` hoặc `target`), lệnh sẽ ghi gì nếu mục được chạy (`skill_files`, `manager_state_only`, `nothing`), lý do (kèm `cause` khi một trạng thái có nhiều nguyên nhân), bước xử lý tiếp và cách hoàn tác.

`ready` chưa có nghĩa là sẽ được ghi. `apply --yes` từ chối **cả lô** khi còn bất kỳ mục nào `blocked` hoặc `needs_opt_in`. Vì vậy mỗi mục có `default_run` (`writes`, `refused`, `untouched`), và đầu ra có `default_run` / `skip_blocked_run` cho cả lô: `apply --yes` sẽ ghi bao nhiêu mục hay bị từ chối và vì sao, còn `apply --yes --skip-blocked` sẽ ghi bao nhiêu mục. Với `--adapter-manifest`, `apply --yes` luôn bị báo `refused` vì apply bản render cần `--allow-rendered-apply` và đúng một skill, một target.

Hai phép so hash được báo riêng, chỉ ghi những gì lệnh thật sự đã so: `installed_vs_recorded` (bản đang cài so với hash manager đã ghi, tức có bị sửa không) và `installed_vs_expected` (bản đang cài so với SSOT hoặc bản render, tức có cần cập nhật không). Giá trị: `MATCH`, `DIFFERS`, `MISSING` (thư mục không tồn tại), `UNREADABLE` (có link hoặc file giống credential nên không băm), `NOT_CHECKED` (lệnh không so). Ví dụ `update` là `MATCH` / `DIFFERS`: cập nhật bình thường, không phải hỏng. Agent có nạp skill (L2) hay chạy được tác vụ (L3) luôn là `NOT_VERIFIED`.

Đầu ra không chứa đường dẫn máy hay hash nội dung, và giống hệt nhau giữa hai lần chạy trên cùng một trạng thái, nên dùng được cho script và báo lỗi. Tên skill và tên target vẫn xuất hiện; xóa bớt nếu chúng riêng tư. `adopt --explain` từ chối chạy cùng `--yes` hoặc `--diff`. `adopt` có thêm trạng thái `managed_elsewhere`: thư mục đang được quản lý từ một đường dẫn SSOT khác, nên không bao giờ được adopt lại.

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

`rollback` cũng ghi journal `rolling_back` trước khi đụng vào bất kỳ skill nào. Từng
skill được dựng lại từ backup ở thư mục tạm rồi đổi tên vào chỗ, nên một lần dừng giữa
chừng chỉ để skill ở một trong ba trạng thái: nguyên như cũ, vắng mặt, hoặc đã khôi phục
đầy đủ, không bao giờ nửa vời. `recover` hoàn tất rollback đó (hiển thị
`rollback-restore`, `rollback-remove` hoặc `rollback-done` cho từng skill) rồi mới cập
nhật `state.json`; nó từ chối nếu một skill đã bị sửa tay hoặc backup không còn khớp.
