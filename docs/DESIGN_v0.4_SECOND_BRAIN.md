# Thiết kế v0.4: Shared Second Brain (bản nháp)

> English summary: a draft design for a local-first, verified memory shared by Claude Code, Codex and Antigravity through one MCP server. Agents may *propose* notes, but only evidence-backed notes are promoted to the trusted store. Nothing in this document is implemented yet; the first step is a read-only survey of what each agent already stores.

Trạng thái: **bản nháp để bạn duyệt**, chưa có code. Dựa trên mục tiêu gốc ở [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md): mỗi agent có "second brain" riêng, không thấy nhau, và không ai kiểm chứng được điều gì là đúng.

## 1. Mục tiêu và ngoài phạm vi

Mục tiêu:
- Một kho ghi chú dự án dùng chung, mọi agent đọc được và đề xuất ghi qua MCP.
- Mỗi ghi chú có **nguồn gốc** (agent nào, khi nào, từ đâu) và **trạng thái kiểm chứng**.
- Local-first, chạy trên Windows, không cần dịch vụ ngoài.

Ngoài phạm vi (v0.4):
- Đồng bộ qua máy khác hoặc cloud.
- Tự động nhập toàn bộ lịch sử hội thoại của các agent.
- Điều phối nhiều agent (đó là v0.5).
- Thay thế bộ nhớ riêng của từng agent: kho chung chạy song song, không xóa hay sửa bộ nhớ gốc.

## 2. Nguyên tắc (kế thừa từ Skill Manager)

1. **Xem trước rồi mới ghi.** Mọi thao tác ghi vào thư mục của agent thật (đăng ký MCP) đi qua `plan` rồi `--yes`, có journal, backup và `recover`, dùng lại cơ chế của v0.3.
2. **Agent chỉ đề xuất, không tự khẳng định.** Ghi chú mới vào hộp thư `inbox`, chưa tin cậy.
3. **Không bí mật trong bộ nhớ.** Quét token, khóa và đường dẫn nhạy cảm trước khi lưu; từ chối hoặc che. (Đã có một lần token lộ qua đầu ra công cụ trong buổi làm việc này, nên quy tắc này không phải lý thuyết.)
4. **Một chủ sở hữu cho mỗi dữ liệu.** Kho chung thuộc OmniAgent-OS; bộ nhớ gốc của từng agent giữ nguyên.
5. **Phạm vi hẹp, quyền tối thiểu.** Mỗi ghi chú thuộc một dự án; agent chỉ thấy dự án được cấp.

## 3. Mô hình dữ liệu

Mỗi ghi chú là một file Markdown có frontmatter, nằm trong `.local/brain/` (không đưa lên git):

```yaml
id: n-20261027-0001
project: omniagent-os
kind: fact | decision | procedure | warning
status: inbox | verified | contested | superseded
source: {agent: claude|codex|antigravity|user, session: <id>, at: <ISO-8601 UTC>}
evidence: [{type: file, path: ..., sha256: ...}, {type: command, cmd: ..., exit: 0}, {type: user, note: ...}]
supersedes: [<id>]
```

Phần thân là văn bản ngắn. Chỉ mục (tìm kiếm) là một file SQLite dựng lại được từ các file Markdown, nên mất chỉ mục không mất dữ liệu.

## 4. Vòng đời và kiểm chứng

- `propose` -> `inbox` (mọi agent được ghi).
- `verify` -> `verified` chỉ khi có bằng chứng kiểm tra lại được: file còn khớp sha256, lệnh chạy lại cho cùng kết quả, hoặc người dùng xác nhận.
- Hai ghi chú mâu thuẫn trên cùng chủ đề -> cả hai thành `contested` cho tới khi người dùng chọn.
- Ghi chú cũ bị thay -> `superseded`, giữ lại để truy vết.
- Chỉ `verified` được trả về mặc định khi agent tìm kiếm; `inbox` chỉ trả về khi hỏi rõ và được gắn nhãn chưa kiểm chứng.

