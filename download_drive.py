"""Ordered, resumable Drive downloads. No remote mutation commands are used."""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent
DOWNLOAD = BASE / "download"
CONTROL = BASE / "download_state"
STATE = CONTROL / "progress.json"
LOG = logging.getLogger("downloader")
FOLDER = "1xZRH-PAlv9FNBp-EuCv92ZmLXz4hm2OM"


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_name(value):
    name = str(value).strip()
    if (not name or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((".", " ")) or len(name) > 180
            or name.casefold() == ".batch-owner.json"
            or re.match(r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", name, re.I)):
        raise ValueError(f"不安全或不支持的 Windows 文件名：{name!r}（仅接受文件名，不接受路径）")
    return name


def read_excel(cfg):
    from openpyxl import load_workbook
    source = Path(cfg["excel"])
    if not source.is_absolute():
        source = BASE / source
    with contextlib.closing(load_workbook(source, read_only=True, data_only=True)) as book:
        sheet = book[cfg["sheet"]] if cfg.get("sheet") else book.active
        rows = iter(sheet.iter_rows(values_only=True))
        header = [str(v).strip() if v is not None else "" for v in next(rows, ())]
        if header.count(cfg["column"]) != 1:
            raise ValueError(f"首行必须且只能出现一次 {cfg['column']} 列")
        col = header.index(cfg["column"])
        names, seen = [], set()
        for rowno, row in enumerate(rows, 2):
            value = row[col] if col < len(row) else None
            if value is None or str(value).strip() == "":
                continue
            name = safe_name(value)
            # Windows ignores case. Reject ambiguity rather than silently skip Excel rows.
            if name.casefold() in seen:
                raise ValueError(f"Excel 第 {rowno} 行文件名重复（含大小写冲突）：{name}")
            seen.add(name.casefold())
            names.append(name)
    if not names:
        raise ValueError("Excel 没有可下载的文件名")
    return names


def read_csv(cfg):
    source = Path(cfg["csv"])
    if not source.is_absolute():
        source = BASE / source
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        if not rows.fieldnames or rows.fieldnames.count(cfg["column"]) != 1:
            raise ValueError(f"CSV 首行必须且只能出现一次 {cfg['column']} 列")
        names, seen = [], set()
        for rowno, row in enumerate(rows, 2):
            value = row.get(cfg["column"])
            if value is None or not value.strip():
                continue
            name = safe_name(value)
            if name.casefold() in seen:
                raise ValueError(f"CSV 第 {rowno} 行文件名重复（含大小写冲突）：{name}")
            seen.add(name.casefold())
            names.append(name)
    if not names:
        raise ValueError("CSV 没有可下载的文件名")
    return names


def read_names(cfg):
    if cfg.get("excel"):
        return read_excel(cfg)
    if cfg.get("csv"):
        return read_csv(cfg)
    return None


def folder_id_from_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"drive.google.com", "www.drive.google.com"}:
        raise ValueError("请输入 Google Drive 文件夹共享网址")
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", parsed.path)
    if not match:
        raise ValueError("网址中未找到 Google Drive 文件夹 ID")
    return match.group(1)


def list_remote_names(exe, cfg):
    proc = subprocess.run([exe, "lsjson", cfg["remote"] + ":"] + common_flags(cfg),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if proc.returncode:
        raise RuntimeError("无法列出共享文件夹：" + proc.stderr[-2000:])
    names, seen = [], set()
    for item in json.loads(proc.stdout):
        if item.get("IsDir"):
            continue
        name = safe_name(item["Name"])
        if name.casefold() in seen:
            raise ValueError(f"云端文件名含大小写冲突：{name}")
        seen.add(name.casefold())
        names.append(name)
    if not names:
        raise ValueError("共享文件夹中没有可下载文件")
    return names


def positive(value, label, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or (maximum and value > maximum):
        requirement = f"1 到 {maximum} 之间的整数" if maximum else "正整数"
        raise ValueError(f"{label} 必须为{requirement}")


def load_config(args):
    path = Path(args.config)
    if not path.is_absolute():
        path = BASE / path
    if not path.is_file():
        # A fresh clone has no local config; fall back to the committed template.
        template = path.with_name('config.example.json')
        if not template.is_file():
            raise ValueError(f"找不到配置文件 {path.name}，也没有可用的 config.example.json")
        print(f"未找到 {path.name}，暂时使用 config.example.json；请按 README 新建 {path.name}。")
        path = template
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    if args.batch_size is not None:
        cfg["batch_size"] = args.batch_size
    if args.remote:
        cfg["remote"] = args.remote
    if cfg.get("excel") and cfg.get("csv"):
        raise ValueError("excel 与 csv 只能配置其中一个")
    positive(cfg["batch_size"], "batch_size")
    positive(cfg["attempts"], "attempts", 20)
    if not isinstance(cfg["retry_delay_seconds"], (int, float)) or cfg["retry_delay_seconds"] < 0:
        raise ValueError("retry_delay_seconds 必须非负")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@+-]*", cfg["remote"]):
        raise ValueError("remote 必须是 rclone 配置名称，不包含冒号、路径或空格")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cfg["folder_id"]):
        raise ValueError("folder_id 格式错误")
    return cfg


def apply_proxy(cfg):
    proxy = cfg.get("proxy", "").strip()
    if not proxy:
        return  # Keep inherited environment settings when no project proxy is configured.
    parsed = urlsplit(proxy)
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname or not parsed.port:
        raise ValueError("proxy 必须为 http://主机:端口 或 socks5://主机:端口")
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"


def plan_id(cfg, names):
    value = {"remote": cfg["remote"], "folder": cfg["folder_id"], "files": names}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def legacy_plan_id(cfg, names, batch_size):
    value = {"remote": cfg["remote"], "folder": cfg["folder_id"],
             "batch_size": batch_size, "files": names}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def new_state(cfg, names):
    return {"version": 1, "plan_id": plan_id(cfg, names), "files": names,
            "batch_size": cfg["batch_size"], "batch_index": 0, "phase": "downloading",
            "downloaded": {}, "failures": {}, "history": []}


def batch_ranges(state):
    return state.get('batch_ranges', [[start, min(start + state['batch_size'], len(state['files']))]
                                      for start in range(0, len(state['files']), state['batch_size'])])


def batch_bounds(state):
    return batch_ranges(state)[state['batch_index']]


def select_batch_size(state, cfg, input_fn):
    index = state['batch_index']
    if (state['phase'] != 'downloading' or state['downloaded'] or state['failures']
            or state.get('batch_size_selected_for') == index):
        return True
    folder = batch_directory(state, create=False)
    if folder.exists() and any(p.name != '.batch-owner.json' for p in folder.iterdir()):
        return True  # Keep the range of a partially transferred batch.
    start, _ = batch_bounds(state)
    default = state.get('preferred_batch_size', cfg['batch_size'])
    if cfg.get('explicit_batch_size') or cfg['batch_size'] != state['batch_size']:
        default = cfg['batch_size']
    size = default
    if cfg.get('interactive_batch_size'):
        while True:
            answer = input_fn(f"第 {index + 1} 批下载多少个文件？输入正整数，回车使用 {default}，q 退出：")
            if answer.lower() == 'q':
                return False
            try:
                size = int(answer) if answer else default
                positive(size, '批次文件数量')
            except ValueError:
                print('请输入大于 0 的整数。')
                continue
            break
    positive(size, '批次文件数量')
    ranges = batch_ranges(state)[:index]
    ranges += [[pos, min(pos + size, len(state['files']))]
               for pos in range(start, len(state['files']), size)]
    state['batch_ranges'] = ranges
    state['preferred_batch_size'] = size
    state['batch_size_selected_for'] = index
    persist(state)
    return True


def load_state(cfg, names):
    if not STATE.exists():
        # Never silently adopt files left behind without their progress record.
        if DOWNLOAD.exists() and any(DOWNLOAD.iterdir()):
            raise ValueError("download 内已有文件但没有进度记录。请先备份并另存这些文件，再启动。")
        state = new_state(cfg, names)
        atomic_json(STATE, state)
        return state
    state = json.loads(STATE.read_text(encoding="utf-8"))
    positive(state.get('batch_size'), '进度中的初始批次大小')
    if not cfg.get('excel') and not cfg.get('csv'):
        saved_names = state.get('files', [])
        saved_ids = {plan_id(cfg, saved_names),
                     legacy_plan_id(cfg, saved_names, state['batch_size'])}
        if state.get('plan_id') in saved_ids and set(saved_names).issubset(names):
            added = set(names) - set(saved_names)
            if added:
                print(f'云端新增 {len(added)} 个文件；本次继续原任务的 {len(saved_names)} 个文件，新增文件需另建任务下载。')
            names = saved_names
    expected_plan = plan_id(cfg, names)
    accepted_plans = {expected_plan, legacy_plan_id(cfg, names, state['batch_size'])}
    if state.get("version") != 1:
        raise ValueError("进度文件版本不受支持。为保护现有批次，拒绝继续。")
    if state.get("files") != names:
        old_files = state.get("files", [])
        mismatch = next((i for i, pair in enumerate(zip(old_files, names), 1)
                         if pair[0] != pair[1]), min(len(old_files), len(names)) + 1)
        old_name = old_files[mismatch - 1] if mismatch <= len(old_files) else "<文件列表结束>"
        new_name = names[mismatch - 1] if mismatch <= len(names) else "<文件列表结束>"
        raise ValueError(f"云端文件清单与已有进度不一致（第 {mismatch} 项：进度={old_name!r}，当前={new_name!r}；"
                         f"进度 {len(old_files)} 个，当前 {len(names)} 个）。为保护已下载文件，拒绝继续。")
    if state.get("plan_id") not in accepted_plans:
        raise ValueError("rclone 连接或 Google Drive 文件夹与已有进度不同。为保护已下载文件，拒绝继续。")
    ranges = batch_ranges(state)
    cursor = 0
    for bounds in ranges:
        if (not isinstance(bounds, list) or len(bounds) != 2
                or any(type(n) is not int for n in bounds)
                or bounds[0] != cursor or bounds[1] <= bounds[0]
                or bounds[1] > len(names)):
            raise ValueError('批次文件范围无效，保留现场并停止。')
        cursor = bounds[1]
    if cursor != len(names):
        raise ValueError('批次文件范围不完整，保留现场并停止。')
    total = len(ranges)
    index = state.get("batch_index")
    if (state.get("files") != names
            or not isinstance(index, int) or not 0 <= index <= total
            or state.get("phase") not in {"downloading", "awaiting_confirmation", "cleanup_pending", "complete"}
            or (state["phase"] == "complete") != (index == total)
            or len(state.get("history", [])) != index):
        raise ValueError("进度记录无效，请保留文件并检查 progress.json；不要直接重置进度。")
    return state


@contextlib.contextmanager
def exclusive_lock():
    CONTROL.mkdir(parents=True, exist_ok=True)
    with (CONTROL / "run.lock").open("a+b") as f:
        try:
            if os.fstat(f.fileno()).st_size == 0:
                f.write(b"0")
                f.flush()
            f.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("已有另一个下载程序正在运行，请勿重复启动。") from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def reject_links(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError(f"拒绝使用符号链接或目录联接：{path}")
    # Also works on Python versions without Path.is_junction().
    if path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError(f"拒绝使用重解析点：{path}")


def batch_directory(state, create=True):
    reject_links(DOWNLOAD)
    folder = DOWNLOAD / f"batch_{state['batch_index'] + 1:04d}"
    reject_links(folder)
    if folder.resolve().parent != DOWNLOAD.resolve() or DOWNLOAD.resolve().parent != BASE:
        raise ValueError("批次路径超出本项目 download 目录")
    owner = {"plan_id": state["plan_id"], "batch_index": state["batch_index"]}
    marker = folder / ".batch-owner.json"
    if not folder.exists():
        if not create:
            return folder
        folder.mkdir(parents=True)
        atomic_json(marker, owner)
    else:
        reject_links(marker)
        if state["phase"] == "cleanup_pending" and not any(folder.iterdir()):
            return folder  # Crash after removing the marker but before removing the empty folder.
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != owner:
            raise ValueError(f"批次目录缺少匹配的归属标记，拒绝覆盖或删除：{folder}")
    return folder


def validate_tree(folder):
    reject_links(folder)
    for root, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            reject_links(Path(root) / name)


def clean_batch(state, answer):
    expected = f"DELETE {state['batch_index'] + 1}"
    if answer != expected:
        raise ValueError("未收到本批次确认，文件保持原样。")
    # Confirmation advances the workflow only; local files are never removed.
    batch_directory(state, create=False)


def rclone_executable(cfg):
    preferred = Path(cfg["rclone"])
    if not preferred.is_absolute():
        preferred = BASE / preferred
    exe = str(preferred) if preferred.is_file() else shutil.which("rclone")
    if not exe:
        raise RuntimeError("未找到 rclone。请先运行 install.bat，或将 rclone.exe 放入 tools 文件夹。")
    return exe


def common_flags(cfg):
    return ["--drive-root-folder-id", cfg["folder_id"],
            "--drive-scope", "drive.readonly", "--retries", "1",
            "--low-level-retries", "3", "--contimeout", "30s", "--timeout", "2m"]


def preflight(exe, cfg):
    # This is a read-only query; do not dump tokens or the rclone configuration.
    proc = subprocess.run([exe, "listremotes"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode or cfg["remote"] + ":" not in proc.stdout.splitlines():
        raise RuntimeError(f"rclone 未配置 {cfg['remote']}。请运行 configure_drive.bat 并完成 Google 授权。")
    proc = subprocess.run([exe, "lsjson", cfg["remote"] + ":", "--stat"] + common_flags(cfg),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if proc.returncode:
        raise RuntimeError("无法读取共享文件夹，请检查网络、授权和 folder_id。\n" + proc.stderr[-3000:])
    if not json.loads(proc.stdout).get("IsDir"):
        raise RuntimeError("指定的 Google Drive 来源不是文件夹")


def copy_file(exe, cfg, name, target):
    reject_links(target)
    metadata = subprocess.run(
        [exe, "lsjson", cfg["remote"] + ":" + name, "--stat"] + common_flags(cfg),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if metadata.returncode:
        raise RuntimeError("无法读取源文件元数据：" + metadata.stderr[-2000:])
    source = json.loads(metadata.stdout)
    if source.get("IsDir") or source.get("Size", -1) < 0:
        raise RuntimeError("来源必须是具有明确大小的普通文件，拒绝递归下载目录")
    # One file, one stream, low bounded buffers; rclone verifies transfer integrity.
    command = [exe, "copyto", cfg["remote"] + ":" + name, str(target),
               "--transfers", "1", "--checkers", "1", "--multi-thread-streams", "0",
               "--buffer-size", cfg["buffer_size"], "--max-buffer-memory", cfg["max_buffer_memory"],
               "--bwlimit", cfg["bandwidth_limit"], "--checksum", "--stats", "2s",
               "--stats-one-line", "--stats-log-level", "NOTICE", "--log-level", "NOTICE",
               "--log-file", str(CONTROL / "rclone.log")] + common_flags(cfg)
    # Keep live progress in the terminal and a separate persistent rclone log.
    command += ["--progress"]
    proc = subprocess.Popen(command)
    try:
        code = proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        raise
    if code:
        raise RuntimeError(f"rclone 退出码 {code}，详见 download_state/rclone.log")
    if not target.is_file():
        raise RuntimeError("rclone 返回成功但目标文件不存在")
    reject_links(target)
    if target.stat().st_size != source["Size"]:
        raise RuntimeError("下载后大小与源文件元数据不一致，源文件可能在下载期间被修改")
    return {"size": target.stat().st_size, "sha256": digest(target)}


def write_failures(state):
    tmp = CONTROL / "failed_files.csv.tmp"
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["batch", "file", "attempts", "error", "time"])
        for name, info in state["failures"].items():
            writer.writerow([state["batch_index"] + 1, name, info["attempts"], info["error"], info["time"]])
    os.replace(tmp, CONTROL / "failed_files.csv")


def persist(state):
    atomic_json(STATE, state)
    write_failures(state)


def defer_failures(state, input_fn=None):
    """Explicit opt-in: archive outstanding failures, then wait for normal cleanup confirmation."""
    if state['phase'] != 'downloading' or not state['failures']:
        raise ValueError('当前没有可暂缓的失败文件；请使用正常下载入口。')
    start, end = batch_bounds(state)
    batch = state['files'][start:end]
    if set(state['downloaded']) & set(state['failures']):
        raise ValueError('成功与失败记录重叠，保留现场并停止。')
    if set(batch) != set(state['downloaded']) | set(state['failures']):
        raise ValueError('本批仍有尚未尝试的文件，请先正常启动并完成本批尝试。')
    folder = batch_directory(state, create=False)
    validate_tree(folder)
    number = state['batch_index'] + 1
    upload_confirmed = False
    try:
        verify_saved(state, folder)
    except RuntimeError as exc:
        print(str(exc))
        print('如果本批成功文件已全部上传并确认成功，可以确认处理完成；否则退出并恢复原文件。')
        answer = (input_fn or ask)(f'输入 UPLOADED {number} 确认本批成功文件已全部上传；其他输入保留进度并退出：')
        if answer != f'UPLOADED {number}':
            return False
        upload_confirmed = True
    record = {'plan_id': state['plan_id'], 'batch': number,
              'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
              'external_upload_confirmed': upload_confirmed,
              'downloaded_files': state['downloaded'],
              'failures': state['failures']}
    archive = CONTROL / 'deferred_failures'
    archive.mkdir(parents=True, exist_ok=True)
    # Never cleared by normal batch advancement. JSON is authoritative; CSV is for convenience.
    path = archive / f'batch_{number:04d}_{time.time_ns()}.json'
    atomic_json(path, record)
    csv_path = path.with_suffix('.csv')
    with csv_path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['batch', 'file', 'attempts', 'error', 'time'])
        for name, info in state['failures'].items():
            writer.writerow([number, name, info['attempts'], info['error'], info['time']])
    state['deferred_current_batch'] = len(state['failures'])
    state['phase'] = 'awaiting_confirmation'
    persist(state)
    LOG.warning('本批 %d 个失败文件已暂缓，永久清单：%s；尚未删除本地文件。', len(state['failures']), csv_path)
    return True


def batch_fully_attempted(state):
    if state['phase'] != 'downloading' or not state['failures']:
        return False
    start, end = batch_bounds(state)
    return (not (set(state['downloaded']) & set(state['failures']))
            and set(state['files'][start:end]) == set(state['downloaded']) | set(state['failures']))


def ask(prompt):
    if not sys.stdin.isatty():
        print("当前不是交互终端，保留文件并退出。请双击 start_download.bat 手动确认。")
        return "q"
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def verify_saved(state, folder):
    for name, info in state["downloaded"].items():
        safe_name(name)
        path = folder / name
        reject_links(path)
        if (not path.is_file() or path.stat().st_size != info["size"] or digest(path) != info["sha256"]):
            raise RuntimeError(f"已下载文件丢失或被修改，已保留现场并停止：{name}。请恢复原文件再继续。")


def run_batches(cfg, state, exe, copier=copy_file, input_fn=ask, sleep_fn=time.sleep):
    names = state["files"]
    while state["phase"] != "complete":
        if not select_batch_size(state, cfg, input_fn):
            LOG.info('已退出，保留当前进度和文件。')
            return 0
        total = len(batch_ranges(state))
        number = state["batch_index"] + 1
        start, end = batch_bounds(state)
        batch = names[start:end]
        LOG.info("批次 %d/%d：Excel 文件 %d–%d，状态 %s", number, total, start + 1,
                 start + len(batch), state["phase"])
        if batch_fully_attempted(state):
            print(f"\n本批已尝试完毕：{len(state['downloaded'])} 个成功，{len(state['failures'])} 个失败。")
            print('失败清单：download_state/failed_files.csv；继续时会另存永久失败记录。')
            answer = input_fn('输入 c 保留失败记录并继续下一批，r 重试失败文件，q 或回车退出：').lower()
            if answer == 'c':
                if not defer_failures(state, input_fn):
                    return 2
                continue
            if answer != 'r':
                return 2
        if state["phase"] in {"awaiting_confirmation", "cleanup_pending"}:
            # Even a crash halfway through cleanup requires a fresh human confirmation.
            folder = batch_directory(state, create=False)
            if state.get('deferred_current_batch'):
                print(f"\n本批有 {state['deferred_current_batch']} 个失败文件已暂缓，清单保存在 download_state/deferred_failures。")
            print(f"\n本批已有 {len(state['downloaded'])} 个成功文件。请先上传并确认成功或备份：{folder}")
            print("确认后保留本地文件并继续下一批；云端源文件保持不变。")
            answer = input_fn(f"输入 DELETE {number} 确认并继续；输入 q 或直接回车保留并退出：")
            if answer != f"DELETE {number}":
                LOG.info("未确认删除，保留当前批次并退出。")
                return 0
            state["phase"] = "cleanup_pending"
            persist(state)
            clean_batch(state, answer)
            state["history"].append({"batch": number, "count": len(batch),
                                     "downloaded_count": len(state['downloaded']),
                                     "deferred_count": state.pop('deferred_current_batch', 0),
                                     "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            state["batch_index"] += 1
            state["downloaded"] = {}
            state["failures"] = {}
            state["phase"] = "complete" if number == total else "downloading"
            persist(state)
            continue
        folder = batch_directory(state)
        validate_tree(folder)
        verify_saved(state, folder)
        if cfg.pop('_preflight_required', False):
            preflight(exe, cfg)
        for position, name in enumerate(batch, 1):
            if name in state["downloaded"]:
                LOG.info("复用已校验文件 [%d/%d] %s", position, len(batch), name)
                continue
            for attempt in range(1, cfg["attempts"] + 1):
                LOG.info("批次 %d/%d，文件 %d/%d，总文件 %d/%d：%s（尝试 %d/%d）",
                         number, total, position, len(batch), start + position, len(names),
                         name, attempt, cfg["attempts"])
                try:
                    info = copier(exe, cfg, name, folder / name)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    previous = state["failures"].get(name, {}).get("attempts", 0)
                    state["failures"][name] = {"attempts": previous + 1, "error": str(exc),
                                                "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                    persist(state)
                    LOG.error("下载失败 %s：%s", name, exc)
                    if attempt < cfg["attempts"]:
                        sleep_fn(min(cfg["retry_delay_seconds"] * 2 ** (attempt - 1), 60))
                else:
                    state["downloaded"][name] = info
                    state["failures"].pop(name, None)
                    persist(state)
                    break
        if any(name not in state["downloaded"] for name in batch):
            print("\n本批次仍有失败文件，已保存失败列表。不会删除或开始下一批。")
            continue
        state["phase"] = "awaiting_confirmation"
        persist(state)
    LOG.info("全部 %d 个文件、%d 个批次已处理完成。", len(names), len(batch_ranges(state)))
    return 0


def main():
    parser = argparse.ArgumentParser(description="按 Excel 顺序分批下载 Google Drive 文件，每批必须手工确认")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--batch-size", type=int, help="任意正整数，用于尚未开始的批次；省略时在交互终端询问")
    parser.add_argument("--remote", help="rclone Google Drive remote 名称")
    parser.add_argument("--url", help="Google Drive 文件夹共享网址")
    parser.add_argument("--plan", action="store_true", help="只检查 Excel 和配置，不联网、不创建进度")
    parser.add_argument("--check", action="store_true", help="检查 Excel、rclone 和云端授权，不下载")
    parser.add_argument("--configure", action="store_true", help="使用项目代理进行 rclone 交互授权配置")
    parser.add_argument("--reconnect", action="store_true", help="使用项目代理重新授权现有连接")
    parser.add_argument("--defer-failures", action="store_true", help="保存失败清单并暂缓失败文件；仍需手工确认本批删除后才继续")
    args = parser.parse_args()
    try:
        cfg = load_config(args)
        url = args.url or (ask("请输入 Google Drive 文件夹网址（直接回车使用配置中的 folder_id）：")
                           if sys.stdin.isatty() and not args.plan and not args.configure and not args.reconnect else "")
        if url:
            cfg["folder_id"] = folder_id_from_url(url)
        names = read_names(cfg)
        if names:
            print(f"名单文件指定 {len(names)} 个文件。")
        else:
            print("未指定 Excel/CSV 名单，将下载链接文件夹中的全部文件。")
        if args.plan and names is None:
            print("未配置 Excel/CSV 名单；下载时会联网列出文件夹中的全部文件。")
        print(f"默认每批 {cfg['batch_size']} 个；未开始的批次可输入任意正整数调整数量。")
        if names:
            print(f"首个文件：{names[0]}；末个文件：{names[-1]}")
        if args.plan:
            return 0
        apply_proxy(cfg)
        if args.configure or args.reconnect:
            exe = rclone_executable(cfg)
            if args.reconnect:
                print(f"使用项目代理重新授权连接：{cfg['remote']}", flush=True)
                return subprocess.call([exe, "config", "reconnect", cfg["remote"] + ":"])
            print(f"使用 config.json 中的代理配置。已有 {cfg['remote']} 连接请选择 e 编辑，保留现有客户端 ID 和密钥。", flush=True)
            return subprocess.call([exe, "config"])
        reject_links(CONTROL)
        reject_links(DOWNLOAD)
        with exclusive_lock():
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                                handlers=[logging.StreamHandler(), logging.FileHandler(
                                    CONTROL / "download.log", encoding="utf-8")])
            exe = rclone_executable(cfg)
            if args.check:
                preflight(exe, cfg)
                print("Excel、rclone 和共享文件夹访问检查通过。")
                return 0
            if names is None:
                names = list_remote_names(exe, cfg)
            state = load_state(cfg, names)
            cfg['interactive_batch_size'] = args.batch_size is None and sys.stdin.isatty()
            cfg['explicit_batch_size'] = args.batch_size is not None
            if state['phase'] != 'complete' and (state['downloaded'] or state['failures']):
                lo, hi = batch_bounds(state)
                print(f"继续当前批次原定的 {hi - lo} 个文件；本批完成后可输入下一批数量。")
            if args.defer_failures and state['phase'] == 'downloading':
                if not defer_failures(state):
                    return 2
            if not select_batch_size(state, cfg, ask):
                return 0
            # Pending confirmation needs no network and must remain available while offline.
            cfg['_preflight_required'] = True
            return run_batches(cfg, state, exe)
    except KeyboardInterrupt:
        print("\n已中断。已完成文件和进度均保留，重新启动可继续。")
        return 130
    except Exception as exc:
        LOG.error("停止：%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
