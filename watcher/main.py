import os
import json
import base64
import functions_framework

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.cloud import secretmanager

# --- Configuração ---
# O ID do seu projeto no Google Cloud.
PROJECT_ID = os.environ.get("GCP_PROJECT")
# O nome do tópico do Pub/Sub para onde o Gmail enviará as notificações.
PUB_SUB_TOPIC = "topico-novos-emails"
# Os nomes exatos dos marcadores (labels) que você deseja monitorar.
LABELS_TO_WATCH = ["Boleto", "BOLETO_LINK"]
# Nomes dos segredos individuais no Secret Manager
SECRET_CLIENT_ID_NAME = "GMAIL_CLIENT_ID"
SECRET_CLIENT_SECRET_NAME = "GMAIL_CLIENT_SECRET"
SECRET_REFRESH_TOKEN_NAME = "GMAIL_REFRESH_TOKEN"


def get_credentials_from_secret_manager():
    """
    Busca as credenciais a partir de segredos individuais no Google Secret Manager.
    """
    try:
        client = secretmanager.SecretManagerServiceClient()

        def access_secret(secret_name):
            """Função auxiliar para buscar o valor de um segredo."""
            name = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8")

        # Busca cada segredo individualmente
        client_id = access_secret(SECRET_CLIENT_ID_NAME)
        client_secret = access_secret(SECRET_CLIENT_SECRET_NAME)
        refresh_token = access_secret(SECRET_REFRESH_TOKEN_NAME)

        if not all([client_id, client_secret, refresh_token]):
             raise ValueError("Um ou mais segredos (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN) não foram encontrados ou estão vazios.")

        # Monta a estrutura de credenciais esperada pela biblioteca
        creds_info = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        
        creds = Credentials.from_authorized_user_info(creds_info)
        return creds

    except Exception as e:
        print(f"Erro ao buscar credenciais no Secret Manager: {e}")
        raise

@functions_framework.http
def setup_watch(request):
    """
    Cloud Function acionada por HTTP (via Cloud Scheduler) para iniciar ou renovar o 'watch'.
    Ela para qualquer 'watch' existente e inicia um novo para os marcadores especificados.
    """
    try:
        creds = get_credentials_from_secret_manager()
        service = build('gmail', 'v1', credentials=creds)

        # 1. Para qualquer 'watch' anterior para evitar duplicatas
        try:
            service.users().stop(userId='me').execute()
            print("Watcher existente foi parado com sucesso.")
        except Exception:
            # É normal ocorrer um erro se nenhum watcher estiver ativo.
            print("Nenhum watcher ativo encontrado para parar (comportamento esperado).")

        # 2. Encontra os IDs dos marcadores (labels) a serem monitorados
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])
        
        label_map = {label['name'].upper(): label['id'] for label in labels}
        label_ids_to_watch = [label_map[name.upper()] for name in LABELS_TO_WATCH if name.upper() in label_map]

        if not label_ids_to_watch:
            return "Erro: Nenhum dos marcadores especificados foi encontrado no Gmail.", 500

        print(f"IDs dos marcadores encontrados: {label_ids_to_watch}")

        # 3. Configura e inicia o novo 'watch'
        watch_request = {
            'labelIds': label_ids_to_watch,
            'topicName': f'projects/{PROJECT_ID}/topics/{PUB_SUB_TOPIC}'
        }

        response = service.users().watch(userId='me', body=watch_request).execute()
        
        success_message = f"Watch configurado com sucesso! Monitorando marcadores: {LABELS_TO_WATCH}. Resposta: {response}"
        print(success_message)
        return success_message, 200

    except Exception as e:
        error_message = f"Erro crítico ao configurar o watch: {e}"
        print(error_message)
        return error_message, 500


@functions_framework.cloud_event
def process_gmail_notification(cloud_event):
    """
    Cloud Function acionada por uma mensagem no Pub/Sub quando um novo e-mail chega.
    """
    # Decodifica a mensagem do Pub/Sub
    pubsub_message = base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8")
    message_data = json.loads(pubsub_message)
    email_address = message_data['emailAddress']
    history_id = message_data['historyId']

    print(f"Notificação recebida para {email_address}. HistoryId: {history_id}")

    try:
        creds = get_credentials_from_secret_manager()
        service = build('gmail', 'v1', credentials=creds)

        # Usa o historyId para buscar as mudanças (novas mensagens)
        history = service.users().history().list(
            userId='me',
            startHistoryId=history_id,
            historyTypes=['messageAdded']
        ).execute()

        if 'history' not in history:
            print("Nenhuma mensagem nova encontrada para o historyId.")
            return "OK", 200

        # Processa cada mensagem nova
        for h in history['history']:
            for msg_added in h.get('messagesAdded', []):
                msg_id = msg_added['message']['id']
                message = service.users().messages().get(userId='me', id=msg_id, format='metadata').execute()
                
                headers = {h['name']: h['value'] for h in message['payload']['headers']}
                subject = headers.get('Subject', 'Sem Assunto')
                sender = headers.get('From', 'Desconhecido')
                snippet = message['snippet']

                # Monta a mensagem de notificação
                notification_text = (
                    f"📧 Novo e-mail recebido!\n\n"
                    f"*De:* {sender}\n"
                    f"*Assunto:* {subject}\n"
                    f"*Prévia:* {snippet}"
                )
                
                # Ação: Enviar a notificação
                # Substitua a função abaixo pela integração desejada (Slack, Telegram, etc.)
                send_notification_message(notification_text)

        return "Notificação processada com sucesso.", 200

    except Exception as e:
        error_message = f"Erro ao processar notificação do Gmail: {e}"
        print(error_message)
        return error_message, 500

def send_notification_message(text):
    """
    Função de exemplo para enviar a notificação.
    Aqui você pode integrar com a plataforma que desejar (Slack, Telegram, etc.).
    Por enquanto, ela apenas imprime a mensagem no log.
    """
    print("--- ENVIANDO NOTIFICAÇÃO ---")
    print(text)
    print("----------------------------")