## 5. Giao diện MCP

Một máy chủ MCP cục bộ (stdio) cho cả ba agent. Công cụ:

| Công cụ | Quyền | Mô tả |
|---|---|---|
| `brain_search` | đọc | Tìm ghi chú theo dự án, loại, trạng thái |
| `brain_read` | đọc | Đọc một ghi chú kèm bằng chứng |
| `brain_propose` | ghi inbox | Đề xuất ghi chú mới; quét bí mật trước khi lưu |
| `brain_verify` | ghi | Chạy kiểm chứng và đổi trạng thái; ghi lại kết quả |
| `brain_conflicts` | đọc | Liệt kê ghi chú `contested` |

Chưa có `brain_delete`: xóa là thao tác của người dùng qua CLI, có xác nhận.

## 6. Cách nối vào từng agent

Mỗi agent đăng ký MCP ở một nơi khác nhau (đã khảo sát chỉ-đọc, chỉ theo tên):

| Agent | Nơi cấu hình MCP | Nơi bộ nhớ riêng |
|---|---|---|
| Claude Code | `~/.claude.json` (mục `mcpServers`) | `~/.claude/projects/<dự án>/memory/` (Markdown) |
| Codex | `~/.codex/config.toml` (mục `mcp_servers`) | `~/.codex/memories_1.sqlite` (lược đồ chưa khảo sát) |
| Antigravity/Gemini | `~/.gemini/config/mcp_config.json` | chưa xác định |

Đăng ký dùng chính bộ máy `plan`/`apply`/`rollback`/`recover` của v0.3: sao lưu file cấu hình trước, chỉ thêm đúng một mục, hoàn tác được. Không bao giờ in giá trị của các mục khác trong file cấu hình (chúng có thể chứa token).

## 7. Lộ trình con

| Mốc | Nội dung | Ghi vào thư mục agent thật? |
|---|---|---|
| M0 | Khảo sát chỉ-đọc: mỗi agent đang lưu gì, bao nhiêu, định dạng nào | Không |
| M1 | Kho ghi chú, CLI (`propose`, `list`, `verify`), quét bí mật, test | Không |
| M2 | Máy chủ MCP stdio dùng kho M1, test bằng client MCP giả | Không |
| M3 | Đăng ký MCP vào từng agent qua plan/apply/rollback | Có, cần bạn duyệt từng lần |
| M4 | Phát hiện mâu thuẫn, nhập ghi chú từ bộ nhớ gốc (chỉ đọc nguồn) | Không |
| M5 | Beta có kiểm soát: dùng thật với một dự án | Có |

## 8. Câu hỏi cần bạn quyết

1. **Tự xây hay dùng Memorix?** Memorix đã làm bộ nhớ qua MCP nhưng không có nguồn gốc/kiểm chứng theo thiết kế này. Đề xuất: giữ giao diện MCP tương thích rồi thử Memorix như một backend ở M2 trước khi quyết; nếu không đạt yêu cầu về nguồn gốc và kiểm chứng thì tự xây kho mỏng.
2. **Có nhập bộ nhớ sẵn có của các agent không?** Đề xuất: chỉ ở M4, chỉ đọc, và mọi thứ nhập vào đều vào `inbox`, không tự `verified`.
3. **Phạm vi dự án:** một kho theo dự án (theo thư mục git) hay một kho toàn cục có nhãn dự án? Đề xuất: theo dự án, để quyền tối thiểu rõ ràng.

## 9. Rủi ro đã biết

- Lược đồ `memories_1.sqlite` của Codex và vị trí bộ nhớ của Antigravity chưa được khảo sát; M0 phải làm trước khi cam kết nhập liệu.
- Kiểm chứng tự động chỉ làm được cho bằng chứng loại file và lệnh; ghi chú dạng quyết định hay thông tin ngoài máy cần người xác nhận.
- Một máy chủ MCP chạy cho cả ba agent là điểm lỗi chung; cần chế độ chỉ-đọc khi kho hỏng.
