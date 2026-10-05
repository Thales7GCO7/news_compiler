import json
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from modules.similarity import (
    matriz_similaridade,
    pares_similares,
    agrupar_noticias,
    similaridade_media_cluster,
)

# Carregar as variáveis do arquivo .env
load_dotenv()


# Definição da estrutura de saída usando Pydantic para garantir o formato do JSON
class TrechoCitacao(BaseModel):
    fonte: str = Field(description="Nome da fonte, igual ao listado nas notícias coletadas")
    trecho: str = Field(description="Frase literal e curta, transcrita da matéria, que menciona o ponto")
    indice: int = Field(
        default=0,
        description="Número da notícia entre colchetes (1..N) de onde o trecho foi copiado; 0 se incerto")


class NoticiaConvergente(BaseModel):
    fato_principal: str = Field(description="O fato ou narrativa em que as fontes concordam")
    fontes: list[str] = Field(description="Lista dos nomes das fontes que relataram este fato")
    trechos: list[TrechoCitacao] = Field(
        default_factory=list,
        description="Frases literais das matérias que mencionam este fato, uma por fonte quando possível")
    similaridade_media: float = Field(
        default=0.0,
        description="Similaridade TF-IDF média (0-1) entre as matérias do fato. Preenchida/valida pelo pré-processamento.")
    confianca: float = Field(
        default=0.5,
        description="Confiança 0-1 no ponto, pelo apoio da similaridade entre as matérias.")
    interpretacao: str = Field(
        default="",
        description="Por que as fontes convergem aqui (mesmo evento, dados oficiais, etc.) e o que isso implica.")


class NoticiaDivergente(BaseModel):
    ponto_conflito: str = Field(description="O tema ou fato onde há divergência de informações ou viés")
    visao_a: str = Field(description="O que uma parte das fontes afirma")
    fontes_a: list[str] = Field(description="Fontes que apoiam a visão A")
    visao_b: str = Field(description="O que a outra parte das fontes afirma")
    fontes_b: list[str] = Field(description="Fontes que apoiam a visão B")
    trechos_a: list[TrechoCitacao] = Field(
        default_factory=list,
        description="Frases literais das matérias que sustentam a visão A")
    trechos_b: list[TrechoCitacao] = Field(
        default_factory=list,
        description="Frases literais das matérias que sustentam a visão B")
    confianca: float = Field(
        default=0.5, description="Confiança 0-1 de que a divergência é real e não ruído de coleta.")
    interpretacao: str = Field(
        default="",
        description="Hipótese explicativa: números preliminares vs. finais, enquadramento político, erro de apuração, etc.")


class RelatorioAnalise(BaseModel):
    resumo_geral: str = Field(description="Resumo executivo extenso (3 a 5 parágrafos), objetivo e sem opinião, cobrindo todas as notícias: fatos, números, atores e desdobramentos principais")
    convergentes: list[NoticiaConvergente]
    divergentes: list[NoticiaDivergente]
    nota_metodologica: str = Field(
        default="",
        description="Limites da análise: cobertura das fontes e threshold de similaridade usado.")


# Ordem de tentativa: Lite primeiro (modelo mais leve = menos pressão de
# capacidade no lado do Google), depois Flash intermediários, por fim o flagship.
# Todos gratuitos no free tier. Nenhum é imune a 503 — a troca automática é o
# que dá resiliência.
_MODELOS = ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash")

# Erros transitórios do lado do Google (sobrecarga) que valem nova tentativa
_TRANSIENTES = ("503", "UNAVAILABLE", "overloaded", "high demand", "500", "deadline", "timeout")


def _eh_transitorio(erro):
    texto = str(erro)
    return any(sinal in texto for sinal in _TRANSIENTES)


def _fontes_conhecidas(lista_noticias):
    return {(n.get("fonte") or "").strip().lower(): (n.get("fonte") or "").strip()
            for n in lista_noticias if n.get("fonte")}


