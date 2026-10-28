# OmniAgent-OS: bối cảnh, lý do và định hướng

> English summary: OmniAgent-OS exists because several AI coding agents on one machine (Claude Code, Codex, Antigravity) each keep their own skills and their own "second brain". The long-term goal is a local-first "Agent OS": shared skills/config, shared verified memory, and safe coordination between agents. This file records why, what was decided, and what is deliberately out of scope. Day-to-day progress is in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md); commands are in [USAGE.md](USAGE.md).

## 1. Lý do ban đầu (đừng quên)

Trên cùng một máy Windows cài nhiều agent: **Claude Code, Codex, Antigravity (Gemini)**. Mỗi agent dùng **một bộ skill riêng** và **một "second brain" riêng**. Hệ quả thực tế:

- Cài hoặc sửa một skill ở agent này thì agent kia không có, hoặc có bản khác.
- Kiến thức tích lũy (quyết định kiến trúc, bài học, lỗi đặc thù của dự án) bị nhốt trong từng agent, không truyền sang agent khác.
- Cùng một skill nhân bản ra 3-4 nơi, trôi lệch nội dung theo thời gian, tốn đĩa (audit thực tế: hơn 60.000 file, trong đó phần lớn là `node_modules` nằm trong vài skill).
- Không có cách nào phối hợp các agent: giao việc, nhận kết quả, review chéo.

Mục tiêu dài hạn của người khởi xướng: **một Agent OS chạy local**, gồm ba lớp:

1. **Skills / rules / MCP dùng chung** (một nguồn chuẩn, mỗi agent nhận bản phù hợp).
2. **Second brain dùng chung** (bộ nhớ xuyên agent, có nguồn gốc, có kiểm chứng).
3. **Điều phối nhiều agent** (giao việc, review, cô lập bằng git worktree).

Mọi quyết định thiết kế bên dưới phải quay về câu hỏi: *nó có giúp ba agent dùng chung năng lực và tri thức mà không làm hỏng môi trường đang chạy không?*

## 2. Hiện trạng hệ sinh thái (kiểm tra 26/10/2026, số sao GitHub chỉ để tham khảo)

Chưa có dự án nào đủ chín để giải quyết cả ba lớp. Mỗi dự án chỉ giải một góc:

| Mảng | Dự án tham khảo | Làm được gì | Khoảng trống |
|---|---|---|---|
| Quản lý skills | Vercel Skills (`npx skills`) | Tìm, cài, cập nhật skill cho nhiều agent | Chỉ là package manager; không xử lý drift, đường dẫn hardcode, dependency |
| Đồng bộ cấu hình | AgentsMesh (config sync) | Một nguồn rules/skills/MCP, sinh cấu hình từng agent, kiểm tra lệch | Còn nhỏ; ghi file sinh ra cần kiểm soát |
| Second brain | Memorix | Memory dùng chung qua MCP | Chỉ phần bộ nhớ; không quản lý skills |
| Điều phối | agent-mux, AgentMux, Agetor | Giao việc giữa các engine | Alpha/experimental |

Định vị của OmniAgent-OS: **bộ điều khiển local-first cho vòng đời agent**: bắt đầu từ việc *hiểu, dọn và hợp nhất an toàn* cấu hình hiện có (audit, adopt, plan, apply, rollback), rồi mới mở sang memory và điều phối. Không cố thay thế các dự án trên; có thể dùng chúng làm thành phần.

## 3. Nguyên tắc thiết kế

1. **An toàn trước, tiện lợi sau.** Mặc định chỉ đọc. Ghi file cần `--yes`. Không bao giờ ghi đè skill chưa được quản lý hoặc đã bị sửa tay.
2. **Một nguồn chuẩn (SSOT), nhiều bản copy.** Dùng *copy có theo dõi*, không dùng symlink: Windows không bật symlink cho người dùng thường, và symlink làm agent khác nhau cùng ghi vào một chỗ.
3. **Có backup, có journal, có rollback** cho mọi thao tác ghi. Rollback từ chối nếu thư mục đã bị sửa hoặc chứa `node_modules`.
4. **Mỗi phần cấu hình chỉ có một chủ sở hữu.** Ví dụ: skills thuộc OmniAgent-OS; MCP/plugin của Memorix thuộc Memorix; credentials và session của từng agent giữ riêng, không đồng bộ.
5. **Không đưa dữ liệu riêng tư lên repo công khai.** `ssot/skills/`, báo cáo chạy thật và state nằm ngoài git (`.gitignore`), vì chứa tài liệu nội bộ và skill bên thứ ba chưa rà giấy phép.
6. **Kiểm chứng bằng chạy thật.** Một báo cáo "đã đạt" chỉ có giá trị khi có lệnh kiểm tra và số liệu đi kèm.

