# Competitive strategy (29/10/2026)

> English summary: the Deep Research report (29/10/2026) is right that "a multi-agent OS" is crowded. It is too optimistic about what is left: a quick check of competitor READMEs shows that Helter5/agentbridge already advertises migration with dry-run, backups and rollback, and agentbridgehq/agentbridge already advertises lockfiles, signatures, provenance and a conformance corpus run against real clients. What survives is narrower than the report's thesis: a fail-closed ownership and explanation contract for changing an existing, dirty agent environment. That is a property worth having, not yet a proven product. This page separates verified facts from claims and lists kill criteria.

## 1. Mức bằng chứng

| Ký hiệu | Nghĩa |
|---|---|
| **V** | Đã kiểm tra trực tiếp trong phiên này (API GitHub: repo tồn tại, license, ngày push gần nhất, số sao) |
| **R** | README của đối thủ *quảng cáo*; chưa chạy thử |
| **D** | Chỉ có trong Deep Research; chưa kiểm |
| **E** | Kết luận kỹ thuật của chúng tôi |

Điểm "similarity" và "bão hòa" (94%, 9/10...) trong báo cáo là ước lượng của người viết báo cáo, không phải số đo; không dùng chúng làm căn cứ.

## 2. Đối thủ ưu tiên

Kiểm tra ngày 29/10/2026 qua `gh api repos/...` (V) và quét từ khóa trong README (R).

