import json
import requests

def enviar_notificacao_chat(request):
    """Função disparada pelo Cloud Scheduler para enviar a mensagem."""

    request_json = request.get_json(silent=True)
    if not request_json:
        return "Erro: Payload inválido.", 400

    # --- INÍCIO DA CORREÇÃO ---
    # Leitura mais segura dos dados para evitar valores 'None' (nulos)
    
    # Pega o valor. Se for None (nulo) ou não existir, usa "N/A"
    valor = request_json.get("valor") or "N/A" 
    
    # Pega a linha digitável. Se for None (nulo) ou não existir, usa "N/A"
    linha_digitavel = request_json.get("linha_digitavel") or "N/A"
    
    webhook_url = request_json.get("webhook_url")
    # --- FIM DA CORREÇÃO ---

    if not webhook_url:
        return "Erro: URL do Webhook não fornecida.", 400

    # Formata o valor para o padrão brasileiro
    try:
        # str(valor) protege caso o valor seja "N/A"
        valor_formatado = f"R$ {float(str(valor)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        valor_formatado = str(valor)

    # Card com seções separadas (estrutura que testamos antes)
    mensagem = {
        "cardsV2": [{
            "cardId": "boleto-lembrete-com-copia",
            "card": {
                "sections": [
                    {
                        # SEÇÃO 1: Textos
                        "widgets": [
                            { "decoratedText": { "topLabel": "Valor", "text": f"<b>{valor_formatado}</b>" }},
                            { "decoratedText": { "topLabel": "Linha Digitável", "text": linha_digitavel }}
                        ]
                    },
                    {
                        # SEÇÃO 2: Botão
                        "widgets": [
                            {
                                "buttonList": {
                                    "buttons": [
                                        {
                                            "text": "Copiar Linha Digitável",
                                            "onClick": {
                                                "copyToClipboard": {
                                                    # Aqui 'linha_digitavel' tem garantia de ser "N/A" ou um valor real
                                                    "text": linha_digitavel 
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        }]
    }

    try:
        response = requests.post(webhook_url, json=mensagem)
        response.raise_for_status() # Lança um erro se o status não for 2xx
        print("Mensagem enviada com sucesso para o Google Chat.")
        return "OK", 200
    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar mensagem para o Google Chat: {e}")
        # Para depuração, é útil ver o que o Google respondeu
        print(f"Detalhe do erro: {e.response.text if e.response else 'Sem resposta'}") 
        return "Erro ao enviar mensagem.", 500