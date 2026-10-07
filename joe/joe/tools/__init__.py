"""Ponto de inicialização das ferramentas centrais.

NÃO importa nada automaticamente: quem orquestra a ordem é `joe.cli._boot()`
(extensões primeiro, depois tools, providers, capacidades). Importar este pacote
não tem efeitos colaterais — evita ciclos config↔tools.
"""
