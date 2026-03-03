from pathlib import Path

data=Path('control/templates/control/chat.html').read_bytes()
errors=[]
for idx,b in enumerate(data):
    if b==0xb7:
        prev=data[idx-1] if idx>0 else None
        if prev!=0xc2:
            errors.append(idx)
print(errors)

