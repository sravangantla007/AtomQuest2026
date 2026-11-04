import re
from collections import defaultdict

lines = open('.pio/build/stm8s003f3/firmware.map').readlines()
symbols = []

in_code = False
for line in lines:
    if 'Area' in line and 'CODE' in line:
        in_code = True
    elif in_code and 'Area' in line:
        in_code = False
    
    if in_code:
        m = re.match(r'^\s+([0-9A-Fa-f]{8})\s+(\S+)\s+(\S+)', line)
        if m:
            addr = int(m.group(1), 16)
            name = m.group(2)
            module = m.group(3)
            symbols.append((addr, name, module))

symbols.sort()
module_sizes = defaultdict(int)
symbol_details = []

for i in range(len(symbols)):
    addr, name, mod = symbols[i]
    if i + 1 < len(symbols):
        size = symbols[i+1][0] - addr
    else:
        size = 0
    module_sizes[mod] += size
    symbol_details.append((size, name, mod))

print(f"{'Module / Component':<32} {'Bytes':<10} {'% of Flash (8KB)':<18}")
print("=" * 60)
for mod, sz in sorted(module_sizes.items(), key=lambda x: x[1], reverse=True):
    pct = (sz / 8192) * 100
    print(f"{mod:<32} {sz:<10} {pct:.1f}%")

print("\nTop 15 Largest Individual Functions/Symbols:")
print("=" * 60)
for sz, name, mod in sorted(symbol_details, key=lambda x: x[0], reverse=True)[:15]:
    pct = (sz / 8192) * 100
    print(f"{name:<30} {mod:<20} {sz:<8} {pct:.1f}%")

