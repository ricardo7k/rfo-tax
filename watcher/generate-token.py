from google_auth_oauthlib.flow import Flow

CLIENT_ID = "586541384046-br8l2sjk5498lkrq5gpttukr5u7i3hsb.apps.googleusercontent.com"
CLIENT_SECRET = "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"

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