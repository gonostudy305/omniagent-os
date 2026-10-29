# M3: đăng ký MCP `omniagent-brain` vào Claude Code, Codex và Antigravity

> English summary: `mcp_register.py` registers the brain server through each agent's own `mcp add/remove`, with the lifecycle inspect → preview → approve → apply → verify → rollback (+ recover). Apply needs the config hash from the plan and stops if the file changed. Rollback restores the original bytes only while the file is exactly as apply left it; otherwise it refuses, and `--entry-only` removes just our entry. Every stage is journaled. M3.1.1 hardening: one OS file lock per config file and user, independent of `--state-dir`, so `recover` leaves a run whose process is alive alone (no PID or age heuristics; an unknown lock state fails closed); `--entry-only` and `recover` remove our entry only while it is exactly the entry that was registered; and every setting outside the MCP tables is compared, so a CLI that removes or changes one (real Claude Code 2.1.246 drops a `theme` key) makes the run fail and be reverted. Proven against real Claude Code 2.1.246 and Codex 0.163.0 in throwaway profiles only; Antigravity only with a fake CLI. Nothing has been written to any real agent config.

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
5. **Kiểm tra sau khi ghi:** (a) danh sách tên mục = danh sách trước + `omniagent-brain`; (b) mọi mục khác không đổi (so sánh sau khi phân tích cú pháp, không so byte vì ứng dụng có thể định dạng lại); (c) mọi thiết lập ngoài bảng MCP không bị xóa hay đổi (khóa CLI thêm mới thì được liệt kê trong kết quả); (d) mục mới chạy đúng lệnh đề xuất và không mang gì thêm; (e) bắt tay MCP thật với **đúng lệnh đã đăng ký** (`initialize`, `tools/list` thấy bốn công cụ).
6. **Hoàn tác:** nếu file vẫn đúng hash đã ghi lúc `apply`, khôi phục nguyên từng byte từ bản sao lưu (ghi qua file tạm rồi thay thế một bước; file vốn không tồn tại thì xóa lại). Nếu file đã đổi sau đó, **từ chối** và không động vào; `--entry-only` gỡ riêng mục của mình bằng `mcp remove`, nhưng chỉ khi mục đó còn đúng từng thuộc tính như lúc `apply` ghi nhận, rồi kiểm tra mọi mục và thiết lập khác vẫn như ngay trước khi gỡ.

Kiểm thử (không động vào máy thật): CLI giả ghi vào file tạm; kịch bản cho thêm, kiểm tra, hoàn tác, mục trùng tên, CLI lỗi giữa chừng, file bị ứng dụng đổi khi đang thao tác, và khẳng định không có giá trị nào xuất hiện trong đầu ra.

## 5. Thứ tự và cổng duyệt

