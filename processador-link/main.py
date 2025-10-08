import base64
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import functions_framework
import requests
from bs4 import BeautifulSoup
from google.api_core import exceptions
from google.cloud import scheduler_v1
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import google.generativeai as genai

# --- Configuração ---
GMAIL_LABEL_NAME = 'BOLETO_LINK'
PROJECT_ID = os.environ.get("GCP_PROJECT", "rfo-tax")

# --- Funções Auxiliares ---

def get_body_html(parts):
    """Percorre as partes do e-mail para encontrar o corpo em HTML."""
    for part in parts:
        if part.get('mimeType') == 'text/html':
            return part['body']['data']
        if 'parts' in part:
            html_data = get_body_html(part['parts'])
            if html_data:
                return html_data
    return None

def find_payment_link_in_body(html_content):
    """Usa BeautifulSoup para encontrar um link de pagamento no HTML."""
    soup = BeautifulSoup(html_content, 'html.parser')
    link_keywords = ['pagar', 'boleto', 'fatura', 'pagamento']
    for keyword in link_keywords:
        link = soup.find('a', string=re.compile(keyword, re.IGNORECASE))
        if link and link.get('href'):
            return link.get('href')
    for keyword in link_keywords:
        link = soup.find('a', href=re.compile(keyword, re.IGNORECASE))
        if link and link.get('href'):
            return link.get('href')
    print("Nenhum link de pagamento encontrado no corpo do e-mail.")
    return None

def get_label_id(service, label_name):
    """Busca o ID de um marcador do Gmail pelo nome."""
    results = service.users().labels().list(userId='me').execute()
    labels = results.get('labels', [])
    for label in labels:
        if label['name'].upper() == label_name.upper():
            return label['id']
    return None

