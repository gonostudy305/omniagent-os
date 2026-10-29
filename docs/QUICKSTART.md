# Bắt đầu nhanh trên Windows (v0.3.0-beta.1)

> English summary: a 10-minute path for a new user on Windows PowerShell 5.1: check the machine with a read-only smoke test, optionally try an adoption in a throwaway state, then know how to recover. Nothing here writes to an agent skills folder.

Mục tiêu: sau khoảng 10 phút bạn biết máy mình có chạy được OmniAgent-OS không, mà **không** thay đổi bất kỳ skill nào của Claude, Codex hay Antigravity.

## Cần có
- Windows 10 hoặc 11, Windows PowerShell 5.1 (có sẵn). Không cần quyền Administrator.
- Python 3.9 trở lên trong `PATH` (`python --version`).
- Bản sao của repo này. Công cụ chỉ dùng thư viện chuẩn của Python, không cần `pip install`.

## Bước 1. Chạy test của chính repo (khoảng 25 giây)
```powershell
python -I -m unittest discover -s tests
```
Kỳ vọng: dòng cuối là `OK`. Nếu không, dừng lại và xem phần "Khi có lỗi".

## Bước 2. Smoke test chỉ-đọc
Smoke test chỉ đọc, ghi báo cáo vào `.local\reports\` (đã ẩn tên người dùng và đường dẫn cá nhân).
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke_beta.ps1 -RunTests
```
`-ExecutionPolicy Bypass` chỉ áp dụng cho lần chạy này, không đổi cấu hình máy.

Nếu bạn đã có SSOT đã template hóa và `adapters.json` (xem [PATH_ADAPTER.md](PATH_ADAPTER.md)), thêm hai tham số để kiểm tra cả ba loại hash:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke_beta.ps1 -Source <thư mục template> -Manifest <adapters.json>
```
Mặc định kiểm tra `threejs`, `ask`, `preview` trong `~\.agents\skills`. Đổi bằng `-Skills a,b` và `-Target <thư mục>`. Không tham số nào được giả định có sẵn: thiếu thì mục đó báo `SKIP`.

Cách đọc kết quả:

| Trạng thái | Ý nghĩa |
|---|---|
| `PASS` | Đã kiểm tra và đạt |
| `FAIL` | Đã kiểm tra và không đạt; mã thoát là 1 |
| `WARN` | Đáng chú ý nhưng không chặn (ví dụ skill chạy `node` mà máy chưa có) |
| `SKIP` | Chưa kiểm tra vì thiếu đầu vào |
| `NOT VERIFIED` | Script **không thể** kiểm chứng: agent có nạp skill không, skill có làm xong việc không. Cần thử tay |

Mã thoát: `0` không mục nào FAIL, `1` có mục FAIL, `2` lỗi môi trường (ví dụ thiếu Python).

## Bước 3 (tùy chọn). Thử ghi nhận và hoàn tác trong state riêng
Chế độ `Trial` ghi nhận một adoption vào **một thư mục state mới** dưới `.local\smoke-state\` (đổi bằng `-StateRoot`), chạy `plan`, hoàn tác, rồi băm toàn bộ thư mục skill trước và sau để chứng minh không byte nào đổi. Nó không ghi vào thư mục skill.
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke_beta.ps1 -Source <thư mục template> -Manifest <adapters.json> -Mode Trial
```
Mỗi lần chạy có một thư mục riêng `<thời điểm UTC>-<8 ký tự ngẫu nhiên>`; script từ chối chạy nếu thư mục đó đã tồn tại. **Script không bao giờ tự xóa thư mục này**: journal bên trong là bằng chứng adoption đã được ghi rồi hoàn tác (`status: rolled_back`). Xem xong thì tự xóa thư mục tương ứng trong `.local\smoke-state\`. Thư mục này nằm trong `.local/` nên không lên git.

Chế độ chỉ-đọc dùng một đường dẫn state mới trong `%TEMP%` cho mỗi lần chạy và không tạo thư mục đó (`plan` chỉ đọc state), nên không thể đọc nhầm state cũ hay để lại rác.

Script tự băm bằng .NET thay vì `Get-FileHash`, nên chạy được cả khi bạn gọi `powershell -File ...` từ cửa sổ PowerShell 7.

## Bước 4. Thử trong agent thật (cần bạn làm tay)
Smoke test không chứng minh được điều này. Mở Codex hoặc Claude, gọi từng skill pilot và đánh dấu danh sách trong [BETA.md](BETA.md).

## Khi có lỗi

| Thông báo hoặc triệu chứng | Nghĩa là | Cách xử lý |
|---|---|---|
| `An interrupted transaction exists; inspect it with 'recover' first` | Một `apply` hoặc `rollback` trước đó bị dừng giữa chừng | `python omniagent.py recover --state-dir <state>` để xem trước; kiểm tra rồi thêm `--yes` |
| `Cannot recover; destination was changed` | Một skill bị sửa tay sau khi giao dịch dở, hoặc không còn là bản mới lẫn bản cũ | Không có gì bị đổi. Xem skill đó, khôi phục từ backup bạn tự tạo, rồi chạy lại `recover` |
| `Cannot recover; backup missing or changed` | Bản sao lưu trong `<state>\backups\` bị xóa hoặc sửa | Đừng xóa thư mục backup. Khôi phục tay từ bản sao lưu bên ngoài |
| `Cannot recover; vendor dependencies present` | Skill có `node_modules` | Công cụ không xóa dependency. Chuyển `node_modules` ra ngoài rồi chạy lại |
| `blocked_unmanaged` | Skill đã có sẵn nhưng chưa được công cụ quản lý | Bình thường. Chỉ `adopt` khi hash khớp; không bao giờ bị ghi đè |
| `blocked_rendered` | Bản cài là bản đã render theo agent; `apply` thường bị chặn | Dùng `--adapter-manifest`; xem [PATH_ADAPTER.md](PATH_ADAPTER.md) |
| `blocked_vendor` | Skill có `node_modules`, update và rollback bị chặn | Giữ nguyên hoặc dọn dependency thủ công |
| `Unknown skill(s): ...` | Tên skill không có trong nguồn | Kiểm tra tên; với script smoke dùng `-Skills a,b` |
| Smoke test báo `skill.X.hashes` FAIL | Bản cài khác bản render từ nguồn | Xem diff bằng `omniagent.py adopt --diff`; không tự động sửa |
| `running scripts is disabled on this system` | Chính sách thực thi PowerShell | Dùng đúng lệnh có `-ExecutionPolicy Bypass` ở trên (chỉ cho lần chạy đó) |
| Ký tự tiếng Việt hiển thị sai | Console dùng cp1252 | Chạy `chcp 65001` hoặc dùng Windows Terminal; dữ liệu không bị hỏng |

Nguyên tắc khôi phục: luôn có backup ngoài công cụ trước khi thử trên thư mục agent thật; luôn chỉ rõ `--state-dir` khi đang dùng state thử; chỉ thêm `--yes` sau khi đã đọc phần xem trước.

## Giới hạn của beta này
- Kiểm thử kill tiến trình bằng `os._exit` trên fixture, chưa thử mất điện thật.
- 11 skill của bản cài `agents` đang lệch do chuyển đổi đường dẫn chưa nhất quán; beta không cập nhật chúng.
- Antigravity chưa được kiểm chứng thực thi.
- Chưa có shared memory (v0.4) và điều phối đa agent (v0.5).
