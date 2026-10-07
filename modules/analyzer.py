import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Carregar as variáveis do arquivo .env
load_dotenv()


# Limiares da validação groundtruth (ajustados empiricamente no repro 07/10/2026:
# 0.8 descartava paráfrase com 2+ palavras trocadas, zerando divergentes).
_LIMIAR_FUZZY = 0.75
_LIMIAR_TOKENS = 0.6


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
    confianca: float = Field(
        default=0.5,
        description="Confiança 0-1 de que a convergência é real (sintaxe + semântica).")
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
        description="Limites da análise: cobertura das fontes e método de validação usado.")


# Ordem de tentativa: Lite primeiro (modelo mais leve = menos pressão de
# capacidade no lado do Google), depois Flash intermediários, por fim o flagship.
# Todos gratuitos no free tier. Nenhum é imune a 503 — a troca automática é o
# que dá resiliência.
_MODELOS = ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash")

# Erros transitórios do lado do Google (sobrecarga) que valem nova tentativa
_TRANSIENTES = ("503", "UNAVAILABLE", "overloaded", "high demand", "500", "deadline", "timeout")


def _eh_transitorio(erro):
    """Diz se o erro do Gemini vale nova tentativa (sobrecarga) ou é permanente.

    Input:
        <Exception | str> erro — exceção ou texto retornado pela API.

    Output:
        <bool> — True se contém marcador de _TRANSIENTES (503, UNAVAILABLE, ...).
    """
    texto = str(erro)
    return any(sinal in texto for sinal in _TRANSIENTES)


def _normalizar(texto):
    """Normaliza texto para comparação (minúscula, sem acento/pontuação, espaços colapsados).

    Input:
        <str | None> texto — título, resumo ou trecho do LLM.

    Output:
        <str> — texto normalizado; "" se entrada vazia.
    """
    texto = (texto or "").lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _fontes_conhecidas(lista_noticias):
    """Mapeia as fontes coletadas para validar nomes citados pelo LLM.

    Input:
        <list[dict]> lista_noticias — cada dict com chave "fonte".

    Output:
        <dict[str, str]> — {nome_lower: nome_original}.
    """
    return {(n.get("fonte") or "").strip().lower(): (n.get("fonte") or "").strip()
            for n in lista_noticias if n.get("fonte")}


def _filtrar_fontes(fontes, conhecidas):
    """Filtra fontes do LLM contra as coletadas; mantém ordem, sem duplicar.

    Primeiro tenta igualdade exata (lower). Se falhar, tenta contenção
    substring nos dois sentidos — o LLM costuma abreviar ("Folha" vs
    "Folha de S.Paulo", "G1" vs "G1 - Globo").

    Input:
        <list[str] | None> fontes — nomes citados pelo LLM.
        <dict[str, str]> conhecidas — mapa de _fontes_conhecidas.

    Output:
        <list[str]> — nomes originais conhecidos, na ordem citada.
    """
    limpas = []
    for f in (fontes or []):
        chave = (f or "").strip().lower()
        if not chave:
            continue
        if chave in conhecidas and conhecidas[chave] not in limpas:
            limpas.append(conhecidas[chave])
            continue
        for k, original in conhecidas.items():
            if not k:
                continue
            if chave in k or k in chave:
                if original not in limpas:
                    limpas.append(original)
                break
    return limpas


def _textos_base(lista_noticias):
    """Monta o groundtruth: título + resumo normalizados por notícia.

    Input:
        <list[dict]> lista_noticias — dicts com "titulo" e "resumo".

    Output:
        <list[str]> — um texto normalizado por notícia, na mesma ordem.
    """
    bases = []
    for n in (lista_noticias or []):
        titulo = n.get("titulo") or ""
        resumo = n.get("resumo") or ""
        bases.append(_normalizar(f"{titulo} {resumo}"))
    return bases


