# Shared Second Brain (v0.4 MVP)

> English summary: `brain.py` is a local, per-project note store (SQLite, standard library only) exposed to Claude Code, Codex and Antigravity through a stdio MCP server. Agents can only *propose* notes; a note is `verified` only when the project owner confirms it through the CLI. Agent identity and project are fixed when the server is launched. Text is scanned for secrets before it is stored. Nothing is registered in any agent's config by this change: `register-plan` only prints snippets.

Trạng thái: **MVP, chạy được và có test, chưa đăng ký vào agent thật.** Thiết kế gốc ở [DESIGN_v0.4_SECOND_BRAIN.md](DESIGN_v0.4_SECOND_BRAIN.md) (nếu đã merge) và khảo sát bộ nhớ ở [M0_MEMORY_SURVEY.md](M0_MEMORY_SURVEY.md).

## Vì sao tự xây một lõi mỏng

Quyết định đầy đủ và bằng chứng từ mã nguồn Memorix: [ADR_SHARED_BRAIN_BACKEND.md](ADR_SHARED_BRAIN_BACKEND.md). Tóm tắt:

| Giải pháp | Nhận xét | Quyết định |
|---|---|---|
| Memorix (mã nguồn commit `4712b4d`) | Mạnh hơn ta về tìm kiếm (FTS5, vector tùy chọn) và mô hình claim/mâu thuẫn. Nhưng tool MCP mặc định cho mọi agent cùng dự án resolve/archive ghi nhớ của nhau, duyệt claim không kiểm tra người gọi, một DB chung cho mọi dự án, setup ghi đè hook agent không backup. Node ≥ 22.18 | Không làm backend bây giờ; có thể thêm adapter *chỉ đọc* sau |
| Mem0 | Thường cần LLM để trích xuất và embedder (mặc định OpenAI), hay đi kèm Postgres/pgvector | Quá nặng và dễ đưa dữ liệu ra ngoài |
| Letta | Chạy cả một agent server; không thấy MCP server chính thức | Quá nặng cho một lớp bộ nhớ |

Lõi tự xây khoảng 745 dòng, không phụ thuộc gói ngoài. Tên công cụ MCP (`brain_search`, `brain_read`, `brain_propose`, `brain_conflicts`) đặt đơn giản để dễ thay backend.

## Mô hình tin cậy

