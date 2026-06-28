# astrbot_plugin_xiaozhao_memory

小昭本地持久记忆插件。插件会在后台记录群聊消息，并在小昭准备回复前注入相关记忆，让小昭拥有至少 12 小时的本地上下文记忆。

它的重点是“群聊隔离 + 同一用户跨群联动 + 多机器人隔离”：

- 当前群聊记忆只来自当前群。
- 同一用户在多个群里出现时，可以给当前发言人联动跨群记忆。
- AstrBot 同时启用多个机器人账号时，记忆按机器人账号隔离，不会互相串。
- 插件只在小昭已经要回复时注入记忆，不会主动触发机器人发言。

## 功能

- 后台记录群聊中的非机器人消息。
- 默认保留最近 `12` 小时记忆，可配置更长。
- 支持 PostgreSQL 存储，适合 Docker 独立数据库容器。
- PostgreSQL 不可用时可回退到 SQLite。
- 支持当前群聊记忆和同一用户跨群记忆两类注入。
- 支持对话场景判断：只有遇到“回忆之前说过什么、用户偏好、刚才上下文、约定”等需要记忆的回复场景才检索。
- 支持用当前对话大模型智能分析是否需要记忆，并把问题提炼成数据库检索关键词。
- 支持把检索到的候选记忆再提炼成和当前问题相关的简短上下文，减少无关群聊噪声。
- 可与 `astrbot_plugin_xiaozhao_smart_mention` 联动；默认兼容所有 AstrBot 回复流程，也可配置为只在智能回复插件标记 `REPLY` 时注入。
- 注入时提醒模型不要泄露数据库、插件机制或其他群的隐私。

## 记忆隔离规则

插件写入和读取记忆时使用以下边界：

| 维度 | 用途 |
| --- | --- |
| `platform_id` | 区分 QQ、Telegram 等平台。 |
| `bot_id` | 区分当前机器人账号，支持 AstrBot 多机器人隔离。 |
| `group_id` | 区分群聊，保证群聊记忆不串群。 |
| `user_id` | 识别同一用户，用于跨群联动。 |

实际效果：

- A 群不会读取 B 群的“群聊记忆”。
- 同一个 QQ 用户在 A 群和 B 群都说过话，小昭回复这个用户时可以看到该用户的跨群相关记忆。
- 如果 AstrBot 里启用了两个机器人账号，即使它们在同一个群里，也会使用不同的 `bot_id` 隔离记忆。

## 安装

### Docker 部署的 AstrBot

把插件放进 AstrBot 数据卷里的插件目录：

```powershell
docker cp .\astrbot_plugin_xiaozhao_memory astrbot:/AstrBot/data/plugins/astrbot_plugin_xiaozhao_memory
docker compose -f .\compose.yml restart astrbot
```

也可以在容器内直接 clone：

```powershell
docker exec astrbot sh -lc "cd /AstrBot/data/plugins && git clone https://github.com/ZhaoJun233/astrbot_plugin_xiaozhao_memory.git"
docker compose -f .\compose.yml restart astrbot
```

### 普通部署

```bash
cd /path/to/AstrBot/data/plugins
git clone https://github.com/ZhaoJun233/astrbot_plugin_xiaozhao_memory.git
```

如果使用 PostgreSQL，需要安装依赖：

```bash
pip install -r astrbot_plugin_xiaozhao_memory/requirements.txt
```

## Docker + PostgreSQL 推荐配置

插件默认使用 PostgreSQL，默认连接串是：

```text
postgresql://xiaozhao:xiaozhao_memory@xiaozhao_memory_db:5432/xiaozhao_memory
```

可以在 AstrBot 的 `compose.yml` 中增加独立数据库服务：

```yaml
services:
  xiaozhao_memory_db:
    image: postgres:16-alpine
    container_name: xiaozhao_memory_db
    restart: always
    environment:
      - POSTGRES_DB=xiaozhao_memory
      - POSTGRES_USER=xiaozhao
      - POSTGRES_PASSWORD=xiaozhao_memory
      - TZ=Asia/Shanghai
    volumes:
      - xiaozhao_memory_db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U xiaozhao -d xiaozhao_memory"]
      interval: 10s
      timeout: 5s
      retries: 5

  astrbot:
    depends_on:
      - xiaozhao_memory_db

volumes:
  xiaozhao_memory_db:
    name: xiaozhao_memory_db
```

如果 AstrBot 没有自动安装插件依赖，可以手动把 `psycopg` 安装到 AstrBot 数据目录：

```powershell
docker exec astrbot python -m pip install --target /AstrBot/data/site-packages "psycopg[binary]>=3.2,<4"
docker compose -f .\compose.yml restart astrbot
```

启动后 Docker Desktop 里数据库容器通常会折叠在 Compose 项目 `astrbot` 下面，容器名是 `xiaozhao_memory_db`。

## SQLite 模式

如果不想单独启动 PostgreSQL，可以把插件配置改成：

```json
{
  "storage_backend": "sqlite"
}
```

