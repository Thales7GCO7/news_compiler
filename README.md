# News Compiler — Monitor de Inteligência Jornalística

Compilador de notícias em Flask: busca notícias sobre um tópico via DDGS, cruza as fontes com IA Gemini para extrair pontos convergentes e divergentes, exibe o relatório analítico em HTML e permite baixá-lo em PDF.

## Funcionalidades

- Busca de notícias recentes multi-fonte (`modules/fetcher.py`, via DDGS).
- Análise jornalística com Gemini (`gemini-3.8-flash`): resumo executivo, fatos convergentes e divergentes com as fontes de cada visão.
- Saída JSON validada com Pydantic (`RelatorioAnalise`).
- Relatório em HTML (Bootstrap 5) com botão **"Baixar relatório em PDF"** (preto, com ícone de download).
- Exportação em PDF com `fpdf2` (puro-Python, sem dependência de sistema).
- Retry automático com espera crescente em erros transitórios da API (503/UNAVAILABLE).

## Estrutura

```
app.py                # Flask: rotas /, /gerar_relatorio (HTML), /baixar_pdf (PDF)
modules/
  fetcher.py          # Busca notícias (DDGS)
  analyzer.py         # Análise com Gemini + retry
templates/
  index.html          # Busca por tópico
  report.html         # Relatório + botão de download do PDF
requirements.txt
.gitignore            # Protege .env (GEMINI_API_KEY) e credenciais
```

## Stack

Flask · DDGS · google-genai (Gemini) · Pydantic · fpdf2 · python-dotenv · Bootstrap 5

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

## Notas

- O PDF usa a fonte DejaVu Sans de `C:\Windows\Fonts` (cobre acentos e símbolos ✓⚠). Em outro SO, ajuste o caminho em `gerar_pdf_relatorio()` no `app.py`.
- O download do PDF executa busca + análise novamente (sem cache).
- O `.env` com a `GEMINI_API_KEY` está no `.gitignore` e nunca deve ser commitado.
