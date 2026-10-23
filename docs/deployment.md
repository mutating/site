# Единый деплой сайта, API и платформы

Production работает без Docker и Docker Compose. Репозиторий содержит Hugo в
`site/`, API в `platform/api/`, кабинет в `platform/web/` и все проверки в `tests/`. Nginx выбирает приложение по
`server_name`, а единый сервис systemd `throng.service` управляет API, контроллером
и кабинетом. Сервер кабинета отдаёт только интерфейс и публичную конфигурацию. Браузер
обращается прямо к `https://api.mutating.tech/v1`; API разрешает CORS только
для origin кабинета и сам обслуживает браузерные сессии.

| Адрес | Обработчик |
| --- | --- |
| `https://mutating.tech/` | Существующий сайт Hugo |
| `https://api.mutating.tech/` | API и контроллер на `127.0.0.1:8000` |
| `https://platform.mutating.tech/` | Главная кабинета, сервис на `127.0.0.1:8001` |

Используются точные имена двух поддоменов. Для добавления других доменов нужно
добавить их в nginx и сертификат. Базовый адрес API — `https://api.mutating.tech/v1`.
Пример запроса: `https://api.mutating.tech/v1/users`.
На том же поддомене доступны `/agent/v1/*`, `/docs` и `/openapi.json`.
Страницы кабинета доступны без префикса: `/login`, `/profile`, `/organizations` и т. д.
Адреса `/account` и `/account/*` не обслуживаются и возвращают `404` без
перенаправления. Cookie выдаётся API с `Path=/v1/` и привязана к его хосту.
После перехода с прежней схемы пользователям нужно войти заново.
В production логотип кабинета ведёт на `https://mutating.tech/`.

## Общий локальный запуск

Из корня репозитория:

```sh
uv sync --project platform/api --frozen
cp platform/api/.env.example platform/api/.env  # только если собственного .env ещё нет
npm ci
npm run build:web
uv run --project platform/api python deploy/run.py
```

API доступен на `http://127.0.0.1:8000`, кабинет — на
`http://127.0.0.1:8001/`. В `platform/api/.env` укажите
`DASHBOARD_API_URL=http://127.0.0.1:8000/v1`,
`THRONG_BROWSER_ORIGINS=["http://127.0.0.1:8001","http://127.0.0.1:5173"]` и
`THRONG_BROWSER_SECURE_COOKIE=false`. Для Vite также используйте
`platform/web/.env.example`. Адрес API должен быть доступен браузеру.

Общая команда применяет миграции, ждёт готовности API, запускает кабинет и
останавливает оба процесса по Ctrl+C/SIGTERM. Неожиданное завершение любого
компонента останавливает второй и возвращает ненулевой код.

## Первичная настройка сервера

Это отдельный административный шаг, который не выполняется обычным CI-деплоем.
Поддерживаются Debian/Ubuntu на amd64/arm64. Используется тот же подход, что в
репозитории `site`: Ansible, nginx, Certbot и SSH-пользователь `user`.

На production nginx совместно обслуживает `mutating.tech` и `pomponchik.org`;
позже можно добавить другие сайты с собственными `server_name`. Обычный деплой
этого репозитория обновляет Hugo, API и кабинет `mutating.tech`. Конфигурации,
сертификаты и содержимое остальных сайтов управляются отдельно. Проверки CI
проверяют домены `mutating.tech`, `www.mutating.tech`, `api.mutating.tech` и
`platform.mutating.tech`.

На уже работающем сервере применяется `infra/playbooks/setup-platform.yml`:
он добавляет виртуальные серверы API и кабинета с точными `server_name`.
Общий `setup-server.yml` предназначен для первоначальной настройки нового
сервера: он также настраивает nginx основного сайта и собирает Hugo-сайт.

