# LinguaLab — estudo de idiomas com IA

Aplicação web em Django para estudar idiomas a partir dos seus próprios textos: cadastre um
texto, receba a tradução, ouça-o em áudio e teste a compreensão com quizzes gerados por IA,
combinando seis tipos de pergunta. Um dashboard mostra o desempenho, o que precisa de revisão e
quanto tempo você dedica aos estudos. A interface funciona em celulares, tablets e computadores.

## Sumário

- [Funcionalidades](#funcionalidades)
- [Execução com Docker (recomendado)](#execução-com-docker-recomendado)
- [Execução local sem Docker](#execução-local-sem-docker)
- [Configuração](#configuração)
- [Tipos de pergunta](#tipos-de-pergunta)
- [Integrações externas](#integrações-externas)
- [Interface responsiva e acessibilidade](#interface-responsiva-e-acessibilidade)
- [Regras de medição](#regras-de-medição)
- [Testes](#testes)
- [Arquitetura](#arquitetura)
- [Solução de problemas](#solução-de-problemas)

## Funcionalidades

| Área | O que faz |
|---|---|
| **Biblioteca** | Cadastro, edição, exclusão e busca de textos. Filtros combináveis por tags (qualquer ou todas), idioma e ordenação, com contagem de textos por tag. Cada cartão tem atalhos para Estudar, 🔊 Ouvir, Quizzes, Editar e Excluir. |
| **Gerar com IA** | Tela para criar textos a partir de um pedido livre (prompt): assunto, formato e o que praticar. Escolha os idiomas, de 1 a 5 textos por vez, o tamanho (~120, ~250 ou ~400 palavras), o nível (A1–C2) e as tags. A IA escreve todos numa chamada só, cada um com uma abordagem diferente, e cada texto é salvo e traduzido como um cadastro normal. Se a geração falhar, nenhum texto é criado. |
| **Trechos** | Textos acima de 800 caracteres são divididos em trechos de até 800 caracteres, preferindo fim de frase e depois limite de palavra. O conteúdo original é preservado byte a byte e a ordem é mantida. |
| **Tradução** | Gerada por IA por trecho e armazenada. Ao editar, só os trechos alterados são traduzidos de novo; mudar o idioma de destino retraduz tudo. Cada trecho guarda qual provedor o traduziu: traduções do modo offline aparecem como **simuladas**, e a página do texto oferece “Traduzir com IA agora”. Falhas ficam registradas na “situação do processamento” e podem ser repetidas. |
| **Termos para reforçar** | Selecione uma palavra ou frase do texto original (na página do texto ou na tela de estudo) e toque em “Reforçar este termo”. O termo entra na lista com a frase em que apareceu e o texto de origem; marcar de novo aumenta o contador, e os mais marcados aparecem primeiro. Os termos ficam realçados em vermelho em todos os textos do mesmo idioma, com o significado ao passar o mouse; clicar no termo abre a página dele, com significado, contexto, textos em que aparece, textos de prática e a opção de gerar um texto só com ele. Na lista dá para buscar, filtrar por idioma, editar ou remover cada termo e pedir à IA uma explicação dos selecionados. |
| **Textos de prática** | Na lista de termos, escolha até 15 termos de um idioma, o tamanho e, se quiser, um assunto: a IA escreve um texto novo que usa todos eles. O texto vai para a biblioteca com a tag “Prática de vocabulário”, tradução, áudio e quizzes, e o aviso no topo lista os termos praticados. Se a IA deixar algum termo de fora, a aplicação avisa. |
| **Tags** | Criação, renomeação e exclusão (a exclusão remove só as associações). Tags também podem ser criadas no formulário do texto. |
| **Quizzes** | Quantos quizzes quiser por texto, cada um com versão, título opcional e configuração próprios. Os [seis tipos de pergunta](#tipos-de-pergunta) podem ser combinados, com quantidade por tipo (até 10 por tipo e 20 por quiz). Cada pergunta tem 5 alternativas (A–E) e exatamente 1 correta, o que é validado no código e garantido por constraint no banco. A IA recebe as perguntas e respostas já usadas no texto para não repeti-las. |
| **Tentativas** | Cada envio cria um registro novo (nunca sobrescreve). O resultado mostra pontuação de 0 a 100, acertos e erros, o tipo de cada pergunta, a resposta escolhida, a correta, a explicação em português e, nas perguntas de áudio, o trecho ouvido. |
| **Áudio** | MP3 gerado no servidor para cada trecho e para cada pergunta de compreensão auditiva, com a voz do navegador como reserva. Há player completo na página do texto, na tela de estudo e no quiz. |
| **Modos de estudo** | *Leitura* (original e tradução, navegação por trechos), *Leitura e áudio* e *Somente áudio* (texto oculto só na interface, inclusive durante o quiz). O player tem reproduzir, pausar/retomar, repetir trecho, anterior/próximo, parar, velocidade e reprodução de trecho individual. |
| **Dashboard** | Indicadores gerais e de tempo, textos com mais acertos e com mais erros, textos que precisam de revisão, desempenho por tag, evolução das pontuações e tempo por dia e por semana. Filtros por período, idioma, tags e texto; ações rápidas para abrir o texto, responder ou gerar quiz e ver o histórico. |
| **Relatório por texto** | Todos os quizzes e tentativas, evolução, médias, melhor e pior resultado, última atividade e comparação entre quizzes. |
| **Cronômetro** | Tempo conectado (HH:MM:SS) no cabeçalho, baseado em horários do servidor: não reinicia ao navegar nem ao recarregar a página. Registra sessões, tempo ativo e tempo de estudo, com expiração por inatividade e por abandono. |

## Execução com Docker (recomendado)

Requisito: Docker com o plugin Compose. São três serviços:

| Serviço | O que é |
|---|---|
| **web** | Django com Gunicorn. Aplica as migrações ao iniciar e serve os arquivos estáticos com WhiteNoise. |
| **db** | PostgreSQL 17, com os dados no volume `pgdata`. |
| **scheduler** | Roda `close_stale_sessions` a cada 5 minutos (`CLEANUP_INTERVAL_SECONDS`). |

```bash
cp .env.example .env            # coloque sua ANTHROPIC_API_KEY (opcional, veja abaixo)
docker compose up -d --build    # constrói a imagem e sobe tudo; migrações rodam sozinhas
docker compose exec web python manage.py createsuperuser    # opcional: acesso ao /admin/
```

Acesse http://localhost:8000.

| Ação | Comando |
|---|---|
| Ver se está tudo rodando | `docker compose ps` |
| Ver logs | `docker compose logs -f web` |
| Parar | `docker compose down` (os dados ficam nos volumes `pgdata` e `media`) |
| Apagar tudo, inclusive o banco | `docker compose down -v` |
| Aplicar mudanças no código ou no `.env` | `docker compose up -d --build` |
| Rodar os testes no PostgreSQL | `docker compose exec -e AI_PROVIDER=mock web python manage.py test` |
| Acessar o banco | `docker compose exec db psql -U lingualab` |
| Backup | `docker compose exec db pg_dump -U lingualab lingualab > backup.sql` |
| Restaurar backup | `docker compose exec -T db psql -U lingualab lingualab < backup.sql` |

- O `.env` só é lido quando os containers iniciam: depois de alterá-lo, rode `docker compose up -d`.
- No Docker, `DATABASE_URL` do `.env` é ignorada: o compose sempre aponta para o serviço `db`.
- O PostgreSQL fica exposto em `localhost:5432` e a aplicação em `localhost:8000`. Usuário, senha,
  banco e portas mudam com `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` e
  `WEB_PORT`.
- Para rodar sem DEBUG em HTTP local, mantenha `DJANGO_HTTPS=False`. Com `True`, cookies seguros
  e redirecionamento para HTTPS exigem um proxy com TLS na frente.

Para importar dados de uma instalação com SQLite:

```bash
python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission \
    -e sessions -e admin.logentry -o dados.json          # fora do Docker, com o SQLite
docker compose cp dados.json web:/tmp/dados.json
docker compose exec web python manage.py loaddata /tmp/dados.json
```

## Execução local sem Docker

Requisito: Python 3.10+ (testado com 3.12). Usa SQLite (`db.sqlite3`) por padrão.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

Acesse http://127.0.0.1:8000 e crie uma conta em **Cadastre-se**. Se o Docker estiver rodando,
a porta 8000 estará ocupada: use
`python manage.py runserver 8080` ou pare os containers. Os dados do SQLite e do PostgreSQL do
Docker são independentes.

### Modo offline (sem chave de API)

Sem `ANTHROPIC_API_KEY`, a aplicação usa o **provedor offline** (`AI_PROVIDER=mock`):

- as traduções aparecem marcadas como “tradução simulada”;
- os quizzes têm os seis tipos de pergunta em versões simples, montadas a partir das frases do
  próprio texto (lacunas, frases trocadas, palavras embaralhadas, uma palavra duplicada para
  “Identifique o erro”);
- sinônimos e antônimos vêm de um pequeno dicionário em inglês embutido. Se o texto não tiver
  palavras desse dicionário, o tipo é informado como indisponível e substituído por outro tipo
  selecionado, demonstrando esse caminho.

Todas as regras (versões, validação, tentativas, dashboard, cronômetro) funcionam igual, então
dá para avaliar o sistema inteiro sem custo.

### Tarefas periódicas

Sessões abandonadas também são encerradas automaticamente durante o uso (no login, no dashboard
e a cada requisição do próprio usuário). Para fechar as de quem não voltou, agende o comando
abaixo. No Docker, o serviço `scheduler` já faz isso.

```bash
python manage.py close_stale_sessions    # ex.: cron a cada 5 minutos
```

Ele encerra sessões de acesso inativas ou abandonadas, sessões de estudo sem heartbeat e tentativas
de quiz iniciadas há mais de `QUIZ_ATTEMPT_ABANDON_HOURS` horas.

### Produção sem Docker

```bash
pip install -r requirements-prod.txt
export DATABASE_URL=postgres://usuario:senha@localhost:5432/lingualab
export DJANGO_DEBUG=False DJANGO_SECRET_KEY=... DJANGO_ALLOWED_HOSTS=seu.dominio
python manage.py migrate
python manage.py collectstatic
gunicorn config.wsgi --timeout 180
```

Com `DJANGO_DEBUG=False` (e `DJANGO_HTTPS=True`, o padrão) são ativados cookies seguros,
redirecionamento HTTPS e HSTS. Os arquivos estáticos são servidos pelo WhiteNoise; um proxy
reverso (ex.: Nginx) só precisa cuidar do TLS. O `--timeout` maior evita que o Gunicorn
interrompa a geração de quizzes grandes. Os áudios ficam em `MEDIA_ROOT` e são entregues por views
autenticadas, então não devem ser expostos publicamente pelo proxy.

## Configuração

Todas as opções ficam no `.env` (modelo comentado em [.env.example](.env.example)). Principais grupos:

| Grupo | Variáveis |
|---|---|
| Django | `DJANGO_DEBUG`, `DJANGO_SECRET_KEY` (obrigatória sem DEBUG), `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_TIME_ZONE`, `DJANGO_HTTPS` |
| Banco | `DATABASE_URL` (vazio = SQLite) · no Docker: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `WEB_PORT` |
| IA | `AI_PROVIDER` (`anthropic`/`mock`; vazio = `anthropic` se houver chave), `ANTHROPIC_API_KEY`, `AI_MODEL`, `AI_TRANSLATION_EFFORT`, `AI_QUIZ_EFFORT`, `AI_TIMEOUT_SECONDS`, `AI_MAX_RETRIES` |
| Áudio | `TTS_PROVIDER` (`gtts`/`browser`), `TTS_TIMEOUT_SECONDS` |
| Quizzes | `QUIZ_MAX_QUESTIONS` (20), `QUIZ_MAX_PER_KIND` (10), `QUIZ_GENERATION_ROUNDS` (3), `QUIZ_SIMILARITY_THRESHOLD`, `QUIZ_ATTEMPT_ABANDON_HOURS` |
| Dashboard | `DASHBOARD_REVIEW_THRESHOLD` (70%), `DASHBOARD_MIN_ATTEMPTS`, `DASHBOARD_PRIOR_WEIGHT` |
| Sessões | `SESSION_INACTIVITY_TIMEOUT_SECONDS`, `SESSION_INACTIVITY_WARNING_SECONDS`, `SESSION_ABANDON_TIMEOUT_SECONDS`, `SESSION_ACTIVE_GAP_SECONDS`, `SESSION_HEARTBEAT_SECONDS`, `STUDY_HEARTBEAT_SECONDS`, `STUDY_MAX_CREDIT_SECONDS` |
| Outros | `TEXT_CHUNK_MAX_CHARS` (800), `CLEANUP_INTERVAL_SECONDS` (Docker) |

A combinação usada pelo botão “Gerar quiz rápido” é `QUIZ_DEFAULT_PLAN` em
[config/settings.py](config/settings.py): 2 de interpretação, 1 de completar frase, 1 de ordem das
palavras e 1 de identificar o erro.

## Tipos de pergunta

| Tipo | Como funciona | Validação específica |
|---|---|---|
| **Complete a frase** | Frase do texto com uma lacuna (`_____`); escolher a palavra ou expressão correta. | O enunciado precisa conter a lacuna. |
| **Interpretação de texto** | Informações explícitas, ideia principal, detalhes e conclusões dedutíveis. | — |
| **Compreensão auditiva** | O estudante ouve um trecho (MP3 gerado no servidor) e responde. O texto de referência começa oculto, com botão para exibi-lo, e o trecho só aparece na correção. | O trecho deve existir no texto. Pequenas diferenças são corrigidas para a frase exata, e trechos inventados são rejeitados. |
| **Sinônimos e antônimos** | Palavra do texto; o enunciado diz se procura sinônimo ou antônimo. | — |
| **Ordem correta das palavras** | Palavras embaralhadas; escolher a frase na ordem gramatical correta. | Todas as alternativas devem usar exatamente as mesmas palavras. |
| **Identifique o erro** | Cinco frases; a resposta é a frase com erro gramatical (o enunciado diz o que procurar). | — |

Regras comuns, verificadas pergunta por pergunta: 5 alternativas não vazias e sem repetição, um único
`correct_index` válido, enunciado preenchido e nenhuma repetição dentro do quiz. A identidade de uma
pergunta é enunciado + resposta correta + trecho, porque em “Identifique o erro” o enunciado costuma se
repetir. As alternativas são embaralhadas ao salvar, e as explicações vêm no idioma de tradução do
texto (português por padrão). O idioma dos enunciados é escolhido a cada quiz.

### Fluxo de geração ([apps/quizzes/services.py](apps/quizzes/services.py))

1. Na página do texto, **Criar quiz** abre o construtor ([templates/quizzes/quiz_new.html](templates/quizzes/quiz_new.html)),
   onde se escolhem os tipos, a quantidade de cada um, o título, o idioma dos enunciados e o modo
   da tentativa. O total é calculado ao vivo, e o envio fica bloqueado sem tipos ou acima do limite.
   **Gerar quiz rápido** usa a combinação padrão `QUIZ_DEFAULT_PLAN`.
2. A IA recebe o texto, as instruções de cada tipo ([apps/quizzes/kinds.py](apps/quizzes/kinds.py)) e
   as perguntas e respostas dos quizzes anteriores do mesmo texto.
3. Cada pergunta é limpa (espaços, prefixos “A)”) e validada. As reprovadas são descartadas e **só
   elas** são pedidas de novo, em até `QUIZ_GENERATION_ROUNDS` rodadas. Na última rodada uma pergunta
   parecida com a de um quiz anterior é tolerada e registrada nas observações.
4. Se a IA informar que o texto não tem material para um tipo, as perguntas desse tipo vão para os
   outros tipos selecionados, com o motivo nas observações do quiz. Se não houver outro tipo, nada é
   inventado: o quiz sai com menos perguntas e um aviso, ou a geração falha com a explicação.
5. O quiz é salvo com a configuração pedida e a efetivamente gerada (`Quiz.config`). Tentativas e
   quizzes anteriores nunca são alterados.

Quizzes criados antes dos tipos de pergunta continuam funcionando com 4 alternativas
(`Quiz.options_per_question`). A migração `quizzes.0002` classificou as perguntas existentes.

## Integrações externas

### Inteligência artificial — Claude (Anthropic)

- Código: [apps/core/ai/anthropic_provider.py](apps/core/ai/anthropic_provider.py), SDK oficial `anthropic`.
- Configuração: `AI_PROVIDER=anthropic` (ou só defina `ANTHROPIC_API_KEY`) e `AI_MODEL` (padrão
  `claude-opus-5-5`). A chave é criada no Claude Console (https://platform.claude.com) em
  *Settings → API Keys*. O uso da API é cobrado à parte da assinatura do claude.ai.
- **Tradução**: uma chamada por texto com todos os trechos pendentes, para manter o contexto e a
  terminologia entre eles. A resposta é JSON validado por schema (`output_config.format`), com uma
  tradução por trecho.
- **Quizzes**: o prompt leva o texto original, os tipos pedidos com instruções e quantidades, os
  idiomas (do enunciado e da explicação) e as perguntas já usadas nesse texto. A resposta é JSON com
  schema: `kind` restrito aos tipos pedidos, `passage` para o áudio e `skipped` para tipos sem
  material.
- As chamadas usam streaming, para quizzes grandes não esbarrarem no tempo limite das requisições.
- Raciocínio adaptativo (`thinking: adaptive`), com esforço configurável:
  `AI_TRANSLATION_EFFORT` (padrão `low`) e `AI_QUIZ_EFFORT` (padrão `medium`).
- Recusas do modelo: o parâmetro `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`)
  faz a própria API reexecutar em outro modelo. Se ainda assim houver recusa, o usuário vê uma
  mensagem clara.
- Erros (credencial, limite de requisições, falha de rede, erro 5xx, JSON inválido, resposta
  truncada) viram `AIServiceError` com mensagem em português, e o SDK repete automaticamente
  (`AI_MAX_RETRIES`). Nenhum erro externo derruba a página: a tradução fica com situação “Erro” e
  botão para tentar de novo, e a geração de quiz mostra o motivo da falha.
- As chamadas são síncronas (a página aguarda a IA, normalmente alguns segundos). Para textos
  longos ou muitos usuários, o caminho natural é mover `translate_text` e `generate_quiz` para uma
  fila (Celery/RQ); as funções já são independentes da camada web.

Para trocar de provedor, implemente `AIProvider` ([apps/core/ai/base.py](apps/core/ai/base.py)),
(tradução, quizzes, explicação de termos, textos de prática e textos a partir de um prompt), e registre-o em `PROVIDERS`
([apps/core/ai/__init__.py](apps/core/ai/__init__.py)).

### Text-to-Speech — gTTS (servidor) com a voz do navegador como reserva

- Padrão `TTS_PROVIDER=gtts`: o servidor gera os MP3 com a biblioteca
  [gTTS](https://gtts.readthedocs.io/), que usa o serviço de voz do Google Tradutor (gratuito e sem
  chave, porém não oficial). Os arquivos ficam em `media/tts/<idioma>/<hash>.mp3`. Conteúdos iguais
  reaproveitam o arquivo, e a primeira reprodução de um trecho leva alguns segundos.
- Há dois tipos de áudio:
  - **trechos do texto**, servidos em `/biblioteca/trechos/<id>/audio.mp3`, com player completo na
    página do texto (inclusive 🔊 por trecho), na tela de estudo e no quiz. O player pré-carrega o
    trecho seguinte enquanto o atual toca;
  - **trechos de perguntas de compreensão auditiva**, servidos em
    `/quizzes/perguntas/<id>/audio.mp3`, com um player na própria pergunta. O trecho é guardado na
    pergunta, então o áudio não muda se o texto for editado depois.
- As duas views exigem login e conferem o dono do texto; os MP3 não ficam públicos.
- Se a geração falhar (sem internet, limite do serviço) ou com `TTS_PROVIDER=browser`, o player
  usa a Web Speech API do navegador (`speechSynthesis`). Ela depende das vozes instaladas no
  sistema, e no Linux muitas vezes não há nenhuma.
- Código: [apps/library/services/tts.py](apps/library/services/tts.py) e [static/js/tts.js](static/js/tts.js).
- Os textos vão para o player num `<script type="application/json">`, por isso o modo
  “somente áudio” oculta apenas a interface.

### Idiomas

Os idiomas ficam em [apps/core/languages.py](apps/core/languages.py): português, inglês, espanhol,
francês, alemão, italiano, japonês, chinês, coreano e russo. Para adicionar um idioma, inclua uma
linha com o código ISO, o nome exibido, o nome em inglês (usado nos prompts), a tag de voz do
navegador e, se necessário, o código do gTTS. O português é o idioma de tradução padrão.

## Interface responsiva e acessibilidade

O CSS ([static/css/app.css](static/css/app.css)) é **mobile-first**: o estilo base é o do celular,
e media queries `min-width` ampliam o layout em 40em (640px), 48em (768px), 64em (1024px),
80em (1280px) e 120em (1920px). Não há framework CSS nem dependências externas.

| Componente | Comportamento |
|---|---|
| Menu | Gaveta lateral abaixo de 1024px (☰, botão de fechar, fundo escurecido, tecla Esc). Ao abrir, o foco vai para o menu e o conteúdo atrás fica `inert`. A partir de 1024px fica fixo. |
| Tabelas | Viram cartões “rótulo: valor” quando a própria tabela fica estreita (container queries: `.table-stack`, `.table-stack-wide`, `.table-stack-xwide`, conforme o número de colunas). O `app.js` copia os cabeçalhos para `data-label`; sem JavaScript, a tabela rola de lado. |
| Toque | Botões, campos, chips e alternativas têm pelo menos 44px em celulares e tablets. Só em telas grandes com mouse (`pointer: fine`) os controles ficam compactos. |
| Formulários | Campos com fonte de 16px (sem zoom automático no iPhone), em 1, 2 ou 3 colunas conforme a largura. Filtros do dashboard e da biblioteca ficam recolhidos no celular quando não há filtro ativo. |
| Gráficos | SVG desenhado na largura real do contêiner, com texto legível (12px) e redesenho ao girar ou redimensionar a tela. O tooltip funciona com toque. |
| Player e quiz | Controles em grade no celular; construtor de quiz com cartões por tipo e barra de envio fixa no rodapé. |

Acessibilidade: link “Pular para o conteúdo”, contorno de foco visível, rótulos para leitores de
tela em botões só com ícone, `aria-expanded` e `aria-current` nos controles que mudam de estado e
`aria-live` nos status. O CSS também respeita `prefers-reduced-motion`, `forced-colors`
(alto contraste), modo escuro do sistema, impressão e áreas seguras de telas com entalhe
(`viewport-fit=cover`).

Os layouts foram verificados de 320px a 2560px (inclusive celular deitado e tablet com toque):
sem rolagem horizontal, sem elementos fora da tela e com alvos de toque de 44px. O layout usa
recursos de navegadores modernos (Chrome/Edge 105+, Safari 16+, Firefox 110+); para container
queries há alternativa com media query.

## Regras de medição

### Desempenho

- Só entram nos cálculos tentativas **concluídas**; quizzes apenas gerados e tentativas
  abandonadas ficam de fora.
- **Aproveitamento** = média das pontuações (0–100) das tentativas. Como os quizzes podem ter
  tamanhos diferentes, as médias de acertos e erros são por tentativa.
- **Precisa de revisão**: média ou última pontuação abaixo de `DASHBOARD_REVIEW_THRESHOLD` (70%).
- **Mais acertos / mais erros**: textos com média ≥ 70% vão para a primeira lista e os demais para a
  segunda; um texto nunca aparece nas duas. A ordem usa uma média ajustada,
  `(n·média + k·média_geral) / (n + k)` com `k = DASHBOARD_PRIOR_WEIGHT`, e textos com menos de
  `DASHBOARD_MIN_ATTEMPTS` tentativas aparecem por último, marcados como “poucas”. Assim uma única
  tentativa ruim não é apontada como o texto mais difícil. A tabela mostra a média real e o número
  de tentativas.
- **Por tag**: um texto com várias tags conta em cada uma. A interface avisa que os números por
  tag não devem ser somados e mostra o total real de tentativas.

### Tempo

| Medida | Regra |
|---|---|
| Tempo conectado | Do login até o logout ou expiração. O horário de início vem do servidor; o JavaScript só desenha o contador, corrigindo a diferença entre o relógio do navegador e o do servidor. |
| Tempo ativo | Soma dos intervalos entre atividades (navegação, clique, tecla, rolagem, áudio tocando) menores que `SESSION_ACTIVE_GAP_SECONDS`. |
| Inatividade | Sem atividade por `SESSION_INACTIVITY_TIMEOUT_SECONDS`, a sessão é encerrada **no servidor**, mesmo com o JavaScript desligado. Um minuto antes, a interface pergunta “Você ainda está aí?”. |
| Abandono | Sem nenhum sinal do navegador (heartbeat a cada `SESSION_HEARTBEAT_SECONDS`) por `SESSION_ABANDON_TIMEOUT_SECONDS`, a sessão é encerrada no horário do último sinal. |
| Tempo de estudo | Contado por heartbeats nas telas de estudo e de quiz, só com a aba visível e interação recente ou áudio tocando (inclusive o áudio das perguntas), com no máximo `STUDY_MAX_CREDIT_SECONDS` por batida. |
| Sem dupla contagem | O encerramento de uma sessão é uma atualização condicional (só fecha uma vez); o tempo conectado do dia é a união dos intervalos das sessões (duas abas não somam em dobro); o crédito de estudo começa no último crédito de qualquer sessão de estudo do usuário. |

O tempo de estudo é atribuído ao dia em que a sessão de estudo começou.

## Testes

```bash
python manage.py test                                              # local (SQLite)
docker compose exec -e AI_PROVIDER=mock web python manage.py test  # no Docker (PostgreSQL)
```

São 117 testes. Eles cobrem:

- textos: divisão em trechos, tags, filtros, tradução (reaproveitamento, falhas, traduções simuladas) e exclusão;
- quizzes: cada tipo de pergunta e combinações, validação de cada regra, regeneração só das
  perguntas inválidas, substituição de tipos sem material, limites do construtor, quizzes
  independentes e quizzes antigos de 4 alternativas;
- tentativas e correção;
- áudio: cache dos MP3, acesso restrito ao dono, falhas;
- dashboard e relatório;
- cronômetro e sessões: início, continuidade, logout, inatividade, abandono e sobreposição;
- provedor Claude, com cliente falso e sem rede.

Os testes **sempre** usam o provedor offline e nunca geram áudio real, mesmo com chave configurada.

## Arquitetura

```
config/                 settings (via variáveis de ambiente), urls
apps/core/              idiomas, provedores de IA (Claude e offline), utilitários, templatetags
apps/accounts/          cadastro, login e logout
apps/library/           Text, TextChunk, Tag · services/chunking.py, services/texts.py (tradução),
                        services/generation.py (textos a partir de um prompt),
                        services/tts.py (áudio)
apps/quizzes/           Quiz, Question, Option, QuizAttempt, AttemptAnswer · kinds.py (tipos de
                        pergunta), services.py (geração, validação, correção), forms.py (construtor)
apps/dashboard/         services.py (indicadores, rankings, relatório) · views
apps/tracking/          AccessSession, StudySession · services.py, middleware, sinais,
                        comando close_stale_sessions
apps/vocabulary/        DifficultTerm · services.py (marcação, explicação e textos de prática)
templates/  static/     HTML, CSS mobile-first e JS (cronômetro, menu, tabelas, player, gráficos SVG)
Dockerfile, docker-compose.yml, docker/entrypoint.sh
```

As regras de negócio ficam nos módulos `services`; as views só validam a entrada, chamam os
serviços e renderizam. Os resultados nunca são gravados no cadastro do texto: o dashboard calcula
tudo a partir do histórico de tentativas.

### Segurança

- Todas as consultas são filtradas pelo usuário autenticado; acessar dados de outro usuário
  (inclusive os áudios) retorna 404.
- Proteções nativas do Django: CSRF (inclusive nas chamadas `fetch`), escape automático nos
  templates, ORM parametrizado, validação de senha, `X-Frame-Options: DENY` e cookies seguros em produção.
- Validação no backend: idiomas suportados, tamanho do texto (até 30 mil caracteres), unicidade de
  tags, configuração e estrutura dos quizzes, alternativas que pertencem à pergunta, tentativa
  concluída não pode ser reenviada.
- Credenciais só em variáveis de ambiente (`.env` está no `.gitignore` e no `.dockerignore`).
- No Docker a aplicação roda com um usuário sem privilégios de administrador.

## Solução de problemas

| Sintoma | Causa e solução |
|---|---|
| Traduções aparecem como “tradução simulada” | A aplicação estava sem chave ou iniciou antes de a chave entrar no `.env`. Reinicie (`docker compose up -d` ou o `runserver`) e use “Traduzir com IA agora” na página do texto. |
| Erro de credenciais da IA | Confira `ANTHROPIC_API_KEY` no `.env` e se há créditos na conta do Claude Console. |
| Áudio não toca ou cai para a voz do navegador | O servidor não conseguiu gerar o MP3 (sem internet ou limite do gTTS). No Linux, a voz do navegador pode não ter nenhuma voz instalada. |
| `ImproperlyConfigured` sobre `DATABASES` | Versões antigas não aceitavam `DATABASE_URL=` vazio. Atualize o código ou remova a linha. |
| Porta 8000 ocupada | O Docker e o `runserver` disputam a mesma porta. Use `runserver 8080` ou `WEB_PORT=8080` no `.env`. |
| Download das imagens dá *TLS handshake timeout* | Instabilidade de rede ao baixar do Docker Hub. Rode `docker compose up -d --build` de novo. |
| Quiz criado com menos perguntas que o pedido | O texto não tinha material para algum tipo, ou perguntas foram reprovadas na validação em todas as rodadas. O motivo aparece nas observações do quiz. |