def analisar_pagina_para_dados_restantes(api_key, html_content):
    """Usa o Gemini para extrair data, valor e mês do HTML."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-pro')
        prompt = "Analise este conteúdo HTML de uma página de pagamento e extraia as seguintes informações em formato JSON: \"data_vencimento\" (no formato \"YYYY-MM-DD\"), \"valor\" (como um número, usando ponto como separador decimal), e \"mes_referencia\" (o nome do mês a que se refere a cobrança, ex: \"Janeiro\"). Ignore a linha digitável. Se não encontrar uma informação, retorne \"nao_encontrado\" para o campo correspondente."
        response = model.generate_content([prompt, html_content])
        json_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(json_text)
    except Exception as e:
        print(f"Erro ao analisar HTML com Gemini: {e}")
        return None

def agendar_notificacao(dados_boleto):
    """Agenda uma notificação no Cloud Scheduler."""
    try:
        data_vencimento_str = dados_boleto.get("data_vencimento")
        linha_digitavel = dados_boleto.get("linha_digitavel")

        if not all([data_vencimento_str, linha_digitavel]) or "nao_encontrado" in [data_vencimento_str, linha_digitavel]:
            print("Dados incompletos para agendamento.")
            return

        data_vencimento = datetime.strptime(data_vencimento_str.strip(), "%Y-%m-%d")
        schedule_time = data_vencimento.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if schedule_time < datetime.now():
            print(f"A data de vencimento {schedule_time} já passou. Job não agendado.")
            return

        sanitized_linha = re.sub(r'[^0-9]', '', linha_digitavel)
        job_id = f"boleto-link-{sanitized_linha[-15:]}"
        job_name = f"projects/{PROJECT_ID}/locations/us-central1/jobs/{job_id}"
        
        payload = json.dumps({
            "valor": dados_boleto.get("valor"),
            "linha_digitavel": linha_digitavel,
            "mes_referencia": dados_boleto.get("mes_referencia"),
            "webhook_url": os.environ.get("GCHAT_WEBHOOK_URL")
        }).encode()

        job = {
            "name": job_name,
            "schedule": f"{schedule_time.minute} {schedule_time.hour} {schedule_time.day} {schedule_time.month} *",
            "time_zone": "America/Sao_Paulo",
            "http_target": {
                "uri": os.environ.get("NOTIFIER_FUNCTION_URL"),
                "http_method": scheduler_v1.HttpMethod.POST,
                "headers": {"Content-Type": "application/json"},
                "body": payload,
            },
        }

        client = scheduler_v1.CloudSchedulerClient()
        parent = f"projects/{PROJECT_ID}/locations/us-central1"
        
        try:
            client.create_job(parent=parent, job=job)
            print(f"Job '{job_id}' agendado com sucesso para {schedule_time}")
        except exceptions.AlreadyExists:
            print(f"Job '{job_id}' já existe.")
    except Exception as e:
        print(f"Erro ao agendar notificação: {e}")

@functions_framework.cloud_event
def processar_link_de_pagamento_gen2(cloud_event):
    """Função principal (Gen2) que processa a notificação do Gmail via Pub/Sub."""
    try:
        pubsub_message = base64.b64decode(cloud_event.data["message"]["data"]).decode('utf-8')
        message_data = json.loads(pubsub_message)
        history_id = message_data['historyId']
    except Exception as e:
        print(f"Erro ao decodificar mensagem do Pub/Sub: {e}")
        return 'OK'

    creds = Credentials.from_authorized_user_info(info={
        "refresh_token": os.environ.get("GMAIL_REFRESH_TOKEN"),
        "client_id": os.environ.get("GMAIL_CLIENT_ID"),
        "client_secret": os.environ.get("GMAIL_CLIENT_SECRET"),
        "token_uri": "https://oauth2.googleapis.com/token"
    })
    service = build('gmail', 'v1', credentials=creds)

    label_id_boleto_link = get_label_id(service, GMAIL_LABEL_NAME)
    if not label_id_boleto_link:
        print(f"O marcador '{GMAIL_LABEL_NAME}' não foi encontrado.")
        return 'OK'

    message_id = None
    for i in range(5):
        try:
            history = service.users().history().list(userId='me', startHistoryId=history_id, historyTypes=['messageAdded']).execute()
            if 'history' in history:
                message_id = history['history'][0]['messages'][0]['id']
                print(f"Mensagem encontrada via histórico com ID: {message_id}")
                break
        except Exception:
            time.sleep(2**i)
    
    if not message_id:
        print("Mensagem não encontrada no histórico após retentativas.")
        return 'OK'

    try:
        msg = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        html_data = get_body_html(msg['payload'].get('parts', []))
        if not html_data:
            print("Não foi possível encontrar o corpo HTML do e-mail.")
            return 'OK'

        html_content = base64.urlsafe_b64decode(html_data).decode('utf-8')
        payment_url = find_payment_link_in_body(html_content)

        if payment_url:
            print(f"Link de pagamento encontrado: {payment_url}")
            try:
                response = requests.get(payment_url, timeout=30)
                response.raise_for_status()
                page_content = response.text
                soup = BeautifulSoup(page_content, 'html.parser')
                
                hidden_input = soup.select_one("section#section-linhadigitavel input[type=hidden]")
                if not (hidden_input and hidden_input.get('value')):
                    print("ERRO: Não encontrou o link para o mini-html da linha digitável.")
                    return 'OK'
                
                mini_html_url = hidden_input['value']
                mini_html_response = requests.get(mini_html_url, timeout=30)
                mini_html_response.raise_for_status()
                mini_soup = BeautifulSoup(mini_html_response.text, 'html.parser')

                img_tag = mini_soup.select_one("td.campotitulo img")
                if not (img_tag and img_tag.get('src')):
                    print("ERRO: Não encontrou a tag da imagem ou seu src.")
                    return 'OK'
                
                parsed_url = urlparse(img_tag['src'])
                linha_digitavel = parse_qs(parsed_url.query).get('l', [None])[0]

                if not linha_digitavel:
                    print("ERRO: Não extraiu o parâmetro 'l' da URL da imagem.")
                    return 'OK'

                dados_gemini = analisar_pagina_para_dados_restantes(os.environ.get("GEMINI_API_KEY"), page_content)
                if not dados_gemini:
                    print("ERRO: Gemini não conseguiu extrair os dados restantes.")
                    return 'OK'

                dados_completos = {**dados_gemini, "linha_digitavel": linha_digitavel}
                print(f"Dados combinados extraídos: {dados_completos}")
                agendar_notificacao(dados_completos)

            except requests.RequestException as e:
                print(f"Erro de rede ao processar o link: {e}")
            except Exception as e:
                print(f"Erro inesperado no processamento do link: {e}")

        service.users().messages().modify(
            userId='me', id=message_id, body={'removeLabelIds': ['UNREAD']}
        ).execute()
        print(f"Mensagem {message_id} marcada como lida.")

    except Exception as e:
        print(f"Erro durante o processamento da mensagem {message_id}: {e}")

    return 'OK'
