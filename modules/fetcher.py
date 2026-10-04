from ddgs import DDGS

def buscar_noticias(topico, max_resultados=15):
    """
    Busca notícias de múltiplas fontes sobre um tópico.
    """
    resultados = []
    with DDGS() as ddgs:
        # CORREÇÃO AQUI: Alterado de 'keywords=' para 'query='
        noticias_ddg = ddgs.news(query=topico, max_results=max_resultados)
        
        for n in noticias_ddg:
            resultados.append({
                "titulo": n.get("title"),
                "fonte": n.get("source"),
                "data": n.get("date"),
                "resumo": n.get("body"),
                "url": n.get("url")
            })
            
    return resultados