# Nhật ký phát triển

Ghi lại những gì đã làm trên máy thật, số liệu và bài học. Ngày: 26/10/2026. Đường dẫn dùng `~` thay cho thư mục người dùng. Số liệu lấy từ các lần chạy thật; chỗ nào chưa tự kiểm chứng sẽ ghi rõ.

## Phase 0: Kiểm kê (chỉ đọc)

- Có: Claude Code và Codex (cài qua npm, chạy native trên Windows), Node 24, git, WSL. Antigravity chạy dạng IDE. Chưa cài: AgentsMesh, Memorix, psmux.
- Bốn kho skills: `~/.claude/skills` (146 skill), `~/.gemini/config/skills` (159), `~/.agents/skills` (186), `~/.codex/skills/.system` (5, runtime nội bộ của Codex).
- Cấu hình nằm ở `~/.claude`, `~/.codex`, `~/.gemini`, `~/.agents`.

## Phase 1: Backup

- Sao lưu 8 file cấu hình và 4 thư mục skills bằng `robocopy` đa luồng vào một thư mục có timestamp ngoài repo.
- Số file nguồn: claude 17.101, codex 49, gemini 21.438, agents 22.453. Backup khớp, trừ `.claude` lệch 6 file do thư mục đang bị ghi trong lúc copy.
- Bài học: `.claude.json` có thể chứa thông tin nhạy cảm, tuyệt đối không đưa backup lên git.

## Phase 2: Audit (báo cáo của script `audit_skills.py`)

- 191 tên skill duy nhất (gồm 2 thư mục rác và 4 skill runtime của Codex). 122 trùng khớp hoàn toàn, 38 bị "lệch" mã băm.
- Trong 38 skill lệch: 31 chỉ khác đường dẫn hardcode theo tên agent, 2 chỉ khác CRLF/LF, 3 là nâng cấp nội dung thật, 2 là thư mục phụ/cache. Chi tiết: [SKILLS_AUDIT_REPORT.md](../SKILLS_AUDIT_REPORT.md) (bản ẩn danh).
- Khoảng 53.500 file là `node_modules` trong vài skill; phần mã/hướng dẫn thật khoảng 3.200 file (~75 MB).
- Lưu ý: các tỷ lệ trên là kết quả của script trên một máy, chưa kiểm chứng độc lập.

## Phase 3: Dựng SSOT

- Chọn nguồn theo thứ tự gemini, agents, claude. Kết quả: 185 thư mục (181 có `SKILL.md`), 2.676 file, 55 MB. 159 từ gemini, 25 từ agents, 1 từ claude.
- Loại: 2 thư mục rác của `.agents` và 4 skill runtime của Codex.
- 4 thư mục không có `SKILL.md` (`common`, `document-skills`, `research-kickoff-workspace`, `synced`) bị công cụ bỏ qua; cần kiểm tra xem có skill khác tham chiếu tới chúng.
- 61 skill chứa đường dẫn thư mục agent; 44 trỏ `.claude/skills`. Chưa sửa vì nhiều đường dẫn là cấu hình riêng của Claude.

## Phát hành repo công khai và vệ sinh dữ liệu

- Lần đầu commit nhầm cả `ssot/skills/`, tài liệu nội bộ và đường dẫn cá nhân. Đã loại khỏi git, ẩn danh báo cáo, viết lại lịch sử thành một commit và force-push.
- Bài học: `.gitignore` không xóa được thứ đã nằm trong lịch sử; quét secret/thông tin cá nhân **trước** commit đầu tiên, kể cả tên nội bộ và email trong metadata.
- Hardening: hai script thành CLI portable, báo cáo chạy thật ghi vào `.local/reports/` (không commit), thêm test dùng dữ liệu giả.

## v0.2: Skill Manager (`omniagent.py`)

Lệnh: `discover`, `plan`, `adopt`, `apply`, `rollback`. Mặc định chỉ đọc; ghi cần `--yes`.

Cơ chế an toàn: từ chối symlink, thư mục chồng lấn, `.codex/.system`; băm SHA-256 trước và sau khi copy; backup và journal giao dịch; khóa chống hai tiến trình (có thu hồi lock khi PID đã chết); rollback chỉ chạy nếu thư mục chưa bị sửa.

Lỗi thật tìm được khi thử với SSOT thật (mà test dữ liệu giả không bắt được):

