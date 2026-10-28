# Shared Second Brain (v0.4 MVP)

> English summary: `brain.py` is a local, per-project note store (SQLite, standard library only) exposed to Claude Code, Codex and Antigravity through a stdio MCP server. Agents can only *propose* notes; a note is `verified` only when the project owner confirms it through the CLI. Agent identity and project are fixed when the server is launched. Text is scanned for secrets before it is stored. Nothing is registered in any agent's config by this change: `register-plan` only prints snippets.

Trạng thái: **MVP, chạy được và có test, chưa đăng ký vào agent thật.** Thiết kế gốc ở [DESIGN_v0.4_SECOND_BRAIN.md](DESIGN_v0.4_SECOND_BRAIN.md) (nếu đã merge) và khảo sát bộ nhớ ở [M0_MEMORY_SURVEY.md](M0_MEMORY_SURVEY.md).

## Vì sao tự xây một lõi mỏng

Đánh giá nhanh (nguồn là các trang thư mục MCP và trang so sánh bên thứ ba, **chưa xác minh từ repo chính thức**):

| Giải pháp | Nhận xét | Quyết định |
|---|---|---|
| Memorix | SQLite cục bộ, MCP stdio; hỗ trợ Claude Code ở mức "core", Codex "extended", Gemini CLI "community". Chưa thấy mô hình nguồn gốc và kiểm chứng như yêu cầu của dự án | Để dành làm backend thử nghiệm sau |
| Mem0 | Thường cần LLM để trích xuất và embedder (mặc định OpenAI), hay đi kèm Postgres/pgvector | Quá nặng và dễ đưa dữ liệu ra ngoài |
| Letta | Chạy cả một agent server; không thấy MCP server chính thức | Quá nặng cho một lớp bộ nhớ |

Lõi tự xây khoảng 650 dòng, không phụ thuộc gói ngoài. Tên công cụ MCP (`brain_search`, `brain_read`, `brain_propose`, `brain_conflicts`) đặt đơn giản để dễ thay backend.

## Mô hình tin cậy

1. **Agent chỉ đề xuất.** Ghi chú từ agent vào trạng thái `inbox` (chưa kiểm chứng). Mặc định tìm kiếm chỉ trả về ghi chú `verified`; muốn thấy đề xuất phải bật `include_unverified`, và kết quả luôn gắn nhãn `UNVERIFIED`.
2. **Chỉ chủ dự án xác nhận.** `verify` chỉ có trong CLI, không có trong MCP. Không có công cụ xóa trong MCP.
3. **Danh tính cố định khi khởi chạy.** `--agent` và `--project` được truyền khi chạy máy chủ; tham số do client gửi (`source_agent`, `status`, `project`...) bị bỏ qua. Không thể chạy máy chủ MCP với danh tính `user`.
4. **Quét bí mật trước khi lưu.** Từ chối khóa riêng, bearer token, token GitHub/Slack/AWS/`sk-`, JWT, URL có mật khẩu, `password = ...`, chuỗi entropy cao. Thông báo lỗi chỉ nêu *loại*, không lặp lại giá trị. Quét theo heuristic nên có thể từ chối nhầm hoặc bỏ sót; đừng coi là bảo đảm.
5. **Che đường dẫn cá nhân.** Thư mục home và `C:\Users\<tên>` được thay bằng `~`.
6. **Mâu thuẫn.** Hai ghi chú cùng loại và tiêu đề nhưng nội dung khác nhau đều thành `contested` và không còn hiện ở tìm kiếm mặc định. Chủ dự án `verify` một bên thì bên kia thành `superseded`.
7. **Bằng chứng tệp.** Có thể đính kèm tệp; hash SHA-256 được lưu. `recheck` đánh dấu `contested` nếu tệp đổi hoặc mất.
8. **Nhật ký.** Mọi hành động (đề xuất, xác nhận, thay thế, mâu thuẫn, nhập) ghi vào bảng `audit` kèm tác nhân.