| Bước | Việc | Ghi vào máy thật? | Cần bạn duyệt |
|---|---|---|---|
| M3.0 | Chuẩn bị `.local\runtime\brain-<hash>.py` và kiểm tra bắt tay stdio với đúng lệnh (`mcp_register.py runtime`) | Không (chỉ `.local`) | Không. Đã code và test; chưa chạy vào `.local` của repo chính |
| M3.1 | `mcp_register.py` (xem trước, sao lưu, áp dụng, hoàn tác đúng từng byte, nhật ký, khóa, `recover`) + test CLI giả + lần chạy CLI thật trong profile dùng một lần | Không | Merge PR. Đã code và test; chạy `plan` chỉ-đọc trên cấu hình thật (28/10) xác nhận phân tích được cả ba file (claude, codex: 5 mục mỗi bên; agy: 11 mục), không ghi gì |
| M3.1.1 | Gia cố: khóa hệ điều hành theo file cấu hình và người dùng (không phụ thuộc `--state-dir`), `recover` không đụng lần chạy còn sống, `--entry-only` chỉ gỡ đúng mục đã đăng ký, so sánh các thiết lập ngoài MCP | Không (chỉ tạo `~/.omniagent-os/register-locks` khi chạy `apply`/`rollback`/`recover`) | Merge PR |
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
| CLI của agent đổi thiết lập không liên quan | Đã gặp thật: Claude Code 2.1.246 xóa khóa `theme` khỏi `.claude.json` khi `mcp add` (profile dùng một lần, cả khi profile đã khởi tạo); `model`, `hooks`, khóa tùy chỉnh thì giữ. `apply` phát hiện và hoàn tác; nghĩa là cấu hình còn `theme` sẽ không đăng ký được bằng Claude CLI bản này. Codex 0.163.0 chỉ đổi `mcp_servers` |
| Python bản Microsoft Store ảo hóa thư mục `AppData` | Khóa đặt dưới `%LOCALAPPDATA%` sẽ khác nhau giữa Python Store và Python khác; vì thế khóa nằm ở `~/.omniagent-os/register-locks` |
| Phiên Claude khác ghi `~/.claude.json` trong lúc `apply` | Đã giải quyết ở M3.2: `apply` đối chiếu `file_sha(config)` với `written` ngay sau khi CLI ghi và trước khi hoàn tất. Nếu file bị sửa ngoài luồng (`current != written`), `apply` từ chối tự động ghi đè bằng `config.bak`, giữ nguyên file để bảo tồn dữ liệu người dùng và đánh dấu `failed-needs-attention` |
| `apply` bị kill trong lúc CLI của agent (tiến trình con) còn chạy | Đã giải quyết ở M3.2: `apply` ghi nhận `child_pid` vào nhật ký ngay khi khởi tạo tiến trình; `recover` kiểm tra `is_pid_alive(child_pid)` qua kernel API an toàn. Nếu writer con còn chạy, `recover` giữ nguyên file và khóa, báo `in-progress` kèm hướng dẫn; chỉ xử lý khi writer đã dừng |
| Khóa chỉ loại trừ các lần chạy của công cụ này | Agent và phiên Claude không dùng khóa này; vì vậy vẫn cần kiểm tra hash ngay trước khi ghi và so sánh sau khi ghi. Chạy song song bản M3.1 cũ (khóa trong `--state-dir`) với bản mới thì không loại trừ nhau |

## 7. Việc bạn cần quyết định

1. Duyệt thứ tự ở mục 5 và bắt đầu từ M3.0 và M3.1 (không ghi vào máy thật).
2. Có chấp nhận phạm vi **toàn cục** cho Codex và Antigravity, hay chỉ làm Claude `--scope local` trước.
3. Khi tới M3.2: thời điểm thuận tiện để đóng các phiên Claude khác.

## 8. Hợp đồng an toàn, bằng chứng và cách khôi phục

**Vòng đời:** inspect → preview (`plan`) → approve (`--yes`, `--expect-sha`, terminal tương tác) → `apply` → verify → `rollback`, cộng `recover`.

**`plan`** không ghi vào cấu hình. Nó cho biết: agent, phạm vi, đường dẫn file (thư mục home thay bằng `~`), file có tồn tại không, `original_sha256` (hoặc `absent`), mục đề xuất chính xác (lệnh, đối số), các mục hiện có (chỉ tên), xung đột tên, điều sẽ thay đổi, cách sao lưu và hoàn tác, và lệnh `apply` kèm hash. `expected_output_sha256` để trống có chủ đích: CLI của agent tự ghi file (định dạng lại, cập nhật khóa riêng của nó), nên không đoán trước được; `apply` ghi lại hash thực tế.

**`apply`** (mỗi bước ghi vào `journal.json` qua file tạm rồi thay thế):

