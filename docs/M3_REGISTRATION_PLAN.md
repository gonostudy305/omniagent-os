# M3: đăng ký MCP `omniagent-brain` vào Claude Code, Codex và Antigravity

> English summary: `mcp_register.py` registers the brain server through each agent's own `mcp add/remove`, with the lifecycle inspect → preview → approve → apply → verify → rollback (+ recover). Apply needs the config hash from the plan and stops if the file changed. Rollback restores the original bytes only while the file is exactly as apply left it; otherwise it refuses, and `--entry-only` removes just our entry. Every stage is journaled and one run per config holds a lock. Proven against real Claude Code 2.1.246 and Codex 0.163.0 in throwaway profiles only; Antigravity only with a fake CLI. Nothing has been written to any real agent config.

Trạng thái: **công cụ đã xây và kiểm thử; chưa ghi vào cấu hình thật của agent nào.** Đăng ký trên máy thật (M3.2 trở đi) cần bạn cho phép riêng từng agent. Phụ thuộc: [BRAIN.md](BRAIN.md) (PR #9, đã merge). Số liệu khảo sát lấy ngày 28/10/2026 trên một máy Windows, chỉ ghi số lượng và tên khóa, không giá trị (file cấu hình có thể chứa token).

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

Công cụ `mcp_register.py` (M3.1, đã xây; 58 test với CLI giả và cấu hình tạm, cộng một lần chạy với CLI thật trong profile dùng một lần, xem mục 8; chưa chạy `apply` trên máy thật). Dùng:

```powershell
python mcp_register.py runtime
python mcp_register.py plan --agent claude                                   # chỉ đọc
python mcp_register.py apply --agent claude --yes --expect-sha <original_sha256 từ plan>   # cần terminal tương tác
python mcp_register.py rollback --backup-dir <thư mục apply in ra>           # khôi phục đúng từng byte
python mcp_register.py rollback --backup-dir <thư mục> --entry-only          # chỉ gỡ mục của mình
python mcp_register.py recover                                               # xử lý lần chạy bị ngắt
```

Mặc định chỉ xem trước. Chi tiết hợp đồng an toàn ở mục 8; các bước:

1. **Điều kiện đầu:** tìm thấy CLI; `brain.py` chạy được; trạng thái sao lưu ghi lại (kích thước, hash, danh sách tên mục).
2. **Xem trước (mặc định):** in lệnh chính xác sẽ chạy, file đích, đường dẫn sao lưu, và khẳng định "chưa ghi gì". Không in giá trị của mục nào.
3. **Sao lưu:** chép file cấu hình đích sang `.local\backups\mcp-<giờ>\` và kiểm tra hash bản sao. File sao lưu có thể chứa token nên nằm ngoài git và phải giữ nguyên quyền riêng tư.
4. **Ghi:** chạy đúng một lệnh `mcp add` (cần `--yes` và, tương tự `verify`, một terminal tương tác).
5. **Kiểm tra sau khi ghi:** (a) danh sách tên mục = danh sách trước + `omniagent-brain`; (b) mọi mục khác không đổi (so sánh sau khi phân tích cú pháp, không so byte vì ứng dụng có thể định dạng lại); (c) bắt tay MCP thật với **đúng lệnh đã đăng ký** (`initialize`, `tools/list` thấy bốn công cụ).
6. **Hoàn tác:** nếu file vẫn đúng hash đã ghi lúc `apply`, khôi phục nguyên từng byte từ bản sao lưu (ghi qua file tạm rồi thay thế một bước; file vốn không tồn tại thì xóa lại). Nếu file đã đổi sau đó, **từ chối** và không động vào; `--entry-only` gỡ riêng mục của mình bằng `mcp remove`, rồi kiểm tra mọi mục khác vẫn như ngay trước khi gỡ.

Kiểm thử (không động vào máy thật): CLI giả ghi vào file tạm; kịch bản cho thêm, kiểm tra, hoàn tác, mục trùng tên, CLI lỗi giữa chừng, file bị ứng dụng đổi khi đang thao tác, và khẳng định không có giá trị nào xuất hiện trong đầu ra.

## 5. Thứ tự và cổng duyệt

| Bước | Việc | Ghi vào máy thật? | Cần bạn duyệt |
|---|---|---|---|
| M3.0 | Chuẩn bị `.local\runtime\brain-<hash>.py` và kiểm tra bắt tay stdio với đúng lệnh (`mcp_register.py runtime`) | Không (chỉ `.local`) | Không. Đã code và test; chưa chạy vào `.local` của repo chính |
| M3.1 | `mcp_register.py` (xem trước, sao lưu, áp dụng, hoàn tác đúng từng byte, nhật ký, khóa, `recover`) + test CLI giả + lần chạy CLI thật trong profile dùng một lần | Không | Merge PR. Đã code và test; chạy `plan` chỉ-đọc trên cấu hình thật (28/10) xác nhận phân tích được cả ba file (claude, codex: 5 mục mỗi bên; agy: 11 mục), không ghi gì |
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
| `~/.claude.json` thật bị các phiên Claude đang chạy ghi lại liên tục | Gần như chắc chắn hash sẽ đổi giữa `apply` và `rollback`, nên hoàn tác đúng từng byte sẽ bị từ chối; dùng `--entry-only`. Đây là thiết kế: không ghi đè thay đổi của ứng dụng |
| Claude CLI tự để lại file của nó (`.claude/backups/.claude.json.backup.*`, log MCP trong `AppData/Local/claude-cli-nodejs/Cache`) | Không phải file của OmniAgent-OS nên `rollback` không xóa; file cấu hình thì về đúng từng byte |
| Một lần chạy bị ngắt giữa chừng giữ khóa | Không bao giờ xóa khóa vì "trông cũ"; `recover` đọc nhật ký rồi mới quyết định (mục 8) |

## 7. Việc bạn cần quyết định

1. Duyệt thứ tự ở mục 5 và bắt đầu từ M3.0 và M3.1 (không ghi vào máy thật).
2. Có chấp nhận phạm vi **toàn cục** cho Codex và Antigravity, hay chỉ làm Claude `--scope local` trước.
3. Khi tới M3.2: thời điểm thuận tiện để đóng các phiên Claude khác.

## 8. Hợp đồng an toàn, bằng chứng và cách khôi phục

**Vòng đời:** inspect → preview (`plan`) → approve (`--yes`, `--expect-sha`, terminal tương tác) → `apply` → verify → `rollback`, cộng `recover`.

**`plan`** không ghi vào cấu hình. Nó cho biết: agent, phạm vi, đường dẫn file (thư mục home thay bằng `~`), file có tồn tại không, `original_sha256` (hoặc `absent`), mục đề xuất chính xác (lệnh, đối số), các mục hiện có (chỉ tên), xung đột tên, điều sẽ thay đổi, cách sao lưu và hoàn tác, và lệnh `apply` kèm hash. `expected_output_sha256` để trống có chủ đích: CLI của agent tự ghi file (định dạng lại, cập nhật khóa riêng của nó), nên không đoán trước được; `apply` ghi lại hash thực tế.

**`apply`** (mỗi bước ghi vào `journal.json` qua file tạm rồi thay thế):

1. Chạy lại `plan`; dừng nếu có vấn đề hoặc nếu hash khác `--expect-sha`.
2. Tạo khóa cho file cấu hình (tạo nguyên tử; lần chạy thứ hai bị từ chối). Nhật ký `prepared`.
3. Sao lưu `config.bak`, kiểm tra hash. Nhật ký `backed-up`.
4. Kiểm tra lại ngay trước khi ghi: file phải đúng hash của plan và của bản sao lưu, nếu không thì `aborted-unchanged`.
5. Nhật ký `writing`, chạy `mcp add` của agent. Nhật ký `verifying`.
6. Kiểm tra: file vẫn phân tích được, có đúng một mục mới, mọi mục khác không đổi, lệnh đã đăng ký bắt tay MCP được. Lỗi thì gỡ mục của mình (`failed-rolled-back`) hoặc báo `failed-needs-attention`.
7. Ghi `applied_sha256`, nhật ký `applied`.

Từ chối ngay, không ghi gì, khi: file không phải UTF-8, JSON không phải object, danh sách server hoặc dự án có dạng lạ, TOML hoặc JSON hỏng (không trích nội dung), đã có mục trùng tên.

**`rollback`**: như mục 4, bước 6. Lỗi ghi (ví dụ bị khóa quyền) để nguyên file như sau `apply` và báo lỗi.

**`recover`** (chỉ chạy khi không có `apply` nào đang chạy; cần xác nhận ở terminal): với mỗi nhật ký còn mở, file vẫn đúng hash gốc thì `aborted-unchanged`; đã có mục của mình và mọi mục khác không đổi thì gỡ mục (`recovered-entry-removed`); có mục khác đổi thì **để nguyên** và báo `needs-attention`. Nhật ký không đọc được cũng được báo và để nguyên. Khóa chỉ được nhả khi lần chạy của nó đã khép lại.

**Bằng chứng (29/10/2026, Windows 11, Python 3.13):**

| Kiểm tra | Claude Code 2.1.246 | Codex CLI 0.163.0 | Antigravity (`agy`) |
|---|---|---|---|
| Vị trí và định dạng cấu hình do CLI thật ghi (profile dùng một lần) | `$CLAUDE_CONFIG_DIR/.claude.json`, LF; mục `{type: stdio, command, args, env: {}}`; phạm vi local nằm trong `projects.<đường dẫn>` | `$CODEX_HOME/config.toml`, LF; bảng `[mcp_servers.omniagent-brain]` | NOT_VERIFIED (không có `agy` trên máy) |
| `scripts/sandbox_register_e2e.py`, chưa có cấu hình: plan → apply → CLI thấy mục → server đúng dự án và danh tính → rollback đúng từng byte | PASS (`mcp list` báo Connected) | PASS (`mcp get`) | NOT_VERIFIED |
| Như trên, với cấu hình có sẵn cài đặt riêng, CRLF | PASS | PASS | NOT_VERIFIED |
| Profile thật giữ nguyên kích thước và thời điểm sửa | PASS | PASS | — |
| Test với CLI giả (`tests/test_mcp_register.py`, 58 test) | PASS | PASS | PASS |

Test với CLI giả bao gồm: thiếu file, file hỏng, UTF-16, BOM + CRLF, mục trùng tên, giữ các mục khác, sửa ngoài giữa plan và apply, giữa sao lưu và ghi, giữa apply và rollback, file chỉ đọc, lỗi khi khôi phục, sập ở từng giai đoạn của nhật ký, nhật ký hỏng, khóa còn sót, bốn lần đăng ký song song (đúng một thành công), không lộ token, và chạy đúng lệnh đã đăng ký để đề xuất một ghi chú với đúng dự án và danh tính. Kiểm tra đột biến: tắt kiểm tra hash trước khi khôi phục, kiểm tra lại trước khi ghi, khóa, `--expect-sha`, kiểm tra dạng cấu hình, hoặc việc `recover` để nguyên file đã bị sửa, đều làm test fail.

**Chưa kiểm chứng:** đăng ký vào profile thật (cần bạn cho phép riêng); Antigravity với CLI hay IDE thật; agent thật tự gọi tool qua mục do công cụ này đăng ký (lần chạy có đăng nhập trong [BRAIN.md](BRAIN.md) dùng cờ từng lần, không dùng mục đã đăng ký); `recover` không phân biệt được một `apply` đang chạy với một lần đã chết, nên chỉ chạy khi chắc không có `apply` nào.
