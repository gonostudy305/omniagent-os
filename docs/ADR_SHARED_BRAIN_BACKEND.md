# ADR: Shared Brain backend (29/10/2026)

> English summary: keep the native thin core from PR #9 (SQLite per project, stdio MCP, agents propose, owner verifies). Do not adopt Memorix as the backend now: its default MCP surface lets any agent in a project resolve, archive or overwrite shared memory, its claim review has no actor check, it keeps every project in one global database, and its setup rewrites agent hooks. Revisit a read-only Memorix retrieval adapter (the hybrid option) only when there is a real need for search we do not have.

**Trạng thái:** đề xuất, chờ chủ dự án duyệt. **Phạm vi:** bộ nhớ dự án dùng chung giữa Claude Code, Codex và Antigravity.

## Bối cảnh

Điều OmniAgent-OS cần không phải "có bộ nhớ chung" (Memorix và nhiều dự án khác đã có) mà là: ai được ghi, ai được xác nhận, bằng chứng còn hợp lệ không, và cài vào agent mà không phá cấu hình đang có. Lõi hiện tại ở `brain.py` khoảng 745 dòng, chỉ dùng thư viện chuẩn Python.

## Bằng chứng về Memorix

Mã nguồn `AVIDS2/memorix` tại commit `4712b4d` (25/10/2026, bản v1.10.1), Apache-2.0, do một agent nghiên cứu đọc qua bản clone nông (không cài, không chạy, đã xóa sau khi đọc); hai dòng ghi "đã tự kiểm lại" được kiểm trực tiếp trên GitHub. Các dòng khác chưa được kiểm lần hai. **CODE** = thấy trong mã, **README** = chỉ có trong tài liệu.

| Điểm | Thực tế | Mức |
|---|---|---|
| Runtime | Node ≥ 22.18, 19 gói runtime (ink/react, Orama, MCP SDK...), `better-sqlite3` và LanceDB là gói native tùy chọn | CODE (`package.json`) |
| Windows | CI chạy Ubuntu, Windows, macOS | CODE (`.github/workflows/ci.yml`) |
| Lưu trữ | Một DB toàn cục `~/.memorix/data/memorix.db`, `projectId` chỉ là một cột | CODE (`src/store/persistence.ts`) |
| Định danh dự án | Git remote `origin`; không có remote thì `local/<tên thư mục>` (hai repo cùng tên thư mục bị trùng) | CODE (`src/project/detector.ts`) |
| Tool MCP mặc định (stdio, profile `micro`) | Có `memorix_resolve`; với bản ghi phạm vi `project`, `canManageObservation` trả `true` cho mọi agent cùng dự án, nên agent nào cũng có thể resolve/archive ghi nhớ của agent khác | CODE (`src/server/tool-profile.ts:37`, `src/memory/visibility.ts`), đã tự kiểm lại |
| Duyệt tri thức | `reviewClaim()` đổi `approved/rejected` mà không kiểm tra ai gọi; gọi được qua `memorix_knowledge` ở profile `team` (mặc định của HTTP) | CODE (`src/knowledge/claims.ts`), đã tự kiểm lại |
| Duyệt long-term memory | Chỉ có trong CLI, không có cổng terminal hay xác nhận | CODE (`src/cli/commands/memory.ts`) |
| Mâu thuẫn, nguồn gốc | `claimKey`/`conflictKey`, bảng sự kiện, `capturedHash` theo commit/symbol, đánh giá lại khi code đổi; phong phú hơn lõi của ta | CODE |
| Cài vào agent | Claude: plugin + hook trên SessionStart, PostToolUse, UserPromptSubmit, PreCompact, Stop; ghi `settings.json`, `CLAUDE.md`. Codex: `config.toml`, `AGENTS.md`. Antigravity: `~/.gemini/config/hooks.json`, `mcp_config.json`, `GEMINI.md`. Gộp hook nông theo từng sự kiện (thay cả mảng hook sẵn có), không backup, nuốt lỗi | CODE (`src/hooks/installers/index.ts`) |
| Xuất dữ liệu | JSON/Markdown chỉ có observations và sessions; không có claims, long-term memory | CODE (`src/memory/export-import.ts`) |
| Mạng | Kiểm tra cập nhật npm mỗi 24 giờ (mặc định chỉ thông báo); gọi LLM chỉ khi có API key | CODE (`src/cli/update-checker.ts`) |