1. 12 file `.env.example` trong skill bị coi là credentials và làm hỏng toàn bộ `plan`. Đã cho phép `.env.example/.sample/.template`.
2. `__pycache__` và `node_modules` trong thư mục đã cài làm skill bị gán `unsafe` dù nội dung khớp. Đã bỏ qua khi đọc bản đã cài; cập nhật và rollback bị chặn nếu có `node_modules`.

## Chạy thật trên máy (đều có kiểm tra số file và thời điểm sửa đổi trước/sau)

| Bước | Kết quả |
|---|---|
| `adopt` claude | 137 skill khớp được ghi nhận, thư mục skill không đổi |
| `adopt` gemini + agents | 150 + 146 skill, thư mục skill không đổi |
| `adopt` sau khi sửa `__pycache__`/`node_modules` | thêm 12 skill (claude 4, gemini 6, agents 2) |
| `apply` gemini + agents | cài 25 + 1 skill |
| `apply` claude thử 1 skill (cloudflare) | cài đúng +165 file, Claude Code nhận diện skill |
| `apply` claude phần còn lại | cài 35 skill, kiểm tra không còn thư mục tạm |

Trạng thái sau cùng (theo `plan`): claude 177 được quản lý + 4 chưa quản lý (lệch nội dung); gemini 181 được quản lý; agents 149 được quản lý + 32 chưa quản lý (lệch đường dẫn hardcode). Tổng 507 bản skill được quản lý. Mọi giao dịch đều có backup state và rollback được.

## Còn lại và bước tiếp theo

1. **Path Adapter:** biến đường dẫn chuẩn thay cho hardcode, render theo agent, diff, kiểm tra tham chiếu, rollback khi lỗi. Đây là cách xử lý 32 skill của agents và 4 skill của claude.
2. **Kiểm thử tương thích thật** trên Codex và Antigravity (đường dẫn, lệnh gọi tool, MCP, dependency). Hiện mới xác nhận file có mặt và Claude Code đọc được skill.
3. **Rà nguồn và giấy phép** từng skill trước khi cân nhắc công khai bộ skills.
4. **Journal phục hồi** khi máy tắt đột ngột giữa lúc ghi.
5. **Shared Brain** qua MCP (xác định chủ sở hữu từng phần cấu hình để không ghi đè nhau), rồi mới tới điều phối đa agent.
6. Merge PR #1 sau khi review mã và thử trên Windows thật.

## Phân tích drift và thí nghiệm Path Adapter (chỉ đọc, 26/10/2026)

Mục tiêu: biết chính xác các skill `blocked_unmanaged` khác SSOT ở đâu, rồi thử cơ chế template của PR #2 (`path_adapter.py`) trên bản sao. Không sửa SSOT thật, thư mục agent hay `state.json`. Bảng chi tiết nằm ở `.local/reports/drift_analysis.md` (không commit).

### Kết quả phân tích 36 skill lệch

| Kho | Chỉ khác đường dẫn agent | Đường dẫn + từ Claude/Codex | Chỉ khác CRLF/LF | Cần xem lại |
|---|---|---|---|---|
| agents (32) | 26 | 1 | 1 | 4 |
| claude (4) | 0 | 0 | 0 | 4 |

- Nhóm "cần xem lại" khác nội dung thật (SSOT lấy bản mới hơn), có file thừa hoặc thiếu, hoặc `package-lock.json` khác. Không dùng placeholder để giải quyết nhóm này.
- Một số khác biệt nhỏ không phải đường dẫn (ví dụ đổi tên trường, định dạng số). Đây là thay đổi nội dung thật, không phải template.

### Luật thay đường dẫn quan sát được ở bản của agents

| Trong SSOT | Trong bản cài của agents | Biến template |
|---|---|---|
| `.claude/skills/...`, `~/.claude/skills/...` | `.agents/skills/...`, `~/.agents/skills/...` | `SKILLS_DIR` |
| `.claude/<thư mục khác>/...` | `.Codex/<thư mục khác>/...` (viết hoa chữ C) | `AGENT_CONFIG_DIR` |

Cần ít nhất hai biến. Thứ tự áp dụng: `.claude/skills` trước, rồi mới tới `.claude/` còn lại.

### Thí nghiệm trên 3 skill (bản sao trong `.local/experiments/`)

Trong bản sao, đổi đường dẫn thành `{{SKILLS_DIR}}` và `{{AGENT_CONFIG_DIR}}`, rồi dùng `path_adapter.py` xem trước so với thư mục thật.

