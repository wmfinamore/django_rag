# conftest.py
# Verifica se torch foi carregado corretamente pelo sitecustomize.py.
import sys

if "torch" not in sys.modules:
    import warnings
    warnings.warn(
        "torch não foi pré-carregado pelo sitecustomize.py. "
        "Testes marcados com @pytest.mark.slow podem falhar."
    )
