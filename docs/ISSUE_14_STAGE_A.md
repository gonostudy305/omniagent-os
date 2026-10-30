# Issue #14 — Stage A: disposable-profile E2E

Ngày thực hiện: 30/10/2026 (Asia/Saigon). Phạm vi: Windows, profile tạm cho Claude Code và Codex; không đăng nhập hoặc sao chép credential thật. Chuyển biến môi trường không phải sandbox cấp hệ điều hành.

## Quyền thực hiện và mốc mã nguồn

Owner yêu cầu “Làm cả 3 đi”: review PR #13, merge PR #13 và bắt đầu Stage A của Issue #14. Yêu cầu này không mở cổng Stage B hay đăng ký Codex/Antigravity vào profile thật.

- PR #13 đã merge lúc 13:51:57, commit `7e9c913f49f74e772ec11bd6d7da89be29a6c01a`.
- HEAD được review và kiểm thử trước merge: `25b236c43cec09d965cc6802b035f1fc6c4a65d4`.
- Nhánh Stage A: `codex/issue-14-sandbox-e2e`, tạo từ commit merge trên. Báo cáo này đi cùng thay đổi chưa commit của nhánh; không gán kết quả harness mới cho SHA PR #13 nguyên bản.
- Review không thấy lỗi chặn merge. Watcher chỉ chẩn đoán; khóa chỉ điều phối writer dùng cùng cơ chế. Writer ngoài vẫn có thể ghi giữa hash check và replace.
- Bộ test trước merge: `python -m unittest discover -s tests -p 'test_*.py'`, Windows Python 3.13.14: **271 PASS**, 173.592 giây, exit 0. Có `ResourceWarning` về BufferedWriter chưa đóng trong cleanup.
- CI cuối PR #13: Python 3.11 Windows và Ubuntu cùng GitGuardian đều SUCCESS, kiểm tra ngay trước merge. [CI của HEAD cuối](https://github.com/gonostudy305/omniagent-os/actions/runs/40859504075). CI này không kiểm tra các thay đổi Stage A chưa push.
- [CI trên `main` sau merge](https://github.com/gonostudy305/omniagent-os/actions/runs/41016599042): Windows và Ubuntu SUCCESS tại `7e9c913`.

## Cách chạy và giới hạn bằng chứng

```powershell
python -I scripts/sandbox_register_e2e.py
python -m unittest discover -s tests -p 'test_*.py'
```

Harness dùng CLI được tìm trên PATH, chạy `--version` trong profile tạm, rồi gọi API Python `plan`/`apply`/`rollback`. Hàm `apply` gọi CLI thật theo lệnh plan: Claude `mcp add --scope local omniagent-brain -- <python> <runtime> ...`; Codex `mcp add omniagent-brain -- <python> <runtime> ...` trong `CODEX_HOME` tạm. Không bỏ cổng xác nhận của CLI tương tác trên profile thật; harness dùng API chỉ cho fixture dùng một lần.

Các thư mục nằm dưới `.local/sandbox/`, được dọn sau mỗi kịch bản. Mỗi project có Git repo độc lập, kiểm tra root bằng `git rev-parse --show-toplevel`; Git global/system config và template cũng được cô lập. Biến home, config, XDG, cache và temp được chuyển vào đó; chỉ kế thừa biến OS/runtime cần thiết. Không kế thừa token, provider switches hay startup injection từ môi trường cha. CLI cài sẵn và runtime vẫn được đọc từ máy.

Đối với profile thật, harness chỉ so **kích thước và mtime** của danh sách file theo dõi và sự tồn tại thư mục khóa. Không mở nội dung credential, không hash credential. Phép so metadata không chứng minh toàn bộ filesystem không thay đổi, cũng không chứng minh writer ngoài không hoạt động.

## Kết quả và ma trận kiểm chứng

Native E2E cuối: **exit 0, 26.005 giây**, stderr rỗng; **40 PASS, 0 FAIL, 6 INFO, 3 NOT_VERIFIED**. Claude Code **2.1.246**, Codex CLI **0.163.0**. Dữ liệu chi tiết lưu cục bộ tại `.local/reports/issue-14-stage-a.json`.

| Trường hợp | Claude Code | Codex |
|---|---|---|
| Không có config: plan → apply với SHA đã duyệt → CLI thấy entry | PASS (`mcp list`, Connected) | PASS (`mcp get`) |
| Config có thiết lập và entry khác: giữ nguyên sau apply | PASS | PASS |
| Gọi trực tiếp registered command, đề xuất note đúng project/source_agent/inbox | PASS (script gọi MCP `brain_propose`) | PASS (script gọi MCP `brain_propose`) |
| Rollback byte-for-byte về trạng thái trước apply | PASS | PASS |
| Rollback entry-only từ chối entry bị sửa, gỡ entry nguyên vẹn và giữ settings | PASS | PASS |
| Entry `omniagent-brain` có sẵn: plan/apply từ chối trước CLI | PASS | PASS |
| `theme`: plan/apply chặn trước CLI, config nguyên byte, không tạo backup | PASS | Không áp dụng |
| Hash lệch sau plan: từ chối trước CLI | Test hồi quy với fixture; không native race | Chưa chạy riêng với CLI thật |
| Model thật gọi `brain_search` / `brain_propose` qua entry | NOT_VERIFIED | NOT_VERIFIED |
| Writer đồng thời, writer mồ côi/không rõ trạng thái | Test synthetic của bộ hồi quy | Test synthetic của bộ hồi quy |
| Writer ngoài bỏ qua khóa, mọi lịch xen kẽ có thể xảy ra | NOT_VERIFIED; không có bảo đảm tuyệt đối | NOT_VERIFIED; không có bảo đảm tuyệt đối |

Antigravity native/IDE: **NOT_VERIFIED**, chỉ có fake-CLI tests. Các dòng `NOT_VERIFIED` trong bảng diễn giải phạm vi rộng hơn ba dòng máy đếm trong JSON.

Ở lần chạy cuối, size/mtime của cả bảy file profile thật được theo dõi không đổi; trạng thái tồn tại thư mục khóa thật không đổi. Đây chỉ là bằng chứng metadata trong lần chạy này.

Sau exact rollback, CLI có thể để lại log/backup/init files trong **profile tạm**. Entry-only giữ thiết lập CLI đã thêm, không đòi toàn bộ file trở về byte gốc. JSON liệt kê file thừa/đổi theo từng scenario; TemporaryDirectory dọn profile sau đó. Không áp dụng kết luận này cho thư mục profile thật.

### Lần chạy đầu thất bại và sửa fixture

Lần đầu sau khi chuyển thư mục tạm vào workspace: **exit 1, 24.726 giây**, 21 PASS, 3 FAIL, 4 INFO, 3 NOT_VERIFIED. Hai scenario Claude ghi entry vào scope Git root của repo cha thay vì project tạm; công cụ phát hiện scope không khớp và dừng với `failed-needs-attention` trong profile tạm. Fixture nay khởi tạo Git repo độc lập và xác minh root. Đây là lỗi fixture được phát hiện khi chạy thật, không được tính là PASS.

Lần đầu còn thấy metadata `.codex/config.toml` thật thay đổi. **Chưa xác định writer hay nội dung thay đổi**, không đọc hoặc hoàn tác file thật. Lần chạy lại giữ metadata không đổi không xóa bỏ quan sát này và không chứng minh nguồn gây thay đổi lần đầu. Bằng chứng đầu được giữ nguyên tại `.local/reports/issue-14-stage-a-first-run.json`.

Kiểm thử hồi quy: bộ đầy đủ sau cập nhật harness ban đầu **274 PASS**, 172.362 giây, exit 0. Sau sửa Git root fixture, nhóm test sandbox **6 PASS**, 0.483 giây. Bộ đầy đủ trên code cuối: **275 PASS, 0 fail, 0 skip**, 120.862 giây, exit 0 trên Windows Python 3.13.14. Log: `.local/reports/issue-14-tests.log`. Vẫn có `ResourceWarning` về BufferedWriter chưa đóng trong cleanup; không làm test fail. Không đồng nhất kết quả này với 271 test của PR #13; CI Linux của thay đổi Stage A chưa chạy vì nhánh chưa commit/push.

## Kế hoạch Stage B — chỉ đọc, chưa được phép chạy apply

Đề xuất chỉ cho Claude Code, dự án `omniagent-os` tại checkout hiện tại, scope `local`. File đích khi không dùng `CLAUDE_CONFIG_DIR` là `~/.claude.json`; nếu có override, phải xác nhận lại đường dẫn chính xác trước phê duyệt. Entry: `projects[<đường dẫn tuyệt đối dự án>].mcpServers.omniagent-brain`. Không tạo `.mcp.json` của project.

Các lệnh dưới là bản kế hoạch, chưa thực thi trên profile thật:

```powershell
$pilotProject = (Get-Location).Path
python mcp_register.py plan --agent claude --scope local --project omniagent-os --project-dir "$pilotProject"
# Sau phê duyệt riêng, dùng đúng original_sha256 và command từ plan:
python mcp_register.py apply --agent claude --scope local --project omniagent-os --project-dir "$pilotProject" --yes --expect-sha '<original_sha256 từ plan>'
# Dùng đúng backup_dir mà apply trả về:
python mcp_register.py rollback --backup-dir '<backup_dir>'
python mcp_register.py rollback --backup-dir '<backup_dir>' --entry-only
```

Lệnh native chính xác, gồm Python executable và runtime theo hash, do `plan.command` cung cấp; không đoán runtime trước. Mặc định backup ở `.local/backups/mcp-<UTC timestamp>-<random>-claude/config.bak`, journal cùng thư mục; runtime ở `.local/runtime/brain-<hash>.py`. Backup có thể chứa credential, phải giữ cục bộ và không đưa vào log/commit.

Dừng khi có `theme`, entry trùng tên, plan có vấn đề, hash lệch, khóa bận, writer chưa xác định, thay đổi ngoài MCP, watcher lỗi hoặc handshake lỗi. Không xóa `theme` để vượt chặn. Trước Stage B, owner cần duyệt dự án, file đích chính xác, lệnh từ plan và thủ tục backup/rollback. Không có phê duyệt này trong Stage A.

## Phục hồi thủ công khi `failed-needs-attention`

1. Dừng mọi lần apply/recover/rollback tiếp theo. Giữ nguyên file hiện tại, `config.bak`, journal và owner record; không force restore hoặc xóa khóa.
2. Kiểm tra writer trong journal. Nếu còn sống hoặc không xác định, owner xác minh và cho nó dừng bình thường trước; không kill theo PID suy đoán.
3. Khi writer đã dừng và không còn ứng dụng ghi cấu hình, tạo thêm bản sao file hiện tại vào nơi riêng tư. So sánh cục bộ với backup, không đưa nội dung lên chat hoặc log.
4. Nếu cần gỡ entry, xác minh fingerprint còn đúng entry đã đăng ký. `--entry-only` vẫn gọi CLI và có thể đổi thiết lập ngoài; chỉ chạy sau kiểm tra và phê duyệt cụ thể. Nếu entry đã sửa, để nguyên cho owner quyết định.
5. Owner đối chiếu và hợp nhất riêng những thiết lập cần khôi phục, giữ sửa đổi mới của người dùng. Không tự động thay toàn file bằng backup. Kiểm tra JSON/TOML, các entry khác và thiết lập ngoài MCP; giữ bằng chứng nếu còn mơ hồ.
6. Owner record chưa xác định không được xóa tùy tiện. Chỉ xem xét recovery sau khi rõ trạng thái writer và bản sao an toàn; không đổi journal thủ công để ép `apply` chạy.
