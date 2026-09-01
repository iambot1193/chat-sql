# Plano A — Nuvem (Gemini + Postgres gerenciado)

Arquitetura pensada para rodar o chatbot com LLM hospedado. Guardado como referência;
o plano em execução é o [PLANO-GRATIS.md](PLANO-GRATIS.md).

## Gargalo que este plano resolve

Pipeline de duas chamadas cegas ao LLM: gera SQL, executa, formata resposta. Sem
validação semântica, sem autocorreção, com parse de string frágil. Qualquer falha vira
erro cru para o usuário.

## Arquitetura

```
Pergunta
   │
   ▼
Gemini com function calling
   ├─ tools: templates paramétricos (vendas_por_periodo, top_produtos, ...)
   └─ tool: executar_sql_livre(sql)          ← fallback para pergunta aberta
   │
   ▼
Validação AST (sqlglot) + LIMIT injetado na árvore
   │
   ▼
Execução com role read-only
   │
   ├─ erro ──► volta como function_response, máx 3 tentativas
   └─ ok  ──► formata resposta
```

Ponto central: o function calling **já é o roteador**. Templates entram como funções
declaradas ao lado da SQL livre; o modelo escolhe. Não existe classificador regex
separado para manter em paralelo.

## Ordem de execução

**Fase 0 — configuração, não código.** É a camada que sustenta todas as outras.

```sql
CREATE ROLE chatbot LOGIN;
GRANT SELECT ON vendas, produtos, clientes, pedidos TO chatbot;
ALTER ROLE chatbot SET default_transaction_read_only = on;
ALTER ROLE chatbot SET statement_timeout = '5s';
```

Com isso, a validação em Python vira feedback de usabilidade em vez de fronteira de
segurança. Se um comando destrutivo escapar da AST, o banco recusa.

**Fase 1 — miolo do processador.**

- `sqlparse` → `sqlglot`. Validação com AST de verdade:
  ```python
  ast = sqlglot.parse_one(sql, read="postgres")
  isinstance(ast, exp.Select)                    # em vez de checar substring
  {t.name for t in ast.find_all(exp.Table)} <= ALLOWED_TABLES   # pega CTE e subquery
  sql = ast.limit(MAX_ROWS).sql()                # LIMIT na árvore, não no prompt
  ```
- `generate_content` → function calling com structured output.
- `temperature=0` na geração de SQL (tarefa determinística).
- Loop de autocorreção: erro de validação ou execução volta como `function_response`,
  máximo 3 tentativas.
- Esquema hardcoded → DDL introspectado do banco + `COMMENT ON COLUMN` para regra de
  negócio. Assim o contexto vive no banco, versionado junto com a migration.
- Log de `(pergunta, sql, row_count)` desde a primeira linha. Esse log é o dataset de
  few-shot depois — só existe se começar a gravar antes de precisar.

**Fase 2 — depois de ler o log.** Mais templates como tools, escolhidos pelo que
apareceu no tráfego real, não por palpite.

**Backlog — LangGraph.** Function calling já faz o loop de autocorreção nativamente. O
que sobra do LangGraph é checkpoint para conversa multi-turno. Puxar do backlog quando
(e se) contexto entre perguntas virar requisito.

## Casos que não são o caminho feliz

- `row_count == 0` não é sucesso. Quase sempre é SQL errada, e hoje o modelo narra
  "não encontrei nada", mascarando o bug. Merece ramo próprio.
- SQL válida mas semanticamente errada não tem correção automática barata. Mitiga
  expondo a SQL gerada no frontend e mantendo o log.
- Cache com `lru_cache` sobre a pergunta normalizada — repetição em BI é alta.
- Rate limit por usuário: a cota do Gemini é global, um usuário em loop derruba o
  serviço para os outros.
- PII: colunas de email e telefone trafegam para a API a cada consulta que toque a
  tabela de clientes. Mascarar antes do prompt de formatação, ou tirar da whitelist.

## Custo e capacidade

Peso por pergunta: ~2.800 tokens de entrada, ~200 de saída, em 2 chamadas.

Composição da entrada: declarações de tools (~480), DDL enriquecido (~400), few-shot de
15-20 pares (~800), regras (~150), pergunta (~25), mais a média de retentativas.

**Cota gratuita.** O limite é requisições por dia, não tokens. A 2,2 requisições por
pergunta, uma cota de 250–1.500 RPD dá de ~115 a ~680 perguntas/dia. Os números por tier
não são mais publicados na documentação; conferir em `aistudio.google.com/rate-limit`.

Dois ajustes dobram isso:

- Pular a segunda chamada quando o resultado é escalar único. "Quanto vendemos ontem?"
  retorna uma linha e uma coluna — formatar em Python, sem LLM. Corta ~40% das chamadas.
- Cache de perguntas repetidas.

**Tier pago**, por pergunta (~2.800 entrada / 200 saída):

| Modelo | $/1M entrada | $/1M saída | Custo/pergunta |
|---|---|---|---|
| Gemini 3.7 / 3.6 Flash | 0,75 (sobe para 1,50 em 2027) | 3,75 (sobe para 7,50) | ~US$ 0,0029 |
| Gemini 2.5 Flash-Lite | 0,10 | 0,40 | ~US$ 0,00036 |

| Perguntas/dia | Flash | Flash-Lite |
|---|---|---|
| 100 | ~R$ 48/mês | ~R$ 6/mês |
| 1.000 | ~R$ 465/mês | ~R$ 59/mês |
| 10.000 | ~R$ 4.640/mês | ~R$ 583/mês |

Maior alavanca de custo: context caching. Cerca de 1.500 dos 2.800 tokens de entrada são
estáticos (tools + DDL + few-shot) e reenviados idênticos a cada chamada.

Escolha de modelo: Flash-Lite é 8x mais barato, e text-to-SQL com few-shot, validação AST
e retry é tarefa bem estruturada. Começar no Lite e subir só se a taxa de retentativa doer.

## Peso de infraestrutura

O sistema é caro em API e barato em máquina.

- FastAPI async é I/O-bound — espera a API, não calcula. 1 vCPU e 512 MB rodam milhares
  de perguntas por dia.
- Latência: duas chamadas em série, ~3-6s por pergunta, até 10s com retentativa. Esse é
  o número que o usuário sente.
- Banco: quatro tabelas pequenas cabem em qualquer tier gratuito de Postgres gerenciado.
- Hosting: Cloud Run com scale-to-zero encaixa melhor que Railway, porque tráfego de
  chatbot é intermitente.

Ordem real dos gargalos: cota diária de requisições, depois latência percebida, depois
custo de token (só acima de ~10k perguntas/dia). RAM, CPU e banco não aparecem na lista.

## Referências

- [Gemini API — pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini API — rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