14 октября 2026 года `setup-platform.yml` применён к production-серверу:
nginx-маршруты, сертификат обоих поддоменов, systemd-сервис, SQLite-конфигурация
и права пользователя CI проверены. Существующая конфигурация nginx и статическая
страница сохранены. Первый релиз приложений загружает CI после merge в `main`.

1. Создайте DNS-записи `api.mutating.tech` и `platform.mutating.tech`, указывающие
   на сервер. Если есть AAAA-записи, IPv6 тоже должен вести на этот сервер.
2. Откройте входящие 80/443 и SSH-порт. Порты 8000/8001 слушают только loopback.
3. Добавьте платформу к существующему nginx с административного аккаунта после
   переключения DNS:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i 'SERVER_IP,' infra/playbooks/setup-platform.yml \
  -u root -e ansible_port=22
```

При парольном административном доступе добавьте `--ask-pass`, а если нужны права
sudo — `--ask-become-pass`. Пароли вводите в приглашении Ansible. Пользователя
деплоя можно задать через `-e deploy_user=user`.

Для нового сервера вместо `setup-platform.yml` используйте `setup-server.yml`:
он сначала устанавливает Hugo Extended 0.164.0 и основной сайт.

Playbook платформы устанавливает `uv` версии 0.10.12, nginx, Certbot и инструменты SSH-деплоя,
создаёт отдельного runtime-пользователя `throng`, один systemd unit и дополнительный
nginx-конфиг `/etc/nginx/sites-available/throng`. Сертификат двух поддоменов
хранится отдельно в `/etc/letsencrypt/live/throng-mutating.tech/`; продление
перезагружает nginx после `nginx -t`. Основной конфиг сайта остаётся самостоятельным.

Сервис включается для старта при загрузке машины. Первый CI-деплой создаёт
`/opt/mutating/current` и запускает его. На сервере не требуется Node.js:
интерфейс собирается в GitHub Actions, Python 3.12 устанавливается через `uv`.

Установленные прежде отдельные процессы API/кабинета нужно остановить перед
первым общим запуском, чтобы освободить 8000/8001. Старую SQLite при необходимости
перенесите в `/var/lib/throng/throng.db` при остановленных писателях, включая WAL
либо используя SQLite backup; владельцем должен быть `throng:throng`.

## Включение Power Cell на существующем сервере

Это отдельный административный шаг после обычного деплоя текущего кода.
Он добавляет настройки в существующий `/etc/throng/throng.env`, сохраняя БД,
CORS и остальные параметры. Обычный CI не создаёт и не заменяет CA/секреты.

По умолчанию relay доступен по `https://api.mutating.tech:8444`: используется
тот же DNS и уже выпущенный публичный сертификат API. Адрес должен вести
непосредственно на сервер. Playbook открывает порт relay в активном UFW или
firewalld и затем проверяет TCP-доступ с машины, запускающей Ansible. Relay
обслуживает сам API, с TLS 1.3, HTTP/2 и проверкой клиентского
сертификата. HTTP-прокси не должен завершать TLS этого порта.

