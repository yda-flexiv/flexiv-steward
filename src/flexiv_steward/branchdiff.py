"""Branch diff web viewer — serve a local diff UI for comparing two branches."""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import TypedDict

from flexiv_steward.process import run_process

DIFF_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
OLD_MODE_RE = re.compile(r"^old mode (\d+)$")
NEW_MODE_RE = re.compile(r"^new mode (\d+)$")
NEW_FILE_RE = re.compile(r"^new file mode (\d+)$")
DEL_FILE_RE = re.compile(r"^deleted file mode (\d+)$")
BIN_RE = re.compile(r"^Binary files a/(.*) and b/(.*) differ$")


class DiffLine(TypedDict):
    type: str
    text: str


class DiffHunk(TypedDict):
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    heading: str
    lines: list[DiffLine]


class ParsedFile(TypedDict, total=False):
    old_path: str
    new_path: str
    old_mode: str
    new_mode: str
    chunks: list[DiffHunk]
    binary: bool


class Patch(TypedDict):
    path: str
    added: int
    removed: int
    status: str
    old_path: str
    new_path: str
    old_mode: str
    new_mode: str
    chunks: list[DiffHunk]
    binary: bool


class DiffSummary(TypedDict):
    files: int
    added: int
    removed: int


class BranchDiff(TypedDict):
    branch_a: str
    branch_b: str
    files: list[Patch]
    summary: DiffSummary


def _git_diff(branch_a: str, branch_b: str) -> BranchDiff:
    spec = f"{branch_a}..{branch_b}"
    name_status = _run(["git", "diff", "--name-status", spec])
    diff = _run(["git", "diff", spec])
    statuses = _parse_name_status(name_status)
    parsed = _parse_diff(diff)
    patches: list[Patch] = []
    seen: set[str] = set()
    for status_path, info in parsed.items():
        path = info.get("new_path") or info.get("old_path") or status_path
        if path in seen:
            continue
        seen.add(path)
        status = statuses.get(path, "M")
        chunks = info.get("chunks", [])
        added = sum(1 for c in chunks for l in c["lines"] if l["type"] == "add")
        removed = sum(1 for c in chunks for l in c["lines"] if l["type"] == "del")
        patches.append({
            "path": path,
            "added": added,
            "removed": removed,
            "status": status,
            "old_path": info.get("old_path", path),
            "new_path": info.get("new_path", path),
            "old_mode": info.get("old_mode", ""),
            "new_mode": info.get("new_mode", ""),
            "chunks": chunks,
            "binary": info.get("binary", False),
        })
    for path, status in statuses.items():
        if path not in seen:
            seen.add(path)
            patches.append({
                "path": path,
                "added": 0,
                "removed": 0,
                "status": status,
                "old_path": path,
                "new_path": path,
                "old_mode": "",
                "new_mode": "",
                "chunks": [],
                "binary": False,
            })
    return {
        "branch_a": branch_a,
        "branch_b": branch_b,
        "files": patches,
        "summary": {
            "files": len(patches),
            "added": sum(f["added"] for f in patches),
            "removed": sum(f["removed"] for f in patches),
        },
    }


def _run(cmd: list[str]) -> str:
    return run_process(cmd, Path.cwd()).output


def _parse_name_status(output: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in output.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st = parts[0][0]
        if len(parts) == 3:
            statuses[parts[1]] = st
            statuses[parts[2]] = st
        else:
            statuses[parts[1]] = st
    return statuses


def _parse_diff(output: str) -> dict[str, ParsedFile]:
    files: dict[str, ParsedFile] = {}
    current_file: str | None = None
    current_chunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None
    current_meta: ParsedFile = {}

    for line in output.split("\n"):
        m = DIFF_RE.match(line)
        if m:
            if current_file and current_hunk:
                current_chunks.append(current_hunk)
            if current_file:
                current_meta["chunks"] = current_chunks
                files[current_file] = current_meta
            current_file = m.group(1)
            current_meta = {"old_path": m.group(1), "new_path": m.group(2)}
            current_chunks = []
            current_hunk = None
            continue

        if current_file is None:
            continue

        if line.startswith("new file mode"):
            m = NEW_FILE_RE.match(line)
            if m:
                current_meta["new_mode"] = m.group(1)
            continue
        if line.startswith("deleted file mode"):
            m = DEL_FILE_RE.match(line)
            if m:
                current_meta["old_mode"] = m.group(1)
            continue
        if line.startswith("old mode"):
            m = OLD_MODE_RE.match(line)
            if m:
                current_meta["old_mode"] = m.group(1)
            continue
        if line.startswith("new mode"):
            m = NEW_MODE_RE.match(line)
            if m:
                current_meta["new_mode"] = m.group(1)
            continue
        if line.startswith("rename from"):
            m = re.match(r"^rename from (.*)$", line)
            if m:
                current_meta["old_path"] = m.group(1)
            continue
        if line.startswith("rename to"):
            m = re.match(r"^rename to (.*)$", line)
            if m:
                current_meta["new_path"] = m.group(1)
            continue
        if line.startswith("similarity index") or line.startswith("index "):
            continue
        if line == "--- /dev/null" or line == "+++ /dev/null":
            continue
        if line.startswith("--- a/") or line.startswith("+++ b/"):
            continue

        bm = BIN_RE.match(line)
        if bm:
            current_meta["binary"] = True
            continue

        m = HUNK_RE.match(line)
        if m:
            if current_hunk:
                current_chunks.append(current_hunk)
            current_hunk = {
                "old_start": int(m.group(1)),
                "old_count": int(m.group(2) or 1),
                "new_start": int(m.group(3)),
                "new_count": int(m.group(4) or 1),
                "heading": m.group(5).strip(),
                "lines": [],
            }
            continue

        if current_hunk is not None:
            if line.startswith("+"):
                current_hunk["lines"].append({"type": "add", "text": line[1:]})
            elif line.startswith("-"):
                current_hunk["lines"].append({"type": "del", "text": line[1:]})
            elif line.startswith(" ") or line == "":
                current_hunk["lines"].append({"type": "ctx", "text": line[1:] if line else ""})
            elif line.startswith("\\"):
                pass

    if current_file and current_hunk:
        current_chunks.append(current_hunk)
    if current_file:
        current_meta["chunks"] = current_chunks
        files[current_file] = current_meta

    return files


def _load_template() -> str:
    tmpl = Path(__file__).parent / "branchdiff.html"
    return tmpl.read_text()


def _build_html(data: BranchDiff) -> str:
    template = _load_template()
    return template.replace("__DATA_PLACEHOLDER__", json.dumps(data))


class _Handler(BaseHTTPRequestHandler):
    data: bytes = b""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.data)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def _start_server(port: int, html_content: str) -> None:
    _Handler.data = html_content.encode("utf-8")
    server = HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Branch diff server: {url}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.server_close()


def _branchdiff_command(args: argparse.Namespace) -> int:
    branch_a = args.branch_a
    branch_b = args.branch_b
    data = _git_diff(branch_a, branch_b)
    if not data["files"]:
        print(f"No diff between {branch_a} and {branch_b}", file=sys.stderr)
        return 0
    html_content = _build_html(data)
    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{args.port}")
    _start_server(args.port, html_content)
    return 0
