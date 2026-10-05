"""Similaridade textual determinística entre notícias.

Sem dependências externas: TF-IDF com normalização + cosseno, sobre
título + resumo. Serve como evidência pré-LLM: o LLM interpreta, mas não
inventa o grau de convergência — ele recebe os números.
"""

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-zà-ú0-9]+", re.IGNORECASE)

# Stopwords mínimas PT para não inflar similaridade com "de/que/com".
_STOPWORDS = frozenset("""
a ao aos aquela aquelas aquele aqueles aquilo as até com como da das de dela
delas dele deles depois do dos e ela elas ele eles em entre era eram essa essas
esse esses esta estas este estes foi foram há isso isto já lhe lhes mais mas me
mesmo meu meus minha minhas muito na nas nem no nos nosso nossos nossa nossas
não nos o os ou para pela pelas pelo pelos por qual quando que quem se sem ser
seu seus sua suas só também te tem têm um uma umas uns vai vão você vocês vos
dos das foi ser ter sobre entre após antes onde qual quais cujo cuja então pois
porque como mais menos muito pouco cada outro outra outros outras mesmo mesma
""".split())


def _texto_noticia(n):
    titulo = (n.get("titulo") or "").strip()
    resumo = (n.get("resumo") or "").strip()
    # Título pesa 2x: manchetes iguais são sinal forte de mesmo fato.
    return f"{titulo} {titulo} {resumo}"


def tokenizar(texto):
    tokens = [t.lower() for t in _TOKEN_RE.findall(texto or "")]
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 2]


def matriz_similaridade(noticias):
    """Retorna matriz NxN de cosseno TF-IDF (0.0–1.0, diagonal 1.0)."""
    n = len(noticias)
    if n == 0:
        return []
    docs = [Counter(tokenizar(_texto_noticia(x))) for x in noticias]
    df = Counter()
    for d in docs:
        for termo in d:
            df[termo] += 1
    idf = {t: math.log((1 + n) / (1 + f)) + 1.0 for t, f in df.items()}

    vetores, normas = [], []
    for d in docs:
        total = sum(d.values()) or 1
        vec = {t: (c / total) * idf[t] for t, c in d.items()}
        norma = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vetores.append(vec)
        normas.append(norma)

    matriz = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matriz[i][i] = 1.0
        for j in range(i + 1, n):
            a, b = vetores[i], vetores[j]
            if len(a) > len(b):
                a, b = b, a
            prod = sum(v * b.get(t, 0.0) for t, v in a.items())
            sim = prod / (normas[i] * normas[j])
            sim = max(0.0, min(1.0, sim))
            matriz[i][j] = sim
            matriz[j][i] = sim
    return matriz


def pares_similares(noticias, matriz, top_k=8):
    """Pares (i, j, similaridade) ordenados por similaridade desc."""
    n = len(noticias)
    pares = [
        (i, j, round(matriz[i][j], 3))
        for i in range(n)
        for j in range(i + 1, n)
    ]
    pares.sort(key=lambda p: p[2], reverse=True)
    return pares[:top_k]


def agrupar_noticias(matriz, limiar=0.22):
    """Clusteriza por union-find: mesma história se sim >= limiar.

    Limiar 0.22 calibrado empiricamente em manchetes PT curtas: abaixo
    disso, quase tudo vira um cluster só ou tudo isolado dependendo do
    tópico; 0.20–0.25 separa histórias distintas sem fragmentar demais.
    """
    n = len(matriz)
    pai = list(range(n))

    def raiz(x):
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    def unir(a, b):
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            pai[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if matriz[i][j] >= limiar:
                unir(i, j)

    grupos = {}
    for i in range(n):
        grupos.setdefault(raiz(i), []).append(i)
    clusters = sorted(grupos.values(), key=len, reverse=True)
    return clusters


def similaridade_media_cluster(matriz, indices):
    if len(indices) < 2:
        return 1.0 if len(indices) == 1 else 0.0
    vals = [matriz[a][b] for x, a in enumerate(indices) for b in indices[x + 1:]]
    return round(sum(vals) / len(vals), 3) if vals else 0.0
