import argparse
import bisect
import collections
import pathlib
import re
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

CODE_SENTINEL = 0x40000 - 1
JUMP_SENTINEL = 0x4000 - 1

SCRIPTVAR_VAR, SCRIPTVAR_INTCONST, SCRIPTVAR_STRCONST = 1, 2, 3
VARARR_NONE, VARARR_ARRAY, VARARR_ENTNOPLUS1, VARARR_ENTNOMINUS1 = 0, 1, 2, 3

JUMP_OPS = {
    "IfEqual", "IfGreater", "IfGreaterOrEqual", "IfLower", "IfLowerOrEqual", "IfNotEqual",
    "WEqual", "WGreater", "WGreaterOrEqual", "WLower", "WLowerOrEqual", "WNotEqual", "switch",
}


def revision_flags(revision):
    return {
        "RSDK_REVISION": revision,
        "RETRO_REV00": int(revision == 0),
        "RETRO_REV01": int(revision >= 1),
        "RETRO_REV02": int(revision >= 2),
        "RETRO_REV03": int(revision >= 3),
        "RETRO_USE_HAPTICS": 1,
        "RETRO_USE_ORIGINAL_CODE": 0,
        "RETRO_USE_MOD_LOADER": 1,
        "RETRO_USE_NETWORKING": 1,
    }


def eval_condition(expr, flags):
    expr = re.sub(r"//.*", "", expr)
    expr = re.sub(r"/\*.*?\*/", "", expr)
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"!(?!=)", " not ", expr)
    expr = re.sub(r"defined\s*\(\s*(\w+)\s*\)", lambda m: str(int(flags.get(m.group(1), 0) != 0)), expr)
    expr = re.sub(r"[A-Za-z_]\w*", lambda m: m.group(0) if m.group(0) in ("and", "or", "not") else str(flags.get(m.group(0), 0)), expr)
    return bool(eval(expr, {"__builtins__": {}}, {}))


def active_lines(block, flags):
    stack = []
    for line in block.split("\n"):
        s = line.strip()
        if s.startswith("#if"):
            cond = eval_condition(s[3:].strip(), flags)
            stack.append([cond, cond])
            continue
        if s.startswith("#elif"):
            top = stack[-1]
            cond = (not top[1]) and eval_condition(s[5:].strip(), flags)
            top[0] = cond
            top[1] = top[1] or cond
            continue
        if s.startswith("#else"):
            top = stack[-1]
            top[0] = not top[1]
            top[1] = True
            continue
        if s.startswith("#endif"):
            stack.pop()
            continue
        if s.startswith("#"):
            continue
        if all(level[0] for level in stack):
            yield line


def load_tables(engine_dir, revision):
    text = (pathlib.Path(engine_dir) / "Script.cpp").read_text(encoding="utf-8", errors="replace")
    flags = revision_flags(revision)

    a = text.index("const FunctionInfo functions[] = {")
    b = text.index("};", a)
    functions = []
    for line in active_lines(text[a:b], flags):
        m = re.search(r'FunctionInfo\("([^"]+)",\s*(\d+)\)', line)
        if m:
            functions.append((m.group(1), int(m.group(2))))

    a = text.index("const char variableNames[][0x20] = {")
    b = text.index("};", a)
    variables = []
    for line in active_lines(text[a:b], flags):
        line = re.sub(r"//.*", "", line)
        for m in re.finditer(r'"([^"]*)"', line):
            variables.append(m.group(1))
    return functions, variables


def read_blocks(data, pos, count):
    out = []
    remaining = count
    while remaining > 0:
        flag = data[pos]
        pos += 1
        n = flag & 0x7F
        if n == 0 or n > remaining:
            raise ValueError("bad block size at byte %d" % (pos - 1))
        if flag >= 0x80:
            for _ in range(n):
                out.append(struct.unpack_from("<i", data, pos)[0])
                pos += 4
        else:
            for _ in range(n):
                out.append(data[pos])
                pos += 1
        remaining -= n
    return out, pos


