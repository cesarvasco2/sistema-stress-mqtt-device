# Sistema de Stress para Devices MQTT

Plataforma web para testes de dispositivos MQTT com:

- Broker MQTT embutido no mesmo processo do FastAPI.
- Configuração de usuário/senha para conexão MQTT.
- Monitoramento de múltiplos devices simultâneos.
- Contadores de pacotes e bytes enviados/recebidos por device.
- Publicação manual de mensagens (Pub) e cadastro de tópicos monitorados (Sub).
- Cadastro e execução de comandos:
  - Manual
  - Por intervalo de tempo
  - Por condição baseada no último dado recebido
- Atualização em tempo real via WebSocket.

## Executar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
fastapi run app/main.py
```

Também funciona com Uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Credenciais MQTT

Por padrão:

- Usuário: `admin`
- Senha: `admin123`
- Broker: `localhost:1883`

Você pode alterar via variáveis de ambiente:

```bash
export MQTT_USER=meu_usuario
export MQTT_PASSWORD=minha_senha
export MQTT_PORT=1883
fastapi run app/main.py
```

## Sugestão de padrão de tópicos

- Telemetria: `devices/<device_id>/telemetry`
- Comandos: `devices/<device_id>/cmd`

