from pathlib import Path
path=Path('control/templates/control/chat.html')
data=path.read_bytes()
for idx in range(980, 1035):
    print(idx, hex(data[idx]))