def _filtrar_fontes(fontes, conhecidas):
    """Remove fontes inventadas pelo LLM; mantém ordem, sem duplicar."""
    limpas = []
    for f in (fontes or []):
        chave = (f or "").strip().lower()
        if chave in conhecidas and conhecidas[chave] not in limpas:
            limpas.append(conhecidas[chave])
    return limpas


def _limpar_trechos(trechos, conhecidas, total):
    """Mantém só trechos de fontes conhecidas, com texto curto e índice válido."""
    limpos = []
    for t in (trechos or [])[:6]:
        if not isinstance(t, dict):
            continue
        fonte = _filtrar_fontes([t.get("fonte")], conhecidas)
        texto = (t.get("trecho") or "").strip()[:400]
        if not fonte or not texto:
            continue
        try:
            indice = int(t.get("indice") or 0)
        except (TypeError, ValueError):
            indice = 0
        if indice < 1 or indice > total:
            indice = 0
        limpos.append({"fonte": fonte[0], "trecho": texto, "indice": indice})
    return limpos


def _validar_relatorio(rel, lista_noticias, matriz, clusters):
    """Pós-validação determinística: corta alucinação e fixa intervalos."""
    conhecidas = _fontes_conhecidas(lista_noticias)
    total = len(lista_noticias)
    for conv in rel.get("convergentes", []):
        conv["fontes"] = _filtrar_fontes(conv.get("fontes"), conhecidas)
        try:
            conv["similaridade_media"] = max(0.0, min(1.0, float(conv.get("similaridade_media", 0.0))))
        except (TypeError, ValueError):
            conv["similaridade_media"] = 0.0
        try:
            conv["confianca"] = max(0.0, min(1.0, float(conv.get("confianca", 0.5))))
        except (TypeError, ValueError):
            conv["confianca"] = 0.5
        conv.setdefault("interpretacao", "")
        conv["trechos"] = _limpar_trechos(conv.get("trechos"), conhecidas, total)
    for div in rel.get("divergentes", []):
        div["fontes_a"] = _filtrar_fontes(div.get("fontes_a"), conhecidas)
        div["fontes_b"] = _filtrar_fontes(div.get("fontes_b"), conhecidas)
        try:
            div["confianca"] = max(0.0, min(1.0, float(div.get("confianca", 0.5))))
        except (TypeError, ValueError):
            div["confianca"] = 0.5
        div.setdefault("interpretacao", "")
        div["trechos_a"] = _limpar_trechos(div.get("trechos_a"), conhecidas, total)
        div["trechos_b"] = _limpar_trechos(div.get("trechos_b"), conhecidas, total)
    rel.setdefault("nota_metodologica", "")
    return rel


def _bloco_evidencias(topico, lista_noticias, matriz, clusters, limiar):
    linhas = [f"Tópico: {topico}\n"]
    linhas.append("NOTÍCIAS COLETADAS (use SOMENTE estas fontes, sem inventar):")
    for i, n in enumerate(lista_noticias):
        linhas.append(
            f"[{i + 1}] Fonte: {n.get('fonte')} | Título: {n.get('titulo')} "
            f"| Resumo: {(n.get('resumo') or '')[:600]}")
    linhas.append(f"\nCLUSTERS PRÉ-COMPUTADOS (TF-IDF cosseno, limiar={limiar}):")
    for cid, membros in enumerate(clusters):
        sim = similaridade_media_cluster(matriz, membros)
        nomes = [lista_noticias[i].get("fonte", "?") for i in membros]
        ids = [str(i + 1) for i in membros]
        linhas.append(
            f"  Cluster {cid}: notícias {', '.join(ids)} | sim_media={sim} "
            f"| fontes: {', '.join(nomes)}")
    return "\n".join(linhas)