Mỗi dự án là một file `<brain-dir>/<project>.db` (mặc định `.local/brain/`, không đưa lên git). Tên dự án chỉ gồm chữ thường, số, `.`, `_`, `-`.

## Dùng thử (không ghi vào agent nào)

```powershell
# Ghi một quyết định với tư cách chủ dự án (tự động verified)
python brain.py --project omniagent-os propose --kind decision --title "Memory backend" --body "SQLite per project, stdio MCP."

python brain.py --project omniagent-os list            # chỉ ghi chú đã xác nhận
python brain.py --project omniagent-os list --all      # kèm đề xuất chưa kiểm chứng
python brain.py --project omniagent-os conflicts       # ghi chú mâu thuẫn
python brain.py --project omniagent-os verify <id>     # xác nhận một đề xuất
python brain.py --project omniagent-os recheck         # kiểm tra lại bằng chứng tệp
python brain.py --project omniagent-os audit

# Sao lưu, khôi phục, xuất, nhập
python brain.py --project omniagent-os backup .local\brain-backups\omniagent-os.db
python brain.py --project omniagent-os restore .local\brain-backups\omniagent-os.db --replace   # giữ bản an toàn *.pre-restore-*.db
python brain.py --project omniagent-os export notes.jsonl
python brain.py --project omniagent-os import notes.jsonl   # luôn vào inbox, không tin cờ verified
```

Mã thoát: `0` thành công, `2` yêu cầu bị từ chối (đầu vào sai, có bí mật, không đủ quyền).

## Nối vào agent (chưa áp dụng)

```powershell
python brain.py --project omniagent-os register-plan
```
In ra lệnh `claude mcp add ...`, khối `[mcp_servers.omniagent-brain]` cho Codex và khối `mcpServers` cho Antigravity. Lệnh này **không ghi gì**. Áp dụng vào cấu hình thật là bước riêng, cần bạn duyệt, có backup và hoàn tác. Không bao giờ in các mục khác trong file cấu hình (chúng có thể chứa token).

## Đã kiểm chứng

- 28 test mới (123 test tổng) cho kho, quét bí mật, danh tính, mâu thuẫn, sao lưu và máy chủ MCP qua tiến trình thật.
- **Vertical slice** (`test_vertical_slice_claude_writes_codex_reads_gemini_retrieves`), ba tiến trình MCP riêng với danh tính `claude`, `codex`, `antigravity`: Claude ghi một quyết định kiến trúc kèm bằng chứng; Codex đọc lại được, gắn nhãn chưa kiểm chứng; chủ dự án xác nhận; Antigravity truy xuất được dưới dạng đã kiểm chứng; một đề xuất mâu thuẫn hiện ra trong `brain_conflicts`; nhật ký nêu đủ các tác nhân.
- Kiểm tra đột biến: khi tạm tắt quét bí mật hoặc cho phép ai cũng `verify`, các test tương ứng fail.

**Giới hạn của phần kiểm chứng:** vertical slice dùng *client MCP giả* nói giao thức MCP, chưa phải Claude Code, Codex hay Antigravity thật. Việc đăng ký vào agent thật và xác nhận từng agent gọi được công cụ chưa làm.

## Chưa làm

- Đăng ký vào cấu hình thật của ba agent (M3) và thử bằng agent thật.
- Tìm kiếm ngữ nghĩa (hiện chỉ khớp chuỗi con, không embedding).
- Nhập có chọn lọc từ bộ nhớ Claude Code (M4); không nhập từ Antigravity.
- Kiểm chứng tự động bằng cách chạy lại lệnh (cố ý không làm: sẽ thực thi lệnh do agent đề xuất).
- Điều phối nhiều agent (v0.5): handoff mới chỉ là một loại ghi chú, chưa có giao thức nhận việc và quyền sở hữu.
- Quét bí mật chỉ là heuristic; che đường dẫn chỉ xử lý thư mục home.
