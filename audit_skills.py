import os
import sys
import hashlib
import json
import re
from pathlib import Path
from datetime import datetime

# Đảm bảo stdout / stderr hỗ trợ tiếng Việt UTF-8 trên Windows console
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

STORES = {
    'claude': Path.home() / '.claude' / 'skills',
    'gemini': Path.home() / '.gemini' / 'config' / 'skills',
    'agents': Path.home() / '.agents' / 'skills',
    'codex_sys': Path.home() / '.codex' / 'skills' / '.system'
}

WORKSPACE = Path(r'D:\Downloads D\Study\WORK\Agent OS Gono')

def hash_file(p: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(p, 'rb') as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        return f"ERROR:{e}"

def extract_frontmatter(p: Path):
    if not p.exists():
        return {}
    try:
        content = p.read_text(encoding='utf-8', errors='ignore')
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if match:
            fm_text = match.group(1)
            meta = {}
            for line in fm_text.splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    k = k.strip()
                    v = v.strip().strip('"\'')
                    if k in ('name', 'description'):
                        meta[k] = v
            return meta
    except Exception:
        pass
    return {}

def scan_skill_dir(skill_dir: Path):
    source_files = []
    total_source_size = 0
    tree_hasher = hashlib.sha256()
    has_node_modules = False
    node_modules_count = 0

    for item in sorted(skill_dir.rglob('*')):
        if item.is_file():
            parts = item.parts
            if 'node_modules' in parts or '.git' in parts:
                has_node_modules = True
                node_modules_count += 1
                continue

            rel = str(item.relative_to(skill_dir)).replace('\\', '/')
            sz = item.stat().st_size
            f_hash = hash_file(item)
            total_source_size += sz
            source_files.append({
                'rel_path': rel,
                'size': sz,
                'hash': f_hash
            })
            tree_hasher.update(f"{rel}:{sz}:{f_hash}".encode('utf-8'))

    skill_md = skill_dir / 'SKILL.md'
    has_md = skill_md.exists()
    md_hash = hash_file(skill_md) if has_md else None
    md_size = skill_md.stat().st_size if has_md else 0
    meta = extract_frontmatter(skill_md) if has_md else {}

    return {
        'name': skill_dir.name,
        'path': str(skill_dir),
        'source_file_count': len(source_files),
        'total_source_size': total_source_size,
        'has_node_modules': has_node_modules,
        'node_modules_count': node_modules_count,
        'tree_hash': tree_hasher.hexdigest(),
        'has_skill_md': has_md,
        'md_hash': md_hash,
        'md_size': md_size,
        'meta': meta
    }

def main():
    print("Bắt đầu quét 4 kho skills...", flush=True)
    audit_data = {}
    store_summaries = {}

    for store_name, base_path in STORES.items():
        skills = {}
        if base_path.exists():
            for item in sorted(base_path.iterdir()):
                if item.is_dir():
                    info = scan_skill_dir(item)
                    skills[item.name] = info
        audit_data[store_name] = skills
        total_skills = len(skills)
        total_src_files = sum(s['source_file_count'] for s in skills.values())
        total_src_bytes = sum(s['total_source_size'] for s in skills.values())
        total_nm_files = sum(s['node_modules_count'] for s in skills.values())
        store_summaries[store_name] = {
            'skills': total_skills,
            'source_files': total_src_files,
            'node_modules_files': total_nm_files,
            'source_size_mb': round(total_src_bytes / (1024 * 1024), 2)
        }
        print(f"[{store_name}] {total_skills} skills, {total_src_files} source files (kèm {total_nm_files} file vendor), {store_summaries[store_name]['source_size_mb']} MB", flush=True)

    all_skill_names = sorted(list(set().union(*(s.keys() for s in audit_data.values()))))
    print(f"\nTổng số skill name duy nhất: {len(all_skill_names)}", flush=True)

    comparison = {}
    exact_duplicates = []
    content_drift = []
    unique_skills = {k: [] for k in STORES.keys()}

    for name in all_skill_names:
        present_in = [s for s in STORES.keys() if name in audit_data[s]]
        entries = {s: audit_data[s][name] for s in present_in}

        if len(present_in) == 1:
            store = present_in[0]
            unique_skills[store].append(name)
            status = 'unique'
            drift_details = None
        else:
            # Check SKILL.md hash equality
            md_hashes = set(e['md_hash'] for e in entries.values() if e['has_skill_md'])
            tree_hashes = set(e['tree_hash'] for e in entries.values())

            if len(md_hashes) == 1 and len(tree_hashes) == 1:
                status = 'exact_match_all'
                exact_duplicates.append(name)
                drift_details = None
            elif len(md_hashes) == 1 and len(tree_hashes) > 1:
                status = 'identical_md_different_assets'
                content_drift.append({
                    'name': name,
                    'stores': present_in,
                    'type': 'assets_only',
                    'details': {s: {'source_files': entries[s]['source_file_count'], 'size': entries[s]['total_source_size']} for s in present_in}
                })
                drift_details = 'assets_differ'
            else:
                status = 'content_drift'
                content_drift.append({
                    'name': name,
                    'stores': present_in,
                    'type': 'md_differ',
                    'details': {s: {'md_size': entries[s]['md_size'], 'source_files': entries[s]['source_file_count'], 'size': entries[s]['total_source_size']} for s in present_in}
                })
                drift_details = 'md_differ'

        comparison[name] = {
            'name': name,
            'present_in': present_in,
            'status': status,
            'drift_details': drift_details,
            'meta': next((e['meta'] for e in entries.values() if e.get('meta')), {})
        }

    # Output JSON
    output_json_path = WORKSPACE / 'skills_audit_data.json'
    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'store_summaries': store_summaries,
            'total_unique_skills': len(all_skill_names),
            'exact_duplicates_count': len(exact_duplicates),
            'content_drift_count': len(content_drift),
            'unique_counts': {k: len(v) for k, v in unique_skills.items()},
            'exact_duplicates': exact_duplicates,
            'content_drift': content_drift,
            'unique_skills': unique_skills,
            'comparison': comparison
        }, f, ensure_ascii=False, indent=2)

    print(f"Đã ghi JSON chi tiết vào {output_json_path}", flush=True)

    # Output Markdown Report
    output_md_path = WORKSPACE / 'SKILLS_AUDIT_REPORT.md'
    with open(output_md_path, 'w', encoding='utf-8') as f:
        f.write("# Báo cáo Audit & Ma trận So sánh 4 Kho Skills (Phase 2)\n\n")
        f.write(f"**Thời gian thực hiện:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"**Thư mục làm việc:** `{WORKSPACE}`\n\n")

        f.write("## 1. Tổng quan các kho Skills\n\n")
        f.write("| Kho lưu trữ | Đường dẫn | Số skills | Số source files | File vendor (node_modules) | Dung lượng source |\n")
        f.write("|---|---|---|---|---|---|\n")
        for s, info in store_summaries.items():
            f.write(f"| **{s}** | `{STORES[s]}` | {info['skills']} | {info['source_files']} | {info['node_modules_files']:,} | {info['source_size_mb']} MB |\n")
        f.write(f"\n* **Tổng số tên skill duy nhất trên toàn bộ hệ thống:** **{len(all_skill_names)}** skills.\n")
        f.write(f"* **Số skill trùng lặp hoàn toàn 100% (cả SKILL.md & source tree):** **{len(exact_duplicates)}** skills.\n")
        f.write(f"* **Số skill cùng tên nhưng bị lệch nội dung (Content Drift):** **{len(content_drift)}** skills.\n")
        f.write("* **Số skill độc nhất (chỉ xuất hiện ở 1 nơi):**\n")
        for s, unq in unique_skills.items():
            f.write(f"  * `{s}`: **{len(unq)}** skills độc nhất\n")

        f.write("\n## 2. Nhóm kỹ năng bị lệch nội dung (Content Drift - Cần hợp nhất có kiểm soát)\n\n")
        f.write("Những skill này cùng tên nhưng nội dung `SKILL.md` hoặc các file mã nguồn/script giữa các agent không giống nhau:\n\n")
        f.write("| Tên Skill | Xuất hiện ở | Kiểu lệch | Chi tiết dung lượng / files |\n")
        f.write("|---|---|---|---|\n")
        for cd in content_drift:
            type_str = "Lệch SKILL.md" if cd['type'] == 'md_differ' else "Chỉ lệch assets/scripts"
            det_parts = []
            for st, d in cd['details'].items():
                if 'md_size' in d:
                    det_parts.append(f"**{st}**: md={d['md_size']}B, {d['source_files']} files ({round(d['size']/1024,1)}KB)")
                else:
                    det_parts.append(f"**{st}**: {d['source_files']} files ({round(d['size']/1024,1)}KB)")
            f.write(f"| `{cd['name']}` | {', '.join(cd['stores'])} | {type_str} | {'<br>'.join(det_parts)} |\n")

        f.write("\n## 3. Nhóm kỹ năng độc nhất (Unique Skills)\n\n")
        for s, unq in unique_skills.items():
            f.write(f"### 3.{list(STORES.keys()).index(s)+1} Độc nhất tại `{s}` ({len(unq)} skills)\n\n")
            if unq:
                f.write("| Tên Skill | Mô tả tóm tắt |\n")
                f.write("|---|---|\n")
                for u in unq:
                    desc = audit_data[s][u]['meta'].get('description', '*(Không có mô tả)*')
                    if len(desc) > 80:
                        desc = desc[:77] + "..."
                    desc = desc.replace('\n', ' ')
                    f.write(f"| `{u}` | {desc} |\n")
            else:
                f.write("*(Không có skill độc nhất nào)*\n")
            f.write("\n")

        f.write("## 4. Danh sách các Skill trùng khớp 100% (Exact Matches)\n\n")
        f.write(f"Có tổng cộng **{len(exact_duplicates)}** skills hoàn toàn giống nhau giữa các kho lưu trữ:\n\n")
        f.write("<details><summary><b>Bấm vào đây để xem toàn bộ danh sách trùng khớp</b></summary>\n\n")
        f.write("| STT | Tên Skill | Xuất hiện ở |\n")
        f.write("|---|---|---|\n")
        for idx, ed in enumerate(exact_duplicates, 1):
            f.write(f"| {idx} | `{ed}` | {', '.join(comparison[ed]['present_in'])} |\n")
        f.write("\n</details>\n\n")

        f.write("## 5. Khuyến nghị chuẩn bị cho Phase 3 (Hợp nhất & Tạo Single Source of Truth)\n\n")
        f.write("1. **Định hình thư mục chuẩn (SSOT):** Gom tất cả 191 skills duy nhất về một kho chuẩn duy nhất (ví dụ `.agentsmesh/skills` hoặc `skills/` trong workspace).\n")
        f.write("2. **Quy tắc giải quyết Content Drift:**\n")
        f.write("   - Đối với skill lệch assets: Chọn bản có đầy đủ scripts / references nhất.\n")
        f.write("   - Đối với skill lệch SKILL.md: Giữ bản có nội dung chỉ dẫn chi tiết, cập nhật mới nhất.\n")
        f.write("3. **Bảo tồn các skill hệ thống của Codex:** 5 skills trong `.codex/skills/.system` (`imagegen`, `openai-docs`, `review-agent`, `skill-creator`, `skill-installer`) là các skill độc quyền của Codex CLI runtime, giữ riêng trong cấu hình Codex.\n")

    print(f"Đã ghi Báo cáo Markdown chi tiết vào {output_md_path}", flush=True)

if __name__ == '__main__':
    main()