1. Giữ khóa của file cấu hình (xem **Khóa** bên dưới); lần chạy thứ hai, dù dùng `--state-dir` khác, bị từ chối.
2. Chạy lại `plan` trong khóa; dừng nếu có vấn đề hoặc nếu hash khác `--expect-sha`. Nhật ký `prepared` (ghi cả mục đề xuất và hash của từng thiết lập ngoài MCP).
3. Sao lưu `config.bak`, kiểm tra hash. Nhật ký `backed-up`.
4. Kiểm tra lại ngay trước khi ghi: file phải đúng hash của plan và của bản sao lưu, nếu không thì `aborted-unchanged`.
5. Nhật ký `writing`, chạy `mcp add` của agent. Ghi hash của mục vừa được viết, nhật ký `verifying`.
6. Kiểm tra như mục 4, bước 5. Lỗi thì: nếu file vẫn đúng như CLI vừa ghi, khôi phục nguyên byte gốc (hoàn tác cả thiết lập CLI đã đổi); nếu có chương trình khác ghi sau CLI, chỉ gỡ mục của mình và báo phần còn khác. Kết quả `failed-rolled-back` hoặc `failed-needs-attention`.
7. Ghi `applied_sha256` và hash của mục đã đăng ký, nhật ký `applied`.

Từ chối ngay, không ghi gì, khi: file không phải UTF-8, JSON không phải object, danh sách server hoặc dự án có dạng lạ, TOML hoặc JSON hỏng (không trích nội dung), đã có mục trùng tên.

**`rollback`**: như mục 4, bước 6. Lỗi ghi (ví dụ bị khóa quyền) để nguyên file như sau `apply` và báo lỗi. `--entry-only` so mục hiện có với hash đã ghi lúc `apply`; khác (lệnh, đối số, `env` hay thuộc tính nào khác) thì từ chối, không in giá trị, và chỉ cách kiểm tra (`<cli> mcp get omniagent-brain`) rồi tự gỡ. Nhật ký từ bản M3.1 không có hash này nên cũng bị từ chối. Nếu `mcp remove` làm đổi thiết lập khác, file được trả về đúng như trước khi gỡ và báo lỗi.

**Khóa.** Mỗi file cấu hình (đường dẫn chuẩn hóa: `..`, chữ hoa/thường trên Windows, symlink) có một khóa trong `~/.omniagent-os/register-locks/`, dùng chung mọi `--state-dir`. Khóa là khóa file của hệ điều hành (`msvcrt.locking` trên Windows, `flock` trên Linux/macOS), giữ suốt lần chạy; hệ điều hành tự nhả khi tiến trình kết thúc, dù kết thúc kiểu gì. Vì vậy "chủ khóa còn sống không" được trả lời bằng chính khóa, không dựa vào PID (tránh PID bị dùng lại) hay tuổi file. Kèm theo là một bản ghi chủ (`<id>.owner.json`): lần chạy kết thúc mà nhật ký còn mở (bị kill, hay lỗi Python giữa chừng) để lại bản ghi này, và mọi lần chạy mới bị từ chối cho tới khi `recover`. Không xác định được trạng thái khóa (lỗi hệ điều hành) thì coi như đang bận: từ chối.

**`recover`** (cần xác nhận ở terminal): với mỗi nhật ký còn mở trong `--state-dir`, trước hết thử giữ khóa của file cấu hình đó. Đang có tiến trình giữ khóa (hay không xác định được) thì báo `in-progress` và **không đụng** gì: không đổi file, không đổi nhật ký, không nhả khóa. Giữ được khóa nghĩa là chủ đã chết; khi đó: file vẫn đúng hash gốc thì `aborted-unchanged`; còn mục của mình, mọi mục và thiết lập khác không đổi, và mục đó đúng là mục đã ghi (hash ghi sau `mcp add`, hoặc nếu chưa kịp ghi thì đúng lệnh đề xuất và không mang `env`/thuộc tính lạ) thì gỡ mục (`recovered-entry-removed`); ngoài ra thì **để nguyên** và báo `needs-attention`. Nhật ký không đọc được cũng được báo và để nguyên. Bản ghi chủ chỉ bị xóa khi lần chạy của nó đã khép lại; nếu nó thuộc một `--state-dir` khác, `recover` chỉ báo thư mục cần dùng.

**Bằng chứng (29/10/2026, Windows 11, Python 3.13):**

