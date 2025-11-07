import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.cloud import secretmanager

# --- Configuração ---
# Substitua pelo ID do seu projeto no Google Cloud.
# Você pode encontrá-lo no console do Google Cloud.
PROJECT_ID = os.environ.get("GCP_PROJECT", "rfo-tax")

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

        client_id = access_secret(SECRET_CLIENT_ID_NAME)
        client_secret = access_secret(SECRET_CLIENT_SECRET_NAME)
        refresh_token = access_secret(SECRET_REFRESH_TOKEN_NAME)

        if not all([client_id, client_secret, refresh_token]):
             raise ValueError("Um ou mais segredos (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN) não foram encontrados ou estão vazios.")

        creds_info = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        
        return Credentials.from_authorized_user_info(creds_info)

    except Exception as e:
        print(f"Erro ao buscar credenciais no Secret Manager: {e}")
        raise

def check_watch_status():
    """
    Verifica se há um 'watch' ativo para a conta do Gmail tentando pará-lo.
    """
    print(f"Verificando 'watch' no projeto '{PROJECT_ID}'...")
    try:
        creds = get_credentials_from_secret_manager()
        service = build('gmail', 'v1', credentials=creds)

        # A API do Gmail não tem um método para "listar" watchers.
        # A forma de verificar é tentando parar o 'watch' existente.
        service.users().stop(userId='me').execute()
        
        print("\n✅ RESULTADO: Um 'watch' estava ativo e foi parado com sucesso.")
        print("   Para reativá-lo, acione a função 'setup_watch' novamente (via Cloud Scheduler ou manualmente).")

    except Exception as e:
        # Um erro 404 é a confirmação de que não havia 'watch' ativo.
        if '404' in str(e):
            print("\nℹ️  RESULTADO: Nenhum 'watch' do Gmail foi encontrado para esta conta.")
        else:
            print(f"\n❌ Ocorreu um erro inesperado: {e}")

if __name__ == '__main__':
    if "SEU_PROJECT_ID" in PROJECT_ID:
        print("⚠️  Atenção: Edite o script e substitua 'SEU_PROJECT_ID' pelo ID do seu projeto Google Cloud.")
    else:
        check_watch_status()
