# Hướng dẫn dùng thử beta (v0.3.0-beta.1)

> English summary: a controlled beta on Windows for the v0.3 Skill Manager and Path Adapter. Scope is deliberately small (three pilot skills, state-only adoption, no content changes). This page lists what is verified, what is not, the safe trial session, and how to recover.

## Phạm vi beta

Dùng được:
- Quản lý skills v0.2: `discover`, `plan`, `adopt`, `apply`, `rollback`.
- v0.3: Path Adapter chỉ-đọc, `plan --adapter-manifest`, `adopt --adapter-manifest` (chỉ ghi state), `recover` cho giao dịch bị ngắt.

Chưa dùng ở beta:
- Tự động cập nhật các skill đang lệch hoặc chuyển đổi dở (giữ nguyên, cần duyệt từng diff).
- Adopt hàng loạt vào state chính; shared memory; điều phối nhiều agent.
- `apply --adapter-manifest` trên thư mục agent thật (thử nghiệm, chỉ một skill, một target, cần `--allow-rendered-apply`).

## Tiêu chí chốt beta và bằng chứng

| # | Tiêu chí | Trạng thái | Bằng chứng / việc còn lại |
|---|---|---|---|
| 1 | PR #2 có CI xanh và đã được review | CI xanh (Windows, Ubuntu, GitGuardian) trên cả chuỗi PR #2, #4, #5. Review độc lập là review tự động bằng đọc code (đăng trên PR #2), không phải review của người | Chủ dự án duyệt và merge |
| 2 | Test kill tiến trình thật trên thư mục giả | Đạt | `tests/test_kill.py`: 4 kịch bản (kill khi cài, sau khi bản cũ bị dời, trước và sau khi ghi `state.json`), tiến trình con chết bằng `os._exit`, rồi `recover` |
| 3 | Ba skill pilot hoạt động trong agent thật | Đạt, do chủ dự án tự xác nhận (27/10/2026) | Chủ dự án báo cả 3 skill chạy tốt trong agent thật. Chưa có log hay ghi lại agent nào đã dùng; hash khớp một mình không chứng minh skill chạy |
| 4 | Adopt và rollback không đổi byte nào | Đạt | `tests/test_byte_identity.py` (chu trình đủ bước trên fixture, kể cả thư mục rỗng) và chạy thật trên 3 skill: băm SHA-256 trước và sau, cả thư mục `~/.agents/skills` giữ nguyên số file và thời điểm sửa đổi |
| 5 | Tài liệu, release notes, hướng dẫn khôi phục | Đạt | Trang này, [RELEASE_NOTES_v0.3.0-beta.1.md](RELEASE_NOTES_v0.3.0-beta.1.md), mục "Phục hồi" trong [USAGE.md](USAGE.md) |

## Buổi dùng thử an toàn (chỉ ghi state thử, không sửa nội dung skill nào)

Điều kiện: đã backup thư mục skill và `.local/manager/`; dùng thư mục state **riêng** cho buổi thử.

```powershell
# Chạy từ thư mục gốc của repo. Hai đường dẫn dưới đây là bản thử nghiệm template hóa
# đã tạo trong .local\ (không được đưa lên git); máy khác phải tự tạo bản của mình.
$Src   = ".local\experiments\templated26\source"
$Man   = ".local\experiments\templated26\adapters.json"
$State = ".local\trial-state"          # state RIÊNG cho buổi thử, không dùng state mặc định
$Agents = "$HOME\.agents\skills"
$Skills = @("--skill","threejs","--skill","ask","--skill","preview")

# 0. Test
python -I -m unittest discover -s tests

# 1. Xem trước (chỉ đọc): ba skill nên là eligible_rendered
python omniagent.py adopt --source $Src --state-dir $State --target "agents=$Agents" --adapter-manifest $Man @Skills

# 2. Ghi nhận vào state thử (không sửa file skill)
python omniagent.py adopt --source $Src --state-dir $State --target "agents=$Agents" --adapter-manifest $Man @Skills --yes
# Ghi lại mã giao dịch in ra ("Adoption transaction: <ID>")

# 3. Kiểm tra: plan có manifest -> unchanged; plan thường -> blocked_rendered
python omniagent.py plan --source $Src --state-dir $State --target "agents=$Agents" --adapter-manifest $Man @Skills
python omniagent.py plan --source $Src --state-dir $State --target "agents=$Agents" @Skills

# 4. Hoàn tác ghi nhận (thay <ID> bằng mã ở bước 2)
python omniagent.py rollback --state-dir $State --transaction <ID> --yes
```

