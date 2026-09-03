# Секреты на закрытом SSH-сервере

Источник правды: **`/opt/secrets`** на `161.104.53.72` (доступ только по SSH).  
В git **нет** реальных `.env` — только `.env.example` и эти скрипты.

## Структура на сервере

```text
/opt/secrets/                    # chmod 700
├── README
├── food_checking/
│   └── .env                     # chmod 600
├── trading_base_machine/
│   └── .env
└── dailybot/
    └── .env
```

## SSH без VPN

HideMyName/OpenVPN перехватывает default route, из‑за этого `161.104.53.72` уходит в VPN и SSH зависает.

На Mac один раз (или после реконнекта VPN):

```bash
./deploy/secrets/bypass-vpn.sh
```

Скрипт добавляет маршрут: сервер → ваш домашний шлюз (`en0`), а не `utun`. Нужен `sudo`.

SSH-алиас: `ssh brynn` (см. `~/.ssh/config`).

## Первый раз (создать vault на сервере)

```bash
chmod +x deploy/secrets/*.sh
./deploy/secrets/bootstrap-server.sh
```

Подтянет существующий `/opt/food_checking/.env` в vault, если он уже есть.

## Новое устройство / новый клон репо

```bash
git clone git@github.com:solovyshka/food_checking.git
cd food_checking
./deploy/secrets/pull.sh food_checking
# появится локальный .env (в .gitignore)
```

## Изменили секреты локально — сохранить в vault

```bash
./deploy/secrets/push.sh food_checking
```

## На сервере применить vault → приложение

```bash
ssh brynn
bash /opt/food_checking/deploy/secrets/apply-local.sh food_checking /opt/food_checking food-api food-bot
```

Или с Mac после push:

```bash
./deploy/secrets/push.sh food_checking
ssh brynn 'bash /opt/food_checking/deploy/secrets/apply-local.sh food_checking /opt/food_checking food-api food-bot'
```

## Список проектов

```bash
./deploy/secrets/list.sh
```

## Переменные окружения скриптов

| Переменная | По умолчанию | Смысл |
|------------|--------------|--------|
| `SECRETS_HOST` | `brynn` | SSH host из `~/.ssh/config` |
| `SECRETS_REMOTE_ROOT` | `/opt/secrets` | Корень vault |
| `SECRETS_SERVER_IP` | `161.104.53.72` | IP для маршрута без VPN |

## Правила

1. Реальные `.env` только в `/opt/secrets/...` и локально (не в git).
2. В репозитории — `.env.example` без секретов.
3. Права на сервере: каталог `700`, файлы `600`.
4. Бэкап vault — отдельно (rsync/tar на другой диск), не в публичный git.
5. Новый проект: `mkdir` на сервере + положить `.env`, либо `push.sh <name>`.

## Почему так

Сервер закрыт по SSH → отдельный Infisical/Docker не нужен.  
Один vault на brynn = одно место правды для всех устройств и проектов.