| Kho | skill-A | skill-B | skill-C |
|---|---|---|---|
| claude | match | match | match |
| agents | match | drift | drift |

- claude khớp 3/3: bản render lại đúng bản đang cài, nên chuyển SSOT sang template không đổi nội dung của claude.
- skill-A khớp ở agents: cơ chế đúng với trường hợp đường dẫn thuần túy.
- skill-B ở agents lệch vì bản cài **chưa được chuyển đổi trọn vẹn**: `SKILL.md` đã đổi sang `.agents/skills`, nhưng một file trong `references/` còn `.claude/skills`. Adapter sẽ sửa được lỗi bỏ sót này.
- skill-C lệch ở một file script (khoảng dòng 431); chưa xác định là đường dẫn hay thay đổi mã.

### Phát hiện cho thiết kế v0.3b

1. **Lỗi `--diff` trên Windows:** in diff chứa ký tự không phải ASCII gây `UnicodeEncodeError` ở console cp1252 và lệnh dừng. Cần ép đầu ra UTF-8 như `audit_skills.py`.
2. **Hash trong `state.json` phải đổi:** `state.json` lưu hash của SSOT chưa render. Nếu SSOT chứa placeholder, các bản đã quản lý sẽ bị coi là cần `update`. v0.3b phải băm bản đã render theo từng agent trước khi nối vào `plan` và `apply`, và có đường nâng cấp state cũ.
3. **CRLF:** `brainstorm` chỉ khác dấu xuống dòng nhưng vẫn bị coi là lệch. Không chuẩn hóa CRLF trong hàm băm chung vì sẽ đổi hash của mọi bản đang quản lý. Xử lý riêng ở bước `adopt`, lưu hash thực của thư mục đích.
4. **Placeholder lạ:** adapter coi mọi chuỗi `{{CHỮ_HOA}}` trong file không khai báo là lỗi `unsafe`. SSOT có 5 file chứa chuỗi kiểu này (thuộc skill tài liệu SEO và nhóm `synced`), nên cần giới hạn kiểm tra vào file được khai báo hoặc cho phép bỏ qua có cấu hình.
5. **Bản cài có thể sai sẵn:** bản của agents có file chưa chuyển đổi (xem skill-B). Khi apply qua adapter, phải xem trước và cần xác nhận riêng, vì nó thay đổi nội dung file mà người dùng đã cài.

### Việc kế tiếp

- Sửa `--diff` (UTF-8) và thêm test.
- Thiết kế render-aware hash trong `plan`/`apply`/state; thử trên bản sao rồi mới nối vào `apply`.
- Xem lại `skill-C` và 4 skill `needs_review` của agents.
- Quyết định cách xử lý CRLF ở `adopt`.

## Template hóa nhóm 26 skill chỉ khác đường dẫn (sandbox, chỉ đọc)

Trong bản sao `.local/experiments/` (không đụng SSOT thật), áp dụng hai luật thay
(`.claude/skills` thành `{{SKILLS_DIR}}`, rồi `.claude/` còn lại thành
`{{AGENT_CONFIG_DIR}}`), sau đó render và so với thư mục thật bằng Path Adapter:

| Kho | match | drift |
|---|---|---|
| claude | 26 | 0 |
| agents | 15 | 11 |

- claude 26/26: bản render đúng bằng bản đang cài, nên việc template hóa không làm đổi nội dung bên claude.
- agents 15/26 khớp từng byte, nên đủ điều kiện `adopt --adapter-manifest` (chỉ ghi state).
- 11 skill còn lại lệch vì **bản cài của agents chuyển đổi chưa nhất quán**: một số file đã đổi sang `.agents/...`/`.Codex/...`, một số file khác trong cùng skill vẫn còn `.claude/...`. Bản render là bản nhất quán, nên đưa 11 skill này về bản render là một `update` làm thay đổi file thật, cần xác nhận riêng, không phải `adopt`.

## Phục hồi giao dịch bị ngắt (v0.3d)

