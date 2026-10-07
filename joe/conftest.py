# Desativa plugins externos problemáticos (ex.: libtmux antigo, incompatível
# com pytest 9) antes de qualquer plugin ser carregado.
import os

os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