`$Src` là bản sao SSOT đã chuyển đường dẫn thành placeholder (ví dụ trong `.local/experiments/`); **không** dùng SSOT thật làm nguồn khi chưa template hóa, vì state chính lưu hash của bản chưa render.

Bước cuối cần làm tay (tiêu chí 3): mở Codex (hoặc Claude) và gọi thử từng skill pilot để xác nhận chúng vẫn chạy.

Kiểm tra chỉ-đọc đã làm trước (27/10/2026, không thay cho việc gọi skill thật): với `threejs`, `ask`, `preview` trong `~/.agents/skills` và `~/.claude/skills`, `SKILL.md` có frontmatter hợp lệ (`name`, `description`), không còn placeholder `{{...}}`, mọi file tham chiếu của `threejs` tồn tại. `preview` không chứa `scripts/server.cjs` của chính nó mà gọi script của skill `markdown-novel-viewer`, nên skill đó phải được cài; beta không quản lý `markdown-novel-viewer`. Bộ skill này chưa có trong `~/.gemini/skills`.

Danh sách thử tay, đánh dấu từng dòng:
- [ ] `threejs`: yêu cầu agent dựng một cảnh Three.js tối thiểu, agent nạp skill và dùng đúng file tham chiếu.
- [ ] `ask`: đặt một câu hỏi kiến trúc ngắn, agent nạp skill và trả lời theo định dạng của skill.
- [ ] `preview`: yêu cầu xem trước một file Markdown, server của `markdown-novel-viewer` khởi động và mở được trang.
- [ ] Sau cả ba lần thử, `plan --adapter-manifest` vẫn báo `unchanged` (agent không sửa file skill).

## Khi có sự cố

1. Công cụ báo "An interrupted transaction exists": chạy `python omniagent.py recover --state-dir .local\trial-state` (chỉ xem trước; nhớ chỉ rõ state đang dùng, nếu không lệnh sẽ nhìn vào state mặc định). Kiểm tra kết quả và bản sao lưu, rồi mới thêm `--yes`. Xem [USAGE.md](USAGE.md#phục-hồi-giao-dịch-bị-ngắt-giữa-chừng).
2. `recover` từ chối: có skill bị sửa tay hoặc có `node_modules`. Không có gì bị thay đổi; xử lý tay rồi chạy lại.
3. Muốn quay về hoàn toàn: khôi phục từ backup trước buổi thử và xóa thư mục state thử.
4. Báo lỗi: kèm lệnh đã chạy và đầu ra, **không** đính kèm nội dung skill hay state (có thể chứa đường dẫn và tên riêng).

## Giới hạn đã biết

- Kiểm thử kill dùng `os._exit` trên fixture; chưa thử mất điện thật. Journal và state được `fsync` trước khi đổi tên file, nhưng chưa thử mất điện thật.
- `rollback` ghi journal `rolling_back`; nếu bị ngắt giữa chừng, `recover` hoàn tất. Đã kiểm bằng test kill tiến trình thật, chưa kiểm bằng mất điện thật.
- Junction của Windows được nhận diện như symlink và bị từ chối trong skill.
- `recover` có thể từ chối nếu chính nó bị ngắt giữa lúc khôi phục (bản sao lưu vẫn nguyên).
- Review độc lập (đọc code, không chạy): đã sửa các điểm: ghi nhận hoàn tất trước khi dọn thư mục tạm, bản sao lưu dở không chặn `recover`, `fsync`, journal cho `rollback`, junction, tên thư mục không phân biệt hoa thường. Còn lại: `recover` bị ngắt giữa lúc khôi phục có thể cần xử lý tay; danh sách tên runtime vẫn bỏ qua mọi `*.pyc` và nội dung `__pycache__`.
- Số liệu audit (số file, tỷ lệ drift) quan sát trên một máy.
- 11 skill của bản cài agents chuyển đổi dở: cập nhật về bản render sẽ đổi file thật, chưa được duyệt.
- Chưa kiểm tra tương thích thực thi trên Antigravity.