Из корня репозитория, с административным SSH-доступом:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i 'SERVER_IP,' infra/playbooks/setup-powercell.yml -u root
```

При необходимости добавьте `--ask-pass`; для административного пользователя
с sudo используйте `-u ADMIN --ask-become-pass`. Пароли вводятся в приглашениях
Ansible. Playbook сначала проверяет, что уже развёрнут supervisor с защитой
ACME-адресов в логах. Затем устанавливает защищённый nginx-конфиг, явно создаёт
постоянный EC CA и случайный секрет восстановления, копирует действующий TLS
сертификат/ключ relay с правами `0640 root:throng`, перезапускает `throng` и
проверяет обе capabilities: enrollment и relay. Перезапуск даёт короткий перерыв
в обслуживании API и кабинета.

Порт берётся из `powercell_relay_url`, поэтому переопределение адреса меняет и
правило firewall. Существующие SSH/web-правила сохраняются; отключённый firewall
не включается. В firewalld исключение добавляется в runtime и постоянную
конфигурацию выбранной зоны без перезагрузки остальных правил. По умолчанию
используется default zone; при другой зоне внешнего интерфейса задайте
`powercell_firewalld_zone`.

Проверка внешнего порта выполняется с Ansible-контроллера без sudo. Если порт
недоступен, playbook завершается ошибкой: возможны неверный DNS, bind-адрес,
собственные nftables/iptables-правила или фильтрация провайдера. Для собственных
firewall-ролей можно передать `-e powercell_manage_firewall=false`; они должны
сами разрешить порт. `-e powercell_verify_external=false` отключает только
внешнюю проверку, например для подготовки из закрытой сети. Внешний firewall
провайдера требует отдельного доступа к его API, если такая фильтрация включена.

CA и ключ находятся в `/etc/throng/powercell/issuer/`, копия секрета восстановления —
в `/etc/throng/powercell/recovery.secret`. Их нужно включить в защищённые резервные
копии вместе с `/etc/throng/throng.env` и БД. Повторный запуск сохраняет существующие
CA и секрет, а при неполной или противоречивой конфигурации останавливается.
Автоматической ротации CA нет. Ранее настроенные внешние CA/ключ и секрет
подхватываются из env и сохраняются; пользователь `throng` должен иметь доступ
к этим внешним файлам.

Hook `/etc/letsencrypt/renewal-hooks/deploy/throng-powercell` реагирует только
на выбранный Certbot lineage. Он публикует полную новую пару TLS через атомарную
ссылку `/etc/throng/powercell/tls` и перезапускает активный `throng`. Неактивный
сервис не запускается. Продление сертификатов других сайтов его не затрагивает.

Адреса, каталог и lineage можно переопределить переменными
`powercell_acme_url`, `powercell_relay_url`, `powercell_relay_host`,
`powercell_directory`, `powercell_env_file`, `powercell_tls_lineage`.
Для другого домена relay сначала подготовьте DNS и публичный сертификат на
этот домен; helper проверяет имя, срок действия и соответствие ключа сертификату.

После настройки проверьте публичную доступность:

```sh
curl --fail https://api.mutating.tech/v1/agent-capabilities
```

В `extensions` должны присутствовать `powercell.enrollment.v1` и
`powercell.relay.v1`, а `attestation_required` — быть `true`. Окончательная
проверка выполняется на физическом Mac: вход, скачивание профиля, ручная
установка в системных настройках, аттестация Apple и завершение подключения.

При дальнейшем деплое сохранённый production env и файлы `/etc/throng/powercell`
не заменяются. Для смены issuer или секрета требуется отдельная процедура
ротации с учётом уже выпущенных сертификатов и незавершённых подключений.
Playbook также обновляет root-owned activation wrapper: после включения Power Cell
он отклоняет релизы без `--no-access-log` в supervisor до остановки сервиса.
Поэтому обычные CI-релизы должны содержать это изменение `deploy/run.py`.

## CI и SSH-секреты

`.github/workflows/deploy.yml` запускается при **каждом push в main**, без фильтров
по файлам. Также есть ручной `workflow_dispatch`. Сборка и деплой запускаются
без ожидания результатов тестов. `.github/workflows/tests.yml` независимо запускает
все site-, API- и web-проверки, включая HTTPS и визуальные тесты, на push в любую
ветку (в том числе `main`) и на pull request. Ошибки тестов не блокируют деплой.
Все production-деплои сериализованы; выполняющийся деплой не отменяется следующим
push. Настройка `queue: max` сохраняет ожидающие запуски вместо замены новым push;
[GitHub Actions допускает до 100 запусков в очереди](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency#example-queueing-multiple-pending-runs).

В GitHub-репозитории добавьте Actions secrets:

| Secret | Значение |
| --- | --- |
| `DEPLOY_HOST` | Публичный IPv4-адрес или DNS-имя сервера, доступное из GitHub |
| `DEPLOY_PORT` | SSH-порт, например `22` |
| `DEPLOY_USER` | Пользователь из playbook, по умолчанию `user` |
| `DEPLOY_PASSWORD` | Пароль этого SSH-пользователя, как в `site` |

Достаточно четырёх прежних SSH-секретов. Дополнительный `DEPLOY_KNOWN_HOSTS`
не требуется: проверенный публичный SSH-ключ сервера сохранён в
`deploy/known_hosts`. Это публичный ключ, его можно хранить в Git.
CI использует `StrictHostKeyChecking=yes` и `HostKeyAlias=mutating-production`,
поэтому проверка сохраняется при использовании IP, DNS-имени или другого SSH-порта.
При пересоздании сервера сначала проверьте новый ключ через доверенный доступ,
затем обновите `deploy/known_hosts`. Пароль в репозитории не хранится.

Deployment user записывает только исходники и окружения в `/opt/mutating/releases/`
и Python в `/opt/mutating/python/`. Секреты и база недоступны ему напрямую. Для
активации разрешён один root-owned скрипт `/usr/local/sbin/mutating-activate` через
sudo без пароля; миграции и SQLite backup внутри него выполняются от `throng`.

## Что происходит при обновлении

1. CI собирает Hugo в `site/public/` с origin `https://mutating.tech/` и интерфейс с `VITE_SITE_ORIGIN=https://mutating.tech` и передаёт
   исходники API/веб-сервера, готовые `site/public/` и `platform/web/dist/`
   через rsync в новый каталог релиза. Локальные
   `.env`, виртуальные окружения, базы и тестовые данные исключены из передачи.