| Kiểm tra | Claude Code 2.1.246 | Codex CLI 0.163.0 | Antigravity (`agy`) |
|---|---|---|---|
| Vị trí và định dạng cấu hình do CLI thật ghi (profile dùng một lần) | `$CLAUDE_CONFIG_DIR/.claude.json`, LF; mục `{type: stdio, command, args, env: {}}`; phạm vi local nằm trong `projects.<đường dẫn>` | `$CODEX_HOME/config.toml`, LF; bảng `[mcp_servers.omniagent-brain]` | NOT_VERIFIED (không có `agy` trên máy) |
| `scripts/sandbox_register_e2e.py`, chưa có cấu hình: plan → apply → CLI thấy mục → server đúng dự án và danh tính → rollback đúng từng byte | PASS (`mcp list` báo Connected) | PASS (`mcp get`) | NOT_VERIFIED |
| Như trên, với cấu hình có sẵn cài đặt riêng (`model`, `hooks`, khóa tùy chỉnh), CRLF | PASS | PASS | NOT_VERIFIED |
| (M3.1.1) Mọi thiết lập có trước vẫn y nguyên sau `apply`; khóa CLI thêm mới được liệt kê | PASS (profile mới: CLI thêm 17 khóa khởi tạo, như `userID`, `firstStartTime`, và bản ghi dự án) | PASS (không thêm khóa nào) | NOT_VERIFIED |
| (M3.1.1) `--entry-only` từ chối mục cùng tên đã bị sửa (thêm `env`), file giữ nguyên; gỡ được mục còn nguyên và mọi thiết lập vẫn như sau `apply` | PASS | PASS | NOT_VERIFIED |
| (M3.1.1) Cấu hình có `theme`: CLI xóa nó khi `mcp add`, `apply` báo `setting 'theme' was removed`, hoàn tác đúng từng byte | PASS | — (Codex không xóa thiết lập nào) | NOT_VERIFIED |
| Profile thật giữ nguyên kích thước và thời điểm sửa; không tạo `~/.omniagent-os` thật | PASS | PASS | — |
| Test với CLI giả (`tests/test_mcp_register.py`, 88 test) | PASS | PASS | PASS |

**Bằng chứng riêng cho bốn biện pháp gia cố M3.1.1** (test với CLI giả, chạy trên Windows; CI chạy cả Ubuntu):

| Biện pháp | Test chứng minh |
|---|---|
| A. `recover` không đụng lần chạy còn sống | `LivenessTests`: một `apply` bị chặn giữa chừng trong luồng khác và **trong một tiến trình khác** được để nguyên (file, nhật ký, khóa); tiến trình đó bị kill thật thì `recover` xử lý được; bản ghi chủ mang PID còn sống không cản `recover` khi khóa đã nhả; khóa đang bị giữ được tôn trọng dù PID ghi lại đã chết; lỗi khi kiểm tra khóa thì từ chối (cả `apply` lẫn `recover`); lỗi Python giữa chừng chặn lần chạy mới cho tới `recover` |
| B. Khóa không phụ thuộc `--state-dir` | `LockIdentityTests`: `--state-dir` thứ hai bị từ chối khi lần đầu đang chạy; bốn luồng với bốn `--state-dir` khác nhau, đúng một thành công; các cách viết khác của cùng đường dẫn dùng chung khóa; lần chạy bị ngắt ở một `--state-dir` chặn `--state-dir` khác cho tới khi `recover` đúng thư mục |
| C. `--entry-only` chỉ gỡ đúng mục đã đăng ký | `EntryOnlyTests` (JSON và TOML): mục bị thêm `env` chứa token thì từ chối, không lộ token, file giữ nguyên; mục còn nguyên thì gỡ; nhật ký bản cũ thì từ chối; `recover` cũng không gỡ mục đã bị sửa |
| Kiểm tra đột biến (bản sao tạm, mỗi lần tắt một biện pháp) | Bỏ khóa hệ điều hành; `recover` bỏ qua khóa đang giữ; lần chạy dở vẫn xóa bản ghi chủ; khóa phụ thuộc `--state-dir`; mã khóa không chuẩn hóa đường dẫn; `--entry-only` bỏ qua hash mục; `recover` bỏ qua thuộc tính mục; bỏ qua thiết lập đổi; không trả lại file sau `mcp remove`: cả 9 đều làm test fail |
| D. Thiết lập ngoài MCP | `SettingsTests` (JSON và TOML): CLI xóa hay đổi một thiết lập (kể cả lồng bên trong `hooks`) thì `apply` thất bại và file về đúng từng byte; khóa CLI thêm mới được báo, không bị từ chối; `mcp remove` đổi thiết lập thì file được trả lại như trước khi gỡ; `recover` để nguyên khi thiết lập đã đổi |