def load_gameconfig(path):
    p = pathlib.Path(path)
    if not p.exists():
        return None
    d = p.read_bytes()
    pos = 0

    def read_str():
        nonlocal pos
        n = d[pos]
        pos += 1
        s = d[pos:pos + n].decode("latin1")
        pos += n
        return s

    read_str()
    read_str()
    pos += 0x60 * 3
    count = d[pos]
    pos += 1
    objects = [read_str() for _ in range(count)]
    for _ in range(count):
        read_str()
    var_count = d[pos]
    pos += 1
    globals_ = []
    for _ in range(var_count):
        name = read_str()
        value = struct.unpack_from("<i", d, pos)[0]
        pos += 4
        globals_.append((name, value))
    return {"objects": objects, "globals": globals_}


def encode_statement(stmt):
    pos, op, operands = stmt
    words = [op]
    for o in operands:
        if o[0] == "var":
            _, arr, index, var = o
            words += [SCRIPTVAR_VAR, arr]
            if index is not None:
                words += [index[0], index[1]]
            words.append(var)
        elif o[0] == "int":
            words += [SCRIPTVAR_INTCONST, o[1]]
        else:
            text = o[1].encode("latin1")
            words += [SCRIPTVAR_STRCONST, len(text)]
            packed = [0] * (len(text) // 4 + 1)
            for c, b in enumerate(text + b'"'):
                packed[c // 4] |= b << (24 - 8 * (c % 4))
            words += [w - (1 << 32) if w >= (1 << 31) else w for w in packed]
    return words


def write_blocks(values):
    out = bytearray()
    i = 0
    while i < len(values):
        wide = not (0 <= values[i] <= 255)
        j = i
        while j < len(values) and (not (0 <= values[j] <= 255)) == wide and j - i < 127:
            j += 1
        run = values[i:j]
        if wide:
            out.append(0x80 | len(run))
            for v in run:
                out += struct.pack("<i", v)
        else:
            out.append(len(run))
            out += bytes(run)
        i = j
    return bytes(out)


def write_bytecode(info):
    out = bytearray()
    out += struct.pack("<I", len(info["code"]))
    out += write_blocks(info["code"])
    out += struct.pack("<I", len(info["jumps"]))
    out += write_blocks(info["jumps"])
    out += struct.pack("<H", len(info["script_code"]))
    for trio in info["script_code"]:
        out += struct.pack("<III", *trio)
    for trio in info["script_jump"]:
        out += struct.pack("<III", *trio)
    out += struct.pack("<H", len(info["func_code"]))
    out += struct.pack("<%dI" % len(info["func_code"]), *info["func_code"])
    out += struct.pack("<%dI" % len(info["func_jump"]), *info["func_jump"])
    return bytes(out)


def load_bytecode(path):
    data = pathlib.Path(path).read_bytes()
    pos = 0
    code_size = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    code, pos = read_blocks(data, pos, code_size)
    jump_size = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    jumps, pos = read_blocks(data, pos, jump_size)
    script_count = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    code_ptrs = [struct.unpack_from("<III", data, pos + 12 * i) for i in range(script_count)]
    pos += 12 * script_count
    jump_ptrs = [struct.unpack_from("<III", data, pos + 12 * i) for i in range(script_count)]
    pos += 12 * script_count
    func_count = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    func_code = list(struct.unpack_from("<%dI" % func_count, data, pos))
    pos += 4 * func_count
    func_jump = list(struct.unpack_from("<%dI" % func_count, data, pos))
    pos += 4 * func_count
    return {
        "code": code,
        "jumps": jumps,
        "script_code": code_ptrs,
        "script_jump": jump_ptrs,
        "func_code": func_code,
        "func_jump": func_jump,
        "consumed": pos,
        "size": len(data),
    }


class DecodeError(Exception):
    pass


def decode_one(code, pos, functions):
    n = len(code)
    start = pos
    op = code[pos]
    pos += 1
    if not 0 <= op < len(functions):
        raise DecodeError("bad opcode %d at word %d" % (op, start))
    name, size = functions[op]
    operands = []
    for _ in range(size):
        if pos >= n:
            raise DecodeError("ran off the end at word %d" % start)
        kind = code[pos]
        pos += 1
        if kind == SCRIPTVAR_VAR:
            arr = code[pos]
            pos += 1
            index = None
            if arr == VARARR_NONE:
                pass
            elif arr in (VARARR_ARRAY, VARARR_ENTNOPLUS1, VARARR_ENTNOMINUS1):
                index = (code[pos], code[pos + 1])
                pos += 2
            else:
                raise DecodeError("bad array kind %d at word %d" % (arr, start))
            var = code[pos]
            pos += 1
            operands.append(("var", arr, index, var))
        elif kind == SCRIPTVAR_INTCONST:
            operands.append(("int", code[pos]))
            pos += 1
        elif kind == SCRIPTVAR_STRCONST:
            length = code[pos]
            pos += 1
            words = code[pos:pos + length // 4 + 1]
            if length < 0 or len(words) != length // 4 + 1:
                raise DecodeError("bad string at word %d" % start)
            chars = [(words[c // 4] >> (24 - 8 * (c % 4))) & 0xFF for c in range(length)]
            pos += length // 4 + 1
            operands.append(("str", bytes(chars).decode("latin1")))
        else:
            raise DecodeError("bad operand kind %d at word %d" % (kind, start))
    return (start, op, operands), pos


def decode_sub(code, start, functions, max_target):
    statements = []
    pos = start
    while True:
        if pos >= len(code):
            raise DecodeError("sub starting at word %d never reaches its end" % start)
        stmt, pos = decode_one(code, pos, functions)
        statements.append(stmt)
        if functions[stmt[1]][0] in ("End", "return") and pos > max_target:
            return statements


GLOBAL_NAMES = []
OBJECT_NAMES = []


def fmt_operand(operand, variables, is_label=False):
    kind = operand[0]
    if kind == "int":
        return ("L%d" % operand[1]) if is_label else str(operand[1])
    if kind == "str":
        return '"%s"' % operand[1]
    _, arr, index, var = operand
    name = variables[var] if 0 <= var < len(variables) else "var%d" % var
    if arr == VARARR_NONE:
        return name
    is_reg, value = index
    inner = ("arrayPos%d" % value) if is_reg == 1 else str(value)
    if name.lower() == "global" and is_reg != 1 and 0 <= value < len(GLOBAL_NAMES):
        return "Global.%s" % GLOBAL_NAMES[value]
    if arr == VARARR_ARRAY:
        return "%s[%s]" % (name, inner)
    if arr == VARARR_ENTNOPLUS1:
        return "%s[this+%s]" % (name, inner)
    return "%s[this-%s]" % (name, inner)


def format_statement(stmt, functions, variables):
    pos, op, operands = stmt
    name = functions[op][0]
    parts = []
    for i, o in enumerate(operands):
        parts.append(fmt_operand(o, variables, is_label=(i == 0 and name in JUMP_OPS)))
    return "%6d  %s %s" % (pos, name, ", ".join(parts)) if parts else "%6d  %s" % (pos, name)


def is_standalone(path, base_code):
    info = load_bytecode(path)
    ptrs = [p for trio in info["script_code"] for p in trio if p != CODE_SENTINEL]
    ptrs += [p for p in info["func_code"] if p != CODE_SENTINEL]
    return bool(ptrs) and all(p < base_code for p in ptrs)


def sub_table(info, base_code, base_jump, own_code_len, own_jump_len):
    subs = []
    for i, trio in enumerate(info["script_code"]):
        label_name = "script%d" % i
        if 0 <= i < len(OBJECT_NAMES):
            label_name = "script%d(%s)" % (i, OBJECT_NAMES[i])
        for k, label in enumerate(("main", "draw", "startup")):
            subs.append(("%s.%s" % (label_name, label), trio[k], info["script_jump"][i][k]))
    for i, c in enumerate(info["func_code"]):
        subs.append(("function%d" % i, c, info["func_jump"][i]))
    own = []
    for name, c, j in subs:
        if c == CODE_SENTINEL or c < base_code or c >= base_code + own_code_len:
            continue
        own.append((name, c - base_code, (j - base_jump) if j != JUMP_SENTINEL else None))
    return own


def owners_by_jump(own):
    best = {}
    for name, c, j in own:
        if j is None:
            continue
        if j not in best or c > best[j][1]:
            best[j] = (name, c, j)
    return [best[j] for j in sorted(best)]


def analyse(path, functions, variables, base_code, base_jump, report):
    info = load_bytecode(path)
    code, jumps = info["code"], info["jumps"]
    own = sub_table(info, base_code, base_jump, len(code), len(jumps))
    problems = []
    ordered = owners_by_jump(own)
    bounds = [s[2] for s in ordered] + [len(jumps)]
    max_target = {}
    for idx, (name, c, j) in enumerate(ordered):
        targets = [c + jumps[k] for k in range(j, bounds[idx + 1])]
        max_target[name] = max(targets) if targets else c
    decoded = {}
    skip = set()
    for name, c, j in sorted(own, key=lambda s: s[1]):
        if c in decoded:
            continue
        try:
            stmts = decode_sub(code, c, functions, max_target.get(name, c))
        except (DecodeError, IndexError) as e:
            problems.append("%s: %s" % (name, e))
            continue
        for stmt in stmts:
            decoded.setdefault(stmt[0], stmt)
            if functions[stmt[1]][0] == "switch" and stmt[2] and stmt[2][0][0] == "int":
                skip.add((name, stmt[2][0][1]))
                skip.add((name, stmt[2][0][1] + 1))
    info["skip"] = skip
    statements = sorted(decoded.values())
    starts = set(decoded)
    if info["consumed"] != info["size"]:
        problems.append("file has %d unread bytes" % (info["size"] - info["consumed"]))
    for idx, (name, c, j) in enumerate(ordered):
        for k in range(j, bounds[idx + 1]):
            if (name, k - j) in skip:
                continue
            target = c + jumps[k]
            if target not in starts:
                problems.append("%s jump %d lands at word %d which is not an instruction" % (name, k - j, target))
    report.append((path.name, len(code), len(statements), len(own), problems))
    return info, statements, own, ordered


def statement_length(stmt):
    n = 1
    for o in stmt[2]:
        if o[0] == "int":
            n += 2
        elif o[0] == "str":
            n += 2 + len(o[1]) // 4 + 1
        else:
            n += 3 + (2 if o[1] != VARARR_NONE else 0)
    return n


def write_listing(out_path, name, info, statements, own, ordered, functions, variables, base_code):
    by_start = {}
    for sub, c, j in own:
        by_start.setdefault(c, []).append(sub)
    labels = {}
    bounds = [s[2] for s in ordered] + [len(info["jumps"])]
    for idx, (sub, c, j) in enumerate(ordered):
        for k in range(j, bounds[idx + 1]):
            if (sub, k - j) in info["skip"]:
                continue
            labels.setdefault(c + info["jumps"][k], []).append((sub, k - j))
    lines = ["file %s: %d words, %d instructions" % (name, len(info["code"]), len(statements)), ""]
    next_pos = 0
    for stmt in statements:
        pos = stmt[0]
        if pos > next_pos:
            lines.append("")
            lines.append("      (%d words of data, words %d-%d)" % (pos - next_pos, next_pos, pos - 1))
        next_pos = pos + statement_length(stmt)
        for sub in by_start.get(pos, []):
            lines.append("")
            lines.append("== %s ==" % sub)
        for sub, k in labels.get(pos, []):
            lines.append("      L%d:" % k)
        lines.append(format_statement(stmt, functions, variables))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_roundtrip(files, functions, base_code, base_jump):
    print("")
    print("round trip: decode every instruction, write it back, compare with the original")
    failures = 0
    for path in files:
        raw = path.read_bytes()
        info = load_bytecode(path)
        file_ok = write_bytecode(info) == raw
        is_global = path.name == "GlobalCode.bin"
        standalone = (not is_global) and is_standalone(path, base_code)
        bc, bj = (0, 0) if (is_global or standalone) else (base_code, base_jump)
        bad_statements = 0
        count = 0
        try:
            _, statements, _, _ = analyse(path, functions, [], bc, bj, [])
        except (DecodeError, ValueError, IndexError, struct.error):
            statements = []
        for stmt in statements:
            count += 1
            words = encode_statement(stmt)
            if info["code"][stmt[0]:stmt[0] + len(words)] != words:
                bad_statements += 1
        ok = file_ok and bad_statements == 0 and count > 0
        failures += not ok
        print("  %-16s file bytes %-9s instructions %6d  mismatches %d  %s" % (path.name, "identical" if file_ok else "DIFFERENT", count, bad_statements, "OK" if ok else "FAILED"))
    print("round trip %s" % ("passed for every file" if not failures else "FAILED for %d files" % failures))


def run_census(files, functions, variables, base_code, base_jump, pattern):
    regex = re.compile(pattern)
    per_script = collections.Counter()
    total = 0
    every = 0
    for path in files:
        is_global = path.name == "GlobalCode.bin"
        standalone = (not is_global) and is_standalone(path, base_code)
        bc, bj = (0, 0) if (is_global or standalone) else (base_code, base_jump)
        try:
            info, statements, own, ordered = analyse(path, functions, variables, bc, bj, [])
        except (DecodeError, ValueError, IndexError, struct.error):
            continue
        starts = sorted((c, name) for name, c, j in own)
        keys = [c for c, _ in starts]
        for stmt in statements:
            every += 1
            text = functions[stmt[1]][0] + " " + " ".join(fmt_operand(o, variables) for o in stmt[2])
            if not regex.search(text):
                continue
            i = bisect.bisect_right(keys, stmt[0]) - 1
            sub = starts[i][1] if i >= 0 else "?"
            sub = re.sub(r"\.(main|draw|startup)$", "", sub)
            per_script[(path.stem, sub)] += 1
            total += 1
    print("")
    print("census for /%s/: %d of %d instructions match" % (pattern, total, every))
    for (stem, sub), n in per_script.most_common(40):
        print("  %5d  %s: %s" % (n, stem, sub))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bytecode_folder")
    ap.add_argument("--engine", default=str(ROOT / "engine" / "RSDKv4"))
    ap.add_argument("--out", default=str(ROOT / "script_listing"))
    ap.add_argument("--revision", type=int, default=None)
    ap.add_argument("--gameconfig", default=None)
    ap.add_argument("--census", default=None)
    ap.add_argument("--roundtrip", action="store_true")
    args = ap.parse_args()

    cfg = load_gameconfig(args.gameconfig or (pathlib.Path(args.bytecode_folder).parent / "Data" / "Game" / "GameConfig.bin"))
    if cfg:
        GLOBAL_NAMES[:] = [n for n, _ in cfg["globals"]]
        OBJECT_NAMES[:] = cfg["objects"]
        print("game config: %d global objects, %d global variables" % (len(cfg["objects"]), len(cfg["globals"])))
    folder = pathlib.Path(args.bytecode_folder)
    files = sorted(folder.glob("*.bin"))
    if not files:
        sys.exit("no .bin files found in " + str(folder))
    global_path = folder / "GlobalCode.bin"
    if not global_path.exists():
        sys.exit("GlobalCode.bin not found in " + str(folder))

    revisions = [args.revision] if args.revision is not None else [3, 2, 1, 0]
    chosen = None
    best = None
    for rev in revisions:
        functions, variables = load_tables(args.engine, rev)
        try:
            trial = []
            analyse(global_path, functions, variables, 0, 0, trial)
            score = len(trial[0][4])
        except (DecodeError, ValueError, IndexError, struct.error):
            score = 10 ** 9
        if best is None or score < best:
            best = score
            chosen = (rev, functions, variables)
    rev, functions, variables = chosen
    print("revision %d: %d instructions, %d variable names" % (rev, len(functions), len(variables)))

    g = load_bytecode(global_path)
    base_code, base_jump = len(g["code"]), len(g["jumps"])
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = []
    for path in files:
        is_global = path.name == "GlobalCode.bin"
        standalone = (not is_global) and is_standalone(path, base_code)
        bc, bj = (0, 0) if (is_global or standalone) else (base_code, base_jump)
        try:
            info, statements, own, ordered = analyse(path, functions, variables, bc, bj, report)
        except (DecodeError, ValueError, IndexError, struct.error) as e:
            report.append((path.name, 0, 0, 0, ["could not decode: %s" % e]))
            continue
        write_listing(out / (path.stem + ".txt"), path.name, info, statements, own, ordered, functions, variables, bc)
    if args.roundtrip:
        run_roundtrip(files, functions, base_code, base_jump)
    if args.census:
        run_census(files, functions, variables, base_code, base_jump, args.census)
    bad = 0
    for name, words, count, subs, problems in report:
        status = "OK" if not problems else "PROBLEMS"
        print("%-16s %7d words  %6d instructions  %4d scripts/functions  %s" % (name, words, count, subs, status))
        for p in problems[:5]:
            print("      " + p)
        bad += bool(problems)
    print("Listings written to %s" % out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
