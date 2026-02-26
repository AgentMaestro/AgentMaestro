import sys  
from pathlib import Path  
sys.path.append(str(Path.cwd() / \" "toolrunner\))  
from toolrunner.app.orchestrator import orchestrate  
from toolrunner.app.tests.test_orchestrator import _write_charter, _write_plan, FakeToolInvoker  
path=Path(\DevAgentMaestrotoolrunnerpytest_temp/test_debug\)  
path.mkdir(parents=True, exist_ok=True)  
charter=_write_charter(path)  
_write_plan(path)  
result=orchestrate(str(path), str(charter), tool_invoker=FakeToolInvoker())  
print(result) 
