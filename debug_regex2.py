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

        # Class declarations
        class_match = RE_CLASS.match(stripped)
        if class_match:
            class_name = class_match.group(1)
            sym_type = "class"
            if "annotation" in stripped:
                sym_type = "annotation"
            elif "interface" in stripped:
                sym_type = "interface"
            elif "object" in stripped:
                sym_type = "object"
            symbols.append(ExtractedSymbol(sym_type, class_name, f"{sym_type}:{class_name}"))
            class_stack.append(class_name)
            # Only count braces AFTER the class declaration
            if opens > 0:
                brace_depth += opens - closes
                brace_depth = max(0, brace_depth)
            continue

        in_class_body = brace_depth >= 1
        current_class = class_stack[-1] if class_stack else None

        if in_class_body:
            func_match = RE_FUNC.search(stripped)
            if func_match:
                symbols.append(ExtractedSymbol("method", func_match.group(1),
                    f"method:{current_class}.{func_match.group(1)}"))

        if not in_class_body:
            prop_match = RE_PROP.match(stripped)
            if prop_match:
                symbols.append(ExtractedSymbol("property", prop_match.group(1),
                    f"property:{prop_match.group(1)}"))

        brace_depth += opens - closes
        brace_depth = max(0, brace_depth)

        if closes > 0 and brace_depth == 0:
            class_stack.clear()

    return symbols

class ExtractedSymbol:
    def __init__(self, symbol_type, name, fully_qualified):
        self.symbol_type = symbol_type
        self.name = name
        self.fully_qualified = fully_qualified

tests = [
    (r'fun hello() { println("hi") }', "function:hello"),
    (r'class MyService { fun run() {} }', "class:MyService"),
    (r'val PI = 3.14\nfun main() {}', "property:PI"),
    (r'class Person {\n    val name = ""\n    fun greet() {}\n}', "method:Person.greet"),
    (r'class Foo {\n    fun a() {}\n}\nclass Bar {\n    fun b() {}\n}', "method:Foo.a"),
    (r'// fun commentedOut() {}\nfun actual() {}', "function:actual"),
    (r'interface Callback { fun invoke() }', "class:Callback"),
    (r'object Logger { fun log() {} }', "class:Logger"),
    (r'annotation class Config(val key: String)', "class:Config"),
    (r'class Outer {\n    class Inner {\n        fun innerMethod() {}\n    }\n}', "method:Inner.innerMethod"),
]

all_ok = True
for code, expected_fq in tests:
    symbols = extract_symbols(code)
    fqs = {s.fully_qualified for s in symbols}
    ok = expected_fq in fqs
    status = "OK" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"{status}: {expected_fq!r} in {fqs!r}")

print("\nAll OK:", all_ok)