def _trecho_tem_suporte(trecho_norm, base_norm):
    """Testa se o trecho existe na matéria: substring, fuzzy por janela, recall de tokens.

    Input:
        <str> trecho_norm — trecho do LLM já normalizado por _normalizar.
        <str> base_norm — título+resumo da notícia já normalizado.

    Output:
        <bool> — True se substring exata, fuzzy >= _LIMIAR_FUZZY em alguma
        janela, ou recall de tokens >= _LIMIAR_TOKENS.
    """
    if not trecho_norm or not base_norm:
        return False
    if trecho_norm in base_norm:
        return True
    # Fuzzy: compara o trecho contra janelas da base com tamanho parecido,
    # em vez do prefixo da base (bug anterior: só acertava trecho no início).
    toks_t = trecho_norm.split()
    toks_b = base_norm.split()
    if toks_t and toks_b:
        janela = max(len(toks_t) + 5, 8)
        passo = max(janela // 2, 1)
        melhor = 0.0
        for ini in range(0, max(len(toks_b) - janela + 1, 1), passo):
            pedaco = " ".join(toks_b[ini:ini + janela])
            r = SequenceMatcher(None, trecho_norm, pedaco).ratio()
            if r > melhor:
                melhor = r
                if melhor >= _LIMIAR_FUZZY:
                    return True
    if not toks_t:
        return False
    toks_b_set = set(toks_b)
    recall = len(set(toks_t) & toks_b_set) / len(set(toks_t))
    return recall >= _LIMIAR_TOKENS


def _corrigir_trecho(t, bases, conhecidas, total):
    """Valida um trecho do LLM contra o groundtruth e corrige fonte + índice.

    Procura o trecho primeiro na notícia do índice declarado, depois nas demais.

    Input:
        <dict> t — {"trecho": str, "fonte": str, "indice": int}.
        <list[str]> bases — textos de _textos_base.
        <dict[str, str]> conhecidas — mapa de _fontes_conhecidas (não usado
            diretamente aqui; a fonte final vem da posição achada).
        <int> total — número de notícias coletadas.

    Output:
        <tuple[dict | None, bool]> — ({"trecho": str, "indice": int, "pos": int},
        corrigiu_indice) se há suporte; (None, False) se trecho vazio, curto
        (< 10 chars normalizados) ou sem suporte em nenhuma base.
    """
    if not isinstance(t, dict):
        return None, False
    texto = (t.get("trecho") or "").strip()[:400]
    if not texto:
        return None, False
    trecho_norm = _normalizar(texto)
    if len(trecho_norm) < 10:
        return None, False
    try:
        indice = int(t.get("indice") or 0)
    except (TypeError, ValueError):
        indice = 0

    # Candidatos: índice declarado primeiro, depois o restante.
    ordem = []
    if 1 <= indice <= total:
        ordem.append(indice - 1)
    ordem += [i for i in range(total) if i not in ordem]

    for pos in ordem:
        if _trecho_tem_suporte(trecho_norm, bases[pos]):
            return {"trecho": texto, "indice": pos + 1, "pos": pos}, (pos + 1 != indice)
    return None, False


def _resolver_trechos(trechos, lista_noticias, bases, conhecidas, correcoes):
    """Filtra trechos pelo groundtruth, corrige fonte + índice, conta ajustes.

    A fonte final é a da notícia onde o trecho foi achado (_fontes_por_pos);
    o nome citado pelo LLM é ignorado.

    Input:
        <list[dict] | None> trechos — trechos do LLM (máx. 6 avaliados).
        <list[dict]> lista_noticias — notícias coletadas.
        <list[str]> bases — textos de _textos_base.
        <dict[str, str]> conhecidas — mapa de _fontes_conhecidas.
        <dict> correcoes — contadores mutados in place
            (trechos_descartados, indices_corrigidos).

    Output:
        <list[dict]> — [{"trecho": str, "fonte": str, "indice": int}] válidos.
    """
    global _fontes_por_pos
    total = len(lista_noticias)
    limpos = []
    for t in (trechos or [])[:6]:
        achado, corrigiu = _corrigir_trecho(t, bases, conhecidas, total)
        if achado is None:
            correcoes["trechos_descartados"] += 1
            continue
        pos = achado.pop("pos")
        fonte_real = _fontes_por_pos[pos]
        if not fonte_real:
            correcoes["trechos_descartados"] += 1
            continue
        achado["fonte"] = fonte_real
        if corrigiu:
            correcoes["indices_corrigidos"] += 1
        limpos.append(achado)
    return limpos


def _fix_confianca(valor):
    """Limita a confiança ao intervalo [0, 1].

    Input:
        <float | None> valor — nota vinda do LLM.

    Output:
        <float> — valor clampado; 0.5 se ausente ou inválido.
    """
    try:
        return max(0.0, min(1.0, float(valor if valor is not None else 0.5)))
    except (TypeError, ValueError):
        return 0.5


_fontes_por_pos = []


def _validar_relatorio(rel, lista_noticias):
    """Valida o relatório do LLM contra o groundtruth; só passa o confirmado.

    Convergente passa com fato + 2+ fontes distintas + 1+ trecho válido
    (dedup por fontes+fato). Divergente passa com conflito/visões preenchidos
    e diferentes, lados com fontes disjuntas e 1+ lado com trecho válido
    (lado sem citação penaliza confiança × 0,7).

    Input:
        <dict> rel — relatório decodificado do JSON do LLM.
        <list[dict]> lista_noticias — notícias coletadas (groundtruth).

    Output:
        <dict> — mesmo rel com "convergentes"/"divergentes" filtrados e
        "correcoes" {trechos_descartados, itens_descartados, indices_corrigidos}.
    """
    global _fontes_por_pos
    conhecidas = _fontes_conhecidas(lista_noticias)
    bases = _textos_base(lista_noticias)
    _fontes_por_pos = [(n.get("fonte") or "").strip() for n in (lista_noticias or [])]
    correcoes = {"trechos_descartados": 0, "itens_descartados": 0, "indices_corrigidos": 0}

    convergentes = []
    vistos = set()
    for conv in rel.get("convergentes", []) or []:
        fontes = _filtrar_fontes(conv.get("fontes"), conhecidas)
        trechos = _resolver_trechos(conv.get("trechos"), lista_noticias, bases, conhecidas, correcoes)
        fato = (conv.get("fato_principal") or "").strip()
        # Regra: fato vazio, <2 fontes distintas ou sem trecho válido = descarta.
        if not fato or len(set(fontes)) < 2 or not trechos:
            correcoes["itens_descartados"] += 1
            continue
        chave = (tuple(sorted(set(fontes))), _normalizar(fato))
        if chave in vistos:
            correcoes["itens_descartados"] += 1
            continue
        vistos.add(chave)
        convergentes.append({
            "fato_principal": fato,
            "fontes": fontes,
            "trechos": trechos,
            "confianca": _fix_confianca(conv.get("confianca")),
            "interpretacao": (conv.get("interpretacao") or "").strip(),
        })

    divergentes = []
    for div in rel.get("divergentes", []) or []:
        fontes_a = _filtrar_fontes(div.get("fontes_a"), conhecidas)
        fontes_b = _filtrar_fontes(div.get("fontes_b"), conhecidas)
        trechos_a = _resolver_trechos(div.get("trechos_a"), lista_noticias, bases, conhecidas, correcoes)
        trechos_b = _resolver_trechos(div.get("trechos_b"), lista_noticias, bases, conhecidas, correcoes)
        conflito = (div.get("ponto_conflito") or "").strip()
        visao_a = (div.get("visao_a") or "").strip()
        visao_b = (div.get("visao_b") or "").strip()
        # Regras: conflito/visão vazia, lado sem fonte, mesma fonte nos dois
        # lados ou visões iguais (paráfrase) = descarta. Trechos: exige ao
        # menos UM lado com suporte literal; se um lado perde tudo, mantém o
        # item com confiança penalizada em vez de zerar os divergentes.
        if not conflito or not visao_a or not visao_b or not fontes_a or not fontes_b:
            correcoes["itens_descartados"] += 1
            continue
        if set(fontes_a) & set(fontes_b):
            correcoes["itens_descartados"] += 1
            continue
        if _normalizar(visao_a) == _normalizar(visao_b):
            correcoes["itens_descartados"] += 1
            continue
        if not trechos_a and not trechos_b:
            correcoes["itens_descartados"] += 1
            continue
        confianca = _fix_confianca(div.get("confianca"))
        if not trechos_a or not trechos_b:
            confianca = round(confianca * 0.7, 2)
        divergentes.append({
            "ponto_conflito": conflito,
            "visao_a": visao_a,
            "fontes_a": fontes_a,
            "visao_b": visao_b,
            "fontes_b": fontes_b,
            "trechos_a": trechos_a,
            "trechos_b": trechos_b,
            "confianca": confianca,
            "interpretacao": (div.get("interpretacao") or "").strip(),
        })

    rel["convergentes"] = convergentes
    rel["divergentes"] = divergentes
    rel.setdefault("nota_metodologica", "")
    rel["correcoes"] = correcoes
    return rel


def _bloco_noticias(topico, lista_noticias):
    """Formata as notícias coletadas como contexto numerado para o prompt do LLM.

    Input:
        <str> topico — tópico pesquisado.
        <list[dict]> lista_noticias — dicts com "fonte", "titulo", "resumo"
            (resumo truncado a 600 chars).

    Output:
        <str> — bloco "[i] Fonte: ... | Título: ... | Resumo: ..." com o tópico.
    """
    linhas = [f"Tópico: {topico}\n"]
    linhas.append("NOTÍCIAS COLETADAS (use SOMENTE estas fontes, sem inventar):")
    for i, n in enumerate(lista_noticias):
        linhas.append(
            f"[{i + 1}] Fonte: {n.get('fonte')} | Título: {n.get('titulo')} "
            f"| Resumo: {(n.get('resumo') or '')[:600]}")
    return "\n".join(linhas)


def analisar_noticias(topico, lista_noticias, tentativas=2):
    """Classifica convergências/divergências com LLM (temperatura 0) e valida trechos.

    Erros transitórios (503/UNAVAILABLE) são repetidos com espera crescente; se o
    modelo segue sobrecarregado, tenta o próximo modelo gratuito da lista.

    Input:
        <str> topico — tópico pesquisado.
        <list[dict]> lista_noticias — notícias de buscar_noticias.
        <int> tentativas — tentativas por modelo (padrão 2).

    Output:
        <dict | None> — relatório validado por _validar_relatorio (com
        "correcoes"); None se erro permanente ou todos os modelos falharem.
    """
    # Necessário ter a variável de ambiente GEMINI_API_KEY configurada
    client = genai.Client()

    texto_noticias = _bloco_noticias(topico, lista_noticias)

    prompt = (
        "Você é um analista jornalístico imparcial. Temperatura de decisão zero: "
        "seja conservador, só afirme convergência/divergência com apoio nas notícias.\n"
        "Compare títulos e resumos por SINTAXE (mesmas palavras/expressões) e por "
        "SEMÂNTICA (mesmo fato dito com palavras diferentes).\n"
        "1. RESUMO EXECUTIVO (extenso e objetivo): escreva de 3 a 5 parágrafos cobrindo "
        "TODAS as notícias coletadas — fatos principais, números, atores e desdobramentos "
        "mais relevantes, com o panorama geral do tópico e menção aos consensos e conflitos "
        "identificados. Sem opinião, só o essencial de cada frente da cobertura.\n"
        "2. CONVERGENTES: fatos em que 2 ou mais fontes concordam (mesmo evento, mesmos "
        "dados, mesma narrativa). Informe confianca (0-1) como grau de convergência.\n"
        "3. DIVERGENTES: fatos, números ou enquadramentos em conflito real entre fontes "
        "distintas (ex.: valores diferentes, causas diferentes, tom oposto). Não liste "
        "paráfrases como divergência, mas NÃO retorne lista vazia se houver números ou "
        "enquadramentos diferentes — registre o conflito; informe confianca (0-1) como "
        "grau de divergência.\n"
        "4. INTERPRETAÇÃO (obrigatória em cada item): explique o mecanismo — mesmo evento "
        "com dados oficiais? números preliminares vs. finais? viés de enquadramento? "
        "erro de apuração de uma fonte isolada?\n"
        "5. TRECHOS (obrigatórios em cada item): copie de 1 a 3 frases LITERAIS das "
        "notícias (copiar-colar de título/resumo, sem trocar palavras), curtas, que "
        "mencionem o ponto; informe fonte e indice (o número entre colchetes da notícia). "
        "Se a divergência for de enquadramento, copie o trecho que mostra o tom de cada "
        "lado. Convergentes vão em trechos; divergentes separam trechos_a (visão A) e "
        "trechos_b (visão B). Trecho reformulado é descartado na validação.\n"
        "6. Use SOMENTE os nomes de fonte listados — nunca invente fonte.\n"
        "7. Preencha nota_metodologica (limites: cobertura das fontes)."
    )

    ultimo_erro = None
    for modelo in _MODELOS:
        for tentativa in range(1, tentativas + 1):
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=[prompt, texto_noticias],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=RelatorioAnalise,
                        temperature=0.0,
                    ),
                )
                if modelo != _MODELOS[0]:
                    print(f"Análise concluída com o modelo alternativo {modelo}.")
                rel = json.loads(response.text)
                rel = _validar_relatorio(rel, lista_noticias)
                print(f"Validação groundtruth: {rel.get('correcoes')} "
                      f"| convergentes={len(rel.get('convergentes', []))} "
                      f"divergentes={len(rel.get('divergentes', []))}")
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
