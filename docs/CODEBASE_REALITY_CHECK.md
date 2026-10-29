# Codebase reality check (29/10/2026)

> English summary: what OmniAgent-OS actually does at `main` (commit `1262b21`, tag `v0.3.0-beta.1`) plus branch `feat/migration-explain`, checked against code, tests and open PRs rather than against the roadmap or the Deep Research report. Each capability gets one label and the evidence behind it. Main finding: the safety core is real and well tested, but until this branch CI ran only 23 of 95 tests, and nothing beyond file hashes has ever been verified automatically on a real agent.

Nhãn: `SHIPPED_AND_VERIFIED` (trên `main`, có test chạy được), `IMPLEMENTED_NOT_FULLY_VERIFIED`, `OPEN_PR`, `DESIGNED_ONLY`, `MISSING`, `BLOCKED`. "Test" nghĩa là test tự động trong `tests/`; "chủ dự án xác nhận" là báo cáo tay, không có log.

## 1. Ma trận năng lực

| Năng lực | Nhãn | Bằng chứng | Tin cậy |
|---|---|---|---|
| Kiểm kê skill, phát hiện drift | `SHIPPED_AND_VERIFIED` (mức hash) | `audit_skills.py`, `omniagent.py discover`; `tests/test_audit.py`. Drift chỉ ở mức SHA-256 cây thư mục và diff dòng, không phân loại ngữ nghĩa | Cao |
| Adopt / migrate cài đặt bẩn | `SHIPPED_AND_VERIFIED` | `adoption_plan()`, `adopt()`: chỉ nhận bản khớp từng byte; `test_adopt_exact_match_without_touching_target_files`, `test_unmanaged_collision_blocks_without_touching_existing_data`, `test_manual_edit_prevents_update_and_rollback`, `test_byte_identity.py`. Không có cơ chế *hòa giải* bản lệch: `mismatch` chỉ có diff `SKILL.md` | Cao |
| Giải thích kế hoạch | `IMPLEMENTED_NOT_FULLY_VERIFIED` (nhánh này) | `plan/adopt --explain` mới: quyết định, bên được giữ, lệnh sẽ ghi gì, lý do, bước tiếp, cách hoàn tác; `tests/test_explain.py` (14 test, gồm đối chiếu với `apply` chạy thật). Chưa có người dùng thật đọc thử | Trung bình |
| Render theo agent | `IMPLEMENTED_NOT_FULLY_VERIFIED` | `path_adapter.py`: chỉ thay biến `{{SKILLS_DIR}}`, `{{SKILL_NAME}}` và các khóa trong `PATH_KEYS`; apply giới hạn 1 skill / 1 target; 46 test trong `test_path_adapter.py`. Không chuyển đổi định dạng giữa agent (frontmatter, hook, MCP) | Cao về phạm vi hẹp |
| Toàn vẹn giao dịch | `SHIPPED_AND_VERIFIED` (kill tiến trình) | Journal `in_progress` → `committing` → `applied`, `recover`; `test_recovery.py` (11), `test_kill.py` (9, `os._exit` giữa chừng), `fsync` trong `save_json`. Chưa thử mất điện thật; chưa test "recover bị kill giữa chừng" | Trung bình–cao |
| Rollback | `SHIPPED_AND_VERIFIED` | Từ chối khi bản đã cài bị sửa, có `node_modules`, thư mục rỗng lạ, backup lệch hash; rollback của adopt chỉ bỏ ghi nhận. Không rollback được nếu `.local/manager/` mất | Cao |
| Mô hình cấu hình chuẩn (IR) | `MISSING` | Chỉ có template chuỗi + manifest `adapters.json` (`version`, `targets`, `skills.files`). Không có biểu diễn cho rule, MCP, hook, permission, dependency | Cao |
| Provenance | `MISSING` (một phần dữ liệu thô có sẵn) | `state.json` ghi `source` path, `hash`, `origin`, `adopted_transaction`; journal ghi lịch sử. Không có repo gốc, phiên bản, license, quyền, trạng thái tin cậy | Cao |
| Chống supply-chain | `IMPLEMENTED_NOT_FULLY_VERIFIED` (rất hẹp) | Từ chối symlink, junction, `.env`, `.pem`, `.key`, `.token`, `auth.json`; bỏ `node_modules`. Không quét nội dung, không chữ ký, không lockfile | Cao |
| Tương thích chéo agent | `BLOCKED` ở L2/L3 | L0 (hash) có test. L1 (tham chiếu tĩnh) có trong PR #8 bằng PowerShell, chỉ cho skill pilot. L2/L3: chủ dự án tự xác nhận 3 skill ngày 27/10, không log (`docs/BETA.md` tiêu chí 3). Cần agent thật và môi trường cô lập | Cao |
| Quản lý dependency | `DESIGNED_ONLY` | Roadmap Phase 5. `blocked_vendor` chỉ ngăn xóa `node_modules`. PR #8 kiểm skill-phụ-thuộc-skill | Cao |
| Shared memory | `OPEN_PR` (#9) | `brain.py` SQLite + MCP stdio, 34 test, agent chỉ đề xuất, chủ dự án xác nhận qua terminal. Vertical slice dùng client MCP giả; chưa đăng ký vào agent thật. Rào cản terminal là ma sát, không phải xác thực (PR tự ghi rõ) | Cao |
| Điều phối đa agent | `DESIGNED_ONLY` | Chỉ có trong roadmap Phase 7 | Cao |
| CI | `SHIPPED_AND_VERIFIED` sau nhánh này | Trước: `tests.yml` chỉ chạy `test_audit.py` và `test_skill_manager.py` (23/95 test). Nhánh này chạy toàn bộ `tests/test_*.py` trên Ubuntu và Windows. Chưa thấy CI Ubuntu chạy `test_kill.py` lần nào | Cao |
| An toàn Windows | `SHIPPED_AND_VERIFIED` (phần lớn) | Junction (`test_junction_is_detected_and_refused`), tên phân biệt hoa thường, mutex OS + PID lock (`test_live_pid_lock_is_never_automatically_removed`), `.codex/.system` (`test_secret_file_and_codex_system_target_are_blocked`). Thiếu: đường dẫn dài >260 ký tự, file bị khóa bởi agent đang chạy, OneDrive/thư mục đồng bộ | Trung bình |

## 2. Mâu thuẫn tìm thấy

| Chỗ | Mâu thuẫn | Xử lý |
|---|---|---|
| `.github/workflows/tests.yml` | README và BETA nói 95 test; CI chỉ chạy 23 + audit. CI xanh của PR #8 và #9 **không** chạy `test_smoke_script.py` và `test_brain.py` | Sửa trong nhánh này |
| `docs/USAGE.md` | Bảng trạng thái nói `blocked_unmanaged` là "nội dung khác SSOT"; thực tế `plan` không so nội dung (bản giống hệt cũng bị chặn) | Sửa trong nhánh này, thêm `blocked`, `blocked_rendered`, `conflict_missing` |
| `adopt` | Thư mục đang được quản lý từ một đường dẫn SSOT khác được báo `eligible`, rồi `adopt --yes` dừng cả lô với lỗi sai "Skill became managed during adoption" | Sửa: trạng thái mới `managed_elsewhere`; test `test_folder_managed_from_another_source_is_explained_not_fatal` |
| README bảng phiên bản, `PROJECT_CONTEXT.md` mục 7–8 | Ghi PR #2 còn Draft, v0.3 "Chưa làm", rollback "cần journal" | Đã sửa trong PR #8; không sửa lại ở đây để tránh xung đột |
| `PROJECT_CONTEXT.md` mục 2 | "Chưa có dự án nào đủ chín để giải quyết cả ba lớp" | Không còn đúng theo Deep Research và kiểm tra README đối thủ | Đã thêm ghi chú cập nhật và câu định vị mới (chủ dự án duyệt) |
| README tagline | "Unified SSOT & Cross-Agent Management Layer" cho Claude/Codex/Antigravity; nhưng chưa có lớp nào được kiểm chứng ở L2 tự động | Đã đổi sang câu định vị mới, chỉ nói điều đã làm được |
| `--explain` bản đầu (review PR #10) | `will_change` cho mục `install` dù `apply --yes` từ chối cả lô khi có mục bị chặn; `l0_hash = FAIL` dùng lẫn cho "cần cập nhật" và "thư mục mất" | Đổi thành `ready` + `default_run` theo hành vi thật của apply; tách `installed_vs_recorded` / `installed_vs_expected` với `MISSING`, `NOT_CHECKED` |
| Deep Research | Mô tả path templating là "roadmap"; thực tế đã có trong `v0.3.0-beta.1`. Báo cáo cũng chấm "safe migration" 5/10 nhưng README của Helter5/agentbridge đã quảng cáo backup, dry-run, rollback | Ghi nhận trong chiến lược |

## 3. Lỗ hổng an toàn còn lại (chưa sửa)

- `recover` bị kill trong lúc đang recover: chưa có test riêng (rollback bị kill thì có).
- Không có kiểm tra quyền ghi trước khi bắt đầu giao dịch; lỗi giữa chừng dựa vào nhánh revert.
- `--diff` của `adopt` chỉ xem `SKILL.md`; khác biệt ở file khác phải xem tay.
- Rollback phụ thuộc `.local/manager/`; mất thư mục này là mất khả năng hoàn tác (README đã cảnh báo).
- Hai manager với hai `--state-dir` khác nhau không phối hợp được với nhau (README đã ghi).
