#!/usr/bin/env python3
"""Executor idle push: keep asking a busy supervisor until it answers.

The idle request (cmux_idle_pull) is the durable, file-only record. This
detached pusher re-asks the supervisor through the journaled ordinary-message
bridge every 60 s (constant, hence non-decreasing; up to 24 h) until the
supervisor answers: a newer task dispatch or ordinary message
from that supervisor to this executor, or an ack bound to the request sha.

Each push is a NEW marked STATUS message, never a resend of an earlier one.
An occupied/queued compose is recorded and left alone; the next tier tries
again with a new marker, so a busy Codex delays the answer but cannot strand
the executor. The executor's turn may end once the pusher is running.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cmux_idle_pull as pull  # noqa: E402

# 用户要求：60 秒没回复就再问一次，循环直到主管回复（上限 24 小时）
# 间隔恒定 60 秒，满足非递减；主管输入框仍被上一条占用时 bridge 零输入拒投，不叠加
FIRST_TIERS = (0,)
STEADY_GAP = 60
HORIZON = 24 * 3600
POLL = 10
# 测试注入假 bridge 的唯一入口：值为 .py 文件绝对路径；默认真实 cmux_bridge
BRIDGE_ENV = "CMUX_IDLE_PUSH_BRIDGE"


def schedule(horizon=HORIZON):
    out = [t for t in FIRST_TIERS if t <= horizon]
    t = FIRST_TIERS[-1] + STEADY_GAP
    while t <= horizon:
        out.append(t)
        t += STEADY_GAP
    return out


def push_dir(workspace, executor):
    return pull.state_root() / "idle-push-v1" / pull._uuid(workspace) / pull._uuid(executor)


def status_path(workspace, executor):
    return push_dir(workspace, executor) / "status.json"


def _message_answered(req):
    """An ordinary message from this supervisor to this executor after the request."""
    root = pull.state_root() / "message-dispatch-v1"
    if not root.is_dir():
        return False
    for attempt in root.glob("*/attempt-*.json"):
        try:
            value = json.loads(attempt.read_text())
            ident = value["binding"]["identity"]
            starts = [float(e["at_epoch"]) for e in value.get("events", [])
                      if e.get("phase") == "PASTE_INTENT"]
            if (str(ident.get("target_surface_uuid", "")).upper() == req["executor_uuid"]
                    and str(ident.get("caller_surface_uuid", "")).upper() == req["supervisor_uuid"]
                    and str(ident.get("workspace_uuid", "")).upper() == req["workspace_uuid"]
                    and starts and min(starts) > float(req["at_epoch"])):
                return True
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return False


def current(workspace, executor):
    """(request, sha, answer) for the live request; answer is None while unanswered."""
    path = pull.request_path(workspace, executor)
    raw = path.read_bytes()
    req = json.loads(raw)
    sha = hashlib.sha256(raw).hexdigest()
    try:
        if json.loads(pull.ack_path(workspace, executor).read_text()).get("request_sha256") == sha:
            return req, sha, "ACKED"
    except (OSError, ValueError):
        pass
    if pull._dispatched_after(req):
        return req, sha, "TASK_DISPATCHED"
    if _message_answered(req):
        return req, sha, "SUPERVISOR_MESSAGED"
    return req, sha, None


def push_text(req, tier, marker):
    me = str(Path(pull.__file__).resolve())
    return (f"STATUS: {marker} executor {req['executor_uuid']} 空闲待派发（第{tier}次催办，新消息非重发）。"
            f"上一任务 {req.get('last_task_id')} 报告已交：{req.get('report')}。"
            f"请派下一任务包；暂无可派则带理由 ack：rtk proxy {me} --ack {req['executor_uuid']} "
            f"--workspace {req['workspace_uuid']} --supervisor {req['supervisor_uuid']} "
            f"--reason '<WAITING_DEPENDENCY: ...>'")


def load_bridge():
    path = os.environ.get(BRIDGE_ENV)
    if path:
        spec = importlib.util.spec_from_file_location("cmux_idle_push_bridge", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    import cmux_bridge
    return cmux_bridge


def _write_status(workspace, executor, value):
    pull._atomic(status_path(workspace, executor), value)


def run(workspace, executor, bridge=None, clock=time.time, sleep=time.sleep, poll=POLL):
    """Push until answered, superseded-and-answered, or the horizon ends."""
    bridge = bridge or load_bridge()
    folder = push_dir(workspace, executor)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(folder / "push.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "ALREADY_RUNNING"
        _write_status(workspace, executor, dict(pid=os.getpid(), state="STARTING",
                                                updated_epoch=clock()))
        return _loop(workspace, executor, bridge, clock, sleep, poll)


def _loop(workspace, executor, bridge, clock, sleep, poll):
    pushes, sha_seen, start = [], None, None
    while True:
        try:
            req, sha, answer = current(workspace, executor)
        except (OSError, ValueError):
            answer, req, sha = "REQUEST_GONE", None, None
        if sha != sha_seen and req is not None:
            # 新请求（新任务交付）重启阶梯；同一 pusher 跟随，不让新请求无人催办
            sha_seen, start, pushes = sha, clock(), []
        state = dict(pid=os.getpid(), workspace_uuid=workspace, executor_uuid=executor,
                     request_sha256=sha, pushes=pushes, updated_epoch=clock())
        if answer:
            _write_status(workspace, executor, dict(state, state="ANSWERED", answer=answer))
            return answer
        elapsed = clock() - start
        due = [t for t in schedule() if t <= elapsed]
        if len(due) > len(pushes):
            tier = len(due)
            marker = "IDLEPUSH-%s-%d-%s" % (executor[:8], tier, sha[:8])
            try:
                result = bridge.submit_text(req.get("supervisor_ref") or req["supervisor_uuid"],
                                            push_text(req, tier, marker), marker=marker)
                outcome = "CONFIRMED" if (isinstance(result, dict) and result.get("confirmed")) else "SUBMITTED"
            except Exception as exc:  # 投递结果是记账事实，不是终止条件
                outcome = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            pushes.append(dict(tier=tier, marker=marker, at_epoch=clock(), outcome=outcome))
            state["pushes"] = pushes
        if elapsed >= schedule()[-1]:
            _write_status(workspace, executor, dict(state, state="EXHAUSTED"))
            return "EXHAUSTED"
        _write_status(workspace, executor, dict(state, state="PUSHING"))
        sleep(poll)


def alive(workspace, executor):
    """Executor Stop side: a pusher holds the lock or the request is already answered."""
    try:
        if current(workspace, executor)[2]:
            return True
    except (OSError, ValueError):
        return False
    lock = push_dir(workspace, executor) / "push.lock"
    if not lock.exists():
        return False
    with open(lock, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def spawn(workspace, executor, wait=5.0):
    """Start one detached pusher; return once it holds the lock."""
    folder = push_dir(workspace, executor)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if alive(workspace, executor):
        return "RUNNING"
    with open(folder / "push.log", "a") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--run",
                                  "--workspace", workspace, "--executor", executor],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
    child.returncode = 0  # 脱离会话的后台进程，不由本进程回收
    deadline = time.time() + wait
    while time.time() < deadline:
        if alive(workspace, executor):
            return "STARTED"
        time.sleep(0.1)
    raise OSError("idle pusher did not start; see " + str(folder / "push.log"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", action="store_true")
    p.add_argument("--workspace", required=True)
    p.add_argument("--executor", required=True)
    a = p.parse_args(argv)
    ws, ex = pull._uuid(a.workspace), pull._uuid(a.executor)
    if a.run:
        print(dt.datetime.now(dt.timezone.utc).isoformat(), run(ws, ex), flush=True)
        return 0
    print(json.dumps(json.loads(status_path(ws, ex).read_text()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