Test với CLI giả bao gồm: thiếu file, file hỏng, UTF-16, BOM + CRLF, mục trùng tên, giữ các mục khác, sửa ngoài giữa plan và apply, giữa sao lưu và ghi, giữa apply và rollback, file chỉ đọc, lỗi khi khôi phục, sập ở từng giai đoạn của nhật ký, nhật ký hỏng, khóa còn sót, bốn lần đăng ký song song (đúng một thành công), không lộ token, và chạy đúng lệnh đã đăng ký để đề xuất một ghi chú với đúng dự án và danh tính. Kiểm tra đột biến: tắt kiểm tra hash trước khi khôi phục, kiểm tra lại trước khi ghi, khóa, `--expect-sha`, kiểm tra dạng cấu hình, hoặc việc `recover` để nguyên file đã bị sửa, đều làm test fail.

**Đảm bảo chính xác của phần so sánh thiết lập:** so theo hash giá trị đã phân tích của từng khóa cấp cao nhất (và từng khóa trong mỗi bản ghi dự án của Claude), nên thay đổi lồng bên trong một khóa cũng bị phát hiện. Không so: định dạng, thứ tự khóa, chú thích TOML. Khóa mới do CLI thêm không bị coi là lỗi mà được liệt kê trong `settings_added`.

**Chưa kiểm chứng:** đăng ký vào profile thật (cần bạn cho phép riêng); Antigravity với CLI hay IDE thật; agent thật tự gọi tool qua mục do công cụ này đăng ký (lần chạy có đăng nhập trong [BRAIN.md](BRAIN.md) dùng cờ từng lần, không dùng mục đã đăng ký); khóa trên macOS (chỉ chạy Windows và Ubuntu); CLI mồ côi sau khi `apply` bị kill (mục 6).

## 9. Gia cố an toàn trước Pilot M3.2 (Preflight Hardening)

Kết quả gia cố an toàn chuẩn bị cho thử nghiệm M3.2 (đăng ký thật vào Claude Code `--scope local`). Bản thân thử nghiệm M3.2 sẽ chỉ thực hiện khi có chỉ đạo và phê duyệt riêng.

### 9.1. Xử lý rủi ro tiến trình con mồ côi (Orphaned CLI Writer) & Spawn-to-journal Gap
- **Trạng thái bền vững của Writer (`writer_status`):** Quá trình ghi cấu hình được theo dõi qua các trạng thái bền vững: `spawning` → `running` → `exited` (hoặc `failed`).
- **Khắc phục khoảng trống Spawn-to-journal:** Trong trường hợp tiến trình cha bị kill ngay sau khi gọi `subprocess.Popen` nhưng trước khi `on_start` kịp ghi nhận `child_pid`, nhật ký lưu trạng thái `state: "writing"` với `writer_status: "spawning"` và `child_pid: None`. Lệnh `recover` coi đây là trạng thái **không xác định (indeterminate)**, tuyệt đối không tự ý khôi phục hay gỡ entry, trả về `needs-attention` và giữ nguyên file cấu hình cùng bằng chứng để người dùng kiểm tra thủ công.
- **Duy trì khóa bảo vệ đối với Writer không xác định:** Trong trạng thái không xác định (`state: "writing"` và thiếu `child_pid` hoặc chưa có bằng chứng writer đã thoát), `recover` **không đánh dấu nhật ký là hoàn tất** (không chuyển thành `failed-needs-attention`) và **không xóa bản ghi sở hữu (`owner`)**. Nhờ vậy, mọi yêu cầu `apply` mới đều bị từ chối an toàn (fail-closed với `RegisterError: an earlier registration was interrupted; run recover first`), và các lần gọi `recover` kế tiếp tiếp tục báo `needs-attention` cho đến khi người dùng xử lý tiến trình con thủ công.
- **Kiểm tra trạng thái sống qua `omniagent.process_alive`:** Tái sử dụng hàm `process_alive` từ `omniagent.py`:
  - Trên Windows: Dùng trực tiếp Windows API với đầy đủ `wintypes` (`OpenProcess(SYNCHRONIZE, False, pid)` + `WaitForSingleObject(handle, 0)`), chuẩn xác trên kiến trúc 64-bit và không phát tán bất kỳ tín hiệu console nào (loại trừ hoàn toàn rủi ro Ctrl+C / `KeyboardInterrupt`). Nếu gặp lỗi truy cập hoặc hệ điều hành, hàm trả về `None` (không rõ, fail-closed).
  - Trên POSIX: Sử dụng `os.kill(pid, 0)` với xử lý tường minh `ProcessLookupError` (False), `PermissionError` (True), `OSError` (None).