## 4. Kiến trúc dữ liệu

| Dữ liệu | Nguồn chuẩn | Ghi chú |
|---|---|---|
| Skills dùng chung | `ssot/skills/` (local, không commit) | Chọn bản tốt nhất cho skill lệch phiên bản |
| Trạng thái quản lý, backup, journal | `.local/manager/` (không commit) | `state.json`, `transactions/`, `backups/` |
| Skill runtime riêng của Codex | `~/.codex/skills/.system` | Không đụng tới |
| Credentials, session của agent | Kho riêng từng agent | Không đồng bộ |
| Bộ nhớ dự án (kế hoạch) | Lớp memory dùng chung qua MCP | Chưa triển khai |
| Task state, thay đổi mã | Git branch / worktree | Mỗi worker một worktree |

## 5. Các quyết định đã chốt và lý do

| Quyết định | Lý do |
|---|---|
| Triển khai theo từng lớp, giữ nguyên agent hiện có | Không cần WSL2/Docker; giảm rủi ro hỏng môi trường |
| Audit trước khi chạm vào cấu hình | 38 skill "lệch" hóa ra phần lớn chỉ khác đường dẫn hardcode hoặc dấu xuống dòng |
| Loại `node_modules` khỏi SSOT | Dependency cài lại được; không nhân bản giữa các agent. Không gọi là "rác" vì có skill thực sự cần |
| Copy có theo dõi thay vì symlink | Xem nguyên tắc 2 ở mục 3 |
| `adopt` chỉ ghi nhận bản khớp 100% | Biến trạng thái "chưa quản lý" thành "đã quản lý" mà không ghi đè |
| Bỏ qua `__pycache__`/`node_modules` khi đọc bản đã cài | Đây là artifact tự sinh; nhưng cập nhật và rollback bị chặn (`blocked_vendor`) nếu có `node_modules` để không xóa nhầm |
| Cho phép `.env.example/.sample/.template` | Là file mẫu; vẫn chặn `.env`, `.env.*` thật, `.pem`, `.key` |
| Repo công khai chỉ chứa công cụ, tài liệu, báo cáo đã ẩn danh | Skill nội bộ và bên thứ ba cần rà nguồn/giấy phép trước |

## 6. Ngoài phạm vi hiện tại (cố ý)

- Đồng bộ credentials, token, session của agent.
- Tự sửa đường dẫn hardcode hàng loạt trong skill (cần Path Adapter, xem lộ trình).
- Shared memory và điều phối đa agent (chưa bắt đầu).
- Bảo đảm skill chạy đúng trên từng agent: mới kiểm tra file có mặt và Claude Code nhận diện, chưa chạy thử trên Codex/Antigravity.

## 7. Lộ trình

| Mốc | Nội dung | Trạng thái |
|---|---|---|
| v0.1 | Audit, SSOT, script portable, test | Xong |
| v0.2 | Skill Manager: discover, plan, adopt, apply, rollback | Xong (tag `v0.2.0`) |
| v0.3 | Path Adapter: biến đường dẫn chuẩn, render theo agent, diff, kiểm tra tham chiếu | Xong trong `v0.3.0-beta.1` |
| v0.3 | Giải quyết drift cho các skill `blocked_unmanaged` | Một phần: 15 skill `agents` khớp bản render nhưng chưa adopt vào state chính; 11 skill lệch chưa xử lý |
| v0.4 | Shared Brain qua MCP, có nguồn gốc và quy tắc xung đột | Chưa làm |
| v0.5 | Điều phối: handoff, worktree, kiểm thử tương thích đa agent | Chưa làm |
| v1.0 | Windows Local Agent OS: cài đặt, hydration dependency, kiểm tra sức khỏe | Chưa làm |

## 8. Rủi ro đã biết

- Rollback và apply đã có journal và `recover`, đã kiểm bằng test kill tiến trình thật; chưa thử mất điện thật.
- Số liệu audit lớn (60.000 file, 81,6% drift là đường dẫn) là quan sát trên một máy; cần phương pháp tái lập trước khi trích dẫn rộng rãi.
- 181/181 skill được đồng bộ file không có nghĩa là 100% chạy được trên mọi agent: cần kiểm thử đường dẫn, lệnh gọi tool, MCP và dependency.
- Metadata commit công khai có email tác giả; lịch sử đã viết lại nhưng commit cũ có thể còn truy cập được qua hash trực tiếp.
