# rfo-tax - Automação de Boletos com Gemini e Google Cloud

Este projeto automatiza o processo de lembrete de pagamento de boletos recebidos por e-mail, utilizando uma combinação de serviços do Google Cloud e a API do Gemini para extrair informações de documentos PDF.

## Visão Geral

O fluxo de trabalho é o seguinte:

1.  **Recebimento do E-mail**: Um e-mail contendo um boleto em PDF é recebido em uma conta do Gmail e marcado com o label `BOLETO`.
2.  **Disparo do Gatilho**: A aplicação do label no e-mail dispara uma notificação para um tópico do Google Cloud Pub/Sub.
3.  **Processamento do Boleto**: Uma Cloud Function (`processador`) é acionada pela mensagem do Pub/Sub. Ela:
    *   Acessa o e-mail correspondente usando a API do Gmail.
    *   Encontra o anexo em PDF.
    *   Envia o PDF para a API do Gemini, que extrai a data de vencimento, o valor e a linha digitável.
    *   Cria um job no Cloud Scheduler para ser executado na data de vencimento do boleto.
4.  **Notificação**: Na data de vencimento, o Cloud Scheduler aciona uma segunda Cloud Function (`notificador`).
5.  **Envio para o Google Chat**: A função `notificador` formata os dados do boleto e envia uma mensagem de lembrete para um espaço no Google Chat através de um Webhook.

## Estrutura do Projeto

*   `processador/`: Contém o código da Cloud Function principal, responsável por processar os e-mails e agendar as notificações.
    *   `main.py`: O código-fonte da função.
    *   `requirements.txt`: As dependências Python da função.
*   `notificador/`: Contém o código da Cloud Function que envia a notificação para o Google Chat.
    *   `main.py`: O código-fonte da função.
    *   `requirements.txt`: As dependências Python da função.
*   `.watch/`: (Suposição de uso) Diretório para auxiliar no desenvolvimento local. Pode conter scripts para monitorar alterações nos arquivos e reimplantar as funções automaticamente.
*   `README.md`: Este documento.

## Configuração e Pré-requisitos

Siga estes passos para configurar todo o ambiente necessário.

### 1. Google Cloud Project