- **Hành vi của `recover`:** Khi nhật ký ở trạng thái `writing`:
  - Nếu `writer_status != "exited"` và `child_pid` còn sống (hoặc trạng thái chưa rõ, `process_alive(child_pid) is not False`), `recover` trả về `in-progress`, không chạm vào file cấu hình, không xóa bản ghi chủ (`owner`).
  - Nếu thiếu PID hoặc thiếu bằng chứng writer đã thoát (`writer_status != "exited"` và `not child_pid`), `recover` trả về `needs-attention`, giữ nguyên trạng thái `writing`, giữ nguyên khóa `owner` để chặn các lần `apply` mới.
  - Chỉ khi có bằng chứng chắc chắn tiến trình con đã kết thúc (`writer_status == "exited"` hoặc `process_alive(child_pid) is False`), `recover` mới tiến hành thu dọn an toàn.
- **Lưu ý về tái sử dụng PID:** Nếu hệ điều hành tái sử dụng (reuse) đúng PID đó cho một tiến trình khác trước khi `recover` chạy, `process_alive` sẽ thấy PID đang chạy và giữ nguyên `in-progress`. Đây là hướng an toàn (fail-closed, tránh ghi đè nhầm lên writer đang hoạt động); người dùng chỉ cần đợi tiến trình kết thúc hoặc dọn dẹp bản ghi owner thủ công.

### 9.2. Xác minh danh tính Entry khi Cleanup & Biên giới sửa đổi ngoài luồng
- **Bảo vệ danh tính Entry (`matches=entry_matcher`):** Trong mọi đường cleanup tự động khi `apply` thất bại (đặc biệt khi `current != written` hoặc có chỉnh sửa ngoài luồng), `apply` truyền hàm kiểm tra danh tính `matches=entry_matcher` vào `rollback_entry(...)`. Nếu một tác nhân khác đã sửa `command`, `args` hoặc `env` của `omniagent-brain` trước khi cleanup:
  - `rollback_entry` phát hiện fingerprint không khớp với entry mà công cụ đã đăng ký, lập tức từ chối gọi `mcp remove`, giữ nguyên entry đã bị sửa để không làm mất dữ liệu của người dùng.
  - Tuyệt đối không làm lộ token/secret trong thông báo lỗi hoặc nhật ký.
  - Nhật ký ghi nhận `failed-needs-attention` và báo lỗi ra terminal.
