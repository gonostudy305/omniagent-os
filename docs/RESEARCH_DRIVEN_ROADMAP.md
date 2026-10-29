# Research-driven roadmap (29/10/2026)

> English summary: a ranked backlog built from the current code ([CODEBASE_REALITY_CHECK.md](CODEBASE_REALITY_CHECK.md)) and the competitor check ([COMPETITIVE_STRATEGY.md](COMPETITIVE_STRATEGY.md)), not from version numbers in older docs. NOW is about evidence and correctness on what exists; NEXT is a minimal provenance inventory and a real-agent check; LATER is everything that needs users we do not have yet. A STOP list names what not to build.

## 1. Cách chấm điểm

| Tiêu chí | Trọng số | Ghi chú |
|---|---:|---|
| Đau thật và mức nghiêm trọng | 20% | Hạ từ 25%: mới có một máy làm bằng chứng |
| Khác biệt so với giải pháp có sẵn | 25% | |
| Tăng an toàn và độ tin cậy | 20% | |
| Tận dụng code hiện có | 15% | |
| Thời gian tới kết quả kiểm chứng được | 15% | Tăng từ 10%: dự án cần bằng chứng nhanh hơn tính năng |
| Chi phí bảo trì | 5% | |

Điểm 1–5 mỗi tiêu chí, là ước lượng của người viết, không phải số đo.

## 2. Backlog xếp hạng

| # | Hạng mục | Người dùng / vấn đề | Đối thủ đã làm | Điểm | Tiêu chí chấp nhận |
|---|---|---|---|---:|---|
| 1 | **Merge nhánh này** (`--explain`, CI đủ test, sửa `managed_elsewhere`) | Người có môi trường bẩn không hiểu vì sao bị chặn | Helter5 có `doctor` nhưng không giải thích theo từng mục (R) | 4.4 | CI xanh trên Ubuntu và Windows với toàn bộ test; `plan --explain --json` giống hệt giữa 2 lần chạy |
| 2 | **Merge PR #8 sau khi rebase lên CI mới** | Người thử beta trên Windows | — | 4.1 | `test_smoke_script.py` chạy thật trong job Windows (hiện chưa từng chạy trong CI) |
| 3 | **So sánh trực tiếp với Helter5/agentbridge** trên fixture bẩn của `tests/test_explain.py` | Kiểm tra tiêu chí dừng số 1 | Chính là đối thủ | 4.0 | Báo cáo trong `.local/` rồi tóm tắt ẩn danh: với mỗi trạng thái bẩn, công cụ nào ghi đè, công cụ nào chặn |
| 4 | **Thử `plan --explain` chỉ-đọc với 5–10 người dùng nhiều agent** | Kiểm tra tiêu chí dừng số 2 | — | 3.9 | Mỗi người gửi `summary` JSON (không tên skill). Đo: % mục bị chặn mà người dùng đồng ý là đúng |
| 5 | **Inventory provenance chỉ-đọc** (`inventory --json`): tên, target, hash, `origin` (installed/adopted/unmanaged), transaction, các trường chưa biết để `unknown` | Platform team muốn biết cái gì đang cài và từ đâu | agentbridgehq có lockfile (R) | 3.6 | Không đường dẫn máy, không nội dung; `unknown` không bao giờ thành `safe`; test đối kháng với file giống credential |
| 6 | **Test `recover` bị kill khi đang recover**; kiểm đường dẫn dài Windows | An toàn | — | 3.5 | Test kill mới pass trên Windows và Ubuntu |
| 7 | **Đánh giá PR #9**: giữ lõi, đổi tên định vị thành "project memory có xác nhận", thêm test đăng ký thật vào một agent trong sandbox | Kiến thức dự án bị nhốt | Memorix (R) | 3.2 | Một agent thật gọi `brain_search` được, có log; không đăng ký vào cấu hình thật khi chưa có backup + hoàn tác |
| 8 | **Kiểm L2 có kiểm soát**: một skill fixture vô hại, một agent, ghi lại agent có liệt kê skill không | Hash khớp không chứng minh agent nạp được | agentbridgehq conformance (R) | 3.0 | Kết quả `PASS/FAIL/NOT_VERIFIED` kèm phiên bản agent; không chạy script của skill bên thứ ba |
| 9 | IR chuẩn tối thiểu | Chuyển đổi định dạng giữa agent | arXiv 2606.26924 (D), agsync (R) | 2.4 | Chỉ bắt đầu khi có ≥2 ca tương thích thật mà template không giải được |
| 10 | Hydration dependency | Skill cần `node_modules` | — | 2.2 | Không bao giờ tự cài; chỉ báo thiếu |

## 3. NOW / NEXT / LATER

**NOW (2 tuần):** hạng mục 1, 2, 3, 6. Kết quả: CI phản ánh đúng số test; biết được mình có thật sự khác Helter5/agentbridge không.

**NEXT (1–2 tháng):** hạng mục 4, 5, 7, 8. Kết quả: dữ liệu người dùng đầu tiên ngoài chủ dự án; inventory có `unknown` trung thực; một bằng chứng L2 có log.

**LATER (khi có người dùng):** IR, hydration, chính sách doanh nghiệp, UI, memory phong phú hơn, điều phối.

## 4. STOP / không xây

- Dashboard hay desktop app điều khiển nhiều agent.
- Runtime điều phối, scheduler, nhắn tin giữa agent.
- Registry hay package manager skill riêng.
- Tìm kiếm vector / embedding cho memory.
- Scanner supply-chain tự viết từ đầu (xem agentbridgehq trước).
- Đồng bộ credential, session, token dưới bất kỳ hình thức nào.
- Bất kỳ tuyên bố "enterprise-ready" nào.

## 5. Chỉ số

| Chỉ số | Cách đo | Hiện có |
|---|---|---|
| % trạng thái bẩn được phân loại đúng | Fixture chuẩn (10 trạng thái trong `test_explain.py`) + phản hồi người dùng | 10/10 trên fixture; 0 người dùng ngoài |
| Ghi đè không được duyệt | Snapshot byte trước/sau trên fixture | 0 trên fixture (`test_byte_identity.py`, `test_explain.py`) |
| Tỷ lệ rollback/recover thành công trong các kịch bản lỗi đã test | `test_recovery.py`, `test_kill.py` | 20/20 kịch bản pass trên Windows; Ubuntu chưa từng chạy |
| Tỷ lệ L2 pass | Kiểm có log, theo agent và phiên bản | 0 có log (3 skill do chủ dự án báo tay) |
| Thời gian migrate một máy thật | Đo khi thử với người dùng | Chưa đo |

Một máy là bằng chứng kỹ thuật, không phải bằng chứng thị trường.
