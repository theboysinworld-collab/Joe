import os
import tempfile

# isola TODAS as leituras de ~/.joe durante os testes (config, SQLite, extensões)
_tmp = tempfile.mkdtemp(prefix="joe_test_home_")
os.environ["JOE_HOME"] = _tmp
os.environ["JOE_PIPER_VOICE"] = os.path.join(_tmp, "voices", "not-installed.onnx")
