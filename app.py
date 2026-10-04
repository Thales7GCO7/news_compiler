from flask import Flask, render_template, request, send_file
from fpdf import FPDF
import io
from modules.fetcher import buscar_noticias
from modules.analyzer import analisar_noticias

app = Flask(__name__)


def gerar_pdf_relatorio(topicos, analise):
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
        pdf.ln(1)
        mc(f"Visão B: {div.get('visao_b', '')}")
        mc(f"Fontes: {', '.join(div.get('fontes_b', []))}", size=10)
        pdf.ln(3)

    return bytes(pdf.output())

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

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

    return render_template('report.html', topicos=topicos, analise=analise, noticias_brutas=noticias)


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

    pdf_bytes = gerar_pdf_relatorio(topicos, analise)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"Relatorio_{topicos.replace(' ', '_')}.pdf"
    )

if __name__ == '__main__':
    app.run(debug=True)