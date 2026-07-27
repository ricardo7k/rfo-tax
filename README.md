Guia de Deploy - Automação Completa de Boletos
Este guia descreve como configurar e fazer o deploy de todas as Cloud Functions que compõem o sistema de automação de boletos: Watcher, Processador e Notificador.

## Pré-requisitos

1.  **Projeto Google Cloud:** Tenha um projeto no GCP com faturamento ativado.
2.  **APIs Habilitadas:** Ative as APIs: Gmail, Cloud Functions, Cloud Build, Cloud Scheduler, Secret Manager, e Pub/Sub.
3.  **Credenciais OAuth 2.0:** Crie um "ID do cliente OAuth 2.0" para "Aplicativo da Web" no [console de APIs](https://console.cloud.google.com/apis/credentials). Baixe o JSON e guarde o `client_id` e `client_secret`.
4.  **Etiquetas no Gmail:** Crie as etiquetas (labels) `Boleto` e `BOLETO_LINK` no seu Gmail.

## Passo 0: Configuração Inicial e Geração de Token

Antes do deploy, você precisa gerar um `refresh_token` que permitirá que as funções acessem seu Gmail de forma segura.

1.  **Clone o repositório** e navegue até a raiz do projeto.

2.  **Crie um arquivo `.env`** na raiz do projeto com o seguinte conteúdo, usando as credenciais do passo anterior:
    ```
    CLIENT_ID="SEU_CLIENT_ID_AQUI"
    CLIENT_SECRET="SEU_CLIENT_SECRET_AQUI"
    ```

3.  **Instale as dependências** do script de geração de token:
    ```bash
    pip install -r watcher/requirements.txt
    ```

4.  **Execute o script `generate-token.py`** e siga as instruções no terminal:
    ```bash
    python watcher/generate-token.py
    ```
    - Você será direcionado para uma página de autorização do Google. Faça login e autorize o acesso.
    - Copie o código da URL de redirecionamento e cole-o de volta no terminal.
    - O script imprimirá seu `refresh_token`. **Guarde-o com segurança.**

5.  **Armazene os Segredos no Secret Manager:** Crie os seguintes segredos no Google Cloud Secret Manager e adicione os valores correspondentes:
    - `GMAIL_CLIENT_ID`: Seu Client ID.
    - `GMAIL_CLIENT_SECRET`: Seu Client Secret.
    - `GMAIL_REFRESH_TOKEN`: O Refresh Token gerado no passo anterior.
    - `GEMINI_API_KEY`: Sua chave de API para o Gemini.
    - `GCHAT_WEBHOOK_URL`: A URL do webhook do seu Google Chat para notificações.
    - `NOTIFIER_FUNCTION_URL`: A URL da função `enviar-notificacao` (você obterá isso após o deploy no Passo 3).

Arquitetura do Sistema
O fluxo de trabalho é o seguinte:

**Watcher (`setup-watch`):** Uma função que inicia o monitoramento do seu Gmail. Ela instrui o Google a enviar uma notificação para um tópico Pub/Sub sempre que um e-mail com as etiquetas `Boleto` ou `BOLETO_LINK` chegar. Esta função deve ser executada uma vez para iniciar e depois periodicamente (a cada 5 dias) para renovar o monitoramento.

**Processador (`processador-unificado`):** Esta função é acionada pelo tópico Pub/Sub quando um novo e-mail chega. Ela baixa o e-mail, analisa o PDF ou o link com a ajuda do Gemini e, se encontrar um boleto válido, agenda uma tarefa no Cloud Scheduler para o dia do vencimento.

**Notificador (`enviar-notificacao`):** Esta é a última etapa. No dia do vencimento, o Cloud Scheduler aciona esta função, que envia um lembrete com os detalhes do boleto para o seu Google Chat.

Passo 1: Deploy do Watcher
Esta função inicia o monitoramento do seu Gmail.

1.  Navegue até o diretório `watcher` no seu terminal:

    ```bash
    cd watcher
    ```

2.  Execute o comando de deploy:
    Esta função é acionada por HTTP, permitindo que você a execute manualmente através da sua URL para iniciar ou renovar o monitoramento.

    ```bash
    gcloud functions deploy setup-watch \
      --gen2 \
      --region=us-central1 \
      --runtime=python311 \
      --source=. \
      --entry-point=setup_watch \
      --trigger-http \
      --allow-unauthenticated \
      --set-secrets="GMAIL_REFRESH_TOKEN=GMAIL_REFRESH_TOKEN:latest,GMAIL_CLIENT_ID=GMAIL_CLIENT_ID:latest,GMAIL_CLIENT_SECRET=GMAIL_CLIENT_SECRET:latest"
    ```

    *(Nota: O `entry-point` `setup_watch` refere-se à função que inicia o monitoramento. A outra função `process_gmail_notification` no mesmo arquivo é um exemplo e não é usada no fluxo principal.)*

Passo 2: Deploy do Processador
Esta função processa os e-mails recebidos.

1.  Navegue até o diretório `processador`:

    ```bash
    cd ../processador  # Use 'cd processador' se estiver na raiz
    ```

2.  Execute o comando de deploy:
    Ele conecta a função ao tópico Pub/Sub `topico-novos-emails` que o Watcher alimenta.

    ```bash
    gcloud functions deploy processador-unificado \
      --gen2 \
      --region=us-central1 \
      --runtime=python311 \
      --source=. \
      --entry-point=processador_unificado \
      --trigger-topic=topico-novos-emails \
      --timeout=540s \
      --memory=256Mi \
      --set-secrets="GCHAT_WEBHOOK_URL=GCHAT_WEBHOOK_URL:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,GMAIL_CLIENT_ID=GMAIL_CLIENT_ID:latest,GMAIL_CLIENT_SECRET=GMAIL_CLIENT_SECRET:latest,GMAIL_REFRESH_TOKEN=GMAIL_REFRESH_TOKEN:latest,NOTIFIER_FUNCTION_URL=NOTIFIER_FUNCTION_URL:latest"
    ```

Passo 3: Deploy do Notificador
Esta função envia o lembrete final para o Google Chat.

1.  Navegue até o diretório `notificador`:

    ```bash
    cd ../notificador  # Use 'cd notificador' se estiver na raiz
    ```

2.  Execute o comando de deploy:
    Esta função será acionada pelo Cloud Scheduler.

    ```bash
    gcloud functions deploy enviar-notificacao \
      --gen2 \
      --region=us-central1 \
      --runtime=python311 \
      --source=. \
      --entry-point=enviar_notificacao_chat \
      --trigger-http \
      --allow-unauthenticated
    ```

3.  **Atualize o Segredo:** Após o deploy, copie a URL de gatilho (Trigger URL) da função `enviar-notificacao` e atualize o valor do segredo `NOTIFIER_FUNCTION_URL` no Secret Manager.

Como Iniciar o Sistema
Após o deploy das três funções, acesse a URL da função `setup-watch` uma vez pelo seu navegador. Isso iniciará o monitoramento. Você deverá ver uma mensagem de sucesso.

Para manter o monitoramento ativo, crie uma tarefa no Cloud Scheduler para chamar a URL da função setup-watch a cada 5 dias.