`apply` ghi journal `in_progress` trước khi đổi thư mục; lệnh `recover` (xem trước, `--yes` để thực hiện) đưa về trạng thái trước giao dịch và từ chối nếu có bản sửa tay hay `node_modules`. `apply`/`adopt`/`rollback` bị chặn khi còn giao dịch dở. 7 test mô phỏng crash (cài, thay thế sau khi bản cũ đã bị dời, trước khi thay, bị sửa tay, có `node_modules`, chặn lệnh khác, giao dịch thành công không để lại journal dở). Giai đoạn cuối là hai pha: journal `committing` (kèm đúng các bản ghi state) rồi ghi `state.json` rồi `applied`; `recover` hoàn tất (roll forward) giao dịch `committing` sau khi kiểm tra nội dung, và revert giao dịch `in_progress`. Có thêm test dừng trước và sau khi ghi `state.json`. Chưa kiểm thử với kill tiến trình thật hay mất điện thật.

## PR #13 và Issue #14 Stage A (30/10/2026)

- Theo yêu cầu owner, đã review và merge [PR #13](https://github.com/gonostudy305/omniagent-os/pull/13) thành commit `7e9c913f49f74e772ec11bd6d7da89be29a6c01a`; không thấy lỗi chặn merge. Bộ Windows trước merge: 271 PASS; CI cuối PR và CI `main` sau merge đều xanh trên Windows/Ubuntu. GitGuardian của HEAD cuối PR xanh.
- Tạo nhánh `codex/issue-14-sandbox-e2e` từ commit merge. Harness chuyển profile/cache/temp vào `.local/sandbox`, lọc biến môi trường, giữ SHA từ plan đã duyệt, kiểm tra chặn `theme` và entry trùng tên trước CLI. Mỗi project tạm có Git root độc lập. Đây là cô lập bằng môi trường, không phải sandbox cấp hệ điều hành.
- Lần E2E đầu thất bại vì Claude chọn Git root của repo cha thay vì project tạm. Đồng thời thấy metadata `.codex/config.toml` thật thay đổi; chưa xác định writer hoặc nội dung thay đổi, không đọc hay hoàn tác file thật. Giữ bằng chứng tại `.local/reports/issue-14-stage-a-first-run.json`; không xóa bỏ quan sát này bằng kết quả chạy lại.
- Sau sửa fixture Git root, E2E CLI thật: **40 PASS, 0 FAIL**, exit 0 trong **26.005 giây**, Claude Code **2.1.246**, Codex CLI **0.163.0**. Kiểm chứng plan/apply, entry đúng scope, script gọi registered MCP command, rollback từng byte và entry-only; size/mtime bảy file profile thật được theo dõi không đổi trong lần chạy cuối. Kết quả tại `.local/reports/issue-14-stage-a.json`; metadata không chứng minh mọi file trên máy đều không đổi.
- Model thật gọi `brain_search`/`brain_propose` và Antigravity native/IDE: **NOT_VERIFIED**. Chưa mở Stage B; không đăng ký vào profile thật. Kế hoạch Claude local-scope, giới hạn writer ngoài và phục hồi thủ công `failed-needs-attention` nằm trong [ISSUE_14_STAGE_A.md](ISSUE_14_STAGE_A.md). Báo cáo đó ghi kết quả bộ test cuối và các giới hạn của bằng chứng.

### Review PR #15: explicit subprocess env/cwd (30/10/2026)

- `propose_through` bắt buộc sandbox `env`/`cwd`; cả MCP proposal và Claude/Codex list/get truyền tường minh các giá trị này. Regression test tiêm credential/config path giả vào môi trường cha để chứng minh các subprocess vẫn dùng bản đã lọc.
- Trước sửa: 2 failure, 1 error; sau sửa: 8 test sandbox PASS. Bộ đầy đủ Windows Python 3.13.14: **277 PASS**, 121.530 giây, exit 0; vẫn có ResourceWarning BufferedWriter trong cleanup.
- Native E2E sau sửa: **40 PASS, 0 FAIL**, 23.801 giây, Claude 2.1.246/Codex 0.163.0. Đây là bằng chứng cục bộ, không phải native E2E trên CI. Báo cáo Stage A liên kết CI nhánh tại `7888945` (Windows 275 PASS; Ubuntu 262 PASS + 13 skipped) và sẽ ghi CI revision riêng.
- Raw report giữ ngoài Git; scan báo chuỗi entropy cao trong tên file tạm. Bản redacted chỉ bỏ detail của bốn INFO file listing, scan 0 phát hiện. Không đọc/sửa profile thật để xử lý mtime lịch sử, không mở Stage B; model calls và Antigravity native vẫn NOT_VERIFIED.
