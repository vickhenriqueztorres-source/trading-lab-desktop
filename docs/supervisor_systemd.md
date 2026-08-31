# Supervisor externo — IQ Option Worker

O worker deve ser executado em Demo/Practice e supervisionado fora do processo. Exemplo de unit
file systemd:

```ini
[Unit]
Description=Trading Lab IQ Option Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/trading-lab/.venv/bin/python -m apps.iqoption_worker --host 127.0.0.1 --port 9102 --protocol-version 1
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

O equivalente em Docker Compose deve expor somente loopback/uma rede interna e usar health check
funcional, não apenas existência do PID:

```yaml
services:
  iqoption-worker:
    image: trading-lab:worker
    command: python -m apps.iqoption_worker --host 0.0.0.0 --port 9102 --protocol-version 1
    restart: on-failure:5
    healthcheck:
      test: ["CMD", "python", "-c", "import socket; s=socket.create_connection(('127.0.0.1',9102),2); s.close()"]
      interval: 10s
      timeout: 3s
      retries: 3
```

Em Kubernetes, combine probes de processo e prontidão operacional. A imagem não recebe conta Real
nem credencial em texto:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: iqoption-worker
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: worker
        image: trading-lab:worker
        command: ["python", "-m", "apps.iqoption_worker", "--host", "0.0.0.0", "--port", "9102", "--protocol-version", "1"]
        livenessProbe:
          tcpSocket: {port: 9102}
          periodSeconds: 10
        readinessProbe:
          httpGet: {path: /ready, port: 9102}
          periodSeconds: 5
```

O supervisor deve respeitar `READ_ONLY`, `RECONCILING` e `HALTED`: reiniciar um processo não é
autorização para rearmar ordens. O Core mantém o estado e reconcilia antes de voltar a
`trading_readiness`.