2. На сервере `uv sync --project platform/api --frozen --no-dev` готовит отдельное Python-окружение.
   Ошибка сборки/установки не останавливает работающий сервис.
3. Скрипт активации получает файловую блокировку и останавливает общий сервис.
4. SQLite backup API сохраняет согласованную копию БД, включая данные из WAL,
   в `/var/lib/throng/backups/<release>-<timestamp>/throng.db` и проверяет её целостность.
5. Alembic применяет все неприменённые миграции до `head`. При ошибке до запуска
   приложений копия БД восстанавливается и прежний активный сервис запускается снова.
6. Атомарно переключается `/opt/mutating/current`. Запускается единый systemd-сервис,
   проверяется готовность API и наличие HTML кабинета. Повторная проверка миграций
   при обычном старте не применяет уже выполненные миграции заново.
7. Скрипт синхронизирует `site/public/` в `/var/www/mutating.tech/`. CI проверяет
   публичные HTTPS-адреса сайта, API и кабинета через nginx.

В Actions загрузка, подготовка Python и активация показаны отдельными шагами.
Загрузка сверяет содержимое (`--checksum`) с зафиксированным текущим релизом
и использует `rsync --copy-dest`: неизменившиеся файлы копируются на сервере,
а по сети передаются изменения. Новые релизы имеют собственные файлы без общих
hard links. Это также работает при изменившихся после checkout временах файлов;
первый релиз без `/opt/mutating/current` загружается целиком. Из `site/`
передаётся только результат Hugo в `site/public/`: исходные картинки, темы,
контент и промежуточные ресурсы на сервере не нужны и не дублируют готовый сайт.

Для CSS библиотеки с хешем в `/ui/styles/` nginx должен выдавать тот же
`Cache-Control: public, max-age=31536000, immutable`, что и для `/css/`.
Правило уже находится в `infra/playbooks/setup-site.yml`. Для существующего
сервера сохранена отдельная операция `infra/playbooks/update-site-ui-cache.yml`:
она добавляет `/ui/styles/` в действующее правило, сохраняя остальную конфигурацию.
Запускайте из корня репозитория с административным доступом:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i 'SERVER_IP,' infra/playbooks/update-site-ui-cache.yml \
  -u root --ask-pass
