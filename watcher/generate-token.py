import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

CLIENT_ID = os.environ.get("GCP_CLIENT_GEN")
CLIENT_SECRET = os.environ.get("GCP_SECRET_GEN")

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