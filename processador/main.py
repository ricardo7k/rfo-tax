import base64
import json
import os
import random
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
PROJECT_ID = os.environ.get("GCP_PROJECT", "rfo-tax")
LABEL_BOLETO_PDF = 'BOLETO'
LABEL_BOLETO_LINK = 'BOLETO_LINK'

# --- Lógica de Processamento de PDF (do processador_boletos.py) ---

def find_pdf_attachments(parts):
    for part in parts:
        if 'parts' in part: yield from find_pdf_attachments(part['parts'])
        if part.get('filename') and 'pdf' in part.get('mimeType', '').lower():
            if attachment_id := part['body'].get('attachmentId'):
                yield {'filename': part.get('filename'), 'attachment_id': attachment_id}

def analisar_pdf_com_gemini(api_key, file_data):
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-pro')
        prompt = "Analise este documento de boleto brasileiro e extraia as seguintes informações em formato JSON: \"data_vencimento\" (no formato \"YYYY-MM-DD\"), \"valor\" (como um número, usando ponto como separador decimal), e \"linha_digitavel\". Se não encontrar, retorne \"nao_encontrado\" para o campo específico."
        response = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        
        # Extração robusta do JSON
        match = re.search(r"```json\s*(\{.*?\})\s*```", response.text, re.DOTALL)
        if match:
            json_text = match.group(1)
        else:
            json_text = response.text.replace("```json", "").replace("```", "").strip()
            
        return json.loads(json_text)
    except Exception as e:
        print(f"[PDF] Erro ao analisar com Gemini: {e}")
        return None

def run_pdf_logic(service, message_id, msg):
    print(f"[PDF] Executando lógica para e-mail com anexo PDF.")
    for attachment_info in find_pdf_attachments(msg['payload'].get('parts', [])):
        try:
            print(f"[PDF] Processando anexo: {attachment_info['filename']}")
            attachment = service.users().messages().attachments().get(
                userId='me', messageId=message_id, id=attachment_info['attachment_id']
            ).execute()
            file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
            dados_boleto = analisar_pdf_com_gemini(os.environ.get("GEMINI_API_KEY"), file_data)
            if dados_boleto and dados_boleto.get("data_vencimento") != "nao_encontrado":
                print(f"[PDF] Dados extraídos: {dados_boleto}")
                agendar_notificacao(dados_boleto, "boleto-pdf")
            else:
                print(f"[PDF] Anexo '{attachment_info['filename']}' não é um boleto válido.")
        except Exception as e:
            print(f"[PDF] Erro ao processar anexo {attachment_info.get('filename')}: {e}")
            continue

# --- Lógica de Processamento de Link (do processador_links.py) ---

def get_body_html(parts):
    for part in parts:
        if part.get('mimeType') == 'text/html': return part['body']['data']
        if 'parts' in part:
            if html_data := get_body_html(part['parts']): return html_data
    return None