SQLite 数据库会写入插件数据目录下的 `xiaozhao_memory.db`。这种方式更简单，但不如独立 PostgreSQL 适合长期运行和排查。

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 是否启用记忆插件。 |
| `retention_hours` | int | `12` | 记忆保留小时数。插件按该窗口读取和清理旧记忆。 |
| `group_memory_limit` | int | `8` | 每次注入当前群聊记忆的最大条数。 |
| `user_memory_limit` | int | `6` | 每次注入同一用户跨群记忆的最大条数。 |
| `max_text_chars` | int | `500` | 单条消息写入记忆库的最大字符数。 |
| `prune_interval_sec` | int | `1800` | 清理过期记忆的最小间隔，单位秒。 |
| `memory_judge_mode` | string | `hybrid` | 记忆场景判断模式。`hybrid` 先用规则，必要时用当前模型分析；`rules` 只用规则；`llm` 每次用当前模型分析；`always` 每次回复都检索；`off` 不注入记忆。 |
| `memory_judge_timeout_sec` | int | `4` | 当前模型判断是否需要记忆的超时时间。失败或超时时降级到规则，不影响主回复。 |
| `memory_refine_enabled` | bool | `true` | 是否用当前模型提炼候选记忆，去掉与当前问题无关的内容。 |
| `memory_refine_timeout_sec` | int | `5` | 候选记忆提炼超时时间。失败或超时时直接使用原始候选记忆。 |
| `inject_only_when_smart_reply` | bool | `false` | 默认兼容所有 AstrBot 回复流程；开启后只在智能回复插件标记 `REPLY` 的请求中注入记忆。 |
| `storage_backend` | string | `postgres` | 存储后端。可选 `postgres` 或 `sqlite`。 |
| `postgres_dsn` | string | 见上文 | PostgreSQL 连接串。 |

配置修改后重启 AstrBot，或在 AstrBot 插件管理中重新加载插件。

## 工作流程

1. 群里有人发消息。
2. 插件忽略机器人自己发的消息。
3. 插件把文本消息写入本地记忆库。
4. 小昭被 `@`、被点名、被主动回复插件触发，或由其他 AstrBot 流程准备回复。
5. 插件先判断这次回复是否需要记忆：明显回忆场景走规则命中；不确定时按配置调用当前对话模型分析。
6. 需要记忆时，插件把当前问题提炼成检索关键词，并按当前平台、机器人账号、群号、用户 ID 检索相关记忆。
7. 开启智能提炼时，插件会用当前模型把候选记忆压缩成和当前问题有关的内容。
8. 插件把提炼后的记忆作为 system reminder 注入本次 LLM 请求。
9. 小昭基于人设、当前上下文和本地记忆生成回复。

注意：本插件不会单独让小昭发言。它只增强“已经要回复”的那一次回复。

## 检索方式说明

当前版本使用 SQL 存储和轻量文本相关性排序，并在注入前增加对话场景判断：

- 优先取保留窗口内的最近消息。
- 根据当前消息和历史消息的文本重合度排序。
- `hybrid` 模式下，明显的回忆/偏好/刚才上下文问题会直接检索；其他不确定场景再交给当前模型判断。
- 智能判断和提炼都设置了超时，失败时只降级记忆增强，不会阻断主回复。
- 不依赖 AstrBot 内置知识库。
- 不依赖向量数据库或 embedding。

因此它适合短期上下文记忆和用户状态联动，不适合替代长期知识库、RAG 文档检索或精确档案系统。

## 日志与验证

插件加载成功时，AstrBot 日志应出现：

```text
[xiaozhao_memory] loaded
```

如果使用 PostgreSQL，日志中的 backend 应为：

```text
backend=PostgresMemoryStore
```

如果 PostgreSQL 不可用，插件会记录 fallback 日志并回退到 SQLite：

```text
fallback to sqlite
```

查看 PostgreSQL 表：

```powershell
docker exec xiaozhao_memory_db psql -U xiaozhao -d xiaozhao_memory -c "\d memories"
```

查看记忆条数：

```powershell
docker exec xiaozhao_memory_db psql -U xiaozhao -d xiaozhao_memory -c "SELECT count(*) AS memory_rows FROM memories;"
```

## 排障

### Docker Desktop 里看不到独立数据库容器

Docker Desktop 会按 Compose 项目分组显示容器。展开 `astrbot` 项目，应该能看到：

- `astrbot`
- `napcat`
- `xiaozhao_memory_db`

也可以用命令确认：

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"
```

### 日志显示回退到 SQLite

常见原因：

- `xiaozhao_memory_db` 没启动或不健康。
- `postgres_dsn` 写错。
- AstrBot 容器里没有安装 `psycopg`。
- AstrBot 和 PostgreSQL 不在同一个 Docker Compose 网络里。

### 记忆条数一直是 0

- 插件只记录群聊消息，不记录私聊。
- 插件忽略机器人自己发的消息。
- 插件启动后需要有新的群消息才会写入。
- `enabled` 必须为 `true`。

### 小昭没有表现出记忆

- 本插件只在小昭准备回复时注入记忆，不会主动触发回复。
- 确认触发小昭回复的是群聊消息。
- 确认当前问题确实属于需要记忆的场景；普通问候和无需历史信息的问题会跳过检索。
- 如果想强制旧行为，可把 `memory_judge_mode` 改成 `always`；如果想更省调用量，可改成 `rules`。
- 如果只想联动智能回复插件，打开 `inject_only_when_smart_reply`；如果还有 @、命令或其他回复流程需要记忆，保持默认 `false`。
- 检查 `group_memory_limit` 和 `user_memory_limit` 是否过低。
- 当前版本仍是轻量文本检索，如果历史消息和智能提炼后的检索关键词没有明显文本关联，可能不会注入预期内容。

## 隐私提醒

插件会保存群聊文本到本地数据库。部署前请确认你对运行环境、数据库访问权限和群聊使用场景有控制权。不要把包含敏感群聊内容的数据库暴露到公网。

## 更新

```bash
cd /path/to/AstrBot/data/plugins/astrbot_plugin_xiaozhao_memory
git pull
```

Docker 部署更新后重启 AstrBot：

```powershell
docker compose -f .\compose.yml restart astrbot
```
