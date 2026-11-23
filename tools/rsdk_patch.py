import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rsdk_scripts as R

ROOT = pathlib.Path(__file__).resolve().parent.parent

PATCH_CODE_BASE = 0x30000
PATCH_JUMP_BASE = 0x3E00
PATCH_FUNC_BASE = 400

CHANGES = [
    ("GlobalCode.bin", 15450, [-458752], [-900000]),
    ("GlobalCode.bin", 15683, [-458752], [-900000]),
]

DEATH_STATE_FUNCTION = 28
ORIGINAL_ALIAS = 400
ONE_UP_SOUND = 24
HASH_WORDS = 8


def fnv(words):
    h = 2166136261
    for w in words:
        w &= 0xFFFFFFFF
        for b in range(4):
            h ^= (w >> (8 * b)) & 0xFF
            h = (h * 16777619) & 0xFFFFFFFF
    return h


def opcode(functions, name):
    for i, (n, _) in enumerate(functions):
        if n == name:
            return i
    sys.exit("this engine has no instruction called %s" % name)


def variable(variables, name):
    if name not in variables:
        sys.exit("this engine has no variable called %s" % name)
    return ("var", R.VARARR_NONE, None, variables.index(name))


def assemble(functions, program):
    words = []
    offsets = []
    for name, operands in program:
        offsets.append(len(words))
        words += R.encode_statement((0, opcode(functions, name), operands))
    return words, offsets


def death_jingle(functions, variables):
    program = [
        ("IfNotEqual", [("int", 0), variable(variables, "object.controlMode"), ("int", -1)]),
        ("PlaySfx", [("int", ONE_UP_SOUND), ("int", 0)]),
        ("endif", []),
        ("CallFunction", [("int", ORIGINAL_ALIAS)]),
        ("return", []),
    ]
    words, offsets = assemble(functions, program)
    jumps = [offsets[2], offsets[3]]
    return words, jumps


def build(folder, engine):
    functions, variables = R.load_tables(engine, 3)
    lines = ["webport-patch 1"]
    for name, pos, old, new in CHANGES:
        info = R.load_bytecode(pathlib.Path(folder) / name)
        actual = info["code"][pos:pos + len(old)]
        if actual != old:
            sys.exit("%s at %d holds %s, expected %s: this is not the file the patch was written for" % (name, pos, actual, old))
        lines.append("set %s %d %08x %d %s" % (name, pos, fnv(old), len(new), " ".join(str(w) for w in new)))

    glob = R.load_bytecode(pathlib.Path(folder) / "GlobalCode.bin")
    original = glob["func_code"][DEATH_STATE_FUNCTION]
    head = glob["code"][original:original + HASH_WORDS]
    words, jumps = death_jingle(functions, variables)
    lines.append("code %d %d %s" % (PATCH_CODE_BASE, len(words), " ".join(str(w) for w in words)))
    lines.append("jumps %d %d %s" % (PATCH_JUMP_BASE, len(jumps), " ".join(str(j) for j in jumps)))
    lines.append("hook %d %d %d %d %d %08x" % (DEATH_STATE_FUNCTION, PATCH_CODE_BASE, PATCH_JUMP_BASE, ORIGINAL_ALIAS, HASH_WORDS, fnv(head)))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bytecode_folder")
    ap.add_argument("--engine", default=str(ROOT / "engine" / "RSDKv4"))
    ap.add_argument("--out", default=str(ROOT / "web" / "webport.patch"))
    args = ap.parse_args()
    text = build(args.bytecode_folder, args.engine)
    pathlib.Path(args.out).write_text(text, encoding="utf-8")
    print("wrote %s (%d lines)" % (args.out, len(text.splitlines())))


if __name__ == "__main__":
    main()