```

Для предварительного просмотра добавьте `--check --diff`. При доступе по ключу
замените `--ask-pass` на `--private-key /private/path/to/key`; для sudo добавьте
`--ask-become-pass`. Проверенный SSH host key берётся из `deploy/known_hosts`.
Пароль вводится в приглашении Ansible и в файлы проекта не записывается.

Playbook проверяет активный symlink и исходную конфигурацию, сохраняет резервную
копию рядом с TLS-конфигом, выполняет `nginx -t` и reload. При ошибке проверки
или reload восстанавливает копию и сообщает об ошибке. Повторный запуск не меняет
уже обновлённый конфиг и не перезагружает nginx. После применения Ansible
проверяет заголовки опубликованных CSS с контроллера; если новая UI-сборка ещё не
опубликована, сообщает о пропуске этой проверки. Параметры путей и домена доступны
через `nginx_ui_cache_config`, `nginx_ui_cache_enabled` и `nginx_ui_cache_origin`.
Обычный CI-пользователь может активировать релиз, но не менять конфигурацию nginx.

При подготовке права на чтение выдаются только файлам deployment user;
чужие файлы и кеши `__pycache__` в общем Python не изменяются.

CI выводит общий прогресс rsync, скорость, итоговую статистику, начало и конец
каждого этапа с длительностью. Долгие этапы каждые 15 секунд подтверждают, что
ещё выполняются. Ограничения: SSH preflight — 60 секунд, передача — 8 минут
и не более 60 секунд без I/O, подготовка Python — 10 минут, наблюдение за
активацией — 7 минут. SSH дополнительно проверяет соединение каждые 15 секунд
и прерывается после трёх пропущенных ответов. Общий предел job — 40 минут;
каждый из трёх этапов завершается раньше со своей ошибкой и кодом выхода.

Активация запускается на сервере независимо от SSH-наблюдателя: отмена CI или
обрыв связи не прерывают SQLite backup, миграцию или восстановление БД.
Её журнал находится в `/opt/mutating/releases/<release>/.activation/output.log`,
а файл `status` в том же каталоге появляется атомарно после завершения
(содержимое `0` означает успех). Отсутствие `status` не означает успех.
Повторное выполнение `bash deploy/upload.sh <release> activate` подключается
к журналу того же процесса и возвращает сохранённый код без повторной активации.
После таймаута наблюдения сначала проверьте этот журнал и `status`: серверный
процесс может продолжать работу. Для отдельной загрузки или подготовки доступны
фазы `upload` и `prepare`; вызов без фазы сохраняет прежний порядок всех операций.

Во время остановки, миграций и запуска возможен короткий перерыв с `502` от nginx.
Если новое приложение не запускается после успешной миграции, сервис останавливается,
CI завершается ошибкой, а новая БД сохраняется для разбора. Автоматический откат
БД после запуска API мог бы удалить уже принятые новые записи и поэтому не выполняется.
Предыдущие каталоги релизов и резервные копии сохраняются; их очистка — отдельная
операция после проверки нужных копий.

## Данные и управление

| Путь | Содержимое |
| --- | --- |
| `/var/lib/throng/throng.db` | Рабочая SQLite; при работе также возможны `-wal` и `-shm` |
| `/var/lib/throng/artifacts/` | Артефакты, аватары и другие данные API |
| `/var/lib/throng/backups/` | Копии SQLite перед обновлениями; файлы артефактов не копируются |
| `/etc/throng/throng.env` | Настройки API, CORS и публичного адреса API для кабинета |
| `/opt/mutating/releases/` | Hugo, API, интерфейс и `platform/api/.venv` каждого релиза |
| `/opt/mutating/current` | Ссылка на релиз, соответствующий текущей схеме БД |

Cookie `tmc_session` выдаётся самим API: Secure/HttpOnly, без Domain,
`Path=/v1/`, SameSite=Lax. Она содержит случайный API-токен; в БД сохраняется
только его хэш. Отдельного хранилища веб-сессий и ключа шифрования кабинета нет.
Для CORS и CSRF используется список точных origin `THRONG_BROWSER_ORIGINS`,
по умолчанию `["https://platform.mutating.tech"]`. В production
`THRONG_BROWSER_SECURE_COOKIE=true`. Кабинет получает публичный адрес из
`DASHBOARD_API_URL` (по умолчанию `https://api.mutating.tech/v1`).
Прежние `DASHBOARD_SESSION_KEY`, `DASHBOARD_SECURE_COOKIE` и
`DASHBOARD_PUBLIC_ORIGIN` больше не используются.
В SQLite одновременно работает один API/контроллер; не запускайте дополнительные
Uvicorn workers или старый независимый API поверх этой базы.

