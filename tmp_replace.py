from pathlib import Path
path=Path('PROJECT_CHECKLIST.md')
text=path.read_text()
old='- [x] Tool loop + incremental continuation with \\nunction_call_output'
new='- [x] Tool loop + incremental continuation with unction_call_output'
if old not in text:
    raise SystemExit('old missing')
path.write_text(text.replace(old,new,1))