- **Nguyên tắc Fail-Closed đối với phục hồi file:**
  - *Bộ giám sát hash (`watch_file`) là công cụ chẩn đoán (diagnostic best-effort)*: Việc lấy mẫu hash (mỗi 10 ms) không thể chứng minh toán học sự vắng mặt của tiến trình ghi ngoài xen kẽ giữa các khoảng lấy mẫu. Do đó, OmniAgent-OS **tuyệt đối không dựa vào kết quả watcher để chứng minh độc quyền**. An toàn dữ liệu được bảo đảm bởi việc đối soát snapshot toàn diện trước/sau (`compare` và `settings_changes`). Watcher chỉ đóng vai trò chẩn đoán hỗ trợ; khi phát hiện bất thường (`concurrent_writes` hoặc `watcher_error`), failure cleanup kích hoạt thêm cờ rủi ro để từ chối `restore_original`.
  - *Xem cả thiết lập thêm mới (`added`) là rủi ro không xác định nguồn gốc khi failure cleanup*: Khi đăng ký hoặc handshake thất bại, nếu phát hiện có bất kỳ khóa thiết lập nào bị xóa, đổi (`changed`) **hoặc thêm mới (`added`)**, OmniAgent-OS coi đây là rủi ro có can thiệp ngoài trong lúc CLI chạy và **tuyệt đối không gọi `restore_original(config.bak)`** để tránh xóa mất khóa mới do người dùng/ứng dụng khác thêm. Thay vào đó, công cụ giữ nguyên file trên đĩa làm bằng chứng, chỉ gỡ riêng mục `omniagent-brain` qua `rollback_entry(..., matches=entry_matcher)`, và chuyển nhật ký sang `failed-needs-attention`.
  - *Điều kiện duy nhất cho phép `restore_original` chạy*: `restore_original(config, folder, original)` chỉ được phép chạy khi và chỉ khi: (1) CLI thực sự có ghi file (`current != original`); (2) file không bị thay đổi sau khi CLI thoát (`current == written`); (3) không có bất kỳ thiết lập nào ngoài MCP bị xóa hay đổi (`not other_settings_changed`); (4) không có bất kỳ thiết lập nào được thêm mới (`not added`); (5) không có mục MCP nào khác bị thay đổi (`not other_entry_problems`); và (6) watcher không phát hiện ghi đồng thời hay lỗi I/O. Chỉ khi thỏa mãn đồng thời 6 điều kiện này thì việc hoàn tác byte-for-byte về `original` mới đảm bảo 100% không làm mất dữ liệu ngoài mục `omniagent-brain`.
- **Loại bỏ hoàn toàn Blind Restore và TOCTOU trong `rollback_entry`:**
  - Trước đây `rollback_entry` kiểm tra `file_sha(config) == written` trước khi gọi `atomic_write(start)` nếu `mcp remove` làm biến đổi thiết lập ngoài. Cơ chế này tiềm ẩn rủi ro TOCTOU đối với các tiến trình ghi đồng thời.
  - Nay `rollback_entry` loại bỏ triệt để `atomic_write(start)`: nếu `mcp remove` làm thay đổi thiết lập ngoài MCP, công cụ **giữ nguyên file như hiện trạng** trên đĩa, trả về `clean: False`, báo cáo `failed-needs-attention` kèm hướng dẫn khôi phục thủ công an toàn từ `config.bak`.

### 9.3. Xử lý triệt để khóa `theme` trên Claude Code 2.1.246
- **Tái hiện:** Khi chạy `claude mcp add` (cả ở phạm vi `--scope local` lẫn `--scope project`), Claude Code 2.1.246 đọc `~/.claude.json`, chuẩn hóa và tự động lược bỏ khóa `theme`.
- **Dừng chặn trước khi thực thi tiến trình (`before Popen`):**
  - Trong `plan()`: tự động phát hiện khóa `theme` khi agent là `claude` và thêm trực tiếp vào danh sách `problems` để chặn đứng đăng ký trước khi bất kỳ file nào bị thay đổi.
  - Trong `apply()`: thiết lập chốt chặn phòng thủ nghiêm ngặt ngay đầu hàm, **trước khi tạo thư mục backup và trước khi gọi `subprocess.Popen`**. Nếu `theme` có trong cấu hình Claude, `apply` lập tức quăng lỗi `RegisterError` và dừng lại mà không chạy lệnh CLI.
  - **Không rò rỉ dữ liệu:** Thông báo lỗi chỉ kiểm tra sự tồn tại của tên khóa, tuyệt đối không in giá trị hay nội dung của `theme`.
  - **Không khuyến nghị phá hủy:** Công cụ không tự ý xóa `theme` và không khuyến nghị người dùng xóa `theme` như một giải pháp mặc định.