| Dự án | License (V) | Sao (V) | Push gần nhất (V) | README quảng cáo (R) | Ghi chú (E) |
|---|---|---:|---|---|---|
| [AVIDS2/memorix](https://github.com/AVIDS2/memorix) | Apache-2.0 | 881 | 25/10/2026 | Memory dùng chung, hỗ trợ Codex và Antigravity, dry-run, backup | Đối thủ mạnh nhất về memory; license cho phép làm backend tùy chọn |
| [agentmuxai/agentmux](https://github.com/agentmuxai/agentmux) | Apache-2.0 | 24 | 29/10/2026 | Workspace đa provider, skills, memory, signature | Runtime/UI; không cạnh tranh ở lớp migration |
| [AgentsMesh/AgentsMesh](https://github.com/AgentsMesh/AgentsMesh) | NOASSERTION | 2362 | 23/09/2026 | Fleet, worktree, scheduling (D) | License chưa rõ: **không** tái dùng mã |
| [rexleimo/aios](https://github.com/rexleimo/aios) | MIT | 61 | 27/10/2026 | Control plane runtime, memory (D) | Runtime long-horizon |
| [Podiom/Podiom](https://github.com/Podiom/Podiom) | MIT | 20 | 25/10/2026 | Workspace Claude/Codex (D) | Runtime/UI |
| [Helter5/agentbridge](https://github.com/Helter5/agentbridge) | MIT | 5 | 10/09/2026 | `link-skills` "migrates existing skills into the central hub without data loss", `--dry-run`, backup tự động trước khi ghi, `rollback` theo snapshot, `doctor` (secret, trùng tên, xung đột MCP), `sync-mcp`, `sync-rules` | **Trùng trực tiếp với wedge migration.** Khác biệt (R+E): dùng junction/symlink về một hub thay vì bản copy có theo dõi; xung đột MCP thì "agent xử lý sau thắng" kèm cảnh báo; rollback khôi phục snapshot, chưa thấy cơ chế từ chối khi bản đích bị sửa sau đó hay phục hồi khi tiến trình chết |
| [yiftahb/agsync](https://github.com/yiftahb/agsync) | MIT | 10 | 27/09/2026 | Skills, MCP, lock file, hỗ trợ Codex/Antigravity | Hướng repo-native/package |
| [vicode117/skill-sync](https://github.com/vicode117/skill-sync) | không có | 0 | 29/08/2026 | Đồng bộ skill | Không license: không tái dùng |
| [vercel-labs/skills](https://github.com/vercel-labs/skills) | MIT | 37042 | 29/10/2026 | Package manager / registry skill | Tiêu chuẩn phân phối thực tế; nên tương thích, không cạnh tranh |
| [agentbridgehq/agentbridge](https://github.com/agentbridgehq/agentbridge) | Apache-2.0 | 0 | 10/09/2026 | v0.2.0: lockfile ghim commit/digest, 15 luật quét nội dung (ký tự ẩn, HTML comment...), báo phát hiện *mới so với bản đã duyệt*, chữ ký Sigstore + SLSA, **conformance corpus** chạy trên 4 client gồm Codex | **Trùng trực tiếp với moat provenance và conformance** mà báo cáo đề xuất |
| arXiv 2606.26924 "A Deterministic Control Plane for LLM Coding Agents" | — | — | — | Canonical definitions, SHA-256, lockfile, compile sang 7 IDE (D) | Chưa đọc bản gốc; tiền lệ học thuật cho IR |

Không có repo nào ở trên đã được chạy thử. Số sao chỉ phản ánh mức chú ý, không phản ánh chất lượng.

## 3. Câu hỏi then chốt

> Vì sao một developer hay platform team hợp lý chọn OmniAgent-OS thay vì Vercel Skills + Memorix + AgentMux/AgentsMesh (hoặc + Helter5/agentbridge + agentbridgehq)?

**Câu trả lời trung thực hôm nay: chưa đủ thuyết phục cho người dùng mới.** Tổ hợp đó phủ phân phối skill, memory, runtime và (theo README) cả migration có backup lẫn provenance. Điều OmniAgent-OS có mà chưa thấy ai quảng cáo ở cùng mức (E, dựa trên code và test của chúng ta, R cho phía đối thủ):

1. **Hợp đồng sở hữu fail-closed theo từng mục:** không bao giờ ghi đè thư mục chưa quản lý hoặc đã bị sửa sau khi quản lý; adopt chỉ ghi metadata; cập nhật bản đã adopt cần opt-in riêng. Các công cụ snapshot cho phép *khôi phục sau khi ghi đè*; ta *từ chối ghi đè* ngay từ đầu.
2. **Giao dịch có journal, phục hồi sau khi tiến trình bị kill**, có test kill thật.
3. **Giải thích quyết định có cấu trúc, xác định, không lộ đường dẫn** (`--explain`, nhánh này).
4. **Copy có theo dõi thay vì symlink/junction**, hợp với Windows không có quyền admin và tránh hai agent ghi chung một chỗ.

Những điểm này có giá trị với người đã có môi trường bẩn và sợ hỏng nó. Nhưng chưa có bằng chứng người dùng ngoài chủ dự án thấy cần chúng đủ để đổi công cụ. Bằng chứng có thể đổi kết luận: (a) chạy Helter5/agentbridge trên fixture bẩn của `tests/test_explain.py` và ghi nhận nó có ghi đè bản đã sửa không; (b) 5–10 người dùng thật chạy `plan --explain` trên máy họ và báo số mục bị chặn đúng/sai.

## 4. Luận điểm: giữ, sửa, bỏ

| Luận điểm | Kết luận |
|---|---|
| "Agent OS chạy nhiều agent" | **Bỏ** làm định vị. Thị trường đông, đối thủ có vốn và người dùng |
| "Đồng bộ skill giữa các agent" | **Bỏ** làm điểm khác biệt; vẫn là tính năng nền |
| "Lifecycle/governance plane, Terraform cho agent environment" (báo cáo) | **Sửa hẹp lại.** Provenance/lockfile/conformance đã có người làm (agentbridgehq); migration có backup cũng vậy (Helter5). Giữ phần *thay đổi an toàn môi trường đang có*: ownership fail-closed, giải thích, journal, xác minh trung thực |
| "Second brain dùng chung" | **Giữ có điều kiện** (PR #9): memory theo dự án, agent chỉ đề xuất. Không tranh với Memorix về tìm kiếm/indexing |
| "Điều phối" | **Hoãn**. Nếu cần, chỉ làm lớp chính sách quanh runtime có sẵn |

Định vị đã chọn (29/10/2026, dùng trong README và PROJECT_CONTEXT): *"OmniAgent-OS is a local-first, fail-closed skill lifecycle manager for AI coding assistants. It explains planned changes, protects unmanaged or modified files, and supports verified rollback and recovery."* Câu này chỉ nói những gì đã có, không tự nhận là hệ điều hành đa agent hay nền tảng conformance.

Báo cáo Deep Research đầy đủ không đưa vào repo công khai; trang này và hai trang đi kèm chỉ giữ các kết luận đã gắn mức bằng chứng.

## 5. Quyết định build / integrate

| Hệ thống con | Quyết định | Lý do |
|---|---|---|
| Ownership, plan, explain, journal, rollback | **Build** (đã có) | Đây là phần duy nhất có khác biệt kiểm chứng được |
| Phân phối / registry skill | **Integrate**: đọc thư mục do `npx skills` cài như một nguồn, không làm registry | Vercel là chuẩn thực tế (37k sao) |
| Provenance, lockfile, quét nội dung | **Adapter tùy chọn / hoãn**: đánh giá đọc lockfile và kết quả quét của agentbridgehq (Apache-2.0) thay vì tự viết scanner | Tránh làm lại; chỉ thêm vào inventory những trường biết chắc, còn lại `unknown` |
| Conformance L2/L3 | **Hoãn**, xem corpus của agentbridgehq trước | Cần agent thật và sandbox; chạy script skill không rõ nguồn là rủi ro |
| IR chuẩn | **Hoãn** tới khi có ít nhất 2 trường hợp tương thích thật mà template không giải được | Tránh schema lớn chưa có người dùng |
| Memory | **Giữ lõi mỏng của PR #9**, thêm backend Memorix tùy chọn khi có nhu cầu | Mô hình xác nhận của #9 khác Memorix; tự viết ~700 dòng là chấp nhận được |
| Orchestration runtime | **Từ chối** trong 6 tháng tới | AgentMux, AgentsMesh, Podiom, Conductor... đã làm |
| Local-only | Mọi state, journal, memory, báo cáo | Đúng nguyên tắc 5 trong PROJECT_CONTEXT |
| Chính sách doanh nghiệp | Chỉ mô tả, không hứa | Chưa có người dùng doanh nghiệp nào để kiểm chứng |

## 6. Tiêu chí dừng (kill criteria)

Thu hẹp hoặc dừng dự án nếu một trong các điều sau đúng:

1. Chạy Helter5/agentbridge (hoặc công cụ tương đương) trên fixture bẩn chuẩn cho thấy nó **cũng** từ chối ghi đè bản đã sửa và bản chưa quản lý, với độ phức tạp thấp hơn.
2. Sau khi mời ít nhất 10 người dùng nhiều agent chạy `plan --explain` chỉ-đọc, dưới 3 người có từ 1 mục `blocked` thật trên máy họ (tức không có đau thật về migration).
3. Claude Code, Codex và Gemini hội tụ về một thư mục/định dạng skill chung trong 6 tháng tới, làm adapter mất giá trị.
4. Sau 2 tháng không có ai ngoài chủ dự án dùng lại công cụ lần thứ hai.