def analisar_noticias(topico, lista_noticias, tentativas=2, limiar_similaridade=0.22):
    """
    Cruza notícias em 2 passos:
    1. Similaridade sintática TF-IDF (clusters + pares) — evidência.
    2. LLM (temperatura 0) interpreta convergências/divergências e o grau
       de acordo/conflito entre as notícias coletadas.
    Erros transitórios (503/UNAVAILABLE) são repetidos com espera crescente; se o
    modelo segue sobrecarregado, tenta o próximo modelo gratuito da lista.
    """
    # Necessário ter a variável de ambiente GEMINI_API_KEY configurada
    client = genai.Client()

    # Evidência determinística (não depende do LLM)
    matriz = matriz_similaridade(lista_noticias)
    clusters = agrupar_noticias(matriz, limiar=limiar_similaridade)

    texto_evidencias = _bloco_evidencias(
        topico, lista_noticias, matriz, clusters, limiar_similaridade)

    prompt = (
        "Você é um analista jornalístico imparcial. Temperatura de decisão zero: "
        "seja conservador, só afirme convergência/divergência com apoio nas evidências.\n"
        "1. RESUMO EXECUTIVO (extenso e objetivo): escreva de 3 a 5 parágrafos cobrindo "
        "TODAS as notícias coletadas — fatos principais, números, atores e desdobramentos "
        "mais relevantes, com o panorama geral do tópico e menção aos consensos e conflitos "
        "identificados. Sem opinião, só o essencial de cada frente da cobertura.\n"
        "2. CONVERGENTES: fatos em que múltiplas fontes concordam. Prefira os clusters "
        "pré-computados com sim_media alta; informe similaridade_media e confianca (0-1) "
        "como grau de convergência.\n"
        "3. DIVERGENTES: fatos, números ou enquadramentos em conflito real entre fontes. "
        "Não liste paráfrases como divergência; informe confianca (0-1) como grau de divergência.\n"
        "4. INTERPRETAÇÃO (obrigatória em cada item): explique o mecanismo — mesmo evento "
        "com dados oficiais? números preliminares vs. finais? viés de enquadramento? "
        "erro de apuração de uma fonte isolada?\n"
        "5. TRECHOS (obrigatórios em cada item): transcreva de 1 a 3 frases literais e "
        "curtas copiadas das notícias que mencionem o ponto, sem inventar nem reformular; "
        "informe fonte e indice (o número entre colchetes da notícia). Convergentes vão em "
        "trechos; divergentes separam trechos_a (visão A) e trechos_b (visão B).\n"
        "6. Use SOMENTE os nomes de fonte listados — nunca invente fonte.\n"
        "7. Preencha nota_metodologica (limites: cobertura das fontes, limiar usado)."
    )

    ultimo_erro = None
    for modelo in _MODELOS:
        for tentativa in range(1, tentativas + 1):
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=[prompt, texto_evidencias],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=RelatorioAnalise,
                        temperature=0.0,
                    ),
                )
                if modelo != _MODELOS[0]:
                    print(f"Análise concluída com o modelo alternativo {modelo}.")
                rel = json.loads(response.text)
                rel = _validar_relatorio(rel, lista_noticias, matriz, clusters)
                # Anexa evidências computadas para exibição/auditoria
                rel["evidencias"] = {
                    "limiar_similaridade": limiar_similaridade,
                    "clusters": [
                        {"id": cid, "noticias": [i + 1 for i in m],
                         "similaridade_media": similaridade_media_cluster(matriz, m)}
                        for cid, m in enumerate(clusters)
                    ],
                    "pares_similares": [
                        {"a": a + 1, "b": b + 1, "similaridade": s}
                        for a, b, s in pares_similares(lista_noticias, matriz)
                    ],
                }
                return rel
            except Exception as e:
                ultimo_erro = e
                print(f"Erro na análise [{modelo}] (tentativa {tentativa}/{tentativas}): {e}")
                if not _eh_transitorio(e):
                    print(f"Erro permanente, sem fallback: {e}")
                    return None  # Ex. 400: trocar de modelo não adiantaria
                if tentativa < tentativas:
                    espera = 2 ** tentativa  # 2s, 4s, ...
                    print(f"Erro transitório, tentando de novo em {espera}s...")
                    time.sleep(espera)
        print(f"Modelo {modelo} indisponível, tentando o próximo...")
    print(f"Análise falhou em todos os modelos: {ultimo_erro}")
    return None
