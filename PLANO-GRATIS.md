# Plano B — 100% gratuito (Ollama local + SQLite)

Custo de API: R$ 0. Sem chave, sem cota, sem limite de requisições. Alternativa ao
[PLANO-NUVEM.md](PLANO-NUVEM.md).

## Ponto de partida

Este repositório já está quase todo aqui. O que existe hoje:

| Peça | Onde | Estado |
|---|---|---|
| LLM local, sem chave | `app/main.py:13` — `qwen2.5-coder:7b` via Ollama | pronto |
| Saída estruturada | `app/main.py:26` — `RESPONSE_SCHEMA` no `format=` | pronto |
| Leitura apenas, no nível do SO | `app/main.py:20` — `file:...?mode=ro` | pronto |
| Segunda camada de bloqueio | `app/sql_guard.py` | pronto |
| Teste da camada de segurança | `test_sql_guard.py` | pronto |
| Esquema introspectado do banco | `app/main.py:66` — `_schema()` | pronto |
| Dados reais e gratuitos | `db/seed.py` — API pública NHTSA vPIC | pronto |
| Sem segunda chamada de LLM | `/chat` devolve grade + nota | pronto |

Duas decisões que já estão certas e vale não desfazer:

**`mode=ro` na URI do SQLite.** O arquivo recusa escrita no nível do sistema
operacional, independente do `sql_guard`. É a mesma ideia do role read-only do plano em
nuvem, com uma linha em vez de um `GRANT`. Isso é o que torna o `sql_guard` uma camada de
mensagem de erro legível, não a única barreira.

**Nenhuma segunda chamada para formatar a resposta.** O endpoint devolve a grade de
resultados mais a `note` que o modelo já produziu junto com a SQL. No plano em nuvem esse
corte é uma otimização de custo; aqui ele corta o tempo de resposta pela metade, que é o
recurso escasso quando o modelo roda na sua máquina.

## Aplicado

**1. Loop de autocorreção.** `answer()` em `app/main.py`. O erro do guard ou do SQLite
volta como turno de conversa e o modelo reescreve a SQL, até 3 tentativas. O modelo não
enxerga o guard nem o banco — erro que ele não lê é erro que ele repete.

**2. Few-shot no prompt.** Cinco exemplos em `SYSTEM`, incluindo um de pergunta que o
esquema não responde (devolve `sql` vazio em vez de inventar coluna). Com uma tabela,
isso é praticamente a especificação completa. Prompt total: ~420 tokens.

**3. `docker-compose.yml` reconstruído.** Antes subia um Postgres com `db/init.sql`
(e-commerce) que o app nunca lia — ele usa SQLite com catálogo de veículos — e exigia
`ANTHROPIC_API_KEY`, que nenhum arquivo do projeto usa. Decisão: fica SQLite. Serviço
`db`, volume `pgdata` e `db/init.sql` removidos.

**4. Container alcança o Ollama.** `OLLAMA_HOST` aponta para `host.docker.internal`, com
`extra_hosts` para funcionar no Linux. O `Dockerfile` agora copia `db/analytics.db` —
antes o container subia sem banco nenhum.

**5. `.env.example` corrigido.** `OLLAMA_HOST`, `OLLAMA_MODEL`, `DB_PATH`. A chave da
Anthropic saiu.

**6. Teto de histórico e timeouts.** `HISTORY = 6` turnos, 120s no cliente Ollama, 5s por
consulta via `conn.set_progress_handler`. O timeout de consulta aborta com
`sqlite3.OperationalError`, que cai no loop de autocorreção — o modelo tem chance de
reescrever com um filtro mais estreito.

**7. `load_extension` bloqueado.** `SELECT load_extension('evil.so')` é um SELECT válido
que roda código nativo. O `sql_guard` cobria Postgres mas não isso. `attach`, `detach` e
`pragma` entraram junto.

**8. Vazamento de conexão.** `with sqlite3.connect(...)` faz commit, não fecha. Trocado
por `closing()`.