def find_payment_link_in_body(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    link_keywords = ['pagar', 'boleto', 'fatura', 'pagamento']
    for keyword in link_keywords:
        for attr in ['string', 'href']:
            kwargs = {attr: re.compile(keyword, re.IGNORECASE)}
            if link := soup.find('a', **kwargs):
                if href := link.get('href'): return href
    return None

def analisar_pagina_para_dados_restantes(api_key, html_content):
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-pro')
        prompt = "Analise este conteúdo HTML de uma página de pagamento e extraia as seguintes informações em formato JSON: \"data_vencimento\" (no formato \"YYYY-MM-DD\"), \"valor\" (como um número, usando ponto como separador decimal), e \"mes_referencia\" (o nome do mês a que se refere a cobrança, ex: \"Janeiro\"). Se não encontrar uma informação, retorne \"nao_encontrado\" para o campo correspondente."
        
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        
        response = model.generate_content(
            [prompt, html_content],
            safety_settings=safety_settings
        )

        if response.prompt_feedback.block_reason:
            print(f"[Link] Gemini bloqueou a resposta. Razão: {response.prompt_feedback.block_reason}")
            return None

        # Extração robusta do JSON
        match = re.search(r"```json\s*(\{.*?\})\s*```", response.text, re.DOTALL)
        if match:
            json_text = match.group(1)
        else:
            print("[Link] Não foi possível encontrar o bloco JSON na resposta do Gemini.")
            json_text = response.text.replace("```json", "").replace("```", "").strip()

        if not json_text:
            print("[Link] Gemini retornou uma resposta vazia, mas não foi bloqueado.")
            return None
            
        return json.loads(json_text)

    except json.JSONDecodeError as e:
        print(f"[Link] Erro de decodificação JSON ao analisar HTML com Gemini: {e}")
        raw_response = getattr(response, 'text', '[RESPOSTA_NAO_DISPONIVEL]')
        print(f"[Link] Resposta bruta recebida do Gemini: '{raw_response}'")
        return None
    except Exception as e:
        print(f"[Link] Erro geral ao analisar HTML com Gemini: {e}")
        return None

def run_link_logic(service, message_id, msg):
    print(f"[Link] Executando lógica para e-mail com link de pagamento.")
    html_data = get_body_html(msg['payload'].get('parts', []))
    if not html_data:
        print("[Link] Não foi possível encontrar o corpo HTML do e-mail.")
        return

    html_content = base64.urlsafe_b64decode(html_data).decode('utf-8')
    payment_url = find_payment_link_in_body(html_content)
    if not payment_url:
        print("[Link] Nenhum link de pagamento encontrado.")
        return

    print(f"[Link] Link de pagamento encontrado: {payment_url}")
    try:
        response = requests.get(payment_url, timeout=30)
        response.raise_for_status()
        page_content = response.text
        soup = BeautifulSoup(page_content, 'html.parser')

        hidden_input = soup.select_one("section#section-linhadigitavel input[type=hidden]")
        if not (hidden_input and hidden_input.get('value')):
            print("[Link] ERRO: Não encontrou o link para o mini-html da linha digitável.")
            return

        mini_html_url = hidden_input['value']
        mini_html_response = requests.get(mini_html_url, timeout=30)
        mini_html_response.raise_for_status()
        mini_soup = BeautifulSoup(mini_html_response.text, 'html.parser')

        img_tag = mini_soup.select_one("td.campotitulo img")
        if not (img_tag and img_tag.get('src')):
            print("[Link] ERRO: Não encontrou a tag da imagem ou seu src.")
            return

        parsed_url = urlparse(img_tag['src'])
        linha_digitavel = parse_qs(parsed_url.query).get('l', [None])[0]
        if not linha_digitavel:
            print("[Link] ERRO: Não extraiu o parâmetro 'l' da URL da imagem.")
            return

        dados_gemini = analisar_pagina_para_dados_restantes(os.environ.get("GEMINI_API_KEY"), page_content)
        if not dados_gemini:
            print("[Link] ERRO: Gemini não conseguiu extrair os dados restantes.")
            return

        dados_completos = {**dados_gemini, "linha_digitavel": linha_digitavel}
        print(f"[Link] Dados combinados extraídos: {dados_completos}")
        agendar_notificacao(dados_completos, "boleto-link")

    except requests.RequestException as e:
        print(f"[Link] Erro de rede ao processar o link: {e}")
    except Exception as e:
        print(f"[Link] Erro inesperado no processamento do link: {e}")

# --- Funções Comuns ---

def get_label_id_map(service):
    """Cria um mapa de NOME_LABEL -> ID_LABEL."""
    results = service.users().labels().list(userId='me').execute()
    return {label['name'].upper(): label['id'] for label in results.get('labels', [])}

def agendar_notificacao(dados_boleto, job_prefix):
    try:
        data_vencimento_str = dados_boleto.get("data_vencimento")
        linha_digitavel = dados_boleto.get("linha_digitavel")

        if not all([data_vencimento_str, linha_digitavel]) or "nao_encontrado" in [data_vencimento_str, linha_digitavel]:
            print("[Agendador] Dados incompletos para agendamento.")
            return

        data_vencimento = datetime.strptime(data_vencimento_str.strip(), "%Y-%m-%d")
        schedule_time = data_vencimento.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if schedule_time < datetime.now():
            print(f"[Agendador] A data de vencimento {schedule_time} já passou.")
            return

        sanitized_linha = re.sub(r'[^0-9]', '', linha_digitavel)
        job_id = f"{job_prefix}-{sanitized_linha[-20:]}"
        job_name = f"projects/{PROJECT_ID}/locations/us-central1/jobs/{job_id}"
        
        payload = {
            "valor": dados_boleto.get("valor"),
            "linha_digitavel": linha_digitavel,
            "webhook_url": os.environ.get("GCHAT_WEBHOOK_URL")
        }
        if "mes_referencia" in dados_boleto:
            payload["mes_referencia"] = dados_boleto.get("mes_referencia")

        job = {
            "name": job_name,
            "schedule": f"{schedule_time.minute} {schedule_time.hour} {schedule_time.day} {schedule_time.month} *",
            "time_zone": "America/Sao_Paulo",
            "http_target": {
                "uri": os.environ.get("NOTIFIER_FUNCTION_URL"),
                "http_method": scheduler_v1.HttpMethod.POST,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
            },
        }

        client = scheduler_v1.CloudSchedulerClient()
        parent = f"projects/{PROJECT_ID}/locations/us-central1"
        
        try:
            client.create_job(parent=parent, job=job)
            print(f"[Agendador] Job '{job_id}' agendado para {schedule_time}")
        except exceptions.AlreadyExists:
            print(f"[Agendador] Job '{job_id}' já existe.")
    except Exception as e:
        print(f"[Agendador] Erro ao agendar notificação: {e}")

# --- Função Principal (Ponto de Entrada) ---

@functions_framework.cloud_event
def processador_unificado(cloud_event):
    """
    Função unificada que recebe notificações do Gmail e decide qual lógica de 
    processamento executar com base nos marcadores (labels) do e-mail.
    """
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
        "token_uri": "[https://oauth2.googleapis.com/token](https://oauth2.googleapis.com/token)"
    })
    service = build('gmail', 'v1', credentials=creds)
    label_id_map = get_label_id_map(service)
    
    id_boleto_pdf = label_id_map.get(LABEL_BOLETO_PDF)
    id_boleto_link = label_id_map.get(LABEL_BOLETO_LINK)
    relevant_label_ids = {id_boleto_pdf, id_boleto_link} - {None}

    # 1. Encontrar o ID da mensagem no histórico (lógica corrigida)
    message_id = None
    for i in range(7):
        try:
            history_response = service.users().history().list(userId='me', startHistoryId=history_id).execute()
            changes = history_response.get('history', [])
            
            for change in reversed(changes):
                if 'labelsAdded' in change:
                    for label_add in change['labelsAdded']:
                        added_label_ids = set(label_add.get('labelIds', []))
                        if not relevant_label_ids.isdisjoint(added_label_ids):
                            message_id = label_add['message']['id']
                            print(f"Mensagem encontrada (via labelsAdded) com ID: {message_id}")
                            break
                elif 'messagesAdded' in change:
                    for msg_add in change['messagesAdded']:
                        message_label_ids = set(msg_add['message'].get('labelIds', []))
                        if not relevant_label_ids.isdisjoint(message_label_ids):
                            message_id = msg_add['message']['id']
                            print(f"Mensagem encontrada (via messagesAdded) com ID: {message_id}")
                            break
                if message_id: break
            if message_id: break

        except Exception as e:
            print(f"Erro ao buscar histórico (tentativa {i+1}): {e}")

        if not message_id:
            wait_time = (2 ** i) + (random.randint(0, 1000) / 1000)
            print(f"Mensagem não encontrada na tentativa {i+1}. Aguardando {wait_time:.2f}s...")
            time.sleep(wait_time)
    
    if not message_id:
        print("Mensagem não encontrada no histórico após todas as retentativas.")
        return 'OK'

    try:
        # 2. Obter a mensagem completa com seus marcadores
        msg = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        message_label_ids = msg.get('labelIds', [])

        # 3. Decidir qual lógica executar
        if id_boleto_pdf and id_boleto_pdf in message_label_ids:
            run_pdf_logic(service, message_id, msg)
        elif id_boleto_link and id_boleto_link in message_label_ids:
            run_link_logic(service, message_id, msg)
        else:
            print(f"Mensagem {message_id} não possui os marcadores esperados. Ignorando.")

        # 4. Marcar como lida
        service.users().messages().modify(
            userId='me', id=message_id, body={'removeLabelIds': ['UNREAD']}
        ).execute()
        print(f"Mensagem {message_id} marcada como lida.")

    except Exception as e:
        print(f"Erro durante o processamento da mensagem {message_id}: {e}")

    return 'OK'