Memorix chọn mô hình "bằng chứng dự án do mọi agent cùng duy trì". Đó là lựa chọn hợp lý cho mục tiêu của họ, không phải lỗi; nhưng nó trái với bất biến của ta: **agent không thể làm một ghi chú đã xác nhận biến mất hay đổi trạng thái**.

`dan-calin/shared-agent-memory` (MIT): proxy trước `@modelcontextprotocol/server-memory` tải qua `npx -y` không ghim phiên bản; mọi agent đều xóa được. Đáng học: khối `BEGIN/END` có đánh dấu khi sửa file hướng dẫn, ghi nguyên tử (file tạm rồi đổi tên), cảnh báo khi thay mục cấu hình lạ.

## Các phương án

| Tiêu chí | 1. Lõi native mỏng (PR #9) | 2. Memorix làm backend + adapter chính sách | 3. Lai: sổ quyết định đã xác nhận + Memorix tùy chọn để tìm kiếm |
|---|---|---|---|
| Windows | Python chuẩn, đã chạy CI Windows | Cần Node 22.18 và có thể gói native | Cả hai |
| Phụ thuộc | Không | Hơn 19 gói, cập nhật nhanh | Không cho lõi; Memorix khi bật |
| Ngữ nghĩa tin cậy | Đúng thiết kế, có test | Phải chặn `memorix_resolve`, `memorix_store` cùng `topicKey`, `memorix_knowledge`... ở lớp MCP; dễ lọt khi Memorix thêm tool | Sổ của ta giữ điều được tin; Memorix chỉ là nguồn đọc, kết quả gắn nhãn chưa xác nhận |
| Cài đặt hoàn tác được | Không đụng agent (đăng ký là bước riêng, M3) | Setup của Memorix ghi hook không backup; phải tự cài thay | Như 1 |
| Phạm vi dữ liệu | Một file DB mỗi dự án; backup/restore từng dự án | Một DB cho mọi dự án | Như 1 cho dữ liệu tin cậy |
| Chi phí test | 37 test hiện có | Cần test tích hợp với mã Node đổi nhanh | Như 1, cộng test adapter khi làm |
| Tìm kiếm | Khớp chuỗi con, không FTS | FTS5, Orama, vector tùy chọn | Có, khi bật |
| Bảo trì dài hạn | Ta sở hữu ~745 dòng | Phụ thuộc lộ trình của bên khác | Thấp cho lõi |

## Quyết định

**Chọn phương án 1 ngay bây giờ.** Giữ lõi `brain.py`, không viết lại.

Phương án 3 là hướng mở rộng có điều kiện, **hoãn**: chỉ làm adapter Memorix *chỉ đọc* khi (a) có ít nhất một dự án mà tìm kiếm chuỗi con không đủ, và (b) adapter có thể chạy Memorix mà không để nó ghi hook hay cấu hình agent. Không bao giờ đưa kết quả từ Memorix vào trạng thái `verified` nếu chủ dự án chưa xác nhận trong sổ của ta.

**Từ chối phương án 2**: chặn mọi đường ghi của Memorix ở lớp MCP là chạy theo bề mặt tool của họ; một lần họ thêm tool là ta lọt.

Mượn ý tưởng, không mượn mã: tách `claimKey`/`conflictKey` để phát hiện mâu thuẫn tốt hơn khớp tiêu đề; bảng sự kiện chỉ-thêm; khối `BEGIN/END` khi sửa file hướng dẫn agent.

## Ranh giới

- **Lưu trữ** (SQLite, tìm kiếm, sao lưu): chi tiết thực thi, có thể thay sau.
- **Quản trị** (của OmniAgent-OS): danh tính agent cố định khi khởi chạy, agent chỉ đề xuất, chỉ chủ dự án xác nhận, bằng chứng có hash, không agent nào hạ hay ẩn ghi chú đã xác nhận, đăng ký vào agent có xem trước, backup và hoàn tác.

## Giới hạn của chính quyết định này

Mọi agent chạy dưới cùng tài khoản Windows với chủ dự án đều có thể ghi thẳng vào file SQLite hoặc gọi `Brain.verify()` từ Python. Cổng "phải có terminal tương tác" chỉ chặn trường hợp agent chạy CLI qua công cụ shell thông thường. Phương án nào trong ba cũng vậy; tách thật sự cần tài khoản OS khác hoặc phê duyệt ngoài băng (xem [BRAIN.md](BRAIN.md)).
