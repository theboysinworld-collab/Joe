import os
import tempfile

# isola TODAS as leituras de ~/.joe durante os testes (config, SQLite, extensões)
_tmp = tempfile.mkdtemp(prefix="joe_test_home_")
os.environ["JOE_HOME"] = _tmp
