import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def make_skill(base: Path, name: str, body: str, vendor: bool = False):
    d = base / name
    d.mkdir(parents=True)
    (d / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: demo\n---\n{body}\n', encoding='utf-8')
    if vendor:
        (d / 'node_modules' / 'dep').mkdir(parents=True)
        (d / 'node_modules' / 'dep' / 'index.js').write_text('x', encoding='utf-8')


def test_audit_on_fake_stores(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    make_skill(a, 'same', 'hello')
    make_skill(b, 'same', 'hello')
    make_skill(a, 'drift', 'v1', vendor=True)
    make_skill(b, 'drift', 'v2')
    make_skill(a, 'only-a', 'x')
    out = tmp_path / 'out'
    subprocess.run([sys.executable, '-I', str(ROOT / 'audit_skills.py'), '--output', str(out),
                    '--store', f'a={a}', '--store', f'b={b}'], check=True)
    data = json.loads((out / 'audit.json').read_text(encoding='utf-8'))
    assert data['total_unique_skills'] == 3
    assert data['exact_duplicates'] == ['same']
    assert [d['name'] for d in data['content_drift']] == ['drift']
    assert data['unique_skills']['a'] == ['only-a']
    assert data['store_summaries']['a']['node_modules_files'] == 1
    assert (out / 'audit_report.md').exists()


def test_scripts_have_no_hardcoded_user_paths():
    for name in ('audit_skills.py', 'generate_report.py'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert 'Users' + chr(92) not in text and 'D:' + chr(92) not in text


if __name__ == '__main__':  # run without pytest: python tests/test_audit.py
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_audit_on_fake_stores(Path(d))
    test_scripts_have_no_hardcoded_user_paths()
    print('ok')
