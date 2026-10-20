import argparse
import re
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DECOMP_DIR = PROJECT_DIR / "decomp"
GAME_BINARY = Path.home() / ".local/share/Steam/steamapps/common/Super Hexagon/SuperHexagon"

# Ghidra output for these functions is compiled as is. They're everything gameplay reaches from
# superhex::update, minus graphics, sound output, scores, saves, and Steam, which become stubs.
COMPILED = [
    "superhex_calculateTime.c", "superhex_gameinput.c", "superhex_generickeypoll.c", "superhex_gamelogic.c",
    "gameclass_generatewave.c", "gameclass_addenemy.c", "gameclass_restart.c", "gameclass_resetwaves.c",
    "gameclass_clearenemies.c", "gameclass_restarthyper.c", "gameclass_changetostage.c", "gameclass_cleanup.c",
    "gameclass_getbesttime_stage.c", "gameclass_setbesttime.c", "gameclass_lastunlocked.c", "gameclass_levelup.c",
    "gameclass_otisrotate.c", "gameclass_nullotisrotate.c", "gameclass_seekangle.c", "gameclass_setrank.c",
    "gameclass_stageselect.c", "gameclass_updatevisualeffects.c", "gameclass_winlevel.c",
    "gameclass_secretending.c", "gameclass_savescore.c", "gameclass_checkhighscore.c",
    "gameclass_switchtonormalmode.c", "musicclass_play.c", "musicclass_playef.c", "musicclass_stop.c",
    "musicclass_fadeout.c", "musicclass_processmusicfadein.c",
    "musicclass_processmusic.c", "musicclass_processmusicfade.c", "helpclass_updateglow.c",
]

# written by hand in runtime.c, since Ghidra's return types for these are wrong or they touch
# process state the simulator replaces
HANDWRITTEN = {
    "oneRound", "ofRandom", "getExpectedFrameDelta", "getLastFrameTimeMillis", "superRemainder",
    "ofGetElapsedTimeMillis", "operator_new", "rand",
    "std__string__string", "std__string__assign", "std__string__append", "std__string__insert",
    "std__string__push_back", "std__string__substr", "ofToString", "ofSplitString",
    "musicclass__loadsongdata", "graphicsclass__setpal", "graphicsclass__changepal",
    "graphicsclass__updatepal", "graphicsclass__attach", "graphicsclass__detach", "graphicsclass__fulldetach",
}

C_WORDS = {
    "if", "while", "for", "switch", "return", "sizeof", "do", "else", "case", "goto",
    "int", "long", "float", "double", "char", "short", "void", "unsigned", "signed", "bool",
}

CLASSES = ["superhex", "gameclass", "musicclass", "graphicsclass", "scoreclass", "helpclass", "deviceclass"]


def class_methods():
    out = subprocess.run(["nm", "-C", str(GAME_BINARY)], capture_output=True, text=True, check=True).stdout
    methods = {c: set() for c in CLASSES}
    for line in out.splitlines():
        m = re.search(r" [Tt] (\w+)::(~?\w+)\(", line)
        if m and m.group(1) in methods:
            methods[m.group(1)].add(m.group(2))
    return methods


def balanced(text, start):
    # index just past the parenthesized group that opens at text[start]
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    raise ValueError("unbalanced parentheses")


# Ghidra shows a branchless float select (andnps on a compare mask) as
# `*(uint *)(addr) = ~-(uint)(cond) & (uint)value;`. In C that casts the float by value, so it
# becomes a real select on floats. Integer masks such as `& 3` don't match.
def fix_float_selects(text):
    pattern = re.compile(r"\*\(uint \*\)(\()")
    out = []
    pos = 0
    while True:
        m = pattern.search(text, pos)
        if not m:
            break
        addr_end = balanced(text, m.start(1))
        rest = re.match(r"\s*=\s*~-\(uint\)(\()", text[addr_end:])
        if not rest:
            out.append(text[pos:addr_end])
            pos = addr_end
            continue
        cond_start = addr_end + rest.start(1)
        cond_end = balanced(text, cond_start)
        value = re.match(r"\s*&\s*\(uint\)", text[cond_end:])
        if not value:
            out.append(text[pos:cond_end])
            pos = cond_end
            continue
        value_start = cond_end + value.end()
        value_end = text.index(";", value_start)
        out.append(text[pos:m.start()])
        out.append(f"*(float *){text[m.start(1):addr_end]} = {text[cond_start:cond_end]} ? 0.0f : "
                   f"(float)({text[value_start:value_end].strip()})")
        pos = value_end
    out.append(text[pos:])
    return "".join(out)


