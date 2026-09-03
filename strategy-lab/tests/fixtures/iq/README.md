# Fixtures IQ Option — R-VEND-3

synthetic-EURUSD-OTC.json contém três velas SINTÉTICAS, não uma gravação IQ.
Os testes de recorder geram 1.000 velas sintéticas apenas em diretórios temporários.
Nenhuma dessas séries cumpre o aceite de 1.000 velas reais.

A coleta real permanece NÃO EXECUTADA. Usar credencial exclusiva de coleta,
sem reutilizar perfil ou conta de operação do EXE. Não colocar senha no comando,
arquivo ou conversa.

No Windows, cadastrar uma Credencial Genérica no Gerenciador de Credenciais:
destino StrategyLab/IQOption/collection; usuário = email; senha = senha da conta
de coleta. O Lab só lê esse destino, não enumera outros.
Na VPS, secret injection para STRATEGY_LAB_IQ_USERNAME e STRATEGY_LAB_IQ_PASSWORD.

No ambiente próprio, após instalação editável do Lab:

```powershell
.venv/Scripts/strategy-lab.exe record-fixture --asset EURUSD-OTC --from 1788211200 --to 1788271200
```

Intervalo [from,to), UTC, M1, até 1.000 velas, inteiramente fechado.
O exemplo pede 1.000 velas; disponibilidade histórica depende do broker.
Epochs ou ISO-8601 com timezone; não usar horário local sem offset.
Não há retry de login nem fallback de ativo, produto ou credencial.
Resposta incompleta, inválida, duplicada ou fora de ordem não gera fixture.

Saída padrão: recorded-EURUSD-OTC-<from>-<to>.json, neste diretório.
Só OHLC, volume de ticks, timestamps e proveniência pública com hash próprio.
Nenhum saldo, conta, token, cookie, profile ou resposta de login é gravado.
Arquivo existente nunca é sobrescrito. Revisar a gravação e o scrub antes do commit.

O payout opcional da fixture sintética é ratio líquido (0.87 = 87%).
O recorder não grava payout; mede somente a série de preços solicitada.
FakeIQClient exige hash válido em recorded, retorna None sem amostra de payout
e usa ID local sintético 1, nunca apresentado como ID do broker.
