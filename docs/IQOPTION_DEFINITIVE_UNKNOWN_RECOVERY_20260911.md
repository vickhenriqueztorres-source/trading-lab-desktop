# IQ Option — recuperação definitiva de ordem UNKNOWN

Data: 11/09/2026

## Resultado

Esta versão corrige a perda de `not_found_evidence` na rota realmente iniciada e
empacotada pelo EXE (`apps.iqoption_connection_worker`). A serialização da resposta de
status agora é compartilhada entre os dois workers, impedindo divergência futura do
contrato IPC.

Também foram endurecidos:

- correlação e isolamento single-flight da resposta legada `get-options`;
- invalidação da conexão após timeout de resposta sem `request_id`;
- rejeição de resposta com identificador pertencente a outra consulta;
- cobertura negativa de histórico, sem inferir completude apenas pelo tamanho da lista;
- identificação por fingerprint, que agora rejeita candidatos incompletos ou duplicados;
- normalização de `active_id` numérico para o símbolo canônico;
- projeção da UI, separando verificação ativa de verificação inconclusiva.

A política financeira permanece fail-closed: a ordem original não é reenviada, a
reserva não é liberada por tempo e o Core exige duas observações negativas completas
separadas pelo intervalo de confirmação.

## Validação

- Ruff: aprovado;
- mypy: 323 arquivos aprovados;
- pytest: 1.481 aprovados, 4 ignorados por plataforma/opt-in externo;
- compileall: aprovado;
- pip check: aprovado;
- PyInstaller 6.22.2: aprovado;
- scanner da distribuição: zero segredos;
- manifesto: 448 arquivos, verificação aprovada;
- health check do onedir: aprovado;
- payload portátil: 890 entradas e `TradingLab.exe` presente;
- fonte, onedir e payload possuem a mesma cópia do worker de produção;
- SHA-256 do payload incorporado confere com o ZIP de origem.

## Artefato

`dist/iqr2/TradingLab-Desktop-v1.9.11-IQ-DEFINITIVE-RECOVERY.exe`

SHA-256: `46EC0B6EE420CF828D92B36BD3F3BA9B74D24BBE82ADCDCF6EBF986FA1BE09EE`