### 9.4. Bảng kiểm tra trước Pilot M3.2 (Read-only Pilot Checklist)

| Mục kiểm tra | Chi tiết |
|---|---|
| **Mục tiêu thử nghiệm** | Đăng ký `omniagent-brain` vào Claude Code ở phạm vi dự án (`--scope local`) |
| **Phạm vi áp dụng** | Duy nhất dự án `omniagent-os` |
| **File đích** | `~/.claude.json` (chỉ thêm mục vào bảng `projects[<đường dẫn omniagent-os>].mcpServers`) |
| **Thay đổi dự kiến** | Thêm 1 entry `omniagent-brain` (command: `python`, args: trỏ tới `.local/runtime/brain-<hash>.py`) |
| **Vị trí sao lưu** | `.local/backups/mcp-<timestamp>-<hash>-claude/config.bak` (ngoài git, bảo mật token) |
| **Chiến lược hoàn tác** | Chạy `rollback` (hoàn tác từng byte nếu file chưa đổi) hoặc `rollback --entry-only` |
| **Điều kiện dừng ngay (Stop Conditions)** | 1. `plan` hoặc `apply` phát hiện `theme` trong `~/.claude.json` (dừng chặn trước `Popen`).<br>2. Tên `omniagent-brain` đã tồn tại.<br>3. File cấu hình bị khóa bởi tiến trình khác hoặc đang có phiên `apply` dở dang.<br>4. Hash file thay đổi giữa bước `plan` và `apply`. |

### 9.5. Phân loại trạng thái kịch bản

- **TESTED (Đã kiểm chứng đạt PASS):**
  - Môi trường sandbox dùng một lần với CLI thật (Claude Code 2.1.246, Codex 0.163.0): plan, apply, handshake, serve project isolation, rollback byte-for-byte, và rollback entry-only.
  - Xử lý tiến trình con mồ côi qua `omniagent.process_alive` và tránh can thiệp khi writer còn sống hoặc chưa rõ trạng thái.
  - Giữ khóa sở hữu và không cho phép `apply` mới khi writer ở trạng thái không xác định (`not child_pid`).
  - Bảo toàn sửa đổi ngoài luồng đồng thời trong khi CLI chạy và sau khi CLI ghi, gỡ bỏ đúng entry khi xác minh thất bại mà không ghi đè `config.bak`.
  - Loại bỏ hoàn toàn blind full-file restore và TOCTOU trong `rollback_entry`; giữ nguyên file và báo `clean: False` khi có đột biến thiết lập ngoài.
  - Chặn đứng hoàn toàn Claude Code trước `Popen` khi cấu hình chứa `theme` mà không để lộ giá trị `theme`.
  - Toàn bộ 271 unit/regression tests địa phương trên cả Windows và Ubuntu.

- **NOT_VERIFIED (Chưa kiểm chứng):**
  - Đăng ký vào profile thật của người dùng trên máy thật (chờ phê duyệt M3.2).
  - Model thật của Claude Code / Codex tự động gọi `brain_search` và `brain_propose` qua entry đã đăng ký (do không sao chép credential thật vào sandbox).
  - Antigravity với CLI `agy` thật hoặc IDE thật (máy hiện chưa cài `agy`).

- **UNSAFE (Từ chối thực hiện / Không an toàn):**
  - Tự động chạy `apply`, `rollback` hoặc `recover` trên profile thật khi chưa có phê duyệt rõ ràng.
  - Tự động kích hoạt auto-merge hoặc merge PR khi chưa có chỉ đạo của chủ dự án.
  - Bỏ qua kiểm tra toàn vẹn hoặc âm thầm xóa khóa `theme` để ép đăng ký thành công.