Одна команда запускает оба приложения:

```sh
sudo systemctl start throng
```

Остальные команды администратора:

```sh
sudo systemctl restart throng
sudo systemctl stop throng
sudo systemctl status throng
sudo journalctl -u throng -f
```

Общий `infra/playbooks/setup-server.yml` последовательно запускает `setup-site.yml`
и `setup-platform.yml`; их можно применять отдельно по необходимости.

При изменении systemd unit, root-owned скрипта активации или nginx-шаблона повторно
примените playbook. Обычный push обновляет приложения и миграции; изменение
административных настроек сервера выполняется явно.

Актуальный nginx-шаблон передаёт все пути платформы приложению без переписывания
или перенаправления корня. На сервере со старой конфигурацией сначала примените
`setup-platform.yml`, затем обновите приложение.

## Диагностика публичного доступа

Проверки с контроллера и из разных стран выполняются через read-only playbook.
На контроллере нужны Ansible, `curl` и `dig`; административный доступ к серверу
для этих внешних проверок не требуется. Check-Host и Globalping получают только
публичные имена сайтов, без паролей, cookie и ключей. Результаты доступны через
их публичные отчёты; у Globalping срок хранения ограничен.

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i localhost, infra/playbooks/diagnose-public-access.yml \
  --tags dns,transport,global,globalping
```

Теги: `dns` — A/AAAA/NS/CAA через Cloudflare DNS и Google DNS;
`transport` — HTTP по имени и IP, TLS с SNI и без него, SSH banner и контрольный
`example.com`; `local` — подробные времена `curl` для четырёх доменов по
HTTP/HTTPS с `-4` и `-6`; `global` — DNS/HTTP/HTTPS/TCP через Check-Host;
`globalping` — независимые HTTPS-проверки Globalping с ASN источника, временем
TCP/TLS и проверкой сертификата. При отсутствии AAAA результат `curl -6` может
отражать IPv4-mapped адрес VPN; он не доказывает доступность настоящего IPv6.

Можно передать `access_domain`, `access_address` (IP для обхода DNS в транспортной
проверке), `access_hosts`, `access_checks`, `access_probe_nodes` и
`access_check_host_nodes`. Для проверки Финляндии и США:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i localhost, infra/playbooks/diagnose-public-access.yml --tags globalping \
  -e '{"access_hosts":["mutating.tech"],"access_globalping_locations":[{"country":"FI","limit":6},{"country":"US","limit":8}]}'
```

Чтобы повторить проверку с тех же узлов, передайте ID предыдущего измерения
строкой: `-e '{"access_globalping_locations":"MEASUREMENT_ID"}'`.
Узлы, выбранные только по стране/ASN, могут меняться между запросами.
`access_globalping_type` и `access_globalping_options` позволяют менять тип
измерения и параметры согласно API Globalping. По умолчанию используется HEAD:
`405` кабинета и `404` корня API означают полученный HTTP-ответ, а не сетевой
тайм-аут. Для проверки страниц используйте
`-e '{"access_globalping_options":{"protocol":"HTTPS","request":{"method":"GET"}}}'`.

