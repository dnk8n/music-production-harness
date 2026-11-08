"""``mph`` command line. Spec: spec/components/cli.md.

Thin layer: parse arguments, build dependencies, call a module, render. No
business logic lives here.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from mph import __version__, components, config, doctor, secrets, speccheck
from mph.catalogue import commands as catalogue_commands
from mph.components import Context, Stage
from mph.platform_info import current_host
from mph.proc import SubprocessRunner

SYMBOL = {
    doctor.Status.OK: "✓",
    doctor.Status.WARN: "!",
    doctor.Status.FAIL: "✗",
    doctor.Status.SKIP: "-",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mph", description="Music Production Harness")
    parser.add_argument("--version", action="version", version=f"mph {__version__}")
    parser.add_argument("--config", type=Path, help="config file (default ~/.config/mph/...)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="one-screen summary of config and health")

    doc = sub.add_parser("doctor", help="diagnose installation and configuration")
    doc.add_argument("--stage", action="append", choices=[s.value for s in Stage], default=[])
    doc.add_argument("--json", action="store_true", help="machine-readable output")

    inst = sub.add_parser("install", help="show (or run) the install plan for a component")
    inst.add_argument("component", choices=[c.id for c in components.REGISTRY])
    inst.add_argument("--yes", action="store_true", help="run the automatable steps")

    sec = sub.add_parser("secrets", help="manage third-party credentials in the Keychain")
    sec_sub = sec.add_subparsers(dest="action", required=True)
    sec_sub.add_parser("list", help="show which credentials are set (never their values)")
    names = [c.name for c in secrets.CREDENTIALS]
    sec_sub.add_parser("set", help="guided entry of every field").add_argument(
        "name", choices=names
    )
    sec_sub.add_parser("delete", help="remove all fields").add_argument("name", choices=names)

    cat = sub.add_parser("catalogue", help="sample catalogue")
    cat_sub = cat.add_subparsers(dest="action", required=True)
    scan = cat_sub.add_parser("scan", help="index local sources (read-only, incremental)")
    scan.add_argument("--source", action="append", default=[], help="source id (repeatable)")
    scan.add_argument(
        "--workers",
        type=int,
        default=4,
        choices=range(1, 17),
        metavar="1-16",
        help="parallel hashing/probing (default 4)",
    )
    cat_sub.add_parser("stats", help="summarise what's in the catalogue")
    ana = cat_sub.add_parser("analyse", help="measure BPM, key, loudness... (cached)")
    ana.add_argument("--role", action="append", default=[], help="only this role (repeatable)")
    ana.add_argument("--pack", action="append", default=[], help="only this pack (repeatable)")
    ana.add_argument("--limit", type=int, help="at most this many sounds")
    ana.add_argument("--workers", type=int, default=4, choices=range(1, 17), metavar="1-16")
    ana.add_argument("--force", action="store_true", help="re-analyse even if cached")

    _add_search(cat_sub)
    _add_provenance(cat_sub)

    spec = sub.add_parser("spec", help="spec tooling")
    spec_sub = spec.add_subparsers(dest="action", required=True)
    chk = spec_sub.add_parser("check", help="deterministic spec gate")
    chk.add_argument("--repo", type=Path, default=Path.cwd())
    chk.add_argument("--strict", action="store_true", help="untested requirements are errors")
    return parser


def _add_search(cat_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    find = cat_sub.add_parser(
        "search",
        help="find sounds by role, tempo, key, loudness, length or words",
        description="e.g. mph catalogue search --role hat --bpm 128 open",
    )
    find.add_argument("text", nargs="*", help="words that must appear in the path or tags")
    find.add_argument(
        "--role", action="append", default=[], help="kick, hat, bass ... (repeatable)"
    )
    find.add_argument("--bpm", type=float, help="target tempo; half and double time also match")
    find.add_argument("--bpm-tolerance", type=float, default=3.0, help="percent (default 3)")
    find.add_argument("--key", help="target key, e.g. Dm, F#, 'Ab minor'; neighbours also match")
    find.add_argument("--form", choices=["oneshot", "loop", "phrase"])
    find.add_argument("--min-lufs", type=float, help="quietest integrated loudness")
    find.add_argument("--max-lufs", type=float, help="loudest integrated loudness")
    find.add_argument("--min-seconds", type=float)
    find.add_argument("--max-seconds", type=float)
    find.add_argument("--licence", help="e.g. commercial-pack, cc0")
    find.add_argument("--limit", type=int, default=20)
    find.add_argument("--json", action="store_true", help="machine-readable output")


def _add_provenance(cat_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    der = cat_sub.add_parser("derive", help="make a derivative (cached, with its recipe)")
    der.add_argument("sound", help="source-relative path or hash prefix")
    der.add_argument("recipe", choices=["trim"])
    der.add_argument("--start", type=float, default=0.0, help="seconds")
    der.add_argument("--end", type=float, required=True, help="seconds")
    der.add_argument("--stash", metavar="NAME", help="also keep it in this stash")
    keep = cat_sub.add_parser("stash", help="keep a derivative safe from cache purges")
    keep.add_argument("sound")
    keep.add_argument("name")
    cat_sub.add_parser("purge-cache", help="delete cached derivatives (stashes stay)")
    cat_sub.add_parser("refresh", help="rebuild missing derivatives from their recipes")
    cat_sub.add_parser("lineage", help="show how a sound was made").add_argument("sound")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "spec":
        return cmd_spec_check(args.repo, strict=args.strict)
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    runner = SubprocessRunner()
    host = current_host()
    store: secrets.SecretStore | None = (
        secrets.KeychainStore(runner, cfg.keychain_service) if host.is_macos else None
    )
    return dispatch(args, cfg, Context(runner, host), store)


def dispatch(
    args: argparse.Namespace, cfg: config.Config, ctx: Context, store: secrets.SecretStore | None
) -> int:
    match args.command:
        case "status":
            return cmd_status(cfg, ctx, store)
        case "doctor":
            stages = [Stage(s) for s in args.stage]
            return cmd_doctor(cfg, ctx, store, stages, as_json=args.json)
        case "install":
            return cmd_install(components.by_id(args.component), ctx, run=args.yes)
        case "catalogue":
            return catalogue_commands.dispatch(args, cfg, ctx.runner)
        case _:
            return cmd_secrets(store, args.action, getattr(args, "name", None))


def cmd_status(cfg: config.Config, ctx: Context, store: secrets.SecretStore | None) -> int:
    report = doctor.run(cfg, ctx, store)
    a = cfg.audio
    files = ", ".join(map(str, cfg.loaded_from)) or "built-in defaults only"
    print(f"mph {__version__}   config: {files}")
    print(f"data dir     {cfg.data_dir}")
    print(
        f"sources      {len(cfg.sources)} configured: "
        + (", ".join(s.id for s in cfg.sources) or "none")
    )
    print(f"production   {a.production_sample_rate} Hz, 32-bit float")
    print(
        f"delivery     {a.delivery_sample_rate} Hz, {a.delivery_bit_depth}-bit "
        f"{a.delivery_format.upper()}  (single SRC + dither point)"
    )
    print(
        f"master       {cfg.master.target_lufs_integrated} LUFS, "
        f"<= {cfg.master.max_true_peak_dbtp} dBTP"
    )
    print(
        f"health       {report.count(doctor.Status.OK)} ok, "
        f"{report.count(doctor.Status.WARN)} warnings, {report.count(doctor.Status.FAIL)} "
        f"failures  -> run `mph doctor` for details"
    )
    return 0 if report.healthy else 1


def cmd_doctor(
    cfg: config.Config,
    ctx: Context,
    store: secrets.SecretStore | None,
    stages: Sequence[Stage],
    *,
    as_json: bool,
) -> int:
    report = doctor.run(cfg, ctx, store, stages)
    if as_json:
        print(json.dumps([f.to_dict() for f in report.findings], indent=2))
    else:
        for f in report.findings:
            print(f"{SYMBOL[f.status]} {f.check:<26} {f.detail}")
            if f.fix and f.status is not doctor.Status.OK:
                print(f"  {'':<26} fix: {f.fix}")
    return 0 if report.healthy else 1


def cmd_install(component: components.Component, ctx: Context, *, run: bool) -> int:
    detection = component.detector.detect(ctx)
    if detection.found and component.version_ok(detection):
        print(f"{component.name} already installed ({detection.version or 'version unknown'})")
        return 0
    print(f"Install plan for {component.name}: {component.purpose}")
    for number, step in enumerate(component.steps, start=1):
        kind = " ".join(step.argv) if step.automatable else "manual"
        print(f"  {number}. {step.description}  [{kind}]")
    if component.docs:
        print(f"  docs: {component.docs}")
    if not run:
        print("Dry run. Re-run with --yes to execute the automatable steps.")
        return 0
    for step in component.steps:
        if not step.automatable:
            print(f"-> manual step, do this yourself: {step.description}")
            continue
        print(f"-> {' '.join(step.argv)}")
        if not ctx.runner.run(step.argv, interactive=True).ok:
            print(f"step failed: {step.description}", file=sys.stderr)
            return 1
    return 0


class TerminalPrompter:
    """Prompts on the terminal; secret fields are not echoed."""

    def say(self, text: str) -> None:
        print(text)

    def ask(self, label: str, *, secret: bool) -> str:
        return getpass.getpass(f"  {label}: ") if secret else input(f"  {label}: ")


def cmd_secrets(
    store: secrets.SecretStore | None,
    action: str,
    name: str | None,
    prompter: secrets.Prompter | None = None,
) -> int:
    if store is None:
        print("secrets need the macOS Keychain", file=sys.stderr)
        return 2
    if action == "list":
        for credential in secrets.CREDENTIALS:
            missing = secrets.missing_fields(store, credential)
            state = (
                "set"
                if not missing
                else "missing"
                if len(missing) == len(credential.fields)
                else "partial"
            )
            print(f"{credential.name:<12} {state:<8} {credential.provider}: {credential.purpose}")
        return 0
    try:
        credential = secrets.credential_for(name or "")
    except secrets.UnknownCredentialError as exc:
        print(exc, file=sys.stderr)
        return 2
    if action == "set":
        ok = secrets.setup(store, credential, prompter or TerminalPrompter())
    else:
        ok = secrets.remove(store, credential)
        print(f"{'removed' if ok else 'nothing to remove for'} {credential.name}")
    return 0 if ok else 1


def cmd_spec_check(repo: Path, *, strict: bool) -> int:
    report = speccheck.check(repo)
    for error in report.errors:
        print(f"✗ {error}")
    for item_id in report.untested:
        print(f"{'✗' if strict else '!'} {item_id} says Verify: test but no test links it")
    print(
        f"{len(report.items)} items, {len(report.adrs)} ADRs, {len(report.errors)} errors, "
        f"{len(report.untested)} untested"
    )
    return 0 if report.ok and not (strict and report.untested) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