Testes: `test_sql_guard.py` e `test_retry.py`, ambos `assert` puro, sem framework.
`test_retry.py` injeta respostas de modelo e verifica que SQL válida não repete, que SQL
inválida repete exatamente uma vez com o texto do erro visível ao modelo, que o loop para
em `MAX_TRIES`, e que pergunta sem resposta possível não queima tentativas.

## O que falta

Nada bloqueante. Em ordem de valor, se for continuar:

- **Cache** de perguntas repetidas (`lru_cache` sobre a pergunta normalizada). Corta a
  latência, que é o recurso escasso aqui.
- **Log** de `(pergunta, sql, row_count)`. É o que diz se vale criar templates e quais.
- **Medir** a taxa de retentativa antes de trocar de modelo. Se o `7b` errar pouco, não
  há motivo para mexer.

## Peso para rodar

Aqui o custo sai da fatura e entra no hardware. É a troca central deste plano.

**Modelo (`qwen2.5-coder:7b`, quantização Q4):** ~4,7 GB de download, ~6 GB de RAM em uso.

| Máquina | Tempo por pergunta |
|---|---|
| CPU apenas, notebook moderno | ~10-30 s |
| GPU com 8 GB de VRAM | ~2-5 s |
| `qwen2.5-coder:3b` em CPU | ~5-10 s, SQL sensivelmente pior |

**Concorrência:** o Ollama atende uma requisição por vez por padrão. Concorrência real é
1, talvez 2 com `OLLAMA_NUM_PARALLEL`. Isso é o limite prático deste plano — não o número
de perguntas por dia.

**Aplicação:** FastAPI e SQLite não pesam nada perto do modelo. O banco de veículos tem
alguns MB.

## Capacidade

Sem cota, sem limite diário, sem chave. O teto é tempo de máquina:

- GPU, ~4 s por pergunta: ~900/hora se houver fila constante.
- CPU, ~20 s por pergunta: ~180/hora.

Para uso interno — uma equipe consultando um painel — sobra. O limite não é volume
diário, é **quantas pessoas perguntam ao mesmo tempo**.

Exatamente o oposto do plano em nuvem: lá a cota diária aperta primeiro e a máquina não
aparece na lista de gargalos; aqui a máquina é o único gargalo e não existe cota.

## Custo real

| Item | Custo |
|---|---|
| API de LLM | R$ 0 |
| Modelo (Qwen2.5-Coder, licença Apache 2.0) | R$ 0 |
| Banco (SQLite) | R$ 0 |
| Dados (API pública NHTSA vPIC, sem chave) | R$ 0 |
| Hospedagem | R$ 0 — roda na máquina que você já tem |
| Energia | centavos por hora de uso |

O que você paga: latência maior e o hardware precisa existir. Se a máquina não tem 8 GB
de RAM livre, o plano não fecha — nesse caso `qwen2.5-coder:3b` é o piso viável.

## Quando este plano deixa de servir

- Vários usuários simultâneos: Ollama serializa e a fila cresce rápido.
- Acesso externo com disponibilidade: a máquina precisa ficar ligada e exposta.
- Esquema com dezenas de tabelas: modelo de 7B degrada bem antes de um modelo hospedado.

Em qualquer um desses casos, a saída é usar a cota gratuita do Gemini como via de escape
para perguntas que o modelo local errar — não migrar tudo. Como o `RESPONSE_SCHEMA` já
existe e o `sql_guard` é independente do provedor, a troca fica isolada na chamada de
`ollama.chat`.

## Ordem de execução

1. Loop de autocorreção em `/chat` — maior ganho de confiabilidade, poucas linhas.
2. Resolver `docker-compose.yml` e `.env.example` — hoje o projeto não sobe via compose.
3. Teto no histórico e timeouts.
4. Medir taxa de retentativa antes de trocar de modelo. Se `7b` errar pouco, não há
   motivo para mexer.