Отказ отдельного публичного узла перепроверьте на `example.com` с того же узла:
неисправность пробника нельзя приписывать сайту. Доступный TCP-порт сам по себе
не доказывает успешный TLS/HTTP. При тайм-ауте после TCP сравните SNI и прямой IP,
другие домены сервера и контрольный сайт. Проверки firewall, nginx и захват
пакетов на production также выполняются только через Ansible. Разбор инцидента
23 октября 2026 года сохранён в `docs/public-access-2026-10-23.md`.

Дополнительные транспортные проверки запускаются явно, поскольку включают
несколько повторных соединений:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i localhost, infra/playbooks/diagnose-public-access.yml --tags profiles
```

Они сравнивают TLS 1.2/1.3, минимальный HTTP-запрос, SSH с разным порядком
отправки banner и счётчики повторной передачи TCP в macOS. `access_profile_filter`
оставляет только имена проверок, содержащие заданную подстроку. Поле `mss`
показывает фактический размер сегмента: запрошенный TCP_MAXSEG может не примениться
после connect в macOS. Для этих проверок VPN отключать не требуется. Пробы
`physical_interface` по умолчанию выключены; они добавляются только при явном
задании `access_physical_interface` и создают отдельный сокет с привязкой к
этому интерфейсу.

Действующий nginx, listeners, firewall и локальный HTTPS проверяет отдельный
read-only playbook с административным доступом. Пароль вводится в приглашении:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i 'SERVER_IP,' infra/playbooks/diagnose-server-access.yml -u root --ask-pass \
  -e access_server_first_ssh=true
```

`access_server_first_ssh=true` включает ProxyCommand внутри транспорта Ansible:
он сначала получает SSH banner сервера, затем передаёт поток клиента. SSH
по-прежнему проверяет закреплённый host key из `deploy/known_hosts` и выполняет
аутентификацию и шифрование. Обход нужен для воспроизведённого случая, когда
соединение с немедленной отправкой client banner зависает. Настройки VPN и
production он не меняет.

Для сопоставления запроса контроллера с пакетами на интерфейсе сервера:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i 'SERVER_IP,' infra/playbooks/diagnose-server-access.yml -u root --ask-pass \
  -e access_server_first_ssh=true -e access_capture=true \
  -e access_probe_source=PROBLEM_PUBLIC_IPV4 --tags capture
```

Захват использует Python AF_PACKET, поэтому устанавливать tcpdump не нужно.
Он печатает только метаданные TCP портов 80/443: время, направление, адреса,
порты, seq/ack, флаги и длину данных; содержимое пакетов не печатается и не
записывается в pcap. По умолчанию окно — 45 секунд, задаётся
`access_capture_seconds`. Включать `access_capture_all_sources=true` следует
только при необходимости сравнить проблемный поток с независимым контрольным
источником. Временные метки запроса и захвата позволяют проверить их пересечение.

Чтобы локализовать потери после установления TCP, запустите TTL-пробы вместе
с серверным захватом. Один TCP-поток последовательно отправляет данные с разным
TTL; ICMP Time Exceeded сопоставляется по портам и seq. Для HTTPS используется
настоящий TLS ClientHello, для HTTP — короткие части запроса:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i 'SERVER_IP,' infra/playbooks/diagnose-server-access.yml -u root --ask-pass \
  -e access_server_first_ssh=true -e access_capture=true \
  -e access_probe_source=PROBLEM_PUBLIC_IPV4 -e access_capture_edge=true \
  -e access_edge_port=443 -e access_capture_seconds=15 \
  -e access_capture_max_packets=400 --tags capture
```

`access_edge_port=80` выбирает HTTP. Пробы меняют TTL только своих сокетов,
не маршруты и не VPN. Отсутствие ICMP-ответа само по себе не доказывает потерю
на конкретном узле; наличие ответа доказывает достижение этого узла пакетом.
Серверный захват позволяет проверить, пришли ли эти данные на VM.

