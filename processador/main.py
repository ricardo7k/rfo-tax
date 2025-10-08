import base64
import json
import os
import re
import time
from datetime import datetime

from google.cloud import scheduler_v1
from google.api_core import exceptions
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import google.generativeai as genai

# Lista de marcadores que a função deve considerar
GMAIL_LABEL_NAMES = ['BOLETO', 'BOLETO_LINK']

def find_pdf_attachments(parts):
    """
    Percorre recursivamente as partes de um e-mail para encontrar todos os anexos PDF.
    """
    for part in parts:
        if 'parts' in part:
            yield from find_pdf_attachments(part['parts'])
        
        if part.get('filename') and 'pdf' in part.get('mimeType', '').lower():
            if attachment_id := part['body'].get('attachmentId'):
                yield {
                    'filename': part.get('filename'),
                    'attachment_id': attachment_id
                }

def get_label_ids(service, label_names):
    """Busca os IDs de uma lista de marcadores do Gmail pelos nomes."""
    label_ids = []
    results = service.users().labels().list(userId='me').execute()
    labels = results.get('labels', [])
    name_to_id_map = {label['name'].upper(): label['id'] for label in labels}
    for name in label_names:
        if name.upper() in name_to_id_map:
            label_ids.append(name_to_id_map[name.upper()])
    return label_ids

def analisar_com_gemini(api_key, file_data):
    """Usa a API do Gemini para extrair dados de um PDF de boleto."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-pro')
        prompt = "Analise este documento de boleto brasileiro e extraia as seguintes informações em formato JSON: \"data_vencimento\" (no formato \"YYYY-MM-DD\"), \"valor\" (como um número, usando ponto como separador decimal), e \"linha_digitavel\". Se não encontrar, retorne \"nao_encontrado\" para o campo específico."
        response = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        json_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(json_text)
    except Exception as e:
        print(f"Erro ao analisar com Gemini: {e}")
        return None

def agendar_notificacao(dados_boleto):
    """Agenda uma notificação no Cloud Scheduler."""
    try:
        project_id = os.environ.get('GCP_PROJECT')
        location_id = 'us-central1'
        notifier_function_url = os.environ.get('NOTIFIER_FUNCTION_URL')
        gchat_webhook_url = os.environ.get('GCHAT_WEBHOOK_URL')

        data_vencimento_str = dados_boleto.get("data_vencimento")
        linha_digitavel = dados_boleto.get("linha_digitavel")

        if not data_vencimento_str or data_vencimento_str == "nao_encontrado" or len(data_vencimento_str) != 10:
            print(f"Data de vencimento inválida ou não encontrada: {data_vencimento_str}")
            return
        
        if not linha_digitavel or linha_digitavel == "nao_encontrado":
            print(f"Linha digitável não encontrada. Não é possível criar um ID de job único.")
            return

        data_vencimento = datetime.strptime(data_vencimento_str.strip(), "%Y-%m-%d")
        schedule_time = data_vencimento.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if schedule_time < datetime.now():
            print(f"A data de vencimento {schedule_time} já passou. Nenhum job será agendado.")
            return

        cron_expression = f"{schedule_time.minute} {schedule_time.hour} {schedule_time.day} {schedule_time.month} *"

        client = scheduler_v1.CloudSchedulerClient()
        
        sanitized_linha_digitavel = re.sub(r'[^0-9]', '', linha_digitavel)
        job_id = f"boleto-{sanitized_linha_digitavel}"
        job_name = f"projects/{project_id}/locations/{location_id}/jobs/{job_id}"
        
        payload = json.dumps({
            "valor": dados_boleto.get("valor"),
            "linha_digitavel": linha_digitavel,
            "webhook_url": gchat_webhook_url
        }).encode()

        job = {
            "name": job_name,
            "schedule": cron_expression,
            "time_zone": "America/Sao_Paulo",
            "http_target": {
                "uri": notifier_function_url,
                "http_method": scheduler_v1.HttpMethod.POST,
                "headers": {"Content-Type": "application/json"},
                "body": payload,
            },
        }

        parent = f"projects/{project_id}/locations/{location_id}"
        
        try:
            client.create_job(parent=parent, job=job)
            print(f"Job '{job_id}' agendado com sucesso para {schedule_time}")
        except exceptions.AlreadyExists:
            print(f"Job '{job_id}' já existe. Nenhuma ação necessária.")

    except Exception as e:
        print(f"Erro ao agendar notificação: {e}")

def processar_boleto(event, context):
    """Função principal que processa a notificação do Gmail via Pub/Sub."""
    
    print("Função acionada por notificação do Gmail via Pub/Sub.")

    try:
        pubsub_message = base64.b64decode(event['data']).decode('utf-8')
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

    label_ids_to_check = get_label_ids(service, GMAIL_LABEL_NAMES)
    if not label_ids_to_check:
        print(f"Nenhum dos marcadores '{GMAIL_LABEL_NAMES}' foi encontrado.")
        return 'OK'

    message_id = None
    retries = 5
    backoff_seconds = 1

    for i in range(retries):
        history = service.users().history().list(userId='me', startHistoryId=history_id).execute()
        changes = history.get('history', [])
        
        for change in reversed(changes):
            if 'labelsAdded' in change:
                for label_add in change['labelsAdded']:
                    if any(label_id in label_add.get('labelIds', []) for label_id in label_ids_to_check):
                        message_id = label_add['message']['id']
                        print(f"Mensagem encontrada (labelsAdded) com ID: {message_id}")
                        break
            elif 'messagesAdded' in change:
                for msg_add in change['messagesAdded']:
                    if any(label_id in msg_add['message'].get('labelIds', []) for label_id in label_ids_to_check):
                        message_id = msg_add['message']['id']
                        print(f"Mensagem encontrada (messagesAdded) com ID: {message_id}")
                        break
            if message_id: break
        if message_id: break
        
        print(f"Tentativa {i + 1}/{retries}: Mensagem não encontrada. Aguardando {backoff_seconds}s.")
        time.sleep(backoff_seconds)
        backoff_seconds *= 2

    if not message_id:
        print("Mensagem não encontrada no histórico após todas as tentativas.")
        return 'OK'

    try:
        msg = service.users().messages().get(userId='me', id=message_id, format='full').execute()

        for attachment_info in find_pdf_attachments(msg['payload'].get('parts', [])):
            try:
                print(f"Processando anexo: {attachment_info['filename']}")
                attachment = service.users().messages().attachments().get(
                    userId='me', messageId=message_id, id=attachment_info['attachment_id']
                ).execute()
                
                file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
                
                api_key = os.environ.get("GEMINI_API_KEY")
                dados_boleto = analisar_com_gemini(api_key, file_data)
                
                if dados_boleto and dados_boleto.get("data_vencimento") != "nao_encontrado":
                    print(f"Dados extraídos: {dados_boleto}")
                    agendar_notificacao(dados_boleto)
                else:
                    print(f"Anexo '{attachment_info['filename']}' não é um boleto válido.")

            except Exception as e:
                print(f"Erro ao processar anexo {attachment_info.get('filename')}: {e}")
                continue

        service.users().messages().modify(
            userId='me', id=message_id, body={'removeLabelIds': ['UNREAD']}
        ).execute()
        print(f"Mensagem {message_id} marcada como lida.")

    except Exception as e:
        print(f"Ocorreu um erro durante o processamento da mensagem {message_id}: {e}")

    return 'OK'