def flatten_names(text):
    # templates inside qualified names, for example std::vector<int,...>::_M_emplace_back_aux<int>
    for _ in range(4):
        text = re.sub(r"<[^<>()]*>(?=::|\()", "", text)
    text = text.replace("::~", "__dtor_")
    for _ in range(6):
        text = re.sub(r"\b([A-Za-z_]\w*)::([A-Za-z_]\w*)", r"\1__\2", text)
    # function local statics such as prepareGame()::gfxResume
    text = re.sub(r"\b(\w+)\(\)__(\w+)", r"\1__\2", text)
    return text


def convert(path, methods):
    text = path.read_text()
    m = re.search(r"/\* (\w+)::", text)
    owner = m.group(1) if m else None
    text = re.sub(r"__thiscall\s+", "", text)
    text = flatten_names(text)
    text = text.replace("std__operator==(", "std__operator_eq(")
    text = fix_float_selects(text)
    text = re.sub(r"\bthis\b", "self", text)
    if owner in methods:
        names = sorted(methods[owner], key=len, reverse=True)
        pattern = r"(?<![\w.>])(" + "|".join(re.escape(n) for n in names) + r")(?=\()"
        text = re.sub(pattern, lambda mm: f"{owner}__{mm.group(1)}", text)
    return text


def definitions(text):
    found = {}
    # the return type may sit on its own line above the name
    for m in re.finditer(r"^([A-Za-z_][\w \*]*?)\s*\n?\s*\b([A-Za-z_]\w*)\(([^;{]*?)\)\s*\n\s*\{", text, re.M):
        found[m.group(2)] = f"{m.group(1).strip()} {m.group(2)}({' '.join(m.group(3).split())});"
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(PROJECT_DIR / "sim" / "gen"))
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    methods = class_methods()
    bodies = []
    defined = {}
    for name in COMPILED:
        text = convert(DECOMP_DIR / name, methods)
        bodies.append(f"/* from decomp/{name} */\n{text}")
        defined.update(definitions(text))
    logic = "\n".join(bodies)

    code_only = re.sub(r"/\*.*?\*/", "", logic, flags=re.S)
    called = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", code_only))
    stubs = sorted(c for c in called if c not in defined and c not in HANDWRITTEN and c not in C_WORDS
                   and not re.match(r"^(LAB_|CONCAT|SUB|ZEXT|SEXT|ABS|CARRY|SBORROW|LOCK|UNLOCK)$|^LAB_", c))

    header = ["/* generated by sim/tools/ghidra2c.py, don't edit */", "#include \"prelude.h\"", ""]
    header += [proto for proto in defined.values()]
    header += ["", "/* stubs for everything gameplay doesn't need, defined in stubs.c */"]
    header += [f"long {s}();" for s in stubs]
    (out_dir / "logic.h").write_text("\n".join(header) + "\n")
    (out_dir / "logic.c").write_text("#include \"logic.h\"\n\n" + logic)
    stub_lines = ["/* generated by sim/tools/ghidra2c.py, don't edit */", "#include \"logic.h\"", ""]
    stub_lines += [f"long {s}() {{ return 0; }}" for s in stubs]
    (out_dir / "stubs.c").write_text("\n".join(stub_lines) + "\n")
    print(f"{len(defined)} functions compiled, {len(stubs)} stubs")
    for s in stubs:
        print("  stub", s)


if __name__ == "__main__":
    main()
