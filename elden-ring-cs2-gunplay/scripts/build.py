from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "sheets" / "balance.json"
DEFAULT_BASELINE = Path("work/elden-ring-analysis/regulation.bin")
DEFAULT_PARAMCRYPT = Path("work/er-deps/soulstruct/base/params/ParamCrypt/ParamCrypt.exe")
PARAMCRYPT_CONFIG = DEFAULT_PARAMCRYPT.with_name("ParamCrypt.runtimeconfig.json")
DOTNET_ROOT = Path("work/tools/dotnet-runtime").resolve()


def setup_soulstruct_imports():
    deps = Path("work/er-deps").resolve()
    sys.path.insert(0, str(deps))
    os.environ["DOTNET_ROOT"] = str(DOTNET_ROOT)
    os.environ["PATH"] = str(DOTNET_ROOT) + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    original_expanduser = Path.expanduser

    def safe_expanduser(path):
        if path.as_posix().lower() == "~/appdata/roaming/soulstruct":
            target = Path("work/soulstruct-appdata").resolve()
            target.mkdir(parents=True, exist_ok=True)
            return target
        return original_expanduser(path)

    Path.expanduser = safe_expanduser
    er = types.ModuleType("soulstruct.eldenring")
    er.__path__ = [str(deps / "soulstruct" / "eldenring")]
    sys.modules["soulstruct.eldenring"] = er


