import re

def extract_symbols(content):
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    symbols = []
    brace_depth = 0
    class_stack = []
    RE_CLASS = re.compile(r"^\s*(?:class|interface|object|annotation\s+class)\s+(\w+)")
    RE_FUNC = re.compile(r"\bfun\s+(\w+)\s*(?:[<{(]|$)")
    RE_PROP = re.compile(r"^\s*(?:val|var)\s+(\w+)")

    for line in content.split("\n"):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("//"):
            continue
        opens = stripped.count("{")
        closes = stripped.count("}")
        class_match = RE_CLASS.match(stripped)
        if class_match:
            class_name = class_match.group(1)
            sym_type = "class"
            if "annotation" in stripped: sym_type = "annotation"
            elif "interface" in stripped: sym_type = "interface"
            elif "object" in stripped: sym_type = "object"
            symbols.append((sym_type, class_name))
            class_stack.append(class_name)
            brace_depth += opens - closes
            brace_depth = max(0, brace_depth)
            continue
        in_class = brace_depth >= 1
        cur = class_stack[-1] if class_stack else None
        if in_class:
            fm = RE_FUNC.search(stripped)
            if fm: symbols.append(("method", fm.group(1)))
        else:
            pm = RE_PROP.match(stripped)
            if pm: symbols.append(("property", pm.group(1)))
        brace_depth += opens - closes
        brace_depth = max(0, brace_depth)
        if closes > 0 and brace_depth == 0:
            class_stack.clear()
    return symbols

tests = [
    (r"fun hello() { println('hi') }", "function:hello"),
    (r"class MyService { fun run() {} }", "class:MyService"),
    (r"val PI = 3.14\nfun main() {}", "property:PI"),
    (r"class Person {\n    val name = ''\n    fun greet() {}\n}", "method:Person.greet"),
    (r"class Foo {\n    fun a() {}\n}\nclass Bar {\n    fun b() {}\n}", "method:Foo.a"),
    (r"// fun commentedOut() {}\nfun actual() {}", "function:actual"),
    (r"interface Callback { fun invoke() }", "interface:Callback"),
    (r"object Logger { fun log() {} }", "object:Logger"),
    (r"annotation class Config(val key: String)", "annotation:Config"),
    (r"class Outer {\n    class Inner {\n        fun innerMethod() {}\n    }\n}", "method:Inner.innerMethod"),
]

all_ok = True
for code, exp in tests:
    syms = extract_symbols(code)
    fqs = {f"{t}:{n}" for t, n in syms}
    ok = exp in fqs
    if not ok: all_ok = False
    print("OK" if ok else "FAIL", repr(exp), "in", fqs)

print("\nAll OK:", all_ok)
