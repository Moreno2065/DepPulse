import re
RE_FUNC = re.compile(r'\bfun\s+(\w+)\s*(?:[<{(]|$)')
content = 'fun hello() { println("hi") }'
match = RE_FUNC.search(content)
print('match:', match, 'group:', match.group(1) if match else None)

# Also test class regex
RE_CLASS = re.compile(r'^\s*(?:class|interface|object|annotation\s+class)\s+(\w+)', re.MULTILINE)
content2 = 'class MyService { fun run() {} }'
match2 = RE_CLASS.search(content2)
print('class match:', match2, 'group:', match2.group(1) if match2 else None)

# Test multiline class
content3 = 'class MyService {\n    fun run() {}\n}'
match3 = RE_CLASS.search(content3)
print('class multiline match:', match3, 'group:', match3.group(1) if match3 else None)
