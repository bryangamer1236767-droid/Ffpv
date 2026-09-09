# Free Fire Private Server

Servidor privado para Free Fire com API HTTP + Game Server TCP/UDP.

## Deploy no Railway

1. Criar conta em railway.app
2. New Project -> Deploy from GitHub
3. Subir os arquivos deste repositório
4. Railway gera a URL automaticamente

## Variáveis de Ambiente

- PORT=5000 (auto no Railway)
- GAME_SERVER_IP=seu_ip (IP do servidor de jogo)
- GAME_SERVER_PORT=2205

## Endpoints

- /health - status do servidor
- /app/info/get - config do jogo
- /oauth/guest/register - registar guest
- /oauth/guest/token/grant - login guest
- /oauth/token - trocar código por token
- /api/heartbeat - manter sessão
- /api/msdk - config e servidor de jogo
- /oauth/user/info/get - info do jogador
- /app/point/get_balance - saldo (99999 diamantes)