1. **Agent chỉ đề xuất.** Ghi chú từ agent vào trạng thái `inbox` (chưa kiểm chứng). Mặc định tìm kiếm chỉ trả về ghi chú `verified`; muốn thấy đề xuất phải bật `include_unverified`, và kết quả luôn gắn nhãn `UNVERIFIED`.
2. **Chỉ chủ dự án xác nhận.** `verify` chỉ có trong CLI, không có trong MCP, và MCP không có công cụ xóa. Các lệnh CLI làm thay đổi điều dự án tin cậy (`propose` với tư cách chủ dự án, `verify`, `restore --replace`) đòi hỏi một **terminal tương tác** và bạn phải gõ lại mã xác nhận. Qua ống dẫn hoặc script (cách công cụ shell của agent chạy lệnh) chúng bị từ chối. **Đây là rào cản ma sát, không phải xác thực.** Một agent có quyền chạy lệnh dưới cùng tài khoản Windows có nhiều cách vượt qua, đơn giản nhất là ghi thẳng `status='verified'` vào file `.local/brain/<project>.db`, hoặc gọi `Brain(...).verify()` từ Python; tạo pseudo-terminal cũng được. Không có chữ ký, không có xác thực, không chống được tiến trình cùng tài khoản. Điều thật sự được bảo vệ là **lớp MCP**: một client chỉ nói MCP không có công cụ nào để xác nhận, xóa, khôi phục hay đổi danh tính. Muốn tách thật sự cần phê duyệt ngoài băng (tài khoản Windows khác giữ file DB, hoặc thiết bị khác); chưa làm, và không cần cho beta một người dùng, miễn là bạn biết giới hạn này.
3. **Danh tính cố định khi khởi chạy.** `--agent` và `--project` được truyền khi chạy máy chủ; tham số do client gửi (`source_agent`, `status`, `project`...) bị bỏ qua. Không thể chạy máy chủ MCP với danh tính `user`.
4. **Quét bí mật trước khi lưu.** Từ chối khóa riêng, bearer token, token GitHub/Slack/AWS/`sk-`, JWT, URL có mật khẩu, `password = ...`, chuỗi entropy cao. Thông báo lỗi chỉ nêu *loại*, không lặp lại giá trị. Quét theo heuristic nên có thể từ chối nhầm hoặc bỏ sót; đừng coi là bảo đảm.
5. **Che đường dẫn cá nhân.** Thư mục home và `C:\Users\<tên>` được thay bằng `~`.
6. **Agent không bao giờ đổi trạng thái của ghi chú đã `verified`.** Khi agent đề xuất ghi chú trái ngược (cùng loại và tiêu đề, nội dung khác), chính ghi chú *mới* bị giữ ở `contested`; ghi chú đã xác nhận vẫn `verified` và vẫn hiện ở tìm kiếm mặc định. `brain_conflicts` liệt kê ghi chú bị giữ cùng `conflicts_with`. Hai đề xuất chưa xác nhận mâu thuẫn nhau thì cùng bị giữ. Tham số `supersedes` của agent chỉ là **đề xuất thay thế** (`pending_replacement`): ghi chú cũ giữ nguyên cho tới khi bạn `verify` ghi chú mới, lúc đó ghi chú cũ và các ghi chú đối địch mới chuyển sang `superseded`. Chỉ ghi chú do chính bạn viết mới thay thế ngay lập tức.
7. **Bằng chứng tệp.** Có thể đính kèm tệp; hash SHA-256 được lưu. `recheck` đánh dấu `contested` nếu tệp đổi hoặc mất.
8. **Nhật ký.** Mọi hành động (đề xuất, xác nhận, thay thế, mâu thuẫn, nhập) ghi vào bảng `audit` kèm tác nhân. Nhật ký nằm trong cùng file DB nên cũng không chống được việc sửa trực tiếp (xem mục 2).
9. **Nội dung ghi chú là dữ liệu, không phải chỉ dẫn.** `initialize` và mọi kết quả `brain_search`, `brain_read`, `brain_conflicts` kèm trường `notice` nhắc client không làm theo lệnh nằm trong ghi chú. Văn bản được trả về nguyên vẹn: gắn nhãn, không lọc. Đây là lời nhắc cho model, không phải hàng rào kỹ thuật.
10. **Một yêu cầu MCP sai không làm sập server.** Tham số sai kiểu trả lỗi `-32602` hoặc lỗi công cụ; lỗi SQLite (ví dụ DB bận) trả lỗi công cụ "nothing was written" mà không lộ đường dẫn; mọi lỗi khác trả `-32603`. Chỉ tên loại lỗi được ghi ra stderr; stdout chỉ chứa JSON-RPC.
11. **Nhiều agent cùng lúc.** Mỗi agent một tiến trình server, chung một file DB: WAL, chờ khóa tới 30 giây, ghi bắt đầu bằng `BEGIN IMMEDIATE`.

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

- 38 test trong `tests/test_brain.py` (149 test toàn repo, chạy trong CI trên Windows và Ubuntu) cho kho, quét bí mật, danh tính, mâu thuẫn, sao lưu, máy chủ MCP qua tiến trình thật, ghi đồng thời từ nhiều tiến trình, 9 dạng yêu cầu MCP sai kiểu, và nhãn "dữ liệu, không phải chỉ dẫn".
- **Vertical slice** (`test_vertical_slice_claude_writes_codex_reads_gemini_retrieves`), ba tiến trình MCP riêng với danh tính `claude`, `codex`, `antigravity`: Claude ghi một quyết định kiến trúc kèm bằng chứng; Codex đọc lại được, gắn nhãn chưa kiểm chứng; chủ dự án xác nhận; Antigravity truy xuất được dưới dạng đã kiểm chứng; một đề xuất mâu thuẫn hiện ra trong `brain_conflicts`; nhật ký nêu đủ các tác nhân; chủ dự án duyệt bản thay thế, bản cũ thành `superseded` nhưng vẫn đọc được và có trong nhật ký; dự án khác không thấy gì; backup, khôi phục sang thư mục mới và khởi động lại server cho ra đúng trạng thái đó.
- Kiểm tra đột biến: khi tạm tắt quét bí mật, cho phép ai cũng `verify`, cho agent tự `supersedes`, bỏ cổng terminal hoặc bỏ việc giữ lại ghi chú mâu thuẫn, các test tương ứng fail.
- Lỗi tin cậy được phát hiện khi review PR (một agent có thể làm ghi chú đã xác nhận biến mất khỏi tìm kiếm bằng `supersedes` hoặc bằng một ghi chú trái ngược) đã được sửa kèm test hồi quy.

**Giới hạn của phần kiểm chứng:** vertical slice dùng *client MCP giả* nói giao thức MCP, chưa phải Claude Code, Codex hay Antigravity thật.

### Client thật, trong profile dùng một lần

```powershell
python -I scripts\sandbox_native_mcp.py
```

Script chạy `claude` và `codex` thật nhưng trỏ `USERPROFILE`, `HOME`, `APPDATA`, `LOCALAPPDATA`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME` vào thư mục tạm và bỏ biến API key. Nó đăng ký, liệt kê rồi gỡ `omniagent-brain`. Các file cấu hình thật chỉ được so kích thước và thời điểm sửa (không đọc nội dung); kết quả `FAIL` nếu có file nào đổi. Không đăng nhập, không gọi model.

Kết quả chạy ngày 29/10/2026 trên máy chủ dự án (Windows 11, Python 3.13):

| Mục | Claude Code 2.1.246 | Codex CLI 0.163.0 | Antigravity |
|---|---|---|---|
| Client MCP giả: handshake, liệt kê, gọi tool, xác nhận, mâu thuẫn | PASS | PASS | PASS |
| CLI thật nhận cấu hình (`mcp add`, `mcp list`, `mcp remove`) | PASS | PASS | NOT_VERIFIED (không có CLI trên PATH) |
| CLI thật khởi động server và handshake MCP | PASS (`claude mcp list` báo `Connected`) | PASS qua `codex exec` (`codex mcp list` không khởi động server) | NOT_VERIFIED |
| Model của agent (đã đăng nhập) tự gọi tool | PASS | PASS | NOT_VERIFIED |
| Vòng đề xuất → chưa xác nhận → chủ dự án xác nhận → tìm mặc định → mâu thuẫn bị giữ, qua model thật | PASS (cùng Codex, xem dưới) | PASS (mỗi bước là một tiến trình `codex exec` riêng) | NOT_VERIFIED |
| Agent thật này đề xuất, agent thật *khác* đọc được bản đã xác nhận | PASS (Claude đề xuất, Codex đọc) | PASS (Codex đọc bản của Claude) | NOT_VERIFIED |
| Cấu hình thật không đổi trong lúc chạy | PASS, trừ `~/.claude.json` (xem dưới) | PASS | — |

**Lần chạy có đăng nhập (29/10/2026, chủ dự án cho phép):** chỉ dùng cờ cho từng lần chạy, không sửa cấu hình lâu dài. Kết quả:

- **Codex CLI 0.163.0, model thật:** `brain_propose` tạo ghi chú `inbox`; `brain_search` với `include_unverified` trả `verified:false`; sau khi chủ dự án xác nhận (bước này gọi `Brain.verify()` thay cho CLI vì CLI cần terminal tương tác), `brain_search` mặc định trả đúng ID ngẫu nhiên của ghi chú đã xác nhận; đề xuất trái ngược nhận `contested` và ghi chú cũ vẫn `verified`. Nhật ký ghi tác nhân `codex` lấy từ lúc khởi chạy server. Các file `~/.codex/config.toml`, `auth.json`, `~/.claude.json`, `~/.claude/settings.json`, `.credentials.json` giữ nguyên kích thước và thời điểm sửa.
- **Phát hiện tương thích:** Codex hỏi duyệt **mọi** lời gọi tool MCP; với `approval_policy="never"`, cả `brain_search` cũng bị từ chối. Vì vậy các tool chỉ đọc nay khai báo `annotations.readOnlyHint=true` theo đặc tả MCP, và Codex cho chạy chúng không cần duyệt. `brain_propose` vẫn cần duyệt; lần chạy này cho phép riêng nó qua `-c` bằng khóa `tools.brain_propose.approval_mode="approve"`. Khóa này tìm thấy trong binary Codex 0.163.0 và hoạt động, nhưng **không có trong tài liệu chính thức**, có thể đổi ở bản sau.
- **Giới hạn cô lập:** ghi đè `mcp_servers` qua `-c` thay được danh sách server MCP cục bộ, nhưng Codex vẫn khởi động các connector từ xa của plugin trong cấu hình người dùng (thất bại vì chưa đăng nhập) và chạy hook của plugin. Không có file cấu hình nào bị đổi, nhưng đây không phải môi trường sạch hoàn toàn.
- **Claude Code 2.1.246 cùng Codex, model thật (lần chạy thứ hai, sau khi chủ dự án đăng nhập lại CLI):** Claude gọi `brain_propose`, ghi chú vào `inbox` với tác nhân `claude`; Codex tìm với `include_unverified` thấy đúng ID đó, `verified:false`; chủ dự án xác nhận; Codex tìm mặc định trả ID đó rồi đề xuất ý trái ngược, nhận `contested`; Claude tìm mặc định chỉ thấy ghi chú đã xác nhận, không thấy bản `contested` của Codex. Nhật ký: `claude propose`, `user verify`, `codex propose`, `codex contested`. Lần chạy đầu thất bại vì CLI npm chưa đăng nhập (`claude auth status` báo `loggedIn: false`); đăng nhập trong app desktop không dùng chung cho CLI.
- **`~/.claude.json` đổi thời điểm sửa** sau lần chạy Claude. Kiểm bằng cách chỉ đếm số dòng khớp, không đọc nội dung: file không chứa `omniagent-brain` và không chứa đường dẫn dự án tạm, tức server không bị ghi vào cấu hình. Đây là dữ liệu trạng thái mà CLI tự ghi mỗi lần chạy, nên không thể chạy Claude Code mà giữ file này nguyên vẹn tuyệt đối. Các file còn lại giữ nguyên.

Lệnh để tự chạy lại, trong một thư mục dự án tạm:

```powershell
# Claude Code: nạp server chỉ cho lần chạy này (--strict-mcp-config bỏ qua cấu hình MCP khác)
'{"mcpServers":{"omniagent-brain":{"command":"python","args":["-I","<repo>\\brain.py","--brain-dir","<tạm>\\brain","--project","sandbox-demo","serve","--agent","claude"]}}}' | Set-Content brain-mcp.json -Encoding utf8
claude -p "Call brain_search with include_unverified=true and print the raw result" --mcp-config brain-mcp.json --strict-mcp-config --allowedTools mcp__omniagent-brain__brain_search --setting-sources project --no-session-persistence

