"""Read immutable baseline blobs. Codex replacement for rejected local attempt."""
import hashlib
import re
import subprocess


def read_baseline(repo, commit, path):
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('baseline_requires_full_commit_sha')
    if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', path)
            or any(part in ('', '.', '..') for part in path.split('/'))):
        raise ValueError('invalid_baseline_path')

    def git(*args):
        return subprocess.run(['git', '--literal-pathspecs', *args], cwd=repo,
                              capture_output=True, check=True, timeout=5).stdout

    # Check the object before interpreting an empty tree lookup as an absent file.
    if git('cat-file', '-t', commit).strip() != b'commit':
        raise ValueError('baseline_object_is_not_commit')
    result = {'commit': commit, 'path': path, 'present': False,
              'content': '', 'sha256': None}
    entry = git('ls-tree', '-z', commit, '--', path)
    if not entry:
        return result
    if not entry.endswith(b'\0') or entry.count(b'\0') != 1:
        raise ValueError('ambiguous_baseline_tree_entry')
    header, actual_path = entry[:-1].split(b'\t', 1)
    mode, kind, oid = header.split(b' ')
    if (actual_path != path.encode('ascii') or mode not in (b'100644', b'100755')
            or kind != b'blob' or not re.fullmatch(rb'[0-9a-f]{40}', oid)):
        raise ValueError('baseline_not_regular_file')
    object_id = oid.decode('ascii')
    size = int(git('cat-file', '-s', object_id).strip())
    if not 0 <= size <= 30000:
        raise ValueError('baseline_blob_too_large')
    content = git('cat-file', 'blob', object_id)
    if len(content) != size:
        raise ValueError('baseline_blob_size_changed')
    return {**result, 'present': True, 'content': content.decode('utf-8'),
            'sha256': hashlib.sha256(content).hexdigest()}
