# Release notes: v0.3.0-beta.1 (dự thảo, chưa phát hành)

Bản beta có kiểm soát cho Windows. Chưa merge PR #2 và chưa tạo tag; xem [BETA.md](BETA.md) cho tiêu chí và việc còn lại.

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
- `apply`, `adopt`, `rollback` bị chặn cho tới khi xử lý giao dịch dở.

**Khác**
- Bỏ qua `__pycache__`, `.pyc` và `node_modules` khi đọc bản đã cài (cập nhật và rollback vẫn bị chặn nếu có `node_modules`); cho phép `.env.example/.sample/.template`.
- 86 test (stdlib `unittest`), gồm test kill tiến trình thật và test byte-identity; CI trên Windows và Ubuntu.

## Kiểm chứng

- Chạy thật trên 3 skill của một máy Windows (adopt vào state thử, `plan`, `rollback`): không byte nào của thư mục skill đổi.
- Chưa kiểm chứng: skill chạy được trong Codex/Claude/Antigravity, mất điện thật, nhiều máy.

## Giới hạn và việc chưa làm

- Path Adapter cần SSOT được chuyển thành template trước; SSOT hiện tại vẫn chứa đường dẫn viết cứng.
- Độ bền khi mất điện (`fsync`), journal cho `rollback`, nhận diện junction, so khớp tên thư mục không phân biệt hoa thường: chưa làm (xem [BETA.md](BETA.md)).
- 11 skill chuyển đổi dở, CRLF khi adopt, dependency hydration, shared memory và điều phối đa agent: chưa làm.
- Skill của bên thứ ba cần rà nguồn và giấy phép trước khi công khai bất kỳ bộ skill nào.
