# Release notes: v0.3.0-beta.1

Bản beta có kiểm soát cho Windows. Xem [BETA.md](BETA.md) cho tiêu chí, bằng chứng và giới hạn.

## Mới so với v0.2.0

**Path Adapter (chỉ đọc)**
- `path_adapter.py`: render đường dẫn theo từng agent trong bộ nhớ từ manifest (`{{SKILLS_DIR}}`, `{{AGENT_CONFIG_DIR}}`, `{{SECRET_DIR}}`, `{{SKILL_NAME}}`), so hash với thư mục đã cài (`match`, `drift`, `missing`, `unsafe`), có `--diff` chạy được trên console Windows (UTF-8).
- `ignore_placeholders` trong manifest cho file chứa chuỗi `{{CHỮ_HOA}}` có chủ đích; file khác vẫn bị chặn nếu có placeholder chưa khai báo.

**Quản lý state tách hash**
- `plan --adapter-manifest` hiển thị `source`, `render` và `installed` hash.
- `adopt --adapter-manifest`: nhận quản lý thư mục khớp bản render (`eligible_rendered`), chỉ ghi state; `plan` thường báo `blocked_rendered` và không ghi đè.
- `apply --adapter-manifest --allow-rendered-apply --yes`: thử nghiệm, một skill và một target mỗi lần.

**Độ bền giao dịch**
- `apply` ghi journal `in_progress` trước khi đổi thư mục, rồi `committing` (kèm đúng các bản ghi state), ghi `state.json`, rồi `applied`.
- `recover` (xem trước, `--yes`): revert giao dịch dở, hoặc hoàn tất giao dịch `committing`; từ chối nếu có bản sửa tay hay `node_modules`.
- `rollback` ghi journal `rolling_back` và dựng lại skill từ backup ở thư mục tạm rồi đổi tên vào chỗ; `recover` hoàn tất rollback bị ngắt.
- `fsync` journal và state trước khi đổi tên file; nhận diện junction của Windows; tên thư mục `node_modules`, `.git`, `__pycache__` không phân biệt hoa thường.
- `apply`, `adopt`, `rollback` bị chặn cho tới khi xử lý giao dịch dở.

**Khác**
- Bỏ qua `__pycache__`, `.pyc` và `node_modules` khi đọc bản đã cài (cập nhật và rollback vẫn bị chặn nếu có `node_modules`); cho phép `.env.example/.sample/.template`.
- 95 test (stdlib `unittest`), gồm test kill tiến trình thật và test byte-identity; CI trên Windows và Ubuntu.

## Kiểm chứng

- Chạy thật trên 3 skill của một máy Windows (adopt vào state thử, `plan`, `rollback`): không byte nào của thư mục skill đổi.
- Chủ dự án xác nhận cả 3 skill pilot (`threejs`, `ask`, `preview`) chạy tốt trong agent thật (27/10/2026, tự báo, chưa có log).
- Chưa kiểm chứng: Antigravity, mất điện thật, nhiều máy.

## Giới hạn và việc chưa làm

- Path Adapter cần SSOT được chuyển thành template trước; SSOT hiện tại vẫn chứa đường dẫn viết cứng.
- `fsync` và journal đã có nhưng chưa thử bằng mất điện thật; `recover` bị ngắt giữa lúc khôi phục có thể cần xử lý tay (xem [BETA.md](BETA.md)).
- 11 skill chuyển đổi dở, CRLF khi adopt, dependency hydration, shared memory và điều phối đa agent: chưa làm.
- Skill của bên thứ ba cần rà nguồn và giấy phép trước khi công khai bất kỳ bộ skill nào.
