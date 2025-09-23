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

GMAIL_LABEL_NAME = 'BOLETO'

def find_pdf_attachments(parts):
    """
    Percorre recursivamente as partes de um e-mail para encontrar todos os anexos PDF.
    Yields (gera) os dados de cada anexo encontrado.
    """
    for part in parts:
        if 'parts' in part:
            yield from find_pdf_attachments(part['parts'])
        
        if part.get('filename') and 'pdf' in part.get('mimeType', '').lower():
            attachment_id = part['body'].get('attachmentId')
            if attachment_id:
                yield {
                    'filename': part.get('filename'),
                    'attachment_id': attachment_id
                }

def get_label_id(service, label_name):
    """Busca o ID de um marcador do Gmail pelo nome."""
    results = service.users().labels().list(userId='me').execute()
    labels = results.get('labels', [])
    for label in labels:
        if label['name'].lower() == label_name.lower():
            return label['id']
    return None

def processar_boleto(event, context):
    """Função principal que processa a notificação do Gmail via Pub/Sub."""
    
    print("Função acionada por notificação do Gmail via Pub/Sub.")

    # 1. Decodificar a mensagem do Pub/Sub
    try:
        pubsub_message = base64.b64decode(event['data']).decode('utf-8')
        message_data = json.loads(pubsub_message)
        history_id = message_data['historyId']
    except Exception as e:
        print(f"Erro ao decodificar mensagem do Pub/Sub: {e}")
        return 'OK'

    # 2. Configurar credenciais e serviço do Gmail
    creds = Credentials.from_authorized_user_info(info={
        "refresh_token": os.environ.get("GMAIL_REFRESH_TOKEN"),
        "client_id": os.environ.get("GMAIL_CLIENT_ID"),
        "client_secret": os.environ.get("GMAIL_CLIENT_SECRET"),
        "token_uri": "https://oauth2.googleapis.com/token"
    })
    service = build('gmail', 'v1', credentials=creds)

    # 3. Obter o ID do marcador 'BOLETO'
    label_id_boleto = get_label_id(service, GMAIL_LABEL_NAME)
    if not label_id_boleto:
        print(f"O marcador '{GMAIL_LABEL_NAME}' não foi encontrado na sua conta do Gmail.")
        return 'OK'

    # 4. Usar o historyId para encontrar a mensagem exata, com retentativas  
    # Tenta por até 5 vezes
    message_id = None
    retries = 5
    backoff_seconds = 1

    for i in range(retries):
        history = service.users().history().list(userId='me', startHistoryId=history_id).execute()
        changes = history.get('history', [])
        
        for change in reversed(changes):
            if 'labelsAdded' in change:
                for label_add in change['labelsAdded']:
                    if label_id_boleto in label_add.get('labelIds', []):
                        message_id = label_add['message']['id']
                        print(f"Mensagem encontrada via histórico (labelsAdded) com ID: {message_id}")
                        break
            elif 'messagesAdded' in change:
                for msg_add in change['messagesAdded']:
                    if label_id_boleto in msg_add['message'].get('labelIds', []):
                        message_id = msg_add['message']['id']
                        print(f"Mensagem encontrada via histórico (messagesAdded) com ID: {message_id}")
                        break
            if message_id:
                break
        
        if message_id:
            break

        print(f"Tentativa {i + 1}/{retries}: Mensagem não encontrada no histórico. Aguardando {backoff_seconds}s.")
        time.sleep(backoff_seconds)
        backoff_seconds *= 2

    if not message_id:
        print("Mensagem não encontrada no histórico após todas as tentativas. Encerrando.")
        return 'OK'

    # 5. Processar a mensagem encontrada
    try:
        msg = service.users().messages().get(userId='me', id=message_id, format='full').execute()

        for attachment_info in find_pdf_attachments(msg['payload'].get('parts', [])):
            try:
                print(f"Processando anexo PDF: {attachment_info['filename']}")
                attachment = service.users().messages().attachments().get(
                    userId='me', messageId=message_id, id=attachment_info['attachment_id']
                ).execute()
                
                file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
                
                genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
                dados_boleto = analisar_com_gemini(file_data)
                
                if dados_boleto and dados_boleto.get("data_vencimento") and dados_boleto.get("data_vencimento") != "nao_encontrado":
                    print(f"Dados extraídos pelo Gemini: {dados_boleto}")
                    agendar_notificacao(dados_boleto)
                else:
                    print(f"Anexo '{attachment_info['filename']}' não é um boleto válido ou não contém data de vencimento. Ignorando.")

            except Exception as e:
                print(f"Erro inesperado ao processar o anexo {attachment_info.get('filename')}: {e}")
                continue

        # 6. Marcar o e-mail como lido para não reprocessar
        service.users().messages().modify(
            userId='me', id=message_id, body={'removeLabelIds': ['UNREAD']}
        ).execute()
        print(f"Mensagem {message_id} marcada como lida.")

    except Exception as e:
        print(f"Ocorreu um erro durante o processamento do histórico {history_id}: {e}")

    return 'OK'

def analisar_com_gemini(file_data):
    try:
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        prompt = "Analise este documento de boleto brasileiro e extraia as seguintes informações em formato JSON: \"data_vencimento\" (no formato \"YYYY-MM-DD\"), \"valor\" (como um número, usando ponto como separador decimal), e \"linha_digitavel\". Se não encontrar, retorne \"nao_encontrado\"."
        response = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        json_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(json_text)
    except Exception as e:
        print(f"Erro ao analisar com Gemini: {e}")
        return None

def agendar_notificacao(dados_boleto):
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