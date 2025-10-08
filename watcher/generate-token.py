import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

# Carrega as variáveis de ambiente do arquivo .env na raiz do projeto
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)

# Carrega as credenciais a partir de variáveis de ambiente
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

# Validação para garantir que as variáveis foram carregadas
if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError("As variáveis de ambiente CLIENT_ID e CLIENT_SECRET devem ser definidas no arquivo .env")

flow = Flow.from_client_config(
    client_config={
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"],
        }
    },
    scopes=['https://www.googleapis.com/auth/gmail.modify'],
    redirect_uri="http://localhost:8080/"
)

auth_url, _ = flow.authorization_url(prompt='consent')
print(f'Acesse esta URL e autorize: {auth_url}')

code = input('Cole o código da URL de retorno aqui: ')
flow.fetch_token(code=code)

print("\n\n--- SEU REFRESH TOKEN ---")
print(flow.credentials.refresh_token)
print("---------------------------\n")