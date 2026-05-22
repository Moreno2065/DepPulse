with open("deppulse/scanners/kotlin_scanner.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the line containing 'return Language(capsule)'
insert_after = None
for i, line in enumerate(lines):
    if "return Language(capsule)" in line:
        insert_after = i
        break

if insert_after is None:
    print("ERROR: could not find 'return Language(capsule)'")
    exit(1)

print(f"Found at line {insert_after + 1}")

new_methods = """

    def __init__(self) -> None:
        self._language: Optional["TSLanguage"] = None
        self._current_source: bytes = b""

    def parse(self, source: bytes) -> "Tree":
        \"\"\"Parse source bytes into a tree-sitter Tree, storing source for extraction.\"\"\"
        from tree_sitter import Parser

        self._current_source = source
        parser = Parser(self.language)
        return parser.parse(source)

"""

lines.insert(insert_after + 1, new_methods)

with open("deppulse/scanners/kotlin_scanner.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Done")
