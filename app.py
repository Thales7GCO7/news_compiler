from flask import Flask, render_template, request, send_file
from fpdf import FPDF
import io
from modules.fetcher import buscar_noticias
from modules.analyzer import analisar_noticias

app = Flask(__name__)

SUGESTOES = [
    "Economia brasileira",
    "Inteligência artificial",
    "Mudanças climáticas",
    "Eleições 2026",
    "Saúde pública",
    "Tecnologia e inovação",
    "Segurança pública",
    "Mercado financeiro",
]


EXPLICACAO_GRAUS = (
    "Grau de convergência (0 a 1): confiança de que 2 ou mais fontes distintas concordam "
    "no mesmo fato, por sintaxe e semântica do título e do resumo (LLM com temperatura 0). "
    "Só entra o ponto com ao menos uma frase copiada literalmente das matérias — substring "
    "exata, similaridade fuzzy >= 0,75 ou >= 60% das palavras no título/resumo. Fonte e número "
    "da matéria são corrigidos para o texto onde o trecho foi encontrado. "
    "Grau de divergência (0 a 1): confiança de que o conflito é real — lados com fontes "
    "distintas e sem interseção, e visões diferentes (não paráfrase). Exige ao menos um lado "
    "com frase literal confirmada; quando só um lado tem citação, a nota é multiplicada por 0,7."
)


def mapa_urls_fontes(noticias):
    """Primeiro link encontrado para cada nome de fonte (para ligar nomes a matérias)."""
    mapa = {}
    for n in (noticias or []):
        fonte = (n.get("fonte") or "").strip()
        url = (n.get("url") or "").strip()
        if fonte and url and fonte not in mapa:
            mapa[fonte] = url
    return mapa


def gerar_pdf_relatorio(topicos, analise, noticias=None):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font("DejaVu", "", r"C:\Windows\Fonts\DejaVuSans.ttf")
    pdf.add_font("DejaVu", "B", r"C:\Windows\Fonts\DejaVuSans-Bold.ttf")
    pdf.add_page()

    def mc(text, style="", size=11, h=7):
        pdf.set_font("DejaVu", style, size)
        pdf.multi_cell(0, h, text, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("DejaVu", "B", 18)
    pdf.multi_cell(0, 10, f"Relatório Analítico: {topicos}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    mc("Resumo Executivo", style="B", size=13, h=10)
    mc(analise.get("resumo_geral", ""))
    pdf.ln(4)

    mc("Pontos Convergentes", style="B", size=13, h=10)
    convergentes = analise.get("convergentes") or []
    if not convergentes:
        mc("Nenhuma convergência clara identificada.")
    for conv in convergentes:
        mc(f"Fato: {conv.get('fato_principal', '')}", style="B")
        mc(f"Fontes em acordo: {', '.join(conv.get('fontes', []))}", size=10)
        mc(f"Grau de convergência — confiança: {conv.get('confianca', 0.5)}", size=10)
        for t in conv.get("trechos", []) or []:
            mc(f"\u201c{t.get('trecho', '')}\u201d — {t.get('fonte', '')}", size=10)
        if conv.get("interpretacao"):
            mc(f"Interpretação: {conv.get('interpretacao', '')}", size=10)
        pdf.ln(2)
    pdf.ln(2)

    mc("Pontos Divergentes", style="B", size=13, h=10)
    divergentes = analise.get("divergentes") or []
    if not divergentes:
        mc("Nenhuma divergência identificada entre as fontes.")
    for div in divergentes:
        mc(f"Conflito: {div.get('ponto_conflito', '')}", style="B")
        mc(f"Visão A: {div.get('visao_a', '')}")
        mc(f"Fontes: {', '.join(div.get('fontes_a', []))}", size=10)
        for t in div.get("trechos_a", []) or []:
            mc(f"\u201c{t.get('trecho', '')}\u201d — {t.get('fonte', '')}", size=10)
        pdf.ln(1)
        mc(f"Visão B: {div.get('visao_b', '')}")
        mc(f"Fontes: {', '.join(div.get('fontes_b', []))}", size=10)
        for t in div.get("trechos_b", []) or []:
            mc(f"\u201c{t.get('trecho', '')}\u201d — {t.get('fonte', '')}", size=10)
        mc(f"Grau de divergência — confiança: {div.get('confianca', 0.5)}", size=10)
        if div.get("interpretacao"):
            mc(f"Interpretação: {div.get('interpretacao', '')}", size=10)
        pdf.ln(3)

    mc("Fontes consultadas", style="B", size=13, h=10)
    if noticias:
        for i, n in enumerate(noticias, 1):
            data = f" ({n.get('data')})" if n.get("data") else ""
            mc(f"{i}. {n.get('fonte', '?')} — {n.get('titulo', '')}{data}", size=10)
            if n.get("url"):
                mc(n.get("url", ""), size=9)
    else:
        mc("Lista de fontes indisponível.", size=10)
    pdf.ln(2)

    mc("Como os graus são calculados", style="B", size=13, h=10)
    mc(EXPLICACAO_GRAUS, size=10)
    pdf.ln(2)

    mc("Nota metodológica", style="B", size=13, h=10)
    mc(analise.get("nota_metodologica") or
       "LLM com temperatura 0 + validação groundtruth de trechos literais.", size=10)

    return bytes(pdf.output())

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html', sugestoes=SUGESTOES)

@app.route('/gerar_relatorio', methods=['POST'])
def gerar_relatorio():
    topicos = request.form.get('topicos')

    if not topicos:
        return "Por favor, insira um tópico.", 400

    # Passo 1: Buscar notícias
    noticias = buscar_noticias(topicos, max_resultados=20)

    if not noticias:
        return "Nenhuma notícia encontrada para este tópico.", 404

    # Passo 2: Analisar convergências e divergências
    analise = analisar_noticias(topicos, noticias)

    if not analise:
        return "Erro ao processar a análise com a IA.", 500

    return render_template('report.html', topicos=topicos, analise=analise,
                           noticias_brutas=noticias, mapa_urls=mapa_urls_fontes(noticias))


@app.route('/baixar_pdf', methods=['POST'])
def baixar_pdf():
    topicos = request.form.get('topicos')

    if not topicos:
        return "Por favor, insira um tópico.", 400

    noticias = buscar_noticias(topicos, max_resultados=20)

    if not noticias:
        return "Nenhuma notícia encontrada para este tópico.", 404

    analise = analisar_noticias(topicos, noticias)

    if not analise:
        return "Erro ao processar a análise com a IA.", 500

    pdf_bytes = gerar_pdf_relatorio(topicos, analise, noticias)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"Relatorio_{topicos.replace(' ', '_')}.pdf"
    )

if __name__ == '__main__':
    app.run(debug=True)