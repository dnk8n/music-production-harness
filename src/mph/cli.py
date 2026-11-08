"""``mph`` command line. Spec: spec/components/cli.md.

Thin layer: parse arguments, build dependencies, call a module, render. No
business logic lives here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from mph import __version__, components, config, doctor, speccheck
from mph.components import Context, Stage
from mph.platform_info import current_host
from mph.proc import SubprocessRunner
from mph.secrets import KNOWN_SECRETS, KeychainStore, SecretStore, UnknownSecretError

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

    sec = sub.add_parser("secrets", help="manage Keychain secrets")
    sec_sub = sec.add_subparsers(dest="action", required=True)
    sec_sub.add_parser("list")
    for action in ("set", "delete"):
        sec_sub.add_parser(action).add_argument("name", choices=[s.name for s in KNOWN_SECRETS])

    spec = sub.add_parser("spec", help="spec tooling")
    spec_sub = spec.add_subparsers(dest="action", required=True)
    chk = spec_sub.add_parser("check", help="deterministic spec gate")
    chk.add_argument("--repo", type=Path, default=Path.cwd())
    chk.add_argument("--strict", action="store_true", help="untested requirements are errors")
    return parser


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
    store: SecretStore | None = (
        KeychainStore(runner, cfg.keychain_service) if host.is_macos else None
    )
    ctx = Context(runner, host)
    match args.command:
        case "status":
            return cmd_status(cfg, ctx, store)
        case "doctor":
            stages = [Stage(s) for s in args.stage]
            return cmd_doctor(cfg, ctx, store, stages, as_json=args.json)
        case "install":
            return cmd_install(components.by_id(args.component), ctx, run=args.yes)
        case _:
            return cmd_secrets(store, args.action, getattr(args, "name", None))


def cmd_status(cfg: config.Config, ctx: Context, store: SecretStore | None) -> int:
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
    store: SecretStore | None,
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


def cmd_secrets(store: SecretStore | None, action: str, name: str | None) -> int:
    if store is None:
        print("secrets need the macOS Keychain", file=sys.stderr)
        return 2
    try:
        if action == "list":
            for spec in KNOWN_SECRETS:
                state = "present" if store.exists(spec.name) else "missing"
                print(f"{spec.name:<20} {state:<8} {spec.purpose}")
            return 0
        assert name is not None  # argparse guarantees a name for set/delete  # noqa: S101
        ok = store.set_interactive(name) if action == "set" else store.delete(name)
    except UnknownSecretError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"{action} {name}: {'done' if ok else 'failed'}")
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
