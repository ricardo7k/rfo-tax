import json
import requests

def enviar_notificacao_chat(request):
    """Função disparada pelo Cloud Scheduler para enviar a mensagem."""

    request_json = request.get_json(silent=True)
    if not request_json:
        return "Erro: Payload inválido.", 400

    valor = request_json.get("valor", "N/A")
    linha_digitavel = request_json.get("linha_digitavel", "N/A")
    webhook_url = request_json.get("webhook_url")

    if not webhook_url:
        return "Erro: URL do Webhook não fornecida.", 400

    # Formata o valor para o padrão brasileiro
    try:
        valor_formatado = f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        valor_formatado = str(valor)

    # Card ultra-simplificado para depuração
    mensagem = {
        "cardsV2": [{
            "cardId": "boleto-lembrete-simplificado",
            "card": {
                "sections": [{
                    "widgets": [
                        { "decoratedText": { "topLabel": "Valor", "text": f"<b>{valor_formatado}</b>" }},
                        { "decoratedText": { "topLabel": "Linha Digitável", "text": linha_digitavel }}
                    ]
                }]
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
        return "Erro ao enviar mensagem.", 500
