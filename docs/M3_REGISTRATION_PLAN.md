# M3: đăng ký MCP `omniagent-brain` vào Claude Code, Codex và Antigravity (kế hoạch)

> English summary: a plan, not an implementation. Each agent already ships an official `mcp add/remove` command, so registration uses those instead of hand-editing config files. Every step is preview-first, backed up, reversible, and needs the owner's approval per agent. Nothing has been written to any agent config.

Trạng thái: **kế hoạch để bạn duyệt. Chưa ghi gì vào cấu hình của agent nào.** Phụ thuộc: [BRAIN.md](BRAIN.md) (PR #9). Số liệu khảo sát lấy ngày 28/10/2026 trên một máy Windows, chỉ ghi số lượng và tên khóa, không giá trị (file cấu hình có thể chứa token).

## 1. Mục tiêu và nguyên tắc

Mục tiêu: sau M3, cả ba agent gọi được `brain_search`, `brain_read`, `brain_propose`, `brain_conflicts`, và vertical slice chạy với **agent thật** (hiện mới chạy với client MCP giả).

1. **Dùng lệnh chính thức của từng agent**, không tự sửa file cấu hình. Lý do: các file này lớn, do ứng dụng ghi liên tục, có kiểu xuống dòng riêng và có thể chứa token.
2. **Xem trước, sao lưu, rồi mới ghi**, mỗi agent một lần duyệt riêng.
3. **Chỉ thêm đúng một mục** (`omniagent-brain`). Không đọc, in hay đổi giá trị của mục khác.
4. **Hoàn tác được** bằng `mcp remove` và kiểm tra rằng mọi mục khác không đổi.
5. **Lệnh đăng ký không chứa bí mật.** Máy chủ brain không cần token; không dùng `-e KEY=...` hay `--header`.
6. **Chủ động hạn chế phạm vi** khi agent hỗ trợ (xem mục 3).

## 2. Hiện trạng đã khảo sát

| Agent | Nơi cấu hình | Định dạng | Quy mô | Lệnh chính thức |
|---|---|---|---|---|
| Claude Code | `~/.claude.json` | JSON, 140 KB, do ứng dụng ghi liên tục | 3 mục ở phạm vi user; 57 dự án, 2 dự án có mục riêng | `claude mcp add/remove`, cờ `--scope` (`local`, `user`, `project`) |
| Codex | `~/.codex/config.toml` | TOML, 20 KB, nhiều bảng khác (`projects`, `plugins`, `hooks`...) | 5 mục `mcp_servers` | `codex mcp add/remove/list/get` |
| Antigravity CLI (`agy`) | `~/.gemini/config/mcp_config.json` | JSON, **kết thúc dòng CRLF** | 11 mục | `agy mcp add/remove/list/enable/disable` |
| Antigravity (hai file khác) | `~/.gemini/antigravity/mcp_config.json`, `~/.gemini/antigravity-ide/mcp_config.json` | JSON | 6 và 10 mục, **khác** danh sách của `agy` | chưa rõ ai đọc |

Bằng chứng cho dòng Antigravity: `agy mcp list` liệt kê đúng 11 tên, trùng hoàn toàn với `config/mcp_config.json` và chỉ trùng một phần với hai file còn lại. Vì vậy mục tiêu của `agy mcp add` là `config/mcp_config.json`. **Chưa biết** Antigravity IDE có đọc `antigravity-ide/mcp_config.json` hay `config/mcp_config.json`; phải kiểm chứng riêng (mục 6), không đoán.

## 3. Quyết định đăng ký theo từng agent

Mục đăng ký chung: tên `omniagent-brain`, lệnh `python`, đối số `<runtime>\brain.py --brain-dir <thư mục brain tuyệt đối> --project omniagent-os serve --agent <claude|codex|antigravity>`.

**Đường dẫn ổn định (`runtime`).** Nếu trỏ thẳng vào thư mục repo thì đổi nhánh Git sẽ làm mất `brain.py` và MCP hỏng âm thầm. Kế hoạch: bước chuẩn bị chép đúng một file `brain.py` sang `.local\runtime\brain-<12 ký tự hash>.py` (trong workspace, không đưa lên git) và đăng ký đường dẫn đó. Nâng cấp = chép bản mới + đăng ký lại. `--brain-dir` luôn truyền tường minh để không phụ thuộc vị trí script.

| Agent | Phạm vi đề xuất | Lệnh sẽ chạy (khi được duyệt) | Ghi chú |
|---|---|---|---|
| Claude Code | `--scope local` từ thư mục dự án: chỉ dự án này thấy, riêng tư với tài khoản | `claude mcp add --scope local omniagent-brain -- python <runtime> ...` | Khớp nguyên tắc "bộ nhớ theo dự án". Không dùng `--scope project` vì nó tạo `.mcp.json` trong repo chứa đường dẫn cá nhân |
| Codex | Toàn cục qua CLI, hoặc theo dự án bằng `<dự án>/.codex/config.toml` (chỉ có hiệu lực với dự án đã được tin cậy; file gần nhất thắng) | `codex mcp add omniagent-brain -- python <runtime> ...` | `codex mcp add` **không có cờ chọn phạm vi** (đã kiểm `--help`): luôn ghi vào cấu hình người dùng. Phạm vi dự án nghĩa là tự thêm một bảng vào `.codex/config.toml` (thay đổi file có kiểm soát, hoàn tác được), chưa làm trong M3.1 |
| Antigravity CLI | Toàn cục (`config/mcp_config.json`) | `agy mcp add omniagent-brain -- python <runtime> ...` (cờ phải đứng trước tên) | Cùng lưu ý phạm vi như Codex |

Vì danh tính (`--agent`) và dự án cố định lúc khởi chạy, ghi chú đề xuất từ một phiên Codex ở dự án khác vẫn vào `omniagent-os` nhưng được gắn `source_agent=codex` và luôn là `inbox`. Đó là lý do cần chấp nhận rủi ro "toàn cục" một cách có ý thức.

## 4. Quy trình mỗi agent (xem trước, sao lưu, ghi, kiểm tra, hoàn tác)

Công cụ `mcp_register.py` (M3.1, đã xây, có 26 test với CLI giả và cấu hình tạm; chưa chạy `apply` trên máy thật). Dùng: `python mcp_register.py runtime`, `plan --agent claude`, `apply --agent claude --yes` (cần terminal tương tác), `rollback --backup-dir <thư mục>`. Mặc định chỉ xem trước:

1. **Điều kiện đầu:** tìm thấy CLI; `brain.py` chạy được; trạng thái sao lưu ghi lại (kích thước, hash, danh sách tên mục).
2. **Xem trước (mặc định):** in lệnh chính xác sẽ chạy, file đích, đường dẫn sao lưu, và khẳng định "chưa ghi gì". Không in giá trị của mục nào.
3. **Sao lưu:** chép file cấu hình đích sang `.local\backups\mcp-<giờ>\` và kiểm tra hash bản sao. File sao lưu có thể chứa token nên nằm ngoài git và phải giữ nguyên quyền riêng tư.
4. **Ghi:** chạy đúng một lệnh `mcp add` (cần `--yes` và, tương tự `verify`, một terminal tương tác).
5. **Kiểm tra sau khi ghi:** (a) danh sách tên mục = danh sách trước + `omniagent-brain`; (b) mọi mục khác không đổi (so sánh sau khi phân tích cú pháp, không so byte vì ứng dụng có thể định dạng lại); (c) bắt tay MCP thật với **đúng lệnh đã đăng ký** (`initialize`, `tools/list` thấy bốn công cụ).
6. **Hoàn tác:** `mcp remove omniagent-brain`, rồi kiểm tra các mục còn lại bằng danh sách ở bước 1. Chỉ khi `remove` lỗi mới cân nhắc khôi phục từ bản sao lưu, và chỉ khi file không đổi ngoài mục của mình kể từ lúc ghi (nếu không sẽ ghi đè thay đổi của ứng dụng).

Kiểm thử (không động vào máy thật): CLI giả ghi vào file tạm; kịch bản cho thêm, kiểm tra, hoàn tác, mục trùng tên, CLI lỗi giữa chừng, file bị ứng dụng đổi khi đang thao tác, và khẳng định không có giá trị nào xuất hiện trong đầu ra.

## 5. Thứ tự và cổng duyệt

| Bước | Việc | Ghi vào máy thật? | Cần bạn duyệt |
|---|---|---|---|
| M3.0 | Chuẩn bị `.local\runtime\brain-<hash>.py` và kiểm tra bắt tay stdio với đúng lệnh (`mcp_register.py runtime`) | Không (chỉ `.local`) | Không. Đã code và test; chưa chạy vào `.local` của repo chính |
| M3.1 | `mcp_register.py` (xem trước, sao lưu, áp dụng, hoàn tác) + test CLI giả | Không | Merge PR. Đã code và test; chạy `plan` chỉ-đọc trên cấu hình thật xác nhận phân tích được cả ba file (claude, codex: 5 mục mỗi bên; agy: 11 mục), không ghi gì |
| M3.2 | Claude Code, `--scope local`, dự án này | Có (`~/.claude.json`) | **Có** |
| M3.3 | Thử thật: Claude gọi `brain_propose` và `brain_search` | Ghi vào `.local\brain` | Có |
| M3.4 | Codex | Có (`config.toml`) | **Có** |
| M3.5 | Antigravity CLI (`agy`) | Có (`config/mcp_config.json`) | **Có** |
| M3.6 | Vertical slice thật: Claude ghi, Codex đọc, Antigravity truy xuất, bạn `verify` | Ghi vào `.local\brain` | Có |
| M3.7 | Antigravity IDE (sau khi xác định nó đọc file nào) | Có | **Có** |

Bắt đầu bằng Claude `--scope local` vì rủi ro thấp nhất: chỉ một dự án, lệnh chính thức, hoàn tác bằng `claude mcp remove`.

## 6. Rủi ro và điều chưa biết

| Rủi ro hoặc điều chưa biết | Giảm thiểu |
|---|---|
| `~/.claude.json` đang được chính phiên Claude này ghi | Dùng CLI (ghi nguyên tử), sao lưu trước, thực hiện khi bạn đã đóng các phiên không cần thiết |
| Token trong file cấu hình và file sao lưu | Không bao giờ in giá trị; sao lưu nằm trong `.local`; không đưa lên git |
| Đổi nhánh Git làm mất `brain.py` | Chạy từ bản chép `.local\runtime\` có hash |
| Phạm vi toàn cục của Codex và Antigravity | Chấp nhận có ý thức, hoặc bỏ qua hai agent đó ở v0.4 |
| Antigravity IDE có thể đọc một file cấu hình khác | Kiểm chứng riêng ở M3.7, không đăng ký "cho chắc" vào cả ba file |
| Codex theo dự án | Có (`.codex/config.toml`, chỉ dự án tin cậy) nhưng CLI không ghi vào đó; để sau, nếu bạn muốn tránh phạm vi toàn cục |
| `agy` không có trên PATH của shell này | `plan` báo "agent CLI not found"; cần truyền `--cli <đường dẫn agy>` khi làm M3.5 |
| Nhiều tiến trình MCP dùng chung một file SQLite | Đã gặp lỗi `database is locked` khi thử tải (5/150 lần ghi) và đã sửa (busy timeout, khởi tạo schema một lần, `BEGIN IMMEDIATE`); có test nhiều tiến trình |
| Agent chạy được shell vẫn có thể gọi CLI chủ dự án | Đã có cổng terminal tương tác; **đó là ma sát, không phải xác thực** (xem BRAIN.md) |
| Client thật có thể xử lý giao thức MCP khác client giả | Đó chính là lý do M3.3 và M3.6 dùng agent thật; chưa kiểm chứng |
| `agy mcp add` "thêm hoặc cập nhật": trùng tên sẽ ghi đè | Bước xem trước kiểm tra tên chưa tồn tại và dừng nếu đã có |

## 7. Việc bạn cần quyết định

1. Duyệt thứ tự ở mục 5 và bắt đầu từ M3.0 và M3.1 (không ghi vào máy thật).
2. Có chấp nhận phạm vi **toàn cục** cho Codex và Antigravity, hay chỉ làm Claude `--scope local` trước.
3. Khi tới M3.2: thời điểm thuận tiện để đóng các phiên Claude khác.
