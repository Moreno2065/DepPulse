# Read with LF preservation
with open("deppulse/scanners/kotlin_scanner.py", "rb") as f:
    content = f.read()

# Replace CRLF with LF
content = content.replace(b"\r\n", b"\n")

# Write back in binary mode
with open("deppulse/scanners/kotlin_scanner.py", "wb") as f:
    f.write(content)

# Verify
with open("deppulse/scanners/kotlin_scanner.py", "rb") as f:
    content = f.read()
print(f"CRLF count: {content.count(b'\\r\\n')}")
print(f"LF count: {content.count(b'\\n')}")

# Check line 118
lines = content.split(b"\n")
print(f"Line 118: {lines[117]}")