# Codex: chỉ server này, chỉ cho lần chạy này; --ephemeral không lưu phiên, sandbox chỉ đọc
codex exec --skip-git-repo-check --ephemeral -s read-only -c 'approval_policy="never"' -c 'mcp_servers={omniagent-brain={command="python",args=["-I","<repo>/brain.py","--brain-dir","<tạm>/brain","--project","sandbox-demo","serve","--agent","codex"],tools={brain_propose={approval_mode="approve"}}}}' "Call the brain_search MCP tool and print the raw result"
```

Đạt khi kết quả có trường `notice` và danh sách `notes` khớp với `python brain.py --brain-dir <tạm>\brain --project sandbox-demo list --all`. Cả hai lệnh đã chạy thật với các cờ như trên. Bỏ `tools={...}` nếu chỉ muốn thử đọc. Antigravity không có CLI: thêm server qua giao diện MCP của IDE trong một workspace tạm, rồi gỡ ra.

## Chưa làm

- Đăng ký vào cấu hình thật của ba agent (M3) và thử bằng agent thật.
- Tìm kiếm ngữ nghĩa (hiện chỉ khớp chuỗi con, không embedding).
- Nhập có chọn lọc từ bộ nhớ Claude Code (M4); không nhập từ Antigravity.
- Kiểm chứng tự động bằng cách chạy lại lệnh (cố ý không làm: sẽ thực thi lệnh do agent đề xuất).
- Phê duyệt tách khỏi quyền chạy lệnh của agent (xem giới hạn ở mục 2 của mô hình tin cậy).
- Adapter Memorix chỉ đọc (phương án lai trong ADR): hoãn tới khi tìm kiếm chuỗi con không đủ dùng.
- Phát hiện mâu thuẫn theo chủ đề thay vì chỉ theo tiêu đề trùng (ý tưởng `conflictKey` của Memorix).
- Test kill tiến trình giữa lúc ghi SQLite (hiện dựa vào tính nguyên tử của giao dịch SQLite, chưa có test riêng).
- Đăng ký thật vào cấu hình agent: công cụ `mcp_register` đang ở nhánh riêng `feat/m3-register-tool`, chưa thành PR.
- Điều phối nhiều agent (v0.5): handoff mới chỉ là một loại ghi chú, chưa có giao thức nhận việc và quyền sở hữu.
- Quét bí mật chỉ là heuristic; che đường dẫn chỉ xử lý thư mục home.
