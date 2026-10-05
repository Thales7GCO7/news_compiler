# News Compiler — Monitor de Inteligência Jornalística

Compilador de notícias em Flask: busca notícias sobre um tópico via DDGS, cruza as fontes com similaridade sintática + IA Gemini (temperatura 0) para extrair pontos convergentes e divergentes, exibe o relatório analítico em HTML e permite baixá-lo em PDF.

## Funcionalidades

- Busca de notícias recentes multi-fonte (`modules/fetcher.py`, via DDGS).
- Página inicial com busca livre + botões de sugestão com os temas em alta (`SUGESTOES` no `app.py`): um clique envia o tema direto para `/gerar_relatorio`, sem digitar.
- Similaridade sintática determinística (`modules/similarity.py`, TF-IDF + cosseno, sem dependências novas): matriz de similaridade, grupos da mesma história e grau de convergência.
- Análise jornalística com Gemini (temperatura `0.0`, fallback entre modelos gratuitos): resumo executivo extenso e objetivo (3 a 5 parágrafos cobrindo todas as notícias), fatos convergentes (com `similaridade_media` e `confianca`) e divergentes (visões A/B com `confianca`), cada item com interpretação do motivo do acordo/conflito e frases literais das matérias (`trechos`/`trechos_a`/`trechos_b`, com fonte e nº da notícia).
- Saída JSON validada com Pydantic (`RelatorioAnalise`), com pós-validação que remove fontes inventadas e fixa os scores em 0–1.
- Relatório em HTML (Bootstrap 5) com nomes das fontes ligados às matérias, seção **Fontes consultadas** com links, explicação de **Como os graus são calculados** no rodapé e botão **"Baixar relatório em PDF"** (preto, com ícone de download).
- Exportação em PDF com `fpdf2` (puro-Python, sem dependência de sistema).
- Retry automático com espera crescente em erros transitórios da API (503/UNAVAILABLE).

## Estrutura

```
app.py                # Flask: rotas /, /gerar_relatorio (HTML), /baixar_pdf (PDF); SUGESTOES dos temas em alta
modules/
  fetcher.py          # Busca notícias (DDGS)
  similarity.py       # Similaridade TF-IDF + grupos (evidência pré-LLM)
  analyzer.py         # Análise com Gemini (temp 0) + retry + validação
templates/
  index.html          # Busca por tópico + botões de sugestão
  report.html         # Relatório + botão de download do PDF
requirements.txt
.gitignore            # Protege .env (GEMINI_API_KEY) e credenciais
```

## Stack

Flask · DDGS · google-genai (Gemini) · Pydantic · fpdf2 · python-dotenv · Bootstrap 5

## Demonstração

![Página principal com busca livre e botões de temas em alta](<media/Página Principal.png>)

![Relatório com resumo executivo extenso](<media/Página - Resumo Executivo.png>)

![Pontos convergentes e divergentes com frases literais das matérias](<media/Página - Pontos Convergentes e Pontos Divergentes.png>)

![Fontes consultadas com links e explicação de como os graus são calculados](<media/Página - Fontes consultadas e Como os graus são calculados.png>)

Vídeo do fluxo completo: [Recording 2026-10-04](<media/Recording 2026-10-04 195443.mp4>)

## Como rodar (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Crie um arquivo `.env` na raiz com sua chave:

```
GEMINI_API_KEY=sua_chave_aqui
```

Inicie e acesse `http://127.0.0.1:5000`:

```powershell
python app.py
```

Para trocar os temas dos botões da página inicial, edite a lista `SUGESTOES` no `app.py`.

## Notas

- O PDF usa a fonte DejaVu Sans de `C:\Windows\Fonts` (cobre acentos e símbolos ✓⚠). Em outro SO, ajuste o caminho em `gerar_pdf_relatorio()` no `app.py`.
- O download do PDF executa busca + análise novamente (sem cache).
- O `.env` com a `GEMINI_API_KEY` está no `.gitignore` e nunca deve ser commitado.
