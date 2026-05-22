from deppulse.scanners.kotlin_scanner import _extract_symbols_regex

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
    symbols = _extract_symbols_regex(code)
    fqs = {s.fully_qualified for s in symbols}
    ok = expected_fq in fqs
    status = "OK" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"{status}: {expected_fq!r} in {fqs!r}")

print()
print("All OK:", all_ok)
