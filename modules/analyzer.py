import os
import json
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Carregar as variáveis do arquivo .env
load_dotenv()

# Definição da estrutura de saída usando Pydantic para garantir o formato do JSON
class NoticiaConvergente(BaseModel):
    fato_principal: str = Field(description="O fato ou narrativa em que as fontes concordam")
    fontes: list[str] = Field(description="Lista dos nomes das fontes que relataram este fato")

class NoticiaDivergente(BaseModel):
    ponto_conflito: str = Field(description="O tema ou fato onde há divergência de informações ou viés")
    visao_a: str = Field(description="O que uma parte das fontes afirma")
    fontes_a: list[str] = Field(description="Fontes que apoiam a visão A")
    visao_b: str = Field(description="O que a outra parte das fontes afirma")
    fontes_b: list[str] = Field(description="Fontes que apoiam a visão B")

class RelatorioAnalise(BaseModel):
    resumo_geral: str = Field(description="Um resumo executivo sobre o tópico com base em todas as fontes")
    convergentes: list[NoticiaConvergente]
    divergentes: list[NoticiaDivergente]

# Erros transitórios do lado do Google (sobrecarga) que valem nova tentativa
_TRANSIENTES = ("503", "UNAVAILABLE", "overloaded", "high demand", "500", "deadline", "timeout")


def _eh_transitorio(erro):
    texto = str(erro)
    return any(sinal in texto for sinal in _TRANSIENTES)


def analisar_noticias(topico, lista_noticias, tentativas=3):
    """
    Envia as notícias para o Gemini cruzar os dados e extrair convergências e divergências.
    Erros transitórios (503/UNAVAILABLE) são repetidos com espera crescente.
    """
    # Necessário ter a variável de ambiente GEMINI_API_KEY configurada
    client = genai.Client() 
    
    # Prepara o texto de entrada
    texto_noticias = f"Tópico: {topico}\n\nNotícias Coletadas:\n"
    for i, n in enumerate(lista_noticias):
        texto_noticias += f"[{i+1}] Fonte: {n['fonte']} | Título: {n['titulo']} | Resumo: {n['resumo']}\n"

    prompt = (
        "Você é um analista jornalístico imparcial. Analise as notícias fornecidas. "
        "1. Identifique fatos em que múltiplas fontes concordam (Convergentes). "
        "2. Identifique fatos, números, opiniões ou abordagens em que as fontes discordam ou apresentam informações conflitantes (Divergentes)."
    )

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            response = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=[prompt, texto_noticias],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RelatorioAnalise,
                    temperature=0.2,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            ultimo_erro = e
            print(f"Erro na análise (tentativa {tentativa}/{tentativas}): {e}")
            if tentativa < tentativas and _eh_transitorio(e):
                espera = 2 ** tentativa  # 2s, 4s, ...
                print(f"Erro transitório, tentando de novo em {espera}s...")
                time.sleep(espera)
            elif not _eh_transitorio(e):
                break
    print(f"Análise falhou após {tentativas} tentativa(s): {ultimo_erro}")
    return None