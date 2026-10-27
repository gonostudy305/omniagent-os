# M0: khảo sát bộ nhớ của các agent (chỉ-đọc)

> English summary: a read-only survey (counts, sizes, schema names; no note contents were read) of what Claude Code, Codex and Antigravity keep as memory on this one Windows machine. Only Claude Code holds curated memory; Codex's store is empty and Antigravity's `brain` is per-conversation working files.

Ngày khảo sát: 27/10/2026, một máy Windows. Số liệu chỉ là quan sát trên máy này. Kịch bản khảo sát chỉ đếm tên, số lượng, kích thước và tên bảng/cột; không đọc nội dung ghi chú và không in giá trị cấu hình.

## Kết quả

| Agent | Nơi lưu | Định dạng | Quy mô quan sát | Đánh giá |
|---|---|---|---|---|
| Claude Code | `~/.claude/projects/<dự án>/memory/` | Markdown, mỗi thư mục có chỉ mục `MEMORY.md` | 19 trên 26 dự án có bộ nhớ; 118 file, khoảng 430 KB | Kiến thức đã chắt lọc, nhỏ, có cấu trúc. Nguồn nhập tốt nhất |
| Codex | `~/.codex/memories_1.sqlite` | SQLite (WAL), các bảng `stage1_outputs`, `jobs`, `consolidation_progress` | 0 dòng dữ liệu bộ nhớ (`stage1_outputs` và `jobs` trống); thư mục `memories/` không tồn tại | Cơ chế có nhưng chưa được dùng. Không có gì để nhập |
| Antigravity (CLI) | `~/.gemini/antigravity/brain/` | Tạo phẩm theo hội thoại: transcript JSONL, `output.txt`, ảnh, mã | khoảng 399 hội thoại, hơn 47 nghìn file, khoảng 2 GB | Không phải kiến thức chắt lọc; rất lớn, dễ chứa bí mật. Không nhập hàng loạt |
| Antigravity (IDE) | `~/.gemini/antigravity-ide/brain/`, `conversations/` | Tạo phẩm hội thoại, có `implementation_plan.md` và `walkthrough.md`; cơ sở dữ liệu hội thoại | khoảng 208 hội thoại, hơn 13 nghìn file, khoảng 0,9 GB; hội thoại khoảng 1,3 GB | Như trên; chỉ các file kế hoạch/walkthrough có thể đáng nhập có chọn lọc |
| Antigravity | `knowledge/` | chỉ có `knowledge.lock` | 0 ghi chú | Tính năng kiến thức chưa được dùng |

## Hệ quả cho thiết kế v0.4

1. **Nguồn nhập đầu tiên là bộ nhớ Claude Code** (Markdown nhỏ, có chỉ mục). Codex và Antigravity không có kiến thức chắt lọc để nhập.
2. **Không nhập hàng loạt từ Antigravity.** Hàng GB tạo phẩm hội thoại không phải ghi chú; nếu nhập, chỉ có chọn lọc `implementation_plan.md` và `walkthrough.md`, qua quét bí mật, vào `inbox`.
3. Giá trị ban đầu của kho chung là **làm cho bộ nhớ Claude được Codex và Antigravity thấy, kèm kiểm chứng**, và là nơi chung để cả ba cùng ghi từ nay. Không phải gộp ba kho có cỡ tương đương.
4. Cần quét bí mật bắt buộc ở mọi đường vào, vì Antigravity lưu nguyên đầu ra lệnh và cấu hình.
5. Câu hỏi 2 ở mục 8 của thiết kế (có nhập bộ nhớ sẵn có không) được thu hẹp: nhập từ Claude, chỉ đọc, vào `inbox`; không nhập từ Antigravity ở v0.4.

## Giới hạn của khảo sát

- Chỉ một máy, một thời điểm. Codex có thể bắt đầu ghi vào kho bộ nhớ khi dùng nhiều hơn.
- Không đọc nội dung nên chưa đánh giá chất lượng hay độ đúng của 118 ghi chú Claude.
- Chưa kiểm tra liệu ghi chú Claude có chứa bí mật; việc này thuộc M1 (quét bí mật).
