#!/usr/bin/env python3
"""Prepare cross-platform sync branches and diff bundles.

The script intentionally separates "prepared diff" from "successful sync".
`prepare` records the HEAD that was diffed, while `mark-success` advances the
checkpoint used by the next incremental run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Any


# STATE_DIR = Path(tempfile.gettempdir()) / "cross_platform_sync_assistant"
STATE_DIR = Path(Path.home()) / "cross_platform_sync_assistant"
STATE_FILE = STATE_DIR / "diff_checkpoints.json"


class ScriptError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", str(repo), *args]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ScriptError(f"git command failed: {shlex.join(cmd)}\n{detail}")
    return result


def git_out(repo: Path, args: list[str]) -> str:
    return run_git(repo, args).stdout.strip()


def git_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    result = run_git(resolved, ["rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip()).resolve()


def current_branch(repo: Path) -> str:
    branch = git_out(repo, ["branch", "--show-current"])
    if not branch:
        raise ScriptError(f"Detached HEAD is not supported: {repo}")
    return branch


def is_dirty(repo: Path) -> bool:
    return bool(git_out(repo, ["status", "--porcelain"]))


def ref_exists(repo: Path, ref: str) -> bool:
    return run_git(repo, ["show-ref", "--verify", "--quiet", ref], check=False).returncode == 0


def rev_exists(repo: Path, rev: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", rev], check=False).returncode == 0


def is_ancestor(repo: Path, old_rev: str, new_rev: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", old_rev, new_rev], check=False).returncode == 0


def has_upstream(repo: Path) -> bool:
    return (
        run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], check=False).returncode
        == 0
    )


def sanitize(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return (cleaned or "branch")[:100]


def repo_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]


def state_key(source_repo: Path, target_repo: Path, branch: str) -> str:
    return f"{repo_fingerprint(source_repo)}:{repo_fingerprint(target_repo)}:{branch}"


def read_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"version": 1, "entries": {}}
    with STATE_FILE.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise ScriptError(f"Invalid state file: {STATE_FILE}")
    return data


def write_state(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_FILE.with_suffix(f".{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    tmp_path.replace(STATE_FILE)


def resolve_target(source_root: Path, target_arg: str) -> Path:
    target_path = Path(target_arg).expanduser()
    if not target_path.is_absolute():
        target_path = source_root / target_path
    return git_root(target_path)


def pull_current_branch(repo: Path, operations: list[str], dry_run: bool) -> None:
    operations.append("git pull --ff-only")
    if not dry_run:
        run_git(repo, ["pull", "--ff-only"])


def checkout(repo: Path, args: list[str], operations: list[str], dry_run: bool) -> None:
    operations.append(shlex.join(["git", "checkout", *args]))
    if not dry_run:
        run_git(repo, ["checkout", *args])


def prepare_target_branch(
    target_repo: Path,
    branch: str,
    target_base: str,
    *,
    no_fetch: bool,
    no_pull: bool,
    allow_dirty: bool,
    dry_run: bool,
) -> dict[str, Any]:
    operations: list[str] = []
    warnings: list[str] = []
    starting_branch = current_branch(target_repo)

    if is_dirty(target_repo) and not allow_dirty:
        raise ScriptError(
            "Target repo has uncommitted changes. Commit/stash them first, or rerun with --allow-dirty."
        )

    if no_fetch:
        warnings.append("Skipped target git fetch because --no-fetch was set.")
    else:
        operations.append("git fetch origin")
        if not dry_run:
            run_git(target_repo, ["fetch", "origin"])

    local_exists = ref_exists(target_repo, f"refs/heads/{branch}")
    remote_exists = ref_exists(target_repo, f"refs/remotes/origin/{branch}")

    if local_exists:
        if starting_branch != branch:
            checkout(target_repo, [branch], operations, dry_run)
        if remote_exists:
            operations.append(f"git branch --set-upstream-to=origin/{branch} {branch}")
            if not dry_run:
                run_git(target_repo, ["branch", f"--set-upstream-to=origin/{branch}", branch])
        if not no_pull:
            if dry_run or has_upstream(target_repo):
                pull_current_branch(target_repo, operations, dry_run)
            else:
                warnings.append(f"Local branch {branch} has no upstream; skipped git pull.")
    elif remote_exists:
        checkout(target_repo, ["-b", branch, "--track", f"origin/{branch}"], operations, dry_run)
        if not no_pull:
            pull_current_branch(target_repo, operations, dry_run)
    else:
        if starting_branch != target_base:
            checkout(target_repo, [target_base], operations, dry_run)
        if not no_pull:
            if dry_run or has_upstream(target_repo):
                pull_current_branch(target_repo, operations, dry_run)
            else:
                warnings.append(f"Base branch {target_base} has no upstream; skipped git pull.")
        checkout(target_repo, ["-b", branch], operations, dry_run)

    final_branch = branch if dry_run else current_branch(target_repo)
    if final_branch != branch:
        raise ScriptError(f"Target branch mismatch: expected {branch}, got {final_branch}")

    return {
        "starting_branch": starting_branch,
        "target_branch": final_branch,
        "operations": operations,
        "warnings": warnings,
    }


def choose_diff_range(
    source_repo: Path,
    branch: str,
    head_hash: str,
    base_ref: str,
    requested_mode: str,
    checkpoint: dict[str, Any] | None,
) -> tuple[str, str, str | None, str, list[str]]:
    warnings: list[str] = []

    if requested_mode == "incremental" and checkpoint:
        checkpoint_hash = checkpoint.get("last_success_hash")
        if checkpoint_hash and rev_exists(source_repo, f"{checkpoint_hash}^{{commit}}"):
            if is_ancestor(source_repo, checkpoint_hash, head_hash):
                return f"{checkpoint_hash}..HEAD", "incremental", checkpoint_hash, "commits", warnings
            warnings.append(
                f"Checkpoint {checkpoint_hash} is not an ancestor of {branch}@{head_hash}; falling back to full diff."
            )
        else:
            warnings.append("Saved checkpoint is missing or invalid; falling back to full diff.")

    if not rev_exists(source_repo, base_ref):
        raise ScriptError(f"Base ref does not exist: {base_ref}")
    from_hash = git_out(source_repo, ["merge-base", base_ref, "HEAD"])
    return f"{base_ref}...HEAD", "full", from_hash, "range", warnings


def unique_lines(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        item = line.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def build_range_bundle(source_repo: Path, diff_range: str) -> dict[str, Any]:
    return {
        "bundle_strategy": "range",
        "included_commits": [],
        "changed_files": git_out(source_repo, ["diff", "--name-only", diff_range]),
        "name_status": git_out(source_repo, ["diff", "--name-status", diff_range]),
        "diff_stat": git_out(source_repo, ["diff", "--stat", diff_range]),
        "diff_patch": run_git(source_repo, ["diff", diff_range]).stdout,
    }


def build_commit_bundle(source_repo: Path, diff_range: str, base_ref: str) -> dict[str, Any]:
    commit_text = git_out(
        source_repo,
        [
            "rev-list",
            "--reverse",
            "--first-parent",
            "--no-merges",
            diff_range,
            "--not",
            base_ref,
        ],
    )
    commits = [line.strip() for line in commit_text.splitlines() if line.strip()]

    changed_files_parts: list[str] = []
    name_status_parts: list[str] = []
    diff_stat_parts: list[str] = []
    diff_patch_parts: list[str] = []

    for commit in commits:
        changed_files_parts.extend(
            unique_lines(git_out(source_repo, ["diff-tree", "--no-commit-id", "--name-only", "-r", commit]))
        )
        name_status = git_out(source_repo, ["diff-tree", "--no-commit-id", "--name-status", "-r", commit])
        if name_status:
            name_status_parts.extend(name_status.splitlines())
        diff_stat = git_out(source_repo, ["show", "--format=", "--stat", "--find-renames", commit])
        if diff_stat:
            diff_stat_parts.append(f"commit {commit}\n{diff_stat}")
        diff_patch_parts.append(
            run_git(source_repo, ["show", "--format=fuller", "--patch", "--find-renames", commit]).stdout
        )

    changed_files = "\n".join(sorted(set(changed_files_parts)))
    name_status = "\n".join(name_status_parts)
    diff_stat = "\n\n".join(diff_stat_parts)
    diff_patch = "\n".join(part.rstrip() for part in diff_patch_parts if part.strip())
    if diff_patch:
        diff_patch += "\n"

    return {
        "bundle_strategy": "commits",
        "included_commits": commits,
        "changed_files": changed_files,
        "name_status": name_status,
        "diff_stat": diff_stat,
        "diff_patch": diff_patch,
    }


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def prepare(args: argparse.Namespace) -> int:
    source_root = git_root(Path(args.source))
    target_root = resolve_target(source_root, args.target)
    source_branch = current_branch(source_root)
    source_head = git_out(source_root, ["rev-parse", "HEAD"])
    warnings: list[str] = []

    if is_dirty(source_root):
        warnings.append("Source repo has uncommitted changes; git diff bundle only covers committed HEAD.")

    if args.no_fetch:
        warnings.append("Skipped source git fetch because --no-fetch was set.")
    else:
        run_git(source_root, ["fetch", "origin", "master"])

    target_result = prepare_target_branch(
        target_root,
        source_branch,
        args.target_base,
        no_fetch=args.no_fetch,
        no_pull=args.no_pull,
        allow_dirty=args.allow_dirty,
        dry_run=args.dry_run,
    )
    warnings.extend(target_result["warnings"])

    key = state_key(source_root, target_root, source_branch)
    state = read_state()
    checkpoint = state["entries"].get(key)
    diff_range, mode_effective, from_hash, bundle_strategy, range_warnings = choose_diff_range(
        source_root,
        source_branch,
        source_head,
        args.base_ref,
        args.mode,
        checkpoint,
    )
    warnings.extend(range_warnings)

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    default_output_dir = STATE_DIR / "runs" / f"{timestamp}_{sanitize(source_branch)}_{source_head[:8]}"
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else default_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if bundle_strategy == "commits":
        bundle = build_commit_bundle(source_root, diff_range, args.base_ref)
    else:
        bundle = build_range_bundle(source_root, diff_range)

    changed_files = bundle["changed_files"]
    name_status = bundle["name_status"]
    diff_stat = bundle["diff_stat"]
    diff_patch = bundle["diff_patch"]
    included_commits = bundle["included_commits"]

    changed_files_path = output_dir / "changed_files.txt"
    name_status_path = output_dir / "name_status.txt"
    diff_stat_path = output_dir / "diff_stat.txt"
    diff_path = output_dir / "diff.patch"
    included_commits_path = output_dir / "included_commits.txt"
    summary_path = output_dir / "summary.json"

    write_text(changed_files_path, changed_files + ("\n" if changed_files else ""))
    write_text(name_status_path, name_status + ("\n" if name_status else ""))
    write_text(diff_stat_path, diff_stat + ("\n" if diff_stat else ""))
    write_text(diff_path, diff_patch)
    write_text(included_commits_path, "\n".join(included_commits) + ("\n" if included_commits else ""))

    changed_count = len([line for line in changed_files.splitlines() if line.strip()])
    script_path = Path(__file__).resolve()
    mark_success_args = [sys.executable or "python3", str(script_path), "mark-success", "--summary", str(summary_path)]

    summary: dict[str, Any] = {
        "version": 1,
        "mode_requested": args.mode,
        "mode_effective": mode_effective,
        "diff_range": diff_range,
        "bundle_strategy": bundle_strategy,
        "from_hash": from_hash,
        "to_hash": source_head,
        "included_commits": included_commits,
        "included_commits_count": len(included_commits),
        "source_branch": source_branch,
        "target_branch": target_result["target_branch"],
        "source_repo": str(source_root),
        "target_repo": str(target_root),
        "target_base": args.target_base,
        "base_ref": args.base_ref,
        "state_file": str(STATE_FILE),
        "state_key": key,
        "checkpoint_before": checkpoint,
        "changed_files_count": changed_count,
        "output_dir": str(output_dir),
        "changed_files_path": str(changed_files_path),
        "name_status_path": str(name_status_path),
        "diff_stat_path": str(diff_stat_path),
        "diff_path": str(diff_path),
        "included_commits_path": str(included_commits_path),
        "summary_path": str(summary_path),
        "target_branch_operations": target_result["operations"],
        "warnings": warnings,
        "mark_success_args": mark_success_args,
        "mark_success_command": shlex.join(mark_success_args),
    }
    write_text(summary_path, json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    if not args.dry_run:
        entry = dict(checkpoint or {})
        entry.update(
            {
                "branch": source_branch,
                "source_repo": str(source_root),
                "target_repo": str(target_root),
                "last_diff_hash": source_head,
                "last_diff_at": now_iso(),
                "last_diff_mode": mode_effective,
                "last_diff_range": diff_range,
                "last_diff_summary_path": str(summary_path),
            }
        )
        state["entries"][key] = entry
        write_state(state)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def mark_success(args: argparse.Namespace) -> int:
    if args.summary:
        summary_path = Path(args.summary).expanduser().resolve()
        with summary_path.open("r", encoding="utf-8") as fh:
            summary = json.load(fh)
        source_root = Path(summary["source_repo"]).resolve()
        target_root = Path(summary["target_repo"]).resolve()
        branch = summary["source_branch"]
        success_hash = summary["to_hash"]
        key = summary["state_key"]
        mode = summary.get("mode_effective")
        diff_range = summary.get("diff_range")
    else:
        source_root = git_root(Path(args.source))
        target_root = resolve_target(source_root, args.target)
        branch = args.branch or current_branch(source_root)
        success_hash = args.hash
        if not success_hash:
            raise ScriptError("--hash is required when --summary is not provided.")
        if not rev_exists(source_root, f"{success_hash}^{{commit}}"):
            raise ScriptError(f"Hash is not a valid source commit: {success_hash}")
        key = state_key(source_root, target_root, branch)
        mode = args.mode
        diff_range = args.diff_range

    state = read_state()
    entry = dict(state["entries"].get(key) or {})
    entry.update(
        {
            "branch": branch,
            "source_repo": str(source_root),
            "target_repo": str(target_root),
            "last_success_hash": success_hash,
            "last_success_at": now_iso(),
            "last_success_mode": mode,
            "last_success_diff_range": diff_range,
        }
    )
    if args.summary:
        entry["last_success_summary_path"] = str(summary_path)
    state["entries"][key] = entry
    write_state(state)

    print(
        json.dumps(
            {
                "state_file": str(STATE_FILE),
                "state_key": key,
                "branch": branch,
                "last_success_hash": success_hash,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def show_state(args: argparse.Namespace) -> int:
    state = read_state()
    result: dict[str, Any] = {"state_file": str(STATE_FILE), "state": state}

    if args.source and args.target:
        source_root = git_root(Path(args.source))
        target_root = resolve_target(source_root, args.target)
        branch = args.branch or current_branch(source_root)
        key = state_key(source_root, target_root, branch)
        result = {"state_file": str(STATE_FILE), "state_key": key, "entry": state["entries"].get(key)}

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare incremental cross-platform sync work.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Prepare target branch and diff bundle.")
    prepare_parser.add_argument("--source", default=".", help="Source repo path. Defaults to current directory.")
    prepare_parser.add_argument("--target", default="../home-lease-m", help="Target repo path, relative to source root by default.")
    prepare_parser.add_argument(
        "--mode",
        choices=["incremental", "full"],
        default="incremental",
        help="Incremental uses the last success checkpoint when available; full ignores it.",
    )
    prepare_parser.add_argument("--base-ref", default="origin/master", help="Base ref for full diff.")
    prepare_parser.add_argument("--target-base", default="master", help="Base branch used when creating target branch.")
    prepare_parser.add_argument("--output-dir", help="Directory for generated diff bundle.")
    prepare_parser.add_argument("--no-fetch", action="store_true", help="Skip source/target git fetch.")
    prepare_parser.add_argument("--no-pull", action="store_true", help="Skip target git pull.")
    prepare_parser.add_argument("--allow-dirty", action="store_true", help="Allow target repo with uncommitted changes.")
    prepare_parser.add_argument("--dry-run", action="store_true", help="Print planned work without mutating target branch or state.")
    prepare_parser.set_defaults(func=prepare)

    mark_parser = subparsers.add_parser("mark-success", help="Advance the incremental checkpoint after sync is verified.")
    mark_parser.add_argument("--summary", help="summary.json path generated by prepare.")
    mark_parser.add_argument("--source", default=".", help="Source repo path when --summary is not provided.")
    mark_parser.add_argument("--target", default="../home-lease-m", help="Target repo path when --summary is not provided.")
    mark_parser.add_argument("--branch", help="Branch name when --summary is not provided.")
    mark_parser.add_argument("--hash", help="Commit hash to mark when --summary is not provided.")
    mark_parser.add_argument("--mode", help="Mode label when --summary is not provided.")
    mark_parser.add_argument("--diff-range", help="Diff range label when --summary is not provided.")
    mark_parser.set_defaults(func=mark_success)

    state_parser = subparsers.add_parser("state", help="Print checkpoint state.")
    state_parser.add_argument("--source", help="Source repo path for a single entry.")
    state_parser.add_argument("--target", help="Target repo path for a single entry.")
    state_parser.add_argument("--branch", help="Branch name for a single entry.")
    state_parser.set_defaults(func=show_state)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ScriptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