Без административного доступа доступны отдельные локальные TTL-пробы и
ICMP/UDP traceroute через текущий VPN:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i localhost, infra/playbooks/diagnose-public-access.yml --tags edge \
  -e access_edge_fixed_flow=true -e access_edge_port=443
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i localhost, infra/playbooks/diagnose-public-access.yml --tags trace
```

TCP traceroute в macOS может потребовать прав для BPF; TTL-пробы `edge`
используют ICMP datagram socket и этого доступа не требуют.

## Проверки

```sh
uv run --project platform/api pytest -c platform/api/pyproject.toml -q tests/platform/api/deployment
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook --syntax-check \
  -i localhost, infra/playbooks/setup-server.yml
bash -n deploy/upload.sh deploy/deploy.sh deploy/activate.sh
```

Проверки используют временную SQLite и реальные API, кабинет и nginx с отдельными
портами и временным TLS-сертификатом. Они проверяют выбор приложения по Host,
статику, CSRF/cookie, сохранение данных и сессии после перезапуска и остановку
обоих компонентов при завершении одного. Системный nginx и рабочая база не
затрагиваются. Для сетевой проверки нужен собранный `platform/web/dist/`, nginx и
OpenSSL; она запускается в группе web общего CI после сборки.

## GitHub App через Ansible

Секреты GitHub устанавливаются playbook, исходный JSON хранится вне checkout:

```sh
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ansible-playbook \
  -i 'SERVER_IP,' infra/playbooks/setup-github-actions.yml -u root \
  -e github_credentials_file=/private/path/github-app.json
```

Схема JSON: `app_id`, `slug`, `client_id`, `client_secret`, `webhook_secret`,
`token_key`, `private_key_path` (локальный PEM), `public_api_url`, `dashboard_url`.
Для текущего стенда файл находится в защищённом локальном каталоге
`~/.config/mutating/server-dev/github-app.json`, права `0600`.
Пароль подключения можно передать через `--ask-pass` либо отдельный приватный
vars-файл; он не входит в репозиторий.

Playbook сначала требует релиз с GitHub API, затем копирует App key в
`/etc/throng/github/app.pem` (`root:throng`, `0640`), добавляет управляемый блок в
существующий `/etc/throng/throng.env` с резервной копией и перезапускает сервис
только при изменении конфигурации. Параметры БД и Power Cells сохраняются.
Несовпадение уже установленного App ID или постоянного Fernet key останавливает
playbook: их замена требует отдельной миграции OAuth credentials. Все операции
с секретами выполняются с `no_log` и без diff. Повторный запуск должен быть
идемпотентным.

Для подготовленного локально архива релиза предусмотрен
`infra/playbooks/deploy-platform.yml`. В архив входят код и собранный
`platform/web/dist`, исключаются `.git`, `.env*`, виртуальные окружения,
node_modules, данные и тестовые результаты. Передайте `release_archive` и новый
`release_id`; он должен соответствовать `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`.
При обновлении только API/кабинета можно передать `release_reuse_static_site=true`:
в архиве тогда не должно быть `site/public`, и playbook сохранит статическую
сборку действующего релиза. В архиве должен присутствовать каталог `site`.

Playbook распаковывает код в новый каталог, готовит окружение от deployment user
и вызывает существующий root-owned activation wrapper. Wrapper сохраняет SQLite
backup, применяет миграции, проверяет готовность и не активирует релиз, если за
время подготовки изменился `/opt/mutating/current`. Установку credentials App
выполняйте после успешного обновления кода.

16 октября 2026 года этими playbook развёрнут релиз
`github-ci-20261016-163116` и установлен тестовый App `mutating-power-cells-dev`.
Повторное применение `setup-github-actions.yml` завершилось с `changed=0`, без
перезапуска сервиса. GitHub подтвердил HTTP 202 при повторной доставке `ping`
и `workflow_run`. Подробности живой проверки и оставшееся ограничение GitHub
billing — в [инструкции стенда](github-actions-dev-setup.md).