def configure_paramcrypt():
    PARAMCRYPT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    PARAMCRYPT_CONFIG.write_text(
        json.dumps({"runtimeOptions": {"tfm": "net10.0", "framework": {"name": "Microsoft.NETCore.App", "version": "10.0.0"}, "rollForward": "Major"}}),
        encoding="utf-8",
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def preflight(sheet, source, build_id):
    print("BALANCE PREFLIGHT")
    print(f"Game: {sheet['game']}")
    print(f"Required baseline build: {sheet['baseline_build_id']}")
    print(f"Detected baseline build: {build_id or 'not supplied'}")
    print(f"Source file: {source}")
    print(f"Scope: {sheet['scope']}")
    print("Balance rows:")
    for row in sheet["rows"]:
        print(f"  {row['param']}[{row['row_id']}] {row['item']}.{row['field']}: {row['baseline']} -> {row['value']} {row['unit']} — {row['reason']}")
    print("Starting loadouts:")
    for row in sheet["starting_loadouts"]:
        print(f"  {row['class']}: right hand {row['baseline_weapon']} -> {row['weapon']}; bolt slot {row['baseline_bolt']} -> {row['bolt']}")
    print("End balance preflight")
    if build_id != sheet["baseline_build_id"]:
        raise SystemExit("Preflight failed: baseline build ID does not match the sheet.")
    if not source.is_file():
        raise SystemExit(f"Preflight failed: source regulation.bin does not exist: {source}")
    if len({row["id"] for row in sheet["rows"]}) != len(sheet["rows"]):
        raise SystemExit("Preflight failed: duplicate sheet row IDs.")
    ids = {row["id"] for row in sheet["rows"]}
    for row in sheet["rows"]:
        missing = set(row["references"]) - ids
        if missing:
            raise SystemExit(f"Preflight failed: {row['id']} references missing rows {sorted(missing)}")
        required = {"id", "param", "row_id", "item", "field", "baseline", "value", "unit", "reason", "references"}
        if set(row) != required:
            raise SystemExit(f"Preflight failed: {row.get('id')} has missing or unexpected cells.")
    loadout_ids = set()
    for row in sheet["starting_loadouts"]:
        required = {"id", "class", "init_id", "origin_id", "baseline_weapon", "baseline_bolt", "weapon", "bolt", "references"}
        if set(row) != required or row["id"] in loadout_ids:
            raise SystemExit(f"Preflight failed: incomplete or duplicate starting loadout {row.get('id')}.")
        loadout_ids.add(row["id"])
        missing = set(row["references"]) - ids
        if missing:
            raise SystemExit(f"Preflight failed: {row['id']} references missing rows {sorted(missing)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--build-id", default="25080141")
    parser.add_argument("--output", type=Path, default=ROOT / "package" / "mod" / "regulation.bin")
    args = parser.parse_args()
    sheet = json.loads(SHEET.read_text(encoding="utf-8"))
    preflight(sheet, args.baseline, args.build_id)

    configure_paramcrypt()
    setup_soulstruct_imports()
    from soulstruct.containers import Binder
    from soulstruct.base.params.param import TypedParam
    from soulstruct.base.params.flags import ParamFlags1, ParamFlags2
    from soulstruct.dcx import DCXType
    from soulstruct.eldenring.params import paramdef
    import zstandard

    scratch = Path("work/er-mod-build").resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    decrypted = scratch / "regulation.decrypted.dcx"
    subprocess.run([str(DEFAULT_PARAMCRYPT.resolve()), str(args.baseline.resolve()), "decrypt", "er", str(decrypted)], check=True)
    binder = Binder.from_path(decrypted)
    original_data = {entry.path: entry.data for entry in binder.entries}
    param_types = {
        "EquipParamWeapon": paramdef.EQUIP_PARAM_WEAPON_ST,
        "CharaInitParam": paramdef.CHARACTER_INIT_PARAM,
    }
    loaded = {}
    entries = {}
    for name, row_type in param_types.items():
        entries[name] = next((e for e in binder.entries if e.path.endswith(name + ".param")), None)
        if entries[name] is None:
            raise SystemExit(f"Preflight failed: {name}.param not found.")
        loaded[name] = TypedParam(row_type).from_bytes(entries[name].get_uncompressed_data())

    print("APPLYING SHEET ROWS")
    for row in sheet["rows"]:
        param = loaded[row["param"]]
        if row["row_id"] not in param.rows:
            raise SystemExit(f"Preflight failed: missing {row['param']}[{row['row_id']}].")
        actual = getattr(param[row["row_id"]], row["field"])
        if actual != row["baseline"]:
            raise SystemExit(f"Preflight failed: {row['item']}.{row['field']} baseline is {actual!r}, expected {row['baseline']!r}.")
        setattr(param[row["row_id"]], row["field"], row["value"])
    for row in sheet["starting_loadouts"]:
        for init_id in (row["init_id"], row["origin_id"]):
            if init_id not in loaded["CharaInitParam"].rows:
                raise SystemExit(f"Preflight failed: missing CharaInitParam[{init_id}] for {row['class']}.")
            init = loaded["CharaInitParam"][init_id]
            if init.RightHandWeapon1 != row["baseline_weapon"] or init.BoltSlot1 != row["baseline_bolt"]:
                raise SystemExit(f"Preflight failed: {row['class']} starting loadout baseline changed at CharaInitParam[{init_id}].")
            init.RightHandWeapon1 = row["weapon"]
            init.BoltSlot1 = row["bolt"]
    # Soulstruct formats the four PARAM header bytes as signed bytes. Its Flags8
    # helper returns unsigned values; normalize only those two header flag bytes.
    for flags_type in (ParamFlags1, ParamFlags2):
        flags_type.pack = lambda self: (sum(2**i if enabled else 0 for i, enabled in enumerate(self.flags)) + 128) % 256 - 128
    for name, param in loaded.items():
        param_bytes = bytearray(bytes(param))
        if param.flags1.LongDataOffset:
            param_bytes[4:6] = b"\0\0"
        entries[name].set_uncompressed_data(bytes(param_bytes))

    # Avoid two upstream Soulstruct/zstandard writer incompatibilities. Serialize
    # the BND uncompressed, then rebuild the verified DCX_ZSTD wrapper from the
    # source header with only its size fields adjusted.
    binder.dcx_type = DCXType.Null
    rebuilt_bnd = bytes(binder)
    reparsed = Binder.from_bytes(rebuilt_bnd)
    if len(reparsed.entries) != len(binder.entries):
        raise SystemExit("Build failed: Binder entry count changed.")
    changed = [e.path for e in reparsed.entries if e.data != original_data[e.path]]
    expected_changed = {entries[name].path for name in entries}
    if set(changed) != expected_changed:
        raise SystemExit(f"Build failed: expected only {sorted(expected_changed)} to change, got {changed}.")

    cparams = zstandard.ZstdCompressionParameters.from_level(15, window_log=16)
    compressed = zstandard.ZstdCompressor(compression_params=cparams).compress(rebuilt_bnd)
    source_dcx = decrypted.read_bytes()
    if source_dcx[:4] != b"DCX\0" or source_dcx[40:44] != b"ZSTD":
        raise SystemExit("Build failed: unexpected source DCX wrapper.")
    header = bytearray(source_dcx[:68])
    struct.pack_into(">I", header, 28, len(rebuilt_bnd))
    struct.pack_into(">I", header, 32, len(compressed))
    packed = scratch / "regulation.decrypted.rebuilt.dcx"
    packed.write_bytes(bytes(header) + b"DCA\0\x00\x00\x00\x08" + compressed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(DEFAULT_PARAMCRYPT.resolve()), str(packed), "encrypt", "er", str(args.output.resolve())], check=True)
    verify_plain = scratch / "regulation.verify.dcx"
    subprocess.run([str(DEFAULT_PARAMCRYPT.resolve()), str(args.output.resolve()), "decrypt", "er", str(verify_plain)], check=True)
    verified = Binder.from_path(verify_plain)
    verified_params = {}
    for name, row_type in param_types.items():
        after = next(e for e in verified.entries if e.path.endswith(name + ".param"))
        verified_params[name] = TypedParam(row_type).from_bytes(after.get_uncompressed_data())
    for row in sheet["rows"]:
        assert getattr(verified_params[row["param"]][row["row_id"]], row["field"]) == row["value"]
    for row in sheet["starting_loadouts"]:
        for init_id in (row["init_id"], row["origin_id"]):
            init = verified_params["CharaInitParam"][init_id]
            assert init.RightHandWeapon1 == row["weapon"] and init.BoltSlot1 == row["bolt"]
    for e in verified.entries:
        if e.path not in expected_changed and e.data != original_data[e.path]:
            raise SystemExit(f"Build failed: untouched entry changed: {e.path}")
    print(f"PASS: encrypted package written to {args.output.resolve()}")
    print(f"PASS: {len(verified.entries)} Binder entries; only {len(expected_changed)} intended parameter entries changed")
    print(f"PASS: package SHA-256 {sha256(args.output.read_bytes())}")
    print("PASS: modified rows reopen with the values in the sheet")


if __name__ == "__main__":
    main()