*   Crie um novo projeto no [Google Cloud Console](https://console.cloud.google.com/).
*   Ative o faturamento para o projeto.
*   Anote o **Project ID**, pois ele será usado em vários comandos.

### 2. Ativar as APIs

Ative as seguintes APIs no seu projeto:

*   **Gmail API**
*   **Google Cloud Pub/Sub API**
*   **Cloud Functions API**
*   **Cloud Scheduler API**
*   **Cloud Build API** (geralmente ativada automaticamente com as Cloud Functions)
*   **Vertex AI API** (para uso do Gemini)
*   **Google Chat API**

Você pode ativá-las usando o `gcloud`:

```bash
gcloud services enable \
    gmail.googleapis.com \
    pubsub.googleapis.com \
    cloudfunctions.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com \
    aiplatform.googleapis.com \
    chat.googleapis.com
```

### 3. Configurar o Gmail

#### a. Criar o Label

*   Acesse sua conta do Gmail.
*   Crie um novo marcador (label) chamado `BOLETO`. O nome deve ser exato.

#### b. Obter Credenciais da API do Gmail

1.  Vá para "APIs & Services" > "Credentials" no Cloud Console.
2.  Clique em "Create Credentials" > "OAuth client ID".
3.  Selecione "Web application" como tipo de aplicação.
4.  Em "Authorized redirect URIs", adicione `https://developers.google.com/oauthplayground`.
5.  Clique em "Create". Anote o **Client ID** e o **Client Secret**.
6.  Acesse o [OAuth 2.0 Playground](https://developers.google.com/oauthplayground).
7.  No canto superior direito, clique na engrenagem, marque "Use your own OAuth credentials" e insira o seu Client ID e Client Secret.
8.  Na Etapa 1, encontre e autorize a API do Gmail v1: `https://mail.google.com/`.
9.  Clique em "Authorize APIs". Faça login na sua conta do Gmail e permita o acesso.
10. Na Etapa 2, clique em "Exchange authorization code for tokens". Copie o **Refresh token** gerado.

### 4. Configurar o Google Chat

1.  Acesse o Google Chat.
2.  Crie um novo espaço (ou use um existente).
3.  Clique no nome do espaço > "Apps & integrações" > "Gerenciar webhooks".
4.  Dê um nome para o webhook (ex: "Notificador de Boletos") e clique em "Salvar".
5.  Copie a **URL do Webhook** gerada.

### 5. Configurar o Pub/Sub

1.  Crie um tópico Pub/Sub:

    ```bash
    gcloud pubsub topics create gmail-triggers
    ```

2.  Conecte o Gmail a este tópico. Isso requer uma ferramenta como o `gs-gmail-sync` ou uma configuração manual via API. A forma mais simples é usar a própria CLI do `gcloud`:

    ```bash
    gcloud alpha pubsub subscriptions create gmail-subscription --topic gmail-triggers
    ```

3.  Assista a sua conta do Gmail para o label `BOLETO`:

    ```bash
    gcloud alpha pubsub subscriptions pull gmail-subscription --auto-ack --limit=1
    ```
    
    Execute o seguinte comando `watch` do Gmail API para o tópico `gmail-triggers` para o label `BOLETO`:
    
    ```bash
    curl -X POST \
      "https://www.googleapis.com/gmail/v1/users/me/watch" \
      -H "Authorization: Bearer $(gcloud auth print-access-token)" \
      -H "Content-Type: application/json" \
      -d 
        "{
          \"labelIds\": [\"<ID_DO_LABEL_BOLETO>\"],
          \"topicName\": \"projects/<SEU_PROJECT_ID>/topics/gmail-triggers\"
        }"
    ```
    
    Para obter o `<ID_DO_LABEL_BOLETO>`, use a API do Gmail ou a ferramenta "Try this API" na documentação do `users.labels.list`.

### 6. Deploy das Cloud Functions

#### a. Variáveis de Ambiente

Você precisará definir as seguintes variáveis de ambiente para as funções. É recomendado usar o Secret Manager para armazenar valores sensíveis.

Para a função `processador`:

*   `GMAIL_REFRESH_TOKEN`: O refresh token obtido no passo 3.
*   `GMAIL_CLIENT_ID`: O client ID obtido no passo 3.
*   `GMAIL_CLIENT_SECRET`: O client secret obtido no passo 3.
*   `GEMINI_API_KEY`: Sua chave de API para o Gemini (obtida no [Google AI Studio](https://aistudio.google.com/app/apikey)).
*   `NOTIFIER_FUNCTION_URL`: A URL da função `notificador` (você obterá após o deploy dela).
*   `GCHAT_WEBHOOK_URL`: A URL do webhook do Google Chat obtida no passo 4.
*   `GCP_PROJECT`: O ID do seu projeto Google Cloud.

Para a função `notificador`:

*   Nenhuma variável de ambiente é estritamente necessária, pois os dados são recebidos no payload.

#### b. Deploy da Função `notificador`

1.  Navegue até o diretório `notificador`.
2.  Execute o comando de deploy:

    ```bash
    gcloud functions deploy notificador \
      --runtime python39 \
      --trigger-http \
      --allow-unauthenticated \
      --region <SUA_REGIAO>
    ```

3.  Após o deploy, anote a **URL do gatilho (Trigger URL)**. Esta será a `NOTIFIER_FUNCTION_URL` para a outra função.

#### c. Deploy da Função `processador`

1.  Navegue até o diretório `processador`.
2.  Crie um arquivo `.env.yaml` com as variáveis de ambiente:

    ```yaml
    GMAIL_REFRESH_TOKEN: 'SEU_REFRESH_TOKEN'
    GMAIL_CLIENT_ID: 'SEU_CLIENT_ID'
    GMAIL_CLIENT_SECRET: 'SEU_CLIENT_SECRET'
    GEMINI_API_KEY: 'SUA_API_KEY_GEMINI'
    NOTIFIER_FUNCTION_URL: 'URL_DA_FUNCAO_NOTIFICADOR'
    GCHAT_WEBHOOK_URL: 'URL_DO_WEBHOOK_CHAT'
    GCP_PROJECT: 'SEU_PROJECT_ID'
    ```

3.  Execute o comando de deploy:

    ```bash
    gcloud functions deploy processador \
      --runtime python39 \
      --trigger-topic gmail-triggers \
      --env-vars-file .env.yaml \
      --region <SUA_REGIAO>
    ```

### 7. Permissões

A conta de serviço usada pela Cloud Function `processador` (geralmente `[PROJECT_ID]@[PROJECT_ID].iam.gserviceaccount.com`) precisa da permissão `Cloud Scheduler Admin` para criar jobs.

```bash
gcloud projects add-iam-policy-binding <SEU_PROJECT_ID> \
  --member="serviceAccount:<SEU_PROJECT_ID>@<SEU_PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/cloudscheduler.admin"
```

## Uso

1.  Envie um e-mail para a conta do Gmail configurada com um boleto em PDF como anexo.
2.  Aplique o label `BOLETO` a este e-mail.
3.  Monitore os logs da função `processador` no Cloud Console para ver o processamento.
4.  Verifique no Cloud Scheduler se um novo job foi criado com o nome `boleto-<linha_digitavel>`.
5.  Na data de vencimento, às 9h da manhã (fuso horário de São Paulo), a função `notificador` será acionada e enviará uma mensagem para o espaço configurado no Google Chat.

## Desenvolvimento Local (`.watch`)

O diretório `.watch` pode ser usado com ferramentas de `hot-reload` para desenvolvimento local. Por exemplo, você pode usar `watchexec` ou `nodemon` para monitorar alterações nos arquivos `.py` e automaticamente rodar testes ou reimplantar as funções em um ambiente de desenvolvimento.

Exemplo com `watchexec`:

```bash

# Instale watchexec: https://watchexec.github.io/
watchexec -w ./processador/ -c -- "gcloud functions deploy processador --source=./processador/ ..."
```

Este comando irá monitorar o diretório `processador` e reimplantar a função sempre que um arquivo for alterado.